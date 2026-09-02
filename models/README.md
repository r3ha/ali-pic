# Offline models

Set `rembg.model` to a supported rembg adapter name and `rembg.model_path` to the
matching local ONNX file in `config.json`. Relative paths start beside the
application, not in the current working directory. See the main README for names.
The runtime checks that the file is readable and nonempty, then loads it using
the selected adapter. It never downloads, replaces, or falls back to another model.
No fixed hash is required for user-supplied weights; the ONNX format and adapter
must still match. SAM and multi-mask cloth segmentation are not supported.

Developer preparation: `python tools/prepare_model.py`, or
`python tools/prepare_model.py --from-file /path/to/matching-model.onnx`.
Existing local files are kept. Missing non-default models must be supplied manually.
Only the automatic default U2-Net download is checked against upstream MD5
`60024c5c889badc19c04ad937298a77b` before being placed at the configured path.

Default model source: https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx
Default architecture/source license: https://github.com/xuebinqin/U-2-Net (Apache-2.0).
See `licenses/` for upstream notices. Large weights stay outside Git and the EXE.
