"""Single-image pipeline; no console input and no dependency on the working directory."""
from __future__ import annotations

import logging
import os
import stat
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from app_config import Config, ResourceError, load_config, parse_directory
from model_store import verify_model
from processors.remove_bg import create_session, remove_background
from processors.resize import prepare_subject, resize_image
from processors.watermark import add_watermark, load_watermark

SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})
INTERNAL_DIRECTORIES = frozenset({"output", "assets", "models", "logs", "processors", "tools", "tests", "build", "dist", ".venv", ".venv-build", ".cache", "__pycache__", "_internal", "legacy"})
Progress = Callable[[str], None]
LOG = logging.getLogger("image_pipeline")
LOG.addHandler(logging.NullHandler())


@dataclass
class Context:
    config: Config
    watermark: Image.Image
    session: object


@dataclass
class FileResult:
    source: Path
    saved: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    error: str | None = None


@dataclass
class BatchResult:
    input_dir: Path
    output_dir: Path
    files: list[FileResult] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def successful(self) -> int:
        return sum(bool(f.saved) and not f.error for f in self.files)

    @property
    def failed(self) -> int:
        return sum(f.error is not None for f in self.files)

    @property
    def skipped(self) -> int:
        return sum(not f.saved and not f.error for f in self.files)


def _linked_directory(path: Path) -> bool:
    if path.is_symlink():
        return True
    if path.exists():
        # Also catch Windows junctions on Python 3.11 (Path.is_junction is 3.12+).
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return False


def validate_input_directory(path: str | Path, config: Config) -> Path:
    directory = parse_directory(path)
    if any(p.name.casefold() == "output" for p in (directory, *directory.parents)):
        raise ValueError("不能将 output 目录或其子目录作为原图目录。请选择原始图片目录。")
    if directory == config.root:
        raise ValueError("程序目录不能作为原图目录。请将产品原图放在单独的目录。")
    if directory.is_relative_to(config.root):
        part = directory.relative_to(config.root).parts[0].casefold()
        if part in INTERNAL_DIRECTORIES:
            raise ValueError("不能处理程序内部资源目录。请选择原始图片目录。")
    for resource_dir in {config.watermark.path.parent, config.rembg.model_path.parent}:
        if directory == resource_dir or directory.is_relative_to(resource_dir):
            raise ValueError("不能将水印或模型资源目录作为原图目录。")
    return directory


def discover_images(directory: Path, config: Config) -> list[Path]:
    # No recursion: output, app directories, temp directories can never be scanned.
    excluded = {config.watermark.path, config.root / "watermark.png"}
    return sorted(
        (p for p in directory.iterdir() if p.is_file() and not p.is_symlink()
         and p.suffix.lower() in SUPPORTED_EXTENSIONS and p.resolve() not in excluded),
        key=lambda p: (p.name.casefold(), p.name),
    )


def plan_outputs(sources: list[Path], variants: tuple[str, ...], output: Path) -> dict[Path, dict[str, Path]]:
    """Reserve all names first, case-insensitively, including variant-name collisions."""
    entries = [(p, v, p.stem + ("_效果图" if v == "golden" else "") + ".png") for p in sources for v in variants]
    counts = Counter(name.casefold() for _, _, name in entries)
    reserved = {name.casefold() for _, _, name in entries if counts[name.casefold()] == 1}
    plans = {p: {} for p in sources}
    for source, variant, name in entries:
        if counts[name.casefold()] > 1:
            base = source.name + ("_效果图" if variant == "golden" else "")
            name = base + ".png"
            serial = 2
            while name.casefold() in reserved:
                name = f"{base}_{serial}.png"
                serial += 1
            reserved.add(name.casefold())
        plans[source][variant] = output / name
    return plans


def validate_environment(config: Config) -> Image.Image:
    # Both assets are checked BEFORE model initialization or processing any input.
    watermark = load_watermark(config.watermark)
    verify_model(config.rembg.model_path)
    return watermark


def save_png(image: Image.Image, destination: Path, config: Config) -> bool:
    """Encode only once, then publish atomically. Never leave a partial final PNG."""
    if destination.is_symlink():
        raise ValueError(f"输出文件是符号链接，拒绝写入：{destination}")
    handle, temporary = tempfile.mkstemp(prefix=".image-pipeline-", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(handle, "wb") as stream:
            image.save(stream, format="PNG", dpi=config.output.dpi, optimize=True)
            stream.flush()
            os.fsync(stream.fileno())
        if config.output.existing == "overwrite":
            os.replace(temporary, destination)
        else:
            try:
                if os.name == "nt":
                    # Windows rename refuses an existing destination, including FAT/exFAT.
                    os.rename(temporary, destination)
                else:
                    os.link(temporary, destination)
            except FileExistsError:
                return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def process_image(input_path: Path, output_paths: dict[str, Path], context: Context,
                  progress: Progress | None = None) -> FileResult:
    emit = progress or (lambda message: None)
    config = context.config
    result = FileResult(input_path)
    pending = {}
    for variant, path in output_paths.items():
        if config.output.existing == "skip" and path.is_file() and not path.is_symlink():
            result.skipped.append(path)
            emit(f"  跳过已有文件：{path.name}")
        else:
            pending[variant] = path
    if not pending:
        LOG.info("SKIP %s -> %s", input_path, result.skipped)
        return result
    try:
        with Image.open(input_path) as source:
            # Camera JPEGs can contain an MPF/MPO preview (these real inputs do).
            # Legacy rembg reads frame zero, the full-size primary image.
            if getattr(source, "n_frames", 1) > 1 and source.format != "MPO":
                raise ValueError("暂不支持动画或多页图片，请先导出为单张图片。")
            source.load()
            # Do not convert before rembg: match CLI behavior for JPEG, palette and EXIF.
            cutout = remove_background(source, context.session, config.rembg)
        emit("  去背景完成")
        subject = prepare_subject(cutout, config.resize)
        for variant, destination in pending.items():
            image = resize_image(subject, config.resize, variant)
            emit(f"  {variant}：尺寸和位置调整完成")
            image = add_watermark(image, context.watermark, config.watermark, background=config.resize.background)
            emit("  水印添加完成")
            if save_png(image, destination, config):
                result.saved.append(destination)
                LOG.info("SAVED %s -> %s", input_path, destination)
                emit(f"  已保存：{destination.name}")
            else:
                result.skipped.append(destination)
                LOG.info("SKIP concurrent destination %s", destination)
                emit(f"  跳过已有文件：{destination.name}")
    except Exception as exc:
        result.error = str(exc) or type(exc).__name__
        LOG.exception("FAILED %s; already saved=%s", input_path, result.saved)
        emit(f"  失败：{result.error}")
    return result


def process_directory(input_dir: str | Path, *, config: Config | None = None,
                      progress: Progress | None = None, context: Context | None = None) -> BatchResult:
    """The public API. A supplied Context lets a GUI reuse the model across batches."""
    started = time.monotonic()
    emit = progress or (lambda message: None)
    config = config or (context.config if context else load_config())
    if context and context.config != config:
        raise ValueError("Context 与 config 不一致。")
    directory = validate_input_directory(input_dir, config)
    output = directory / "output"
    if _linked_directory(output):
        raise ValueError("output 不能是符号链接或 Windows 目录联接。")
    watermark = validate_environment(config)
    sources = discover_images(directory, config)
    output.mkdir(exist_ok=True)
    result = BatchResult(directory, output)
    emit(f"输入目录：{directory}\n输出目录：{output}\n找到 {len(sources)} 张图片。")
    LOG.info("INPUT %s; OUTPUT %s; COUNT %d", directory, output, len(sources))
    plans = plan_outputs(sources, config.resize.variants, output)
    needs_model = any(config.output.existing == "overwrite" or not p.is_file() or p.is_symlink()
                      for paths in plans.values() for p in paths.values())
    if needs_model and context is None:
        emit("正在加载去背景模型，请稍候……")
        try:
            context = Context(config, watermark, create_session(config.rembg))
        except Exception as exc:
            LOG.exception("rembg initialization failed")
            raise ResourceError(f"去背景模型初始化失败：{exc}\n请查看日志；Windows 上请使用完整的发布目录。") from exc
        LOG.info("Model loaded; %s; CPUExecutionProvider; %s", config.rembg.model, config.rembg.model_path)
        emit("模型加载完成。")
    # No pending files => process_image returns before accessing this empty session.
    context = context or Context(config, watermark, None)
    for index, source in enumerate(sources, 1):
        emit(f"\n[{index}/{len(sources)}] {source.name}")
        result.files.append(process_image(source, plans[source], context, emit))
    result.elapsed = time.monotonic() - started
    LOG.info("SUMMARY total=%d successful=%d failed=%d skipped=%d elapsed=%.2fs",
             len(result.files), result.successful, result.failed, result.skipped, result.elapsed)
    return result
