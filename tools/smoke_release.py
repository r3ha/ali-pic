"""Test a COPY of a frozen onedir release, from a different CWD, without source paths."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


def smoke(executable: Path) -> None:
    executable = executable.resolve()
    with tempfile.TemporaryDirectory(prefix="ImagePipeline 发布 验证 ") as temp:
        root = Path(temp)
        release = root / "Portable App"
        shutil.copytree(executable.parent, release)
        config = json.loads((release / "config.json").read_text(encoding="utf-8-sig"))
        inputs = root / "产品图片 Test Batch"
        inputs.mkdir()
        image = Image.new("RGB", (480, 360), (238, 238, 238))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((65, 75, 415, 270), radius=25, fill=(190, 32, 23), outline=(45, 45, 45), width=8)
        draw.ellipse((85, 245, 165, 325), fill=(24, 24, 24))
        draw.ellipse((315, 245, 395, 325), fill=(24, 24, 24))
        image.save(inputs / "product.jpg")
        digest = hashlib.sha256((inputs / "product.jpg").read_bytes()).digest()
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env.pop("VIRTUAL_ENV", None)
        # Remove development Python locations; OS DLL directories remain available.
        if os.name == "nt":
            system = Path(env.get("SystemRoot", r"C:\Windows"))
            env["PATH"] = os.pathsep.join([str(system / "System32"), str(system)])
        else:
            env["PATH"] = "/usr/bin:/bin"
        command = [str(release / executable.name), str(inputs), "--no-pause"]
        result = subprocess.run(command, cwd=root, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=600)
        print(result.stdout)
        if result.returncode:
            details = "\n".join(p.read_text(encoding="utf-8") for p in (release / "logs").glob("*.log"))
            raise RuntimeError(f"Frozen inference failed ({result.returncode}): {result.stderr}\n{details}")
        for variant in config["resize"]["variants"]:
            name = "product" + ("_效果图" if variant == "golden" else "") + ".png"
            dimensions = tuple(config["resize"][variant]["canvas"])
            with Image.open(inputs / "output" / name) as output:
                assert output.format == "PNG" and output.mode == "RGBA" and output.size == dimensions
        assert hashlib.sha256((inputs / "product.jpg").read_bytes()).digest() == digest
        rerun = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=600)
        assert rerun.returncode == 0
        assert len(list((inputs / "output").glob("*.png"))) == len(config["resize"]["variants"])
        assert not list(release.glob("*.py"))
        print("Relocated frozen inference and repeat-run checks passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    smoke(parser.parse_args().executable)
