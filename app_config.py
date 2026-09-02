"""External resources and validated, immutable user settings (no ML imports)."""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path


# Single-file, single-mask adapters shipped with the pinned rembg version.
# SAM needs multiple files/prompts; cloth segmentation returns several masks.
SUPPORTED_MODELS = (
    "u2net", "u2netp", "u2net_human_seg", "u2net_custom", "silueta",
    "isnet-general-use", "isnet-anime", "dis_custom",
    "birefnet-general", "birefnet-general-lite", "birefnet-portrait",
    "birefnet-dis", "birefnet-hrsod", "birefnet-cod", "birefnet-massive",
    "bria-rmbg", "ben_custom",
)


class ConfigurationError(ValueError):
    pass


class ResourceError(RuntimeError):
    pass


def application_root() -> Path:
    # _MEIPASS contains bundled implementation files, never editable user resources.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def parse_directory(value: str | Path) -> Path:
    text = str(value).strip().strip("\"'").strip()
    if not text:
        raise ValueError("图片目录不能为空。")
    path = Path(text).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"找不到图片目录：{path}")
    return path


@dataclass(frozen=True)
class RemBgConfig:
    model: str
    model_path: Path
    alpha_matting: bool
    foreground_threshold: int
    background_threshold: int
    erode_size: int
    post_process_mask: bool


@dataclass(frozen=True)
class ResizeConfig:
    alpha_threshold: int
    rotate_portrait: bool
    resample: str
    background: str
    variants: tuple[str, ...]
    main_canvas: tuple[int, int]
    main_target_size: int
    main_offset: tuple[int, int]
    golden_canvas: tuple[int, int]
    golden_target: tuple[int, int]
    golden_ratio: float
    golden_offset: tuple[int, int]


@dataclass(frozen=True)
class WatermarkConfig:
    path: Path
    scale: float
    position: tuple[int, int]
    opacity: float


@dataclass(frozen=True)
class OutputConfig:
    format: str
    existing: str
    dpi: tuple[int, int]


@dataclass(frozen=True)
class Config:
    root: Path
    rembg: RemBgConfig
    resize: ResizeConfig
    watermark: WatermarkConfig
    output: OutputConfig


def _object(value, keys: str, name: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} 必须是 JSON 对象。")
    expected = set(keys.split())
    missing, extra = expected - value.keys(), value.keys() - expected
    if missing:
        raise ConfigurationError(f"{name} 缺少参数：{', '.join(sorted(missing))}")
    if extra:
        raise ConfigurationError(f"{name} 包含未知参数：{', '.join(sorted(extra))}")
    return value


def _int(value, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ConfigurationError(f"{name} 必须是 {low} 到 {high} 的整数。")
    return value


def _number(value, name: str, low: float, high: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ConfigurationError(f"{name} 必须是 {low} 到 {high} 的有限数值。")
    return float(value)


def _bool(value, name: str) -> bool:
    if type(value) is not bool:
        raise ConfigurationError(f"{name} 必须是 true 或 false。")
    return value


def _choice(value, name: str, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ConfigurationError(f"{name} 必须是：{', '.join(choices)}")
    return value


def _pair(value, name: str, low: int, high: int) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ConfigurationError(f"{name} 必须是包含两个整数的数组。")
    return tuple(_int(v, f"{name}[{i}]", low, high) for i, v in enumerate(value))


def _path(root: Path, value, name: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ConfigurationError(f"{name} 必须是有效的非空路径字符串。")
    return resource_path(root, value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(f"JSON 参数重复：{key}")
        result[key] = value
    return result


def load_config(root: Path | None = None) -> Config:
    root = (root or application_root()).resolve()
    path = root / "config.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"config.json 格式错误：第 {exc.lineno} 行，第 {exc.colno} 列。") from exc
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(f"无法读取配置文件：{path}（{exc}）") from exc
    d = _object(raw, "schema_version rembg resize watermark output", "config")
    _int(d["schema_version"], "schema_version", 1, 1)
    r = _object(d["rembg"], "model model_path alpha_matting foreground_threshold background_threshold erode_size post_process_mask", "rembg")
    rembg = RemBgConfig(
        _choice(r["model"], "rembg.model", SUPPORTED_MODELS),
        _path(root, r["model_path"], "rembg.model_path"),
        _bool(r["alpha_matting"], "rembg.alpha_matting"),
        _int(r["foreground_threshold"], "rembg.foreground_threshold", 0, 255),
        _int(r["background_threshold"], "rembg.background_threshold", 0, 255),
        _int(r["erode_size"], "rembg.erode_size", 0, 100),
        _bool(r["post_process_mask"], "rembg.post_process_mask"),
    )
    if rembg.foreground_threshold <= rembg.background_threshold:
        raise ConfigurationError("rembg.foreground_threshold 必须大于 background_threshold。")
    r = _object(d["resize"], "alpha_threshold rotate_portrait resample background variants main golden", "resize")
    m = _object(r["main"], "canvas target_size offset", "resize.main")
    g = _object(r["golden"], "canvas target ratio_threshold offset", "resize.golden")
    variants = r["variants"]
    if not isinstance(variants, list) or not variants or any(v not in ("main", "golden") for v in variants) or len(set(variants)) != len(variants):
        raise ConfigurationError('resize.variants 必须是 ["main"]、["golden"] 或 ["main", "golden"]，不能重复。')
    resize = ResizeConfig(
        _int(r["alpha_threshold"], "resize.alpha_threshold", 0, 255),
        _bool(r["rotate_portrait"], "resize.rotate_portrait"),
        _choice(r["resample"], "resize.resample", ("LANCZOS", "BICUBIC", "BILINEAR", "NEAREST")),
        _choice(r["background"], "resize.background", ("white", "transparent")),
        tuple(variants),
        _pair(m["canvas"], "resize.main.canvas", 1, 10000),
        _int(m["target_size"], "resize.main.target_size", 1, 10000),
        _pair(m["offset"], "resize.main.offset", -10000, 10000),
        _pair(g["canvas"], "resize.golden.canvas", 1, 10000),
        _pair(g["target"], "resize.golden.target", 1, 10000),
        _number(g["ratio_threshold"], "resize.golden.ratio_threshold", 0.01, 100),
        _pair(g["offset"], "resize.golden.offset", -10000, 10000),
    )
    w = _object(d["watermark"], "path scale position opacity", "watermark")
    watermark = WatermarkConfig(
        _path(root, w["path"], "watermark.path"),
        _number(w["scale"], "watermark.scale", 0.001, 10),
        _pair(w["position"], "watermark.position", -10000, 10000),
        _number(w["opacity"], "watermark.opacity", 0, 1),
    )
    o = _object(d["output"], "format existing dpi", "output")
    output = OutputConfig(
        _choice(o["format"], "output.format", ("PNG",)),
        _choice(o["existing"], "output.existing", ("skip", "overwrite")),
        _pair(o["dpi"], "output.dpi", 1, 2400),
    )
    return Config(root, rembg, resize, watermark, output)
