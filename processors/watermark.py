"""Load once; default placement reproduces the legacy RGB paste operation."""
from PIL import Image

from app_config import ResourceError, WatermarkConfig


def load_watermark(config: WatermarkConfig) -> Image.Image:
    if not config.path.is_file():
        raise ResourceError(f"找不到水印文件：\n{config.path}")
    try:
        with Image.open(config.path) as source:
            watermark = source.convert("RGBA")
        dimensions = tuple(round(v * config.scale) for v in watermark.size)
        if min(dimensions) < 1 or max(dimensions) > 10000:
            raise ValueError("缩放后的水印边长必须在 1 到 10000 像素之间。")
        if config.scale != 1.0:
            watermark = watermark.resize(dimensions, Image.Resampling.LANCZOS)
        if config.opacity != 1.0:
            watermark.putalpha(watermark.getchannel("A").point(lambda p: round(p * config.opacity)))
        return watermark
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ResourceError(f"无法加载水印：{config.path}（{exc}）") from exc


def add_watermark(image: Image.Image, watermark: Image.Image, config: WatermarkConfig, *, background: str) -> Image.Image:
    if background == "white":
        # RGBA.paste(mask=watermark) would square the watermark's alpha.
        # Legacy RGB paste keeps the canvas opaque and preserves its exact RGBs.
        result = image.convert("RGB")
        result.paste(watermark, config.position, mask=watermark)
        return result.convert("RGBA")
    result = image.convert("RGBA")
    result.alpha_composite(watermark, dest=config.position)
    return result
