"""Windows build orchestration; image-processing logic never lives in BAT."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build() -> None:
    if sys.platform != "win32" or sys.version_info[:2] != (3, 11) or sys.maxsize <= 2**32:
        raise RuntimeError("The Windows release must be built on Windows with 64-bit Python 3.11.")
    from app_config import load_config
    from pipeline import validate_environment
    from prepare_model import prepare_model

    prepare_model()  # Existing local models are used as-is; only missing default U2-Net is downloaded.
    validate_environment(load_config(ROOT))
    # Only known generated directories; never remove the project or any input folder.
    for folder in (ROOT / "build", ROOT / "dist" / "ImagePipeline"):
        if folder.is_symlink() or not folder.resolve().is_relative_to(ROOT) or any(p.is_symlink() for p in folder.parents if p != ROOT and p.is_relative_to(ROOT)):
            raise RuntimeError(f"Refusing to clean a linked build directory: {folder}")
        if folder.exists():
            shutil.rmtree(folder)
    # Build only; image quality is reviewed manually with the packaged application.
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "ImagePipeline.spec"], cwd=ROOT, check=True)
    executable = ROOT / "dist" / "ImagePipeline" / "ImagePipeline.exe"
    print(f"\nBuild completed successfully.\nOutput:\n{executable.parent}")


if __name__ == "__main__":
    try:
        build()
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
