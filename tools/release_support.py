"""Assemble external resources and third-party license notices, never source images."""
from __future__ import annotations

import importlib.metadata
import json
import shutil
import sys
from pathlib import Path


def stage_resources(destination: Path, root: Path) -> None:
    from app_config import load_config
    from model_store import verify_model
    from processors.watermark import load_watermark

    config = load_config(root)
    load_watermark(config.watermark)
    verify_model(config.rembg.model_path)
    raw = json.loads((root / "config.json").read_text(encoding="utf-8-sig"))
    for value in (raw["watermark"]["path"], raw["rembg"]["model_path"]):
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("发布配置中的资源路径必须是程序目录内的相对路径。")
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / path, target)
    shutil.copy2(root / "config.json", destination / "config.json")
    shutil.copy2(root / "使用说明.txt", destination / "使用说明.txt")
    shutil.copytree(root / "licenses", destination / "licenses", dirs_exist_ok=True)
    records = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = distribution.metadata["Name"]
        records.append(f"{name}=={distribution.version}")
        for file in distribution.files or []:
            lower_parts = [p.lower() for p in file.parts]
            # Distribution metadata often carries bundled native-library notices, too.
            if any(x.endswith(".dist-info") for x in lower_parts) and (
                "licenses" in lower_parts or file.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "AUTHORS"))
            ):
                source = distribution.locate_file(file)
                if source.is_file():
                    target = destination / "licenses" / name / file
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    (destination / "BUILD-INFO.txt").write_text(
        f"Python: {sys.version}\nPlatform: {sys.platform}\n" + "\n".join(records) + "\n", encoding="utf-8")
