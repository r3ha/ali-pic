# ImagePipeline

本地图片批处理：**去背景 → 裁剪/旋转/缩放/定位 → 水印 → PNG**。
面向 Windows 的 PyInstaller `onedir` 便携发布；核心逻辑为 Python，无 GUI、无服务、无多进程。

## 普通用户

1. 复制完整的 `ImagePipeline` 发布文件夹，双击 `ImagePipeline.exe`。
2. 输入原图目录，支持中文、空格、拖入目录和两侧引号。
3. 在原图目录的 `output` 中取图。

不用安装 Python/pip/rembg，不需要网络；模型必须随发布包一起提供。
原始图片不改动，默认跳过已有成品，只扫描目录第一层，不接受程序内部目录或 `output` 作为输入。
默认每张原图保留旧版的两种白底构图：`原名.png` 和 `原名_效果图.png`。
仅需主图时，把 `resize.variants` 改成 `["main"]`。

## 开发和运行

使用 **Python 3.11，Windows 构建需 x64**。`requirements.txt` 供开发/构建使用，不是最终用户操作步骤。

```text
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python tools\prepare_model.py
.venv\Scripts\python main.py
```

macOS/Linux 对应解释器为 `.venv/bin/python`。模型已存在时只检查可读且非空，不下载。
也可以离线导入与配置匹配的权重：`python tools/prepare_model.py --from-file "已有的模型.onnx"`。
仅当 `rembg.model` 为 `u2net` 且文件缺失时，准备脚本自动下载默认官方权重并校验下载完整性。
其他模型缺失时会报错，请先自行准备文件；不会下载默认模型代替。

```text
python main.py "D:\产品图片\Test Batch"
python main.py "D:\产品图片\Test Batch" --no-pause
python main.py --check
```

退出码：`0` 全部成功/跳过，`1` 存在单图失败，`2` 配置/资源/初始化/目录错误，`130` 用户取消。
`--check` 只检查配置、水印及模型文件存在、可读且非空；不校验固定权重哈希，不加载模型。
模型格式和兼容性在实际处理图片时由 ONNX Runtime 和对应适配器检查。
双击 Windows EXE 结束后等待回车；自动化使用 `--no-pause`。

## Windows 构建

在 Windows 电脑安装 Python 3.11 x64 和 Python Launcher，双击 **`build.bat`**。
首次构建需要网络准备依赖和模型；成品运行不需要网络。
脚本创建独立 `.venv-build`，安装 `requirements-windows.lock`，检查打包所需的模型、配置和水印，
清理本项目生成的 `build/` 与 `dist/ImagePipeline/`，运行 PyInstaller 并复制发布资源。
默认不运行回归测试、打包后自检或图片推理测试，也不需要旧版对照脚本和测试图片。
构建完成后，双击 `dist/ImagePipeline/ImagePipeline.exe`，输入图片目录，自行检查 `output` 中的成品。
依赖安装、必要资源准备或打包失败时，脚本仍会报错退出。

```text
dist/ImagePipeline/
├── ImagePipeline.exe
├── config.json
├── assets/watermark.png
├── models/u2net.onnx
├── _internal/
├── licenses/
├── BUILD-INFO.txt
└── 使用说明.txt
```

已准备构建依赖和模型时，也能直接执行 `python -m PyInstaller ImagePipeline.spec`；spec 会复制外部资源。
Windows EXE 必须在 Windows 构建。`dist/macos-validation/` 若存在，只是 macOS 验证产物。
需要验证其他电脑的兼容性时，可手动在无 Python 的 Windows x64 机器上断网试运行。

## 配置

读取规则：开发时使用项目目录，冻结时使用 EXE 所在目录，与当前 CMD 目录无关。
不会从 `sys._MEIPASS` 读取用户配置、水印或模型。
配置的相对路径相对于程序目录；开发时可使用绝对资源路径，发布构建要求相对路径以便搬移。

| 参数 | 默认及作用 |
| --- | --- |
| `rembg.model` | 默认 `u2net`；选择与本地权重对应的模型适配器，支持列表见下方 |
| `rembg.model_path` | 默认 `models/u2net.onnx`；读取此处的本地文件，不限制文件名或固定权重哈希 |
| `rembg.alpha_matting` | `false`，阈值 240/10，腐蚀大小 10 |
| `rembg.post_process_mask` | `false` |
| `resize.alpha_threshold` | `10`；仅计算裁剪框，不改主体内部 alpha；0 保留旧代码的“不裁透明边界”行为 |
| `resize.rotate_portrait` | `true`；裁剪后高大于宽则逆时针 90° |
| `resize.resample` | `LANCZOS`，也可 BICUBIC/BILINEAR/NEAREST |
| `resize.background` | `white` 保持旧白底；`transparent` 是主动选择的新输出模式 |
| `resize.variants` | `["main", "golden"]`，可只保留其中一种 |
| `resize.main.canvas/target_size/offset` | `[1000,1000]` / `900` / `[0,0]` |
| `resize.golden.canvas/target/ratio_threshold/offset` | `[1000,618]` / `[900,600]` / `1.618` / `[0,0]` |
| `watermark.path` | `assets/watermark.png` |
| `watermark.scale` | `1.0`，不缩放；修改后使用 LANCZOS 等比缩放 |
| `watermark.position` | `[0,0]`，相对于画布左上角；没有额外边距或锚点 |
| `watermark.opacity` | `1.0`，乘在水印原 alpha 上；原文件最高 alpha 77/255 |
| `output.format` | 只允许 `PNG`，防止丢失透明通道 |
| `output.existing` | `skip`；可改为 `overwrite`，仅替换 output 中的成品 |
| `output.dpi` | `[72,72]` |

### 更换去背景模型

在 `config.json` 的 `rembg` 对象中改下面两项，保留其他参数，例如：

```json
"model": "birefnet-general",
"model_path": "models/birefnet-general.onnx"
```

把与 `birefnet-general` 适配器对应的 ONNX 权重放到程序旁的 `models/birefnet-general.onnx`，
保存配置并重新启动程序即可。文件名可以自行命名，`model` 必须使用下列准确名称。
路径相对于 EXE 所在目录；运行时也可使用绝对路径。Windows JSON 路径建议写成
`D:/模型/birefnet-general.onnx`，或将反斜杠写成 `\\`。

当前支持 rembg 2.0.67 的以下单文件、单遮罩适配器：

- `u2net`、`u2netp`、`u2net_human_seg`、`u2net_custom`、`silueta`
- `isnet-general-use`、`isnet-anime`、`dis_custom`
- `birefnet-general`、`birefnet-general-lite`、`birefnet-portrait`、`birefnet-dis`、`birefnet-hrsod`、`birefnet-cod`、`birefnet-massive`
- `bria-rmbg`、`ben_custom`

运行时始终读取 `model_path`，不会自动联网下载，也不会回退到其他模型。
文件存在并不代表任何 ONNX 都能使用：输入输出结构、预处理必须与所选适配器匹配。
SAM 需要多个模型文件及提示参数，服装分割会产生多个遮罩，当前单模型自动抠图流程不支持这两类。
上述列表表示程序提供的适配器，不代表每个模型都已用真实权重验证过效果。

使用本次修改后的源码重新构建一次 Windows EXE 后，在支持列表内换模型就只需改配置和文件。
旧 EXE 不会因更换 `config.json` 自动获得此能力。比较新旧模型效果时，请换用新的图片测试目录，
或将 `output.existing` 设为 `overwrite`，否则默认会跳过已有成品。

`golden` 故意保留旧算法：按 1.618 分支判断，而不是新的“完全适配 900×600”算法。
因此宽高比介于 1.5 和 1.618 时主体可能宽于 900，这不是本次重构引入的变化。
不自动把 1000×618 水印缩放到 1000×1000 主图，也不自动移到底部。

同名冲突按 Windows 不区分大小写的规则统一预分配，包含主图/效果图名称冲突。
如 `ABC.jpg` 与 `ABC.png`，输出使用 `ABC.jpg.png`、`ABC.png.png`；更复杂冲突追加 `_2` 等序号。
同一组输入的命名固定；增删输入可能改变冲突名称，已有历史成品不自动清理。

## 核心 API

```python
from pipeline import process_directory

result = process_directory(r"D:\产品图片", progress=print)
print(result.successful, result.failed, result.skipped, result.output_dir)
```

`process_directory` 管理启动校验、扫描、命名、一次 session 初始化和汇总。
`process_image` 管理单图；`Context` 可由未来 GUI 持有，以跨批次复用 session。
处理步骤之间传递 Pillow 图像，不产生阶段目录。每个最终成品编码一次；同目录临时文件只用于
原子提交，避免中断留下半张 PNG。两种构图共用一次抠图。
单个变体保存后另一变体失败，会在汇总标记该原图失败并列出已经保存的部分成品。

## 可选验证与历史证据

以下命令供开发时手动使用，不属于 `build.bat` 构建流程。
旧版对比测试需要另外提供旧脚本 `png-resize.py`、`watermark.py`；真实图片测试还需要相应输入图片。
缺少旧脚本时，单元测试只跳过对应的两项旧版像素对比，其余测试仍正常执行。

```text
python -m unittest discover -s tests -v
python -m pip install -r requirements-test.txt
python tests/verify_real_images.py 4T8A8532.JPG 4T8A8682.JPG
python tools/smoke_release.py dist/ImagePipeline/ImagePipeline.exe
```

真实对比测试需要 rembg CLI 额外依赖，正式发布不包含这些 CLI/Web 依赖。
旧脚本仅作为对照基准，不参与新业务流程或默认构建。

详细结果和旧参数审计见 [docs/REFACTOR_REPORT.md](docs/REFACTOR_REPORT.md)。
机器可读像素对比见 `tests/artifacts/real-image-report.json`；实际成品见 `tests/artifacts/new_output/`。
这些历史验证产物只保存在本地，不随 Git 仓库提供。

## Git 提交范围

提交源码、测试脚本、`config.json` 默认配置、`requirements*.txt`、`requirements-windows.lock`、
`ImagePipeline.spec`、`build.bat`、水印资源、说明文档和许可证。
`models/` 只提交 `README.md`，模型权重通过 `tools/prepare_model.py` 下载或自行导入。

`.gitignore` 排除本地虚拟环境、模型权重、构建产物、ZIP 压缩包、日志、测试产物、
`output/` 成品目录和系统缓存。原始产品图片请放在项目外的独立目录。
Git 中不保存模型权重，但交付给用户的完整发布包仍须包含所选模型和运行依赖。
