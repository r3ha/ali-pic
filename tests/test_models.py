"""Configurable local models: adapter selection, offline behavior and packaging."""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from app_config import ConfigurationError, ResourceError, SUPPORTED_MODELS, load_config
from model_store import verify_model
from processors.remove_bg import create_session, remove_background
from tools import prepare_model
from tools.release_support import stage_resources

ROOT = Path(__file__).resolve().parents[1]


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="本地模型 Test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.raw = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

    def config(self, name="u2net", path="models/自选 权重.onnx"):
        self.raw["rembg"].update(model=name, model_path=str(path))
        (self.root / "config.json").write_text(json.dumps(self.raw), encoding="utf-8")
        return load_config(self.root)

    def local_file(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"user-supplied weights; no fixed checksum")
        return path

    def test_supported_names_and_relative_or_absolute_paths(self):
        for name in SUPPORTED_MODELS:
            with self.subTest(model=name):
                cfg = self.config(name)
                self.assertEqual(cfg.rembg.model, name)
                self.assertEqual(cfg.rembg.model_path, self.root / "models/自选 权重.onnx")
        absolute = self.root / "other.onnx"
        self.assertEqual(self.config(path=absolute).rembg.model_path, absolute)
        for invalid in ("unknown", "sam", "u2net_cloth_seg", "", None, []):
            with self.subTest(invalid=invalid), self.assertRaises(ConfigurationError):
                self.config(invalid)

    def test_file_check_accepts_new_weights_rejects_missing_directory_empty_and_unreadable(self):
        path = self.root / "custom.onnx"
        with self.assertRaisesRegex(ResourceError, "找不到"):
            verify_model(path)
        with self.assertRaisesRegex(ResourceError, "找不到"):
            verify_model(self.root)
        path.touch()
        with self.assertRaisesRegex(ResourceError, "为空"):
            verify_model(path)
        self.local_file(path)
        verify_model(path)
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(ResourceError, "无法读取"):
                verify_model(path)

    def test_every_adapter_uses_configured_file_and_its_own_prediction_without_downloads(self):
        from rembg.sessions import sessions

        configs = []
        instances = []
        image = Image.new("RGB", (31, 23), (40, 100, 200))
        with patch("pooch.retrieve", side_effect=AssertionError("unexpected download")) as download, \
                patch("onnxruntime.InferenceSession") as inference:
            inference.return_value.get_inputs.return_value = [SimpleNamespace(name="input")]
            inference.return_value.run.return_value = [np.linspace(-1, 1, 16, dtype=np.float32).reshape(1, 1, 4, 4)]
            for name in SUPPORTED_MODELS:
                with self.subTest(model=name):
                    cfg = self.config(name, f"models/{name} 自选.onnx").rembg
                    self.local_file(cfg.model_path)
                    session = create_session(cfg)
                    self.assertIsInstance(session, sessions[name])
                    self.assertIs(type(session).predict, sessions[name].predict)
                    self.assertEqual(session.model_name, name)
                    self.assertEqual(inference.call_args.args[0], str(cfg.model_path))
                    self.assertEqual(inference.call_args.kwargs["providers"], ["CPUExecutionProvider"])
                    result = remove_background(image, session, cfg)
                    self.assertEqual((result.mode, result.size), ("RGBA", image.size))
                    inputs = inference.return_value.run.call_args.args[1]["input"]
                    size = 320 if name in {"u2net", "u2netp", "u2net_human_seg", "u2net_custom", "silueta"} else 1024
                    self.assertEqual(inputs.shape, (1, 3, size, size))
                    self.assertLess(result.getchannel("A").getextrema()[0], result.getchannel("A").getextrema()[1])
                    configs.append(cfg)
                    instances.append(session)
            # Later sessions must not change earlier sessions' model paths.
            for cfg, session in zip(configs, instances):
                self.assertEqual(type(session).download_models(), str(cfg.model_path))
            download.assert_not_called()

    def test_missing_model_fails_before_runtime_or_network(self):
        cfg = self.config("birefnet-general").rembg
        with patch("onnxruntime.InferenceSession") as inference, patch("pooch.retrieve") as download:
            with self.assertRaisesRegex(ResourceError, "找不到"):
                create_session(cfg)
            inference.assert_not_called()
            download.assert_not_called()

    def test_invalid_onnx_reports_configured_model_and_path_without_fallback(self):
        cfg = self.config("isnet-general-use").rembg
        self.local_file(cfg.model_path)
        with patch("pooch.retrieve", side_effect=AssertionError("unexpected download")) as download:
            # Use the actual ONNX Runtime parser, not a mocked initialization error.
            with self.assertRaises(ResourceError) as error:
                create_session(cfg)
            self.assertIn(cfg.model, str(error.exception))
            self.assertIn(str(cfg.model_path), str(error.exception))
            download.assert_not_called()

    def test_unknown_model_missing_adapter_and_wrong_dependency_do_not_fall_back(self):
        cfg = self.config().rembg
        self.local_file(cfg.model_path)
        with patch("onnxruntime.InferenceSession") as inference:
            with self.assertRaisesRegex(ResourceError, "不支持"):
                create_session(replace(cfg, model="unknown"))
            with patch("processors.remove_bg.version", return_value="wrong"):
                with self.assertRaisesRegex(ResourceError, "版本不匹配"):
                    create_session(cfg)
            with patch.dict("rembg.sessions.sessions", {"u2net": None}):
                with self.assertRaisesRegex(ResourceError, "缺少模型适配器"):
                    create_session(cfg)
            inference.assert_not_called()

    def test_preparation_reuses_existing_model_without_download_or_hash(self):
        cfg = self.config("birefnet-general")
        self.local_file(cfg.rembg.model_path)
        with patch.object(prepare_model, "ROOT", self.root), patch("urllib.request.urlopen") as download:
            self.assertEqual(prepare_model.prepare_model(), cfg.rembg.model_path)
            download.assert_not_called()

    def test_preparation_missing_alternate_model_never_downloads_default(self):
        cfg = self.config("isnet-general-use")
        with patch.object(prepare_model, "ROOT", self.root), patch("urllib.request.urlopen") as download:
            with self.assertRaisesRegex(ResourceError, "isnet-general-use"):
                prepare_model.prepare_model()
            self.assertFalse(cfg.rembg.model_path.exists())
            download.assert_not_called()

    def test_preparation_imports_user_weights_and_keeps_existing_destination(self):
        cfg = self.config("isnet-general-use")
        source = self.local_file(self.root / "source.onnx")
        with patch.object(prepare_model, "ROOT", self.root), patch("urllib.request.urlopen") as download:
            prepare_model.prepare_model(source)
            self.assertEqual(cfg.rembg.model_path.read_bytes(), source.read_bytes())
            source.write_bytes(b"different weights")
            prepare_model.prepare_model(source)
            self.assertNotEqual(cfg.rembg.model_path.read_bytes(), source.read_bytes())
            download.assert_not_called()

    def test_bad_default_download_is_not_published_and_temporary_file_is_cleaned(self):
        cfg = self.config()
        with patch.object(prepare_model, "ROOT", self.root), \
                patch("urllib.request.urlopen", return_value=io.BytesIO(b"incomplete download")):
            with self.assertRaisesRegex(ResourceError, "下载校验失败"):
                prepare_model.prepare_model()
        self.assertFalse(cfg.rembg.model_path.exists())
        self.assertEqual(list(cfg.rembg.model_path.parent.iterdir()), [])

    def test_release_stages_selected_model_and_config(self):
        cfg = self.config("birefnet-general")
        source = self.local_file(cfg.rembg.model_path)
        shutil.copytree(ROOT / "assets", self.root / "assets")
        shutil.copytree(ROOT / "licenses", self.root / "licenses")
        shutil.copy2(ROOT / "使用说明.txt", self.root / "使用说明.txt")
        destination = self.root / "release"
        with patch("importlib.metadata.distributions", return_value=[]):
            stage_resources(destination, self.root)
        staged = load_config(destination)
        self.assertEqual(staged.rembg.model, cfg.rembg.model)
        self.assertEqual(staged.rembg.model_path.read_bytes(), source.read_bytes())
        self.assertEqual(staged.rembg.model_path, destination / "models/自选 权重.onnx")
        self.assertFalse((destination / "models/u2net.onnx").exists())


if __name__ == "__main__":
    unittest.main()
