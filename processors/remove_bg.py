"""Offline adapters: use rembg prediction with the configured local model file."""
import os
from importlib.metadata import version

from PIL import Image

from app_config import RemBgConfig, ResourceError, SUPPORTED_MODELS
from model_store import REMBG_VERSION, verify_model


def create_session(config: RemBgConfig):
    if config.model not in SUPPORTED_MODELS:
        raise ResourceError(f"不支持的 rembg.model：{config.model}。可选：{', '.join(SUPPORTED_MODELS)}")
    verify_model(config.model_path)
    # Heavy imports are lazy: resource/config failures remain quick and readable.
    import onnxruntime as ort

    if version("rembg") != REMBG_VERSION:
        raise ResourceError(f"rembg 版本不匹配，需要 {REMBG_VERSION}。请使用固定依赖重新构建。")

    from rembg.sessions import sessions

    session_class = sessions.get(config.model)
    if session_class is None:
        raise ResourceError(f"当前程序缺少模型适配器：{config.model}。请使用完整发布包。")

    class OfflineSession(session_class):
        @classmethod
        def download_models(cls, *args, **kwargs):
            # BaseSession invokes this method to obtain the path. No pooch/network.
            return str(config.model_path)

    options = ort.SessionOptions()
    # Match rembg 2.0.67's new_session() behavior.
    if "OMP_NUM_THREADS" in os.environ:
        threads = int(os.environ["OMP_NUM_THREADS"])
        if threads < 1:
            raise ResourceError("环境变量 OMP_NUM_THREADS 必须是正整数。")
        options.inter_op_num_threads = threads
        options.intra_op_num_threads = threads
    try:
        return OfflineSession(config.model, options, providers=["CPUExecutionProvider"],
                              model_path=str(config.model_path))
    except Exception as exc:
        raise ResourceError(
            f"无法加载本地模型（{config.model}）：\n{config.model_path}\n"
            f"请确认文件是完整且与 rembg.model 对应的 ONNX 模型。详情：{exc}"
        ) from exc


def remove_background(image: Image.Image, session, config: RemBgConfig) -> Image.Image:
    from rembg import remove

    return remove(
        image,
        session=session,
        alpha_matting=config.alpha_matting,
        alpha_matting_foreground_threshold=config.foreground_threshold,
        alpha_matting_background_threshold=config.background_threshold,
        alpha_matting_erode_size=config.erode_size,
        only_mask=False,
        post_process_mask=config.post_process_mask,
        # p_command's default is transparent black, not None.
        bgcolor=(0, 0, 0, 0),
    ).convert("RGBA")
