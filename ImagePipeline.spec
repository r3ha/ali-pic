# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from app_config import load_config
from pipeline import validate_environment
validate_environment(load_config(root))
# Editable resources and large weights deliberately stay OUTSIDE Analysis.datas.
datas = collect_data_files("onnxruntime", excludes=["**/datasets/**", "**/transformers/**"])
datas += copy_metadata("rembg")
binaries = collect_dynamic_libs("onnxruntime") + collect_dynamic_libs("llvmlite")
# rembg has model registry imports; collect sessions only, never its CLI/web server.
hiddenimports = collect_submodules("rembg.sessions")
hiddenimports += ["onnxruntime.capi._pybind_state", "onnxruntime.capi.onnxruntime_pybind11_state"]

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(root / "tools" / "frozen_runtime.py")],
    excludes=["rembg.cli", "rembg.commands", "gradio", "gradio_client", "fastapi", "uvicorn", "torch", "tensorflow", "IPython", "pytest", "pandas", "matplotlib"],
    # Numba's cached/JIT functions need the actual pymatting source at runtime.
    # Source-only is required: a PYZ code object's relative co_filename prevents
    # Numba 0.61 from finding a cache locator after the release directory moves.
    module_collection_mode={"pymatting": "py"},
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ImagePipeline",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    contents_directory="_internal",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ImagePipeline")

# Also prepare external resources for a direct `pyinstaller ImagePipeline.spec`.
# The build wrapper additionally checks/cleans and writes a dependency/license manifest.
from tools.release_support import stage_resources
stage_resources(Path(DISTPATH) / "ImagePipeline", root)
