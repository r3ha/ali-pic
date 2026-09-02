"""Developer utility. Runtime never downloads models or sends pictures anywhere."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app_config import ResourceError, load_config
from model_store import MODEL_MD5, MODEL_URL, verify_model


def verify_default_download(path: Path) -> None:
    # Only our automatic default download has a known checksum. User-supplied
    # weights are checked for availability, never tied to the U2-Net checksum.
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "md5").hexdigest()
    if digest != MODEL_MD5:
        raise ResourceError("默认 U²-Net 模型下载校验失败，请重试或手动提供模型文件。")


def prepare_model(source: Path | None = None) -> Path:
    config = load_config(ROOT).rembg
    destination = config.model_path
    if destination.exists():
        verify_model(destination)
        print(f"Local model available ({config.model}): {destination}")
        return destination
    if source is None and config.model != "u2net":
        raise ResourceError(
            f"找不到 {config.model} 的本地模型：{destination}\n"
            "请将对应模型放入 rembg.model_path，或使用 --from-file 导入；不会下载 U²-Net 代替。"
        )
    if source is not None:
        verify_model(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="model-", suffix=".part", dir=destination.parent)
    try:
        with os.fdopen(handle, "wb") as output:
            if source is not None:
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, output)
            else:
                print("Downloading official U2-Net model (~176 MB); developer preparation only.", flush=True)
                with urllib.request.urlopen(MODEL_URL, timeout=60) as stream:
                    shutil.copyfileobj(stream, output, length=1024 * 1024)
        verify_model(Path(temporary))
        if source is None:
            verify_default_download(Path(temporary))
        os.replace(temporary, destination)
        print(f"Model prepared: {destination}")
        return destination
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-file", type=Path, help="copy a local ONNX model matching config.json without networking")
    options = parser.parse_args()
    try:
        prepare_model(options.from_file)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
