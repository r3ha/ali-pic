"""Keep the legacy crop, rounding, scaling and placement; preserve input direction."""
from PIL import Image

from app_config import ResizeConfig


def prepare_subject(image: Image.Image, config: ResizeConfig) -> Image.Image:
    image = image.convert("RGBA")
    # The threshold changes only the crop rectangle, never the original soft alpha.
    alpha = image.getchannel("A").point(lambda p: 0 if p < config.alpha_threshold else 255)
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError("图片完全透明，没有检测到有效主体。")
    image = image.crop(bbox)
    return image


def resize_image(image: Image.Image, config: ResizeConfig, variant: str) -> Image.Image:
    ratio = image.width / image.height
    if variant == "main":
        size = config.main_target_size
        dimensions = (size, round(size / ratio)) if ratio >= 1 else (round(size * ratio), size)
        canvas, offset = config.main_canvas, config.main_offset
    elif variant == "golden":
        width, height = config.golden_target
        # Intentionally NOT min(target_w/w, target_h/h): legacy uses 1.618.
        # For ratios in (1.5, 1.618), subject width can exceed 900. Preserve this.
        dimensions = (width, round(width / ratio)) if ratio >= config.golden_ratio else (round(height * ratio), height)
        canvas, offset = config.golden_canvas, config.golden_offset
    else:
        raise ValueError(f"未知图片类型：{variant}")
    if min(dimensions) < 1:
        raise ValueError("图片长宽比过于极端，缩放后边长不足 1 像素。")
    resized = image.resize(dimensions, getattr(Image.Resampling, config.resample))
    xy = ((canvas[0] - resized.width) // 2 + offset[0], (canvas[1] - resized.height) // 2 + offset[1])
    if config.background == "white":
        result = Image.new("RGB", canvas, (255, 255, 255))
        result.paste(resized, xy, resized.getchannel("A"))
        return result.convert("RGBA")
    result = Image.new("RGBA", canvas, (0, 0, 0, 0))
    result.alpha_composite(resized, dest=xy)
    return result
