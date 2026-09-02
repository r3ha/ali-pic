"""Local model file checks. Downloading is a separate developer-only operation."""
from __future__ import annotations

from pathlib import Path

from app_config import ResourceError

MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx"
MODEL_MD5 = "60024c5c889badc19c04ad937298a77b"
REMBG_VERSION = "2.0.67"


def verify_model(path: Path) -> None:
    """Check availability only; ONNX Runtime validates the model when loading it."""
    if not path.is_file():
        raise ResourceError(f"找不到离线模型文件：\n{path}\n请检查 config.json 中的 rembg.model_path，并放入与 rembg.model 对应的模型。")
    try:
        with path.open("rb") as stream:
            first_byte = stream.read(1)
    except OSError as exc:
        raise ResourceError(f"无法读取模型：{path}（{exc}）") from exc
    if not first_byte:
        raise ResourceError(f"模型文件为空：\n{path}\n请放入完整模型文件。")
