"""Run before third-party imports in a frozen process; never write into _internal."""
import os
import tempfile
from pathlib import Path

# These are caches only, not editable resources or model locations.
cache_base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "ImagePipeline" / "cache"
try:
    (cache_base / "numba").mkdir(parents=True, exist_ok=True)
except OSError:
    cache_base = Path(tempfile.mkdtemp(prefix="ImagePipeline-cache-"))
os.environ["NUMBA_CACHE_DIR"] = str(cache_base / "numba")
