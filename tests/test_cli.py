"""CLI startup failures must remain readable even before the model imports."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_startup_errors_have_no_console_traceback(self):
        for case, expected in [("json", "config.json"), ("type", "canvas"), ("watermark", "水印"), ("model", "模型")]:
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix="CLI 配置 ") as temp:
                root = Path(temp)
                for name in ("main.py", "app_config.py", "pipeline.py", "model_store.py"):
                    shutil.copy2(ROOT / name, root / name)
                shutil.copytree(ROOT / "processors", root / "processors", ignore=shutil.ignore_patterns("__pycache__"))
                config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
                config["watermark"]["path"] = str(ROOT / "assets" / "watermark.png")
                if case == "type":
                    config["resize"]["main"]["canvas"] = ["1000", 1000]
                elif case == "watermark":
                    config["watermark"]["path"] = "missing.png"
                (root / "config.json").write_text("{" if case == "json" else json.dumps(config), encoding="utf-8")
                completed = subprocess.run([sys.executable, str(root / "main.py"), "--check", "--no-pause"], cwd=ROOT.parent,
                                           text=True, encoding="utf-8", capture_output=True, timeout=30,
                                           env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
                self.assertEqual(completed.returncode, 2)
                self.assertIn(expected, completed.stdout)
                self.assertNotIn("Traceback", completed.stdout + completed.stderr)
                logs = list((root / "logs").glob("*.log"))
                self.assertEqual(len(logs), 1)
                self.assertIn("Traceback", logs[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
