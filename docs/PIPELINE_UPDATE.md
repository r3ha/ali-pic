# 原始抠图保存与取消旋转：变更和验收

日期：2026-09-03。基于当前可运行版本调整处理顺序，无架构或打包重构。

## 修改前核对

| 检查项 | 当前实现位置与结论 |
| --- | --- |
| rembg 调用 | `processors/remove_bg.py::remove_background`，调用 rembg Python API，返回 RGBA |
| 配置加载 | `app_config.py::load_config`；源码模式读取项目旁配置，冻结模式读取 EXE 旁配置 |
| 模型传递 | `config.rembg` → `create_session` → `sessions.get(config.model)` → 对应离线适配器；本地路径来自 `config.model_path` |
| 原业务旋转 | 只有 `processors/resize.py::prepare_subject` 中的竖向主体判断与 `rotate(90, expand=True)` |
| 旋转配置 | `config.json` 中的 `resize.rotate_portrait`，以及 `ResizeConfig` 字段、加载与校验 |
| 主体尺寸 | `processors/resize.py::resize_image`；原有 main / golden 比例、round 和重采样算法 |
| 主体位置 | 同函数中的 `(canvas - resized) // 2 + offset` |
| 水印 | `processors/watermark.py::load_watermark` 和 `add_watermark` |
| 最终保存 | `pipeline.py::process_image` → `save_png`，写入输入目录下的 `output` |
| 输入扫描 | `pipeline.py::discover_images`，只扫描当前层的支持格式，排除符号链接与水印 |
| 输出排除 | 不递归扫描；`validate_input_directory` 禁止选择 output 及其子目录；本次补充 `_nobg.png` 过滤 |
| Windows 打包 | `build.bat` → `tools/build_release.py` → `ImagePipeline.spec` → `stage_resources`，PyInstaller onedir |

修改前先运行现有测试：25 项中 23 项通过，2 项因本地没有旧版对照脚本而跳过。

## 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `pipeline.py` | 增加原始抠图原子保存与日志；原始抠图始终覆盖；跳过最终成品时仍更新抠图；扫描排除 `_nobg.png` |
| `processors/resize.py` | 删除竖图判断和 90° 旋转；保留原裁剪、缩放、尺寸取整、定位和合成 |
| `app_config.py` | 删除旋转字段及其配置加载/校验；模型读取与其他参数不变 |
| `config.json` | 仅删除 `resize.rotate_portrait`；保留用户原先设置的 `birefnet-general` 和权重路径 |
| `tests/test_pipeline.py` | 新增六项回归测试，调整旧尺寸对比的预期和重复运行检查 |
| `tests/verify_pipeline_update.py` | 新增可选真实模型验证，使用当前配置、本地权重和输入副本，输出验收报告 |
| `README.md` | 更新流程、命名、覆盖/跳过、模型现值、测试与重新发布说明 |
| `使用说明.txt` | 更新随 Windows 发布包提供的使用步骤和升级说明 |
| `docs/REFACTOR_REPORT.md` | 添加历史记录提示，避免把旧旋转和默认模型说明当作新版行为 |
| `docs/PIPELINE_UPDATE.md` | 本次变更与验收记录 |

开始修改前已发现 `config.json` 有用户未提交的模型改动。与本次修改前的配置逐项比较，
除删除旋转开关外全部相同；Git diff 中的模型变化来自用户原有修改。

## 新处理顺序

```text
读取原图
→ 按 config.json 选择 rembg 模型去背景
→ 在原图目录保存 stem + "_nobg.png"
→ 使用同一份内存结果裁剪透明边
→ 原有主体尺寸与位置调整
→ 原有 watermark.png 合成
→ 保存 output 中的最终成品
```

原始抠图保存发生在 `prepare_subject` 之前，因此也早于透明边裁剪。
该文件直接按 rembg 返回的 RGBA 图像编码 PNG，不改变像素、尺寸、主体位置或方向，不加水印，
不附加成品 DPI 设置。保存后不重新读取磁盘文件进行后续处理。

已删除全部应用业务旋转逻辑及旋转配置，没有新增 rotation 参数。
现有宽高比运算仅用于等比缩放；不用于判断图片方向或交换宽高。
rembg 依赖本身的 EXIF 方向标签处理保持原状，没有修改第三方代码。

命名示例：`ABC.jpg`、`ABC.png` 都得到同目录的 `ABC_nobg.png`。
存在则原子覆盖，不生成 `ABC.jpg.png` 或带编号的原始抠图。
若两张原图 stem 相同，则按这一规则共用一份抠图，扫描排序中后处理的原图覆盖前一份。
最终成品原有的同名冲突处理规则保持不变。

扫描对 `_nobg.png` 后缀不区分大小写排除。成品仍位于 `output`，由已有目录规则排除。
不会仅凭 `_效果图` 文件名排除输入，因为原有命名规则允许真实原图使用这个名称。

`output.existing` 仍是用户配置的 `skip`：已有最终成品不变，但每次都会重新抠图并覆盖原始 PNG。
因此再次运行仍需要模型推理。成功、跳过和保存成品数量继续统计最终成品；原始抠图保存另有日志提示。
原始抠图保存失败时停止该张的后续处理，并由现有错误隔离机制继续下一张；原子写入保留旧文件。
若裁剪或后续处理失败，已经保存的原始抠图仍然保留。

## 模型确认

当前实际加载配置：

```json
"model": "birefnet-general",
"model_path": "models/BiRefNet-general-epoch_244.onnx"
```

`processors/remove_bg.py`、`model_store.py`、模型准备和发布资源复制代码均未修改。
模型名称没有硬编码进新的业务逻辑。真实验证中确认 session 的模型名和本地权重路径均来自配置，
并禁止 rembg 创建默认 session、禁止模型下载和网络连接；推理仍然通过。

## 验收结果

`python -m unittest discover -s tests -v`：共 31 项，29 项通过，2 项跳过。
跳过的是需要项目中已不存在的 `png-resize.py`、`watermark.py` 的旧版像素对照，
本次新增的测试均已实际执行。新测试包括：

- 横图、竖图、正方形及 golden 比例阈值两侧，使用非对称色块逐像素验证方向、尺寸和居中结果。
- JPG/PNG 原始抠图在裁剪前就已保存，尺寸、RGBA 全通道、透明和软边像素保持一致。
- 默认 skip 下覆盖旧抠图、补回缺失抠图，同时不修改最终成品。
- `_nobg.png` 大小写过滤，子目录及最终成品不重复扫描。
- 后续主体处理失败时原始抠图仍然存在。
- 模拟原始抠图写入失败，旧文件保留、临时文件清理、下一张继续处理。

原有配置校验、模型适配器选择/禁止回退、发布资源复制、CLI 错误、MPO 第一帧、
透明模式、位置偏移、成品命名、原子写入及单图错误隔离测试也通过。

另使用当前真实 BiRefNet 权重，在本机 CPU 上处理一张历史产品照片的副本和一张预先准备的竖向裁切样本。
照片原件与传入样本未被修改；测试过程禁止网络和默认模型回退。

| 测试输入 | 原始抠图尺寸 | Alpha 范围 | 与 rembg 输出逐像素一致 | 方向保持 |
| --- | --- | --- | --- | --- |
| 横向 JPG 产品照片 | 5472×3648 | 0～255 | 通过 | 通过 |
| 预先准备的竖向 PNG | 2000×3300 | 0～255 | 通过 | 通过 |

首次运行两张输入成功生成两份原始抠图及四份成品。
随后主动将原始抠图替换为 3×5 的旧测试文件，再运行：旧抠图恢复为最新 rembg 结果，
只扫描两张原图，没有生成 `_nobg_nobg.png`，四份成品的 SHA-256 不变。

本地证据（测试产物不随 Git 或发布包提供）：

- `logs/pipeline-update-unittest.log`
- `logs/pipeline-update-real.log`
- `tests/artifacts/pipeline-update-real.json`
- `tests/artifacts/pipeline-update-real-images/`

已检查当前业务源码，没有旋转调用、方向判断或旋转配置；`git diff --check` 通过。

## Windows 更新

需要重新运行 **`build.bat`**，并重新生成、整体替换 **`dist/ImagePipeline/`**。
仅替换配置不能让旧 EXE 获得新流程。使用更新后的配置，旧 `resize.rotate_portrait` 字段应删除。
启动方式、PyInstaller spec、Windows 固定依赖、模型和水印资源复制方式均保留。

本次在 macOS 上完成源码和真实推理验证；没有执行 Windows 构建，也没有生成或声称已验证新版 Windows EXE。
Windows 发布时请用新目录测试原图、原始抠图、最终成品和重复运行。
