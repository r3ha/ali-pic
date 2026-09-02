"""User-facing CLI. Core pipeline remains independent of terminal interaction."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from app_config import application_root, load_config


def setup_logging(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    candidates = [root / "logs"]
    local_data = os.environ.get("LOCALAPPDATA")
    if local_data:
        candidates.append(Path(local_data) / "ImagePipeline" / "logs")
    candidates.append(Path(tempfile.gettempdir()) / "ImagePipeline" / "logs")
    for folder in candidates:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{stamp}.log"
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger = logging.getLogger("image_pipeline")
            logger.setLevel(logging.INFO)
            for old in logger.handlers[:]:
                old.close()
                logger.removeHandler(old)
            logger.addHandler(handler)
            logger.propagate = False
            return path
        except OSError:
            continue
    raise OSError("无法创建日志文件，请将程序移到可写目录后运行。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="图片批处理：去背景 → 尺寸和位置 → 水印 → output")
    parser.add_argument("directory", nargs="?", help="原始图片目录；省略则交互输入")
    parser.add_argument("--no-pause", action="store_true", help="结束后不等待回车，适用于自动化")
    parser.add_argument("--check", action="store_true", help="检查配置、水印和模型文件可读性后退出（不加载模型）")
    args = parser.parse_args(argv)
    # ASCII fallback avoids a UnicodeEncodeError in older redirected Windows consoles.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    log_path = None
    print("Image Processing Pipeline\n图片批处理工具\n", flush=True)
    try:
        log_path = setup_logging(application_root())
        logger = logging.getLogger("image_pipeline")
        logger.info("START executable=%s frozen=%s root=%s", sys.executable, getattr(sys, "frozen", False), application_root())
        config = load_config()
        # Import Pillow/core inside the error boundary: missing dependencies are readable.
        from pipeline import process_directory, validate_environment, validate_input_directory

        validate_environment(config)
        if args.check:
            print("配置、水印和模型文件可读性检查通过（尚未验证模型兼容性）。")
            logger.info("Resource check passed")
            return 0
        directory = args.directory
        if directory is None:
            while True:
                value = input("请输入图片目录（可拖入目录，输入 q 退出）：\n").strip()
                if value.lower() == "q":
                    return 0
                try:
                    directory = validate_input_directory(value, config)
                    break
                except (ValueError, OSError) as exc:
                    print(f"错误：{exc}\n请重新输入。")
        result = process_directory(directory, config=config, progress=lambda text: print(text, flush=True))
        print("\n" + "=" * 48)
        print(f"处理完成。\n总数：{len(result.files)}\n成功：{result.successful}\n失败：{result.failed}\n跳过：{result.skipped}")
        print(f"保存成品：{sum(len(f.saved) for f in result.files)} 张\n总耗时：{result.elapsed:.1f} 秒\n输出目录：{result.output_dir}")
        for item in result.files:
            if item.error:
                print(f"\n失败文件：{item.source.name}\n原因：{item.error}")
                if item.saved:
                    print("该文件已保存的部分成品：" + ", ".join(p.name for p in item.saved))
        print("=" * 48)
        return 1 if result.failed else 0
    except (KeyboardInterrupt, EOFError):
        logging.getLogger("image_pipeline").info("Cancelled by user")
        print("\n操作已取消。已保存的成品保留在 output 中。")
        return 130
    except Exception as exc:
        if log_path:
            logging.getLogger("image_pipeline").exception("Startup/batch error")
        print(f"\n错误：{exc}")
        return 2
    finally:
        if log_path:
            print(f"\n日志：{log_path}")
        # Double-clicked Windows EXE stays open; command/CI use --no-pause.
        if not args.no_pause and not args.check and (getattr(sys, "frozen", False) or (os.name == "nt" and args.directory is None)):
            try:
                input("\n按 Enter 退出……")
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
