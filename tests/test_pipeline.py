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
    def test_resize_matches_legacy_scaling_and_placement_without_direction_changes(self):
        legacy_resize = load_legacy("old_resize", "png-resize.py")
        for size in [(160, 80), (80, 160), (80, 80), (159, 100), (162, 100)]:
            with self.subTest(size=size), redirect_stdout(io.StringIO()):
                original = subject(*size)
                old = legacy_resize.crop_transparent_area(original, 10)
                prepared = prepare_subject(original, self.config.resize)
                self.assertPixelsEqual(old, prepared)
                for variant in ("main", "golden"):
                    layer = legacy_resize.resize_layer(old) if variant == "main" else legacy_resize.resize_with_golden_ratio(old)
                    canvas = (1000, 1000) if variant == "main" else (1000, 618)
                    expected = legacy_resize.center_on_white_bg(layer, canvas)
                    self.assertPixelsEqual(expected, resize_image(prepared, self.config.resize, variant))

    def test_subject_direction_and_existing_resize_placement(self):
        # Fixed expected dimensions cover both axes and both sides of the golden threshold.
        cases = [
            ((160, 80), (900, 450), (900, 450)),
            ((80, 160), (450, 900), (300, 600)),
            ((80, 80), (900, 900), (600, 600)),
            ((159, 100), (900, 566), (954, 600)),
            ((162, 100), (900, 556), (900, 556)),
        ]
        for (width, height), main_size, golden_size in cases:
            with self.subTest(size=(width, height)):
                original = subject(width, height)
                # Asymmetric colors and soft alpha detect flips and 180-degree changes, too.
                expected_subject = original.crop((15, 15, width + 15, height + 15))
                prepared = prepare_subject(original, self.config.resize)
                self.assertPixelsEqual(expected_subject, prepared)
                for variant, dimensions, canvas in (
                    ("main", main_size, (1000, 1000)),
                    ("golden", golden_size, (1000, 618)),
                ):
                    layer = expected_subject.resize(dimensions, Image.Resampling.LANCZOS)
                    expected = Image.new("RGB", canvas, "white")
                    xy = ((canvas[0] - dimensions[0]) // 2, (canvas[1] - dimensions[1]) // 2)
                    expected.paste(layer, xy, layer.getchannel("A"))
                    self.assertPixelsEqual(expected, resize_image(prepared, self.config.resize, variant))

    def test_raw_cutout_is_saved_pixel_exact_before_any_subject_processing(self):
        for name, size in (("ABC.jpg", (160, 80)), ("ABC.png", (80, 160))):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                original = subject(*size)
                original.convert("RGB").save(directory / name)
                source_bytes = (directory / name).read_bytes()
                raw_path = directory / "ABC_nobg.png"
                seen = []

                def prepare(image, config):
                    # This callback runs before even the existing transparent-border crop.
                    self.assertIs(image, original)
                    with Image.open(raw_path) as raw:
                        self.assertEqual((raw.format, raw.mode), ("PNG", "RGBA"))
                        self.assertPixelsEqual(original, raw)
                        self.assertEqual(raw.getchannel("A").getextrema(), (0, 255))
                        self.assertIn(9, raw.getchannel("A").getdata())
                    self.assertFalse(list((directory / "output").iterdir()))
                    seen.append(True)
                    return prepare_subject(image, config)

                with patch("pipeline.verify_model"), patch("pipeline.create_session", return_value=object()) as session, \
                        patch("pipeline.remove_background", return_value=original) as remove, \
                        patch("pipeline.prepare_subject", side_effect=prepare):
                    result = process_directory(directory, config=self.config)
                self.assertEqual(result.successful, 1)
                self.assertEqual(seen, [True])
                session.assert_called_once_with(self.config.rembg)
                self.assertIs(remove.call_args.args[2], self.config.rembg)
                self.assertEqual((directory / name).read_bytes(), source_bytes)
                self.assertEqual(sorted(p.name for p in directory.glob("*_nobg.png")), ["ABC_nobg.png"])

    def test_repeat_refreshes_raw_cutout_even_when_final_outputs_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            subject().convert("RGB").save(directory / "ABC.jpg")
            raw_path = directory / "ABC_nobg.png"
            latest = subject(80, 160)
            with patch("pipeline.verify_model"), patch("pipeline.create_session", return_value=object()) as session, \
                    patch("pipeline.remove_background", side_effect=[subject(), latest, latest]) as remove:
                first = process_directory(directory, config=self.config)
                self.assertEqual(first.successful, 1)
                finals = {p: p.read_bytes() for p in first.files[0].saved}
                for missing_raw in (False, True):
                    if missing_raw:
                        raw_path.unlink()
                    with patch("pipeline.prepare_subject") as prepare, patch("pipeline.resize_image") as resize, \
                            patch("pipeline.add_watermark") as watermark:
                        rerun = process_directory(directory, config=self.config)
                    self.assertEqual((len(rerun.files), rerun.failed, rerun.skipped), (1, 0, 1))
                    prepare.assert_not_called()
                    resize.assert_not_called()
                    watermark.assert_not_called()
                    with Image.open(raw_path) as raw:
                        self.assertPixelsEqual(latest, raw)
                    self.assertEqual(finals, {p: p.read_bytes() for p in finals})
                self.assertEqual(session.call_count, 3)
                self.assertEqual(remove.call_count, 3)
            self.assertEqual(sorted(p.name for p in directory.iterdir()), ["ABC.jpg", "ABC_nobg.png", "output"])

    def test_discovery_excludes_raw_cutouts_case_insensitively_and_output_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            for name in ("ABC.jpg", "ABC_nobg.png", "Other_NoBg.PNG", "X_效果图.png"):
                subject().convert("RGB").save(directory / name)
            (directory / "output").mkdir()
            subject().save(directory / "output" / "ABC.png")
            cfg = replace(self.config, watermark=replace(self.config.watermark, path=directory / "watermark.png"))
            subject().save(cfg.watermark.path)
            self.assertEqual([p.name for p in discover_images(directory, cfg)], ["ABC.jpg", "X_效果图.png"])

    def test_raw_cutout_survives_later_processing_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            subject().save(directory / "ABC.png")
            blank = Image.new("RGBA", (77, 101))
            with patch("pipeline.verify_model"), patch("pipeline.create_session", return_value=object()), \
                    patch("pipeline.remove_background", return_value=blank):
                result = process_directory(directory, config=self.config)
            self.assertEqual(result.failed, 1)
            self.assertIn("完全透明", result.files[0].error)
            with Image.open(directory / "ABC_nobg.png") as raw:
                self.assertPixelsEqual(blank, raw)
            self.assertEqual(list((directory / "output").iterdir()), [])

    def test_raw_save_failure_preserves_previous_cutout_and_isolates_file(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            subject().save(directory / "01.png")
            subject().save(directory / "02.png")
            old_path = directory / "01_nobg.png"
            Image.new("RGBA", (3, 4)).save(old_path)
            previous = old_path.read_bytes()
            real_replace = os.replace

            def fail_first(source, destination):
                if destination == old_path:
                    raise PermissionError("raw cutout is read-only")
                return real_replace(source, destination)

            with patch("pipeline.verify_model"), patch("pipeline.create_session", return_value=object()), \
                    patch("pipeline.remove_background", return_value=subject()), \
                    patch("pipeline.os.replace", side_effect=fail_first), \
                    patch("pipeline.prepare_subject", wraps=prepare_subject) as prepare:
                result = process_directory(directory, config=self.config)
            self.assertEqual((result.failed, result.successful), (1, 1))
            self.assertIn("read-only", result.files[0].error)
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(old_path.read_bytes(), previous)
            self.assertFalse((directory / "output" / "01.png").exists())
            self.assertFalse(list(directory.glob(".image-pipeline-*.tmp")))

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
            ("boolean", lambda d: d["rembg"].update(alpha_matting="yes")),
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
                self.assertEqual(remove.call_count, 4)
                self.assertEqual(sorted(p.name for p in directory.glob("*_nobg.png")), ["01_nobg.png", "03_nobg.png"])
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
