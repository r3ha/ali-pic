"""Opt-in real U2-Net/legacy comparison; not part of the lightweight unittest suite.

Run: python tests/verify_real_images.py <original1.jpg> <original2.jpg>
Requires requirements-test.txt and the verified external model. Inputs stay untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
import numpy as np
import pipeline
from app_config import load_config
from processors.watermark import load_watermark


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(left, right):
    a, b = np.array(left.convert("RGBA"), dtype=np.int16), np.array(right.convert("RGBA"), dtype=np.int16)
    assert a.shape == b.shape, (a.shape, b.shape)
    diff = np.abs(a - b)
    return {"different_pixels": int(np.any(diff != 0, axis=2).sum()), "max_channel_difference": int(diff.max()), "mean_channel_difference": float(diff.mean())}


def verify(originals: list[Path]) -> None:
    originals = [p.resolve() for p in originals]
    hashes = {str(p): digest(p) for p in originals}
    artifacts = ROOT / "tests" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="产品图片 Test Batch "))
    inputs = work / "新流程 输入"
    inputs.mkdir()
    legacy_input = work / "legacy input"
    legacy_input.mkdir()
    for original in originals:
        shutil.copy2(original, inputs / original.name)
        shutil.copy2(original, legacy_input / original.name)
    with Image.open(originals[0]) as source:
        small = source.convert("RGBA")
        small.thumbnail((800, 800))
        small.putpixel((0, 0), (0, 0, 0, 0))
        small.save(inputs / "PNG 样本.png")
        small.save(inputs / "WebP sample.webp")
    (inputs / "损坏.png").write_bytes(b"deliberately damaged image")
    (inputs / "ignored subdirectory").mkdir()
    shutil.copy2(originals[0], inputs / "ignored subdirectory" / "ignored.jpg")
    config = load_config(ROOT)
    raw_new = artifacts / "new_cutouts"
    raw_new.mkdir(exist_ok=True)
    real_remove = pipeline.remove_background

    def capture(image, session, settings):
        output = real_remove(image, session, settings)
        name = Path(image.filename).stem
        if name in {p.stem for p in originals}:
            output.save(raw_new / (name + ".png"))
        return output

    def no_network(*args, **kwargs):
        raise AssertionError("Offline runtime attempted network access")

    # One real session, damaged image isolation, all source formats, no network.
    with patch("pipeline.create_session", wraps=pipeline.create_session) as session, patch("pipeline.remove_background", side_effect=capture), patch.object(socket.socket, "connect", no_network), patch("socket.create_connection", no_network):
        result = pipeline.process_directory(f'"{inputs}"', config=config, progress=print)
        assert session.call_count == 1
    assert result.successful == len(originals) + 2 and result.failed == 1
    output_hashes = {p.name: digest(p) for p in result.output_dir.glob("*.png")}
    # Even a bad image on rerun must not cause good output to be reprocessed.
    rerun = pipeline.process_directory(inputs, config=config)
    assert rerun.skipped == len(originals) + 2 and rerun.failed == 1
    assert output_hashes == {p.name: digest(p) for p in result.output_dir.glob("*.png")}
    shutil.copytree(result.output_dir, artifacts / "new_output", dirs_exist_ok=True)

    # Actual untouched legacy CLI under the pinned version, same CPU threads and model.
    env = os.environ.copy()
    env["U2NET_HOME"] = str(config.rembg.model_path.parent)
    env.pop("MODEL_CHECKSUM_DISABLED", None)
    executable = Path(sys.executable).parent / ("rembg.exe" if os.name == "nt" else "rembg")
    old_rembg = work / "legacy rembg"
    completed = subprocess.run([str(executable), "p", str(legacy_input), str(old_rembg)], env=env, text=True, capture_output=True, timeout=600)
    (artifacts / "legacy-rembg.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    # BAT itself only appends _no_bg; preserve that naming before invoking resize.
    for path in old_rembg.glob("*.png"):
        path.rename(path.with_name(path.stem + "_no_bg.png"))
    completed = subprocess.run([sys.executable, str(ROOT / "png-resize.py")], input=str(old_rembg) + "\n", text=True, capture_output=True, timeout=120)
    (artifacts / "legacy-resize.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    old_resized = old_rembg / "final_png"
    shutil.copytree(old_resized, artifacts / "legacy_resize", dirs_exist_ok=True)

    spec = importlib.util.spec_from_file_location("legacy_watermark", ROOT / "watermark.py")
    old_watermark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old_watermark)
    # The real old watermark code sees zero files here: PNG/JPEG mismatch is recorded.
    text = io.StringIO()
    with redirect_stdout(text):
        old_watermark.batch_add_watermark_to_new_folder(str(old_resized), str(config.watermark.path))
    (artifacts / "legacy-watermark-format-gap.log").write_text(text.getvalue(), encoding="utf-8")
    assert not (old_resized / "mark").exists()

    comparison = []
    watermark = load_watermark(config.watermark)
    legacy_pixel_outputs = artifacts / "legacy_composited_before_jpeg"
    legacy_pixel_outputs.mkdir(exist_ok=True)
    for original in originals:
        stem = original.stem
        with Image.open(old_rembg / f"{stem}_no_bg.png") as old, Image.open(raw_new / f"{stem}.png") as new:
            record = {"source": original.name, "rembg": compare(old, new), "variants": {}}
        assert record["rembg"]["different_pixels"] == 0
        for variant, suffix in (("main", "_主图"), ("golden", "_效果图")):
            with Image.open(old_resized / f"{stem}_no_bg{suffix}.png") as old:
                expected = old.convert("RGB")
                expected.paste(watermark, config.watermark.position, mask=watermark)
            name = stem + ("_效果图" if variant == "golden" else "") + ".png"
            expected.save(legacy_pixel_outputs / name)
            with Image.open(result.output_dir / name) as actual:
                record["variants"][variant] = compare(expected, actual)
                assert record["variants"][variant]["different_pixels"] == 0
            # Also run the actual old watermark function on a genuine JPEG conversion.
            jpeg_dir = work / f"jpeg-{stem}-{variant}"
            jpeg_dir.mkdir()
            with Image.open(old_resized / f"{stem}_no_bg{suffix}.png") as old:
                old.convert("RGB").save(jpeg_dir / "reference.jpg", quality=95)
            with redirect_stdout(io.StringIO()):
                old_watermark.batch_add_watermark_to_new_folder(str(jpeg_dir), str(config.watermark.path))
            with Image.open(jpeg_dir / "mark" / "reference.jpg") as jpeg:
                record["variants"][variant]["jpeg_bridge_difference"] = compare(expected, jpeg)
        comparison.append(record)

    # Real single-image transparent PNG mode, through the CLI API's exact model path.
    single = work / "单张 透明"
    single.mkdir()
    shutil.copy2(inputs / "PNG 样本.png", single / "sample.png")
    transparent = replace(config, resize=replace(config.resize, background="transparent", variants=("main",)))
    with patch.object(socket.socket, "connect", no_network):
        single_result = pipeline.process_directory(single, config=transparent)
    assert single_result.successful == 1
    with Image.open(single / "output" / "sample.png") as png:
        assert png.mode == "RGBA" and png.getchannel("A").getextrema() == (0, 255)
    shutil.copy2(single / "output" / "sample.png", artifacts / "transparent-sample.png")
    assert hashes == {str(p): digest(p) for p in originals}
    report = {"platform": sys.platform, "python": sys.version, "test_workspace": str(work), "original_sha256": hashes,
              "batch": {"total": len(result.files), "successful": result.successful, "failed": result.failed, "saved": sum(len(f.saved) for f in result.files), "elapsed_seconds": result.elapsed, "sessions": 1},
              "offline_socket_blocked": True, "repeat_skipped": rerun.skipped, "single_transparent_passed": True,
              "legacy_format_gap": "Old watermark ignores all PNG results. Comparison before JPEG uses the exact old paste operation; actual JPEG bridge is measured separately.",
              "comparison": comparison}
    (artifacts / "real-image-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("originals", type=Path, nargs="+")
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    verify(parser.parse_args().originals)
