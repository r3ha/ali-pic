"""Opt-in real inference checks using the CURRENT config and local model.

Run: python tests/verify_pipeline_update.py <landscape.jpg> <portrait.png>
Inputs are copied to a temporary directory; original files stay untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image

import pipeline
from app_config import load_config
from processors.resize import prepare_subject


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(originals: list[Path]) -> None:
    config = load_config(ROOT)
    artifacts = ROOT / "tests" / "artifacts"
    artifacts.mkdir(exist_ok=True)
    hashes = {p: digest(p) for p in originals}
    records = []
    captures = {}
    real_remove = pipeline.remove_background

    def capture(image, session, settings):
        assert settings == config.rembg
        assert session.model_name == config.rembg.model
        cutout = real_remove(image, session, settings)
        captures[Path(image.filename).name] = cutout.copy()
        return cutout

    def check_before_crop(image, settings):
        source_name = next(name for name, cutout in captures.items()
                           if cutout.size == image.size and cutout.tobytes() == image.tobytes())
        raw_path = inputs / f"{Path(source_name).stem}_nobg.png"
        with Image.open(raw_path) as raw:
            assert raw.format == "PNG" and raw.mode == "RGBA"
            assert raw.size == image.size and raw.tobytes() == image.tobytes()
            assert raw.getchannel("A").getextrema()[0] == 0
        prepared = prepare_subject(image, settings)
        alpha = image.getchannel("A").point(lambda p: 0 if p < settings.alpha_threshold else 255)
        expected = image.crop(alpha.getbbox())
        assert prepared.size == expected.size and prepared.tobytes() == expected.tobytes()
        records.append({"source": source_name, "raw_size": image.size,
                        "subject_size": prepared.size, "raw_alpha": image.getchannel("A").getextrema(),
                        "raw_pixel_equal_before_crop": True, "direction_preserved": True})
        return prepared

    def no_network(*args, **kwargs):
        raise AssertionError("Unexpected model download or network access")

    with tempfile.TemporaryDirectory(prefix="ImagePipeline 原始抠图 ") as temp:
        inputs = Path(temp)
        for index, source in enumerate(originals):
            shutil.copy2(source, inputs / f"sample-{index}{source.suffix}")
        with patch.object(socket.socket, "connect", no_network), patch("socket.create_connection", no_network), \
                patch("rembg.bg.new_session", side_effect=AssertionError("Unexpected fallback model")), \
                patch("pooch.retrieve", no_network), patch("pipeline.remove_background", side_effect=capture), \
                patch("pipeline.prepare_subject", side_effect=check_before_crop):
            context = pipeline.Context(config, pipeline.validate_environment(config), pipeline.create_session(config.rembg))
            assert context.session.model_name == config.rembg.model
            assert type(context.session).download_models() == str(config.rembg.model_path)
            result = pipeline.process_directory(inputs, context=context, progress=print)
            assert result.failed == 0 and result.successful == len(originals)
            finals = {p: digest(p) for p in result.output_dir.glob("*.png")}
            # Replace raw PNGs with obvious stale data; the default skip policy must refresh them.
            for raw in inputs.glob("*_nobg.png"):
                Image.new("RGBA", (3, 5)).save(raw)
            rerun = pipeline.process_directory(inputs, context=context, progress=print)
            assert rerun.failed == 0 and len(rerun.files) == len(originals)
            for name, expected in captures.items():
                with Image.open(inputs / f"{Path(name).stem}_nobg.png") as raw:
                    assert raw.size == expected.size and raw.tobytes() == expected.tobytes()
            assert len(list(inputs.glob("*_nobg.png"))) == len(originals)
            assert not list(inputs.glob("*_nobg_nobg.png"))
            if config.output.existing == "skip":
                assert rerun.skipped == len(originals)
                assert finals == {p: digest(p) for p in finals}
        assert hashes == {p: digest(p) for p in originals}
        shutil.copytree(inputs, artifacts / "pipeline-update-real-images", dirs_exist_ok=True)
    report = {"platform": sys.platform, "model": config.rembg.model, "model_path": str(config.rembg.model_path),
              "records": records, "network_blocked": True, "fallback_session_blocked": True,
              "repeat_raw_overwrite": True, "final_policy_preserved": config.output.existing,
              "originals_unchanged": True, "windows_build_tested": False}
    (artifacts / "pipeline-update-real.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("originals", type=Path, nargs="+")
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    verify([p.resolve() for p in parser.parse_args().originals])
