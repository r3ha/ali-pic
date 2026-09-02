from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

from app_config import ConfigurationError, application_root, load_config, parse_directory
from pipeline import Context, discover_images, plan_outputs, process_directory, save_png, validate_input_directory
from processors.resize import prepare_subject, resize_image
from processors.watermark import add_watermark, load_watermark

ROOT = Path(__file__).resolve().parents[1]


def load_legacy(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    # Legacy resize prompts even at import time; no legacy file is modified.
    with patch("builtins.input", return_value="unused"), redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)
    return module


def subject(width=160, height=80):
    result = Image.new("RGBA", (width + 30, height + 30))
    draw = ImageDraw.Draw(result)
    draw.rectangle((15, 15, width + 14, height + 14), fill=(37, 89, 149, 255))
    draw.rectangle((16, 16, width // 2, height // 2), fill=(180, 25, 16, 150))
    draw.line((14, 15, 14, height + 14), fill=(90, 20, 70, 9))
    draw.line((15, 15, width + 14, 15), fill=(90, 20, 70, 10))
    return result


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config(ROOT)

    def assertPixelsEqual(self, expected, actual):
        self.assertEqual(expected.size, actual.size)
        # getbbox() on RGBA defaults to alpha-only; compare all raw channels instead.
        self.assertEqual(expected.convert("RGBA").tobytes(), actual.convert("RGBA").tobytes())

    @unittest.skipUnless((ROOT / "png-resize.py").is_file(), "Optional legacy comparison requires png-resize.py")
    def test_resize_matches_legacy_landscape_portrait_square_and_ratio_boundary(self):
        legacy_resize = load_legacy("old_resize", "png-resize.py")
        for size in [(160, 80), (80, 160), (80, 80), (159, 100), (162, 100)]:
            with self.subTest(size=size), redirect_stdout(io.StringIO()):
                original = subject(*size)
                old = legacy_resize.rotate_to_landscape(legacy_resize.crop_transparent_area(original, 10))
                prepared = prepare_subject(original, self.config.resize)
                self.assertPixelsEqual(old, prepared)
                for variant in ("main", "golden"):
                    layer = legacy_resize.resize_layer(old) if variant == "main" else legacy_resize.resize_with_golden_ratio(old)
                    canvas = (1000, 1000) if variant == "main" else (1000, 618)
                    expected = legacy_resize.center_on_white_bg(layer, canvas)
                    self.assertPixelsEqual(expected, resize_image(prepared, self.config.resize, variant))

    @unittest.skipUnless((ROOT / "watermark.py").is_file(), "Optional legacy comparison requires watermark.py")
    def test_watermark_matches_actual_legacy_function_before_jpeg_encoding(self):
        legacy_watermark = load_legacy("old_watermark", "watermark.py")
        watermark = load_watermark(self.config.watermark)
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            image = resize_image(prepare_subject(subject(), self.config.resize), self.config.resize, "golden")
            # PNG bytes under .jpg allow the untouched legacy filter to accept lossless input.
            image.convert("RGB").save(temp / "reference.jpg", format="PNG")
            captured = []
            original_save = Image.Image.save

            def capture(im, *args, **kwargs):
                captured.append(im.copy())
                return original_save(im, *args, **kwargs)

            with patch.object(Image.Image, "save", capture), redirect_stdout(io.StringIO()):
                legacy_watermark.batch_add_watermark_to_new_folder(str(temp), str(self.config.watermark.path))
            actual = add_watermark(image, watermark, self.config.watermark, background="white")
            self.assertPixelsEqual(captured[0], actual)
            self.assertEqual(actual.getchannel("A").getextrema(), (255, 255))
            self.assertEqual(watermark.size, (1000, 618))
            self.assertEqual(watermark.getchannel("A").getextrema(), (0, 77))

    def test_transparent_mode_preserves_alpha(self):
        resize = replace(self.config.resize, background="transparent")
        image = resize_image(prepare_subject(subject(), resize), resize, "main")
        actual = add_watermark(image, load_watermark(self.config.watermark), self.config.watermark, background="transparent")
        self.assertEqual(actual.mode, "RGBA")
        self.assertEqual(actual.getpixel((0, 0))[3], 0)
        self.assertEqual(actual.getchannel("A").getextrema(), (0, 255))

    def test_threshold_does_not_replace_soft_alpha(self):
        image = prepare_subject(subject(), self.config.resize)
        self.assertIn(150, image.getchannel("A").getdata())
        with self.assertRaisesRegex(ValueError, "完全透明"):
            prepare_subject(Image.new("RGBA", (5, 5)), self.config.resize)

    def test_nonzero_offsets(self):
        cfg = replace(self.config.resize, main_offset=(11, -9), background="transparent")
        im = resize_image(prepare_subject(subject(), cfg), cfg, "main")
        baseline = resize_image(prepare_subject(subject(), cfg), replace(cfg, main_offset=(0, 0)), "main")
        box = baseline.getbbox()
        self.assertEqual(im.getbbox(), (box[0] + 11, box[1] - 9, box[2] + 11, box[3] - 9))

    def test_names_handle_extensions_case_and_variant_collisions(self):
        names = ["ABC.jpg", "ABC.png", "abc.jpeg", "ABC.jpg.png", "X.jpg", "X_效果图.png"]
        sources = sorted([Path(n) for n in names], key=lambda p: (p.name.casefold(), p.name))
        plans = plan_outputs(sources, ("main", "golden"), Path("output"))
        all_names = [p.name.casefold() for targets in plans.values() for p in targets.values()]
        self.assertEqual(len(all_names), len(set(all_names)))
        self.assertEqual(plans, plan_outputs(sources, ("main", "golden"), Path("output")))
        simple = plan_outputs([Path("ABC001.jpg")], ("main",), Path("output"))
        self.assertEqual(simple[Path("ABC001.jpg")]["main"].name, "ABC001.png")

    def test_resources_are_independent_of_cwd_and_meipass(self):
        with tempfile.TemporaryDirectory() as temp:
            before = Path.cwd()
            try:
                os.chdir(temp)
                self.assertEqual(load_config().watermark.path, ROOT / "assets" / "watermark.png")
                with patch("sys.frozen", True, create=True), patch("sys.executable", str(Path(temp) / "ImagePipeline.exe")), patch("sys._MEIPASS", "/different/internal", create=True):
                    self.assertEqual(application_root(), Path(temp).resolve())
            finally:
                os.chdir(before)

    def test_config_validation(self):
        base = json.loads((ROOT / "config.json").read_text())
        cases = [
            ("canvas", lambda d: d["resize"]["main"].update(canvas=[0, 1000])),
            ("boolean", lambda d: d["resize"].update(rotate_portrait="yes")),
            ("opacity", lambda d: d["watermark"].update(opacity=1.1)),
            ("missing", lambda d: d.pop("output")),
            ("extra", lambda d: d["watermark"].update(opactiy=0.5)),
            ("model", lambda d: d["rembg"].update(model="unknown")),
            ("variants", lambda d: d["resize"].update(variants=["main", "main"])),
            ("nan", lambda d: d["watermark"].update(scale=float("nan"))),
        ]
        with tempfile.TemporaryDirectory() as temp:
            for name, mutate in cases:
                with self.subTest(name=name):
                    data = json.loads(json.dumps(base))
                    mutate(data)
                    (Path(temp) / "config.json").write_text(json.dumps(data))
                    with self.assertRaises(ConfigurationError):
                        load_config(Path(temp))
            for text in ('{', '{"schema_version":1,"schema_version":1}'):
                (Path(temp) / "config.json").write_text(text)
                with self.assertRaises(ConfigurationError):
                    load_config(Path(temp))

    def test_missing_watermark_and_model_fail_before_session(self):
        with tempfile.TemporaryDirectory() as temp, patch("pipeline.create_session") as session:
            from app_config import ResourceError
            cfg = replace(self.config, watermark=replace(self.config.watermark, path=Path(temp) / "missing.png"))
            with self.assertRaisesRegex(ResourceError, "水印"):
                process_directory(temp, config=cfg)
            cfg = replace(self.config, rembg=replace(self.config.rembg, model_path=Path(temp) / "missing.onnx"))
            with self.assertRaisesRegex(ResourceError, "模型"):
                process_directory(temp, config=cfg)
            session.assert_not_called()

    def test_batch_fault_isolation_repeat_and_current_level_only(self):
        with tempfile.TemporaryDirectory(prefix="批处理 测试 ") as temp:
            directory = Path(temp)
            subject().convert("RGB").save(directory / "01.jpg")
            (directory / "02.png").write_bytes(b"broken png")
            subject().save(directory / "03.png")
            (directory / "subfolder").mkdir()
            subject().save(directory / "subfolder" / "ignored.png")
            originals = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
            with patch("pipeline.verify_model"), patch("pipeline.create_session", return_value=object()) as session, patch("pipeline.remove_background", side_effect=lambda image, *_: image.convert("RGBA")) as remove:
                result = process_directory(f'"{temp}"', config=self.config)
                self.assertEqual((len(result.files), result.successful, result.failed, result.skipped), (3, 2, 1, 0))
                self.assertEqual(session.call_count, 1)
                self.assertEqual(remove.call_count, 2)
                rerun = process_directory(temp, config=self.config)
                self.assertEqual((len(rerun.files), rerun.failed, rerun.skipped), (3, 1, 2))
                self.assertEqual(len(list((directory / "output").glob("*.png"))), 4)
                for name, data in originals.items():
                    self.assertEqual((directory / name).read_bytes(), data)
                self.assertFalse(list((directory / "output").glob("*.tmp")))

    def test_output_link_and_internal_directories_are_rejected(self):
        for folder in (ROOT, ROOT / "assets", ROOT / "models"):
            with self.assertRaises(ValueError):
                validate_input_directory(folder, self.config)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            output.mkdir()
            with self.assertRaises(ValueError):
                validate_input_directory(output, self.config)
            output.rmdir()
            try:
                output.symlink_to(ROOT, target_is_directory=True)
            except OSError:
                self.skipTest("Windows symlink privilege not available")
            with self.assertRaisesRegex(ValueError, "联接"):
                process_directory(temp, config=self.config)

    def test_camera_mpo_jpeg_uses_primary_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            subject().convert("RGB").save(directory / "camera.jpg", format="MPO", save_all=True,
                                           append_images=[Image.new("RGB", (12, 8), "black")])
            seen = []
            def remove(image, *_):
                seen.append(image.size)
                return image.convert("RGBA")
            with patch("pipeline.verify_model"), patch("pipeline.create_session", return_value=object()), patch("pipeline.remove_background", side_effect=remove):
                result = process_directory(directory, config=self.config)
            self.assertEqual(result.successful, 1)
            self.assertEqual(seen, [subject().size])

    def test_atomic_skip_overwrite_and_save_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "result.png"
            image = subject()
            self.assertTrue(save_png(image, path, self.config))
            original = path.read_bytes()
            self.assertFalse(save_png(Image.new("RGB", (4, 4)), path, self.config))
            self.assertEqual(original, path.read_bytes())
            cfg = replace(self.config, output=replace(self.config.output, existing="overwrite"))
            with patch.object(Image.Image, "save", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    save_png(image, path, cfg)
            self.assertEqual(original, path.read_bytes())
            self.assertEqual(list(Path(temp).iterdir()), [path])
            self.assertTrue(save_png(Image.new("RGBA", (4, 4)), path, cfg))
            with Image.open(path) as saved:
                self.assertEqual(saved.size, (4, 4))


if __name__ == "__main__":
    unittest.main()
