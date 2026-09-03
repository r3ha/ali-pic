# 图片批处理重构报告

> 历史记录：本报告描述 2026-09-02 的旧版基线，其中旋转、默认模型和跳过整图的行为已变更。
> 当前流程及验收见 [PIPELINE_UPDATE.md](PIPELINE_UPDATE.md)；本报告的历史测试不代表新版测试结果。

日期：2026-09-02。开发/实测环境：macOS arm64、Python 3.11.15。

## 1. 完成范围和重要边界

已将独立脚本重构为单张图片流水线、统一配置、启动校验、错误隔离、日志与命名管理，
并提供 Windows `onedir` 构建入口、固定依赖锁和搬移后的推理检查。
新主流程不调用 BAT、rembg CLI 或中间处理脚本。原文件及用户两张原图均保留。

两点不能从旧文件推断：

1. BAT 只写了 `rembg p input output`，没有依赖版本、模型名或 provider。原 Windows 安装环境未提供。
   本次固定 **rembg 2.0.67 + 官方 U²-Net + CPU**，核对了这个版本的 CLI 默认值，
   并用这个版本执行旧 CLI 对照；不能声称已复现未知 Windows 版本或 GPU 后端。
2. 旧 resize 保存 PNG，旧 watermark 只读取 JPG/JPEG，原样串联水印步骤会处理 0 张。
   本次修复格式衔接，直接对内存图像加水印并保存 PNG。没有猜测或加入未知的人工 JPEG 转换步骤。

默认保留旧版**两种白底构图**，而非擅自改成一张透明图。
输出简化为 `原名.png`、`原名_效果图.png`。仅主图/透明背景通过明确配置选择。

## 2. 新目录结构

```text
ali-pic/
├── main.py                         命令行、输入重试、日志、退出码
├── app_config.py                   路径解析、严格配置校验、类型化配置
├── config.json                     外部可编辑参数
├── pipeline.py                     批处理/单图 API、扫描命名、原子保存
├── model_store.py                  固定模型来源、版本和权重完整性校验
├── processors/
│   ├── __init__.py
│   ├── remove_bg.py                本地模型 session 和 rembg API
│   ├── resize.py                   保持旧缩放和定位
│   └── watermark.py                提前加载水印、保持旧合成
├── assets/
│   └── watermark.png               与原水印字节一致
├── models/
│   ├── README.md
│   └── u2net.onnx                  实际已下载、校验，外部文件
├── requirements.txt               固定核心依赖
├── requirements-build.txt          PyInstaller 和 hook 版本
├── requirements-windows.lock       Windows / Python 3.11 完整依赖锁
├── requirements-test.txt           旧 CLI 对照测试额外依赖
├── ImagePipeline.spec              正式 onedir 配置
├── build.bat                       Windows 开发者构建入口
├── tools/
│   ├── prepare_model.py            下载/导入并校验模型
│   ├── build_release.py            构建和验收步骤
│   ├── release_support.py          复制外部资源、依赖许可证和版本清单
│   ├── frozen_runtime.py           冻结模式计算缓存路径
│   └── smoke_release.py            搬移发布包、清理开发环境变量后实际推理
├── tests/
│   ├── test_pipeline.py            像素回归、隔离、命名、路径、保存等
│   ├── test_cli.py                 实际子进程验证友好启动错误
│   ├── verify_real_images.py       可重复执行的真实旧/新对比
│   ├── artifacts-real-run.log      本次真实运行记录（开发产物）
│   └── artifacts/                 本次实测证据（开发产物）
│       ├── real-image-report.json
│       ├── new_output/             两张原图及 PNG/WebP 样本的 8 张成品
│       ├── new_cutouts/            两张真实原图的新版 rembg 输出
│       ├── legacy_resize/          实际运行旧 resize 的 4 张结果
│       ├── legacy_composited_before_jpeg/
│       ├── transparent-sample.png
│       └── legacy-*.log
├── licenses/
│   ├── README.md
│   ├── U-2-Net-LICENSE.txt
│   └── rembg-LICENSE.txt
├── docs/
│   └── REFACTOR_REPORT.md
├── README.md
├── 使用说明.txt
├── .gitignore
├── dir-rembg.bat                   legacy，原样保留
├── png-resize.py                   legacy，原样保留
├── watermark.py                    legacy，原样保留
├── watermark.png                  原水印，原样保留
├── 4T8A8532.JPG                    用户提供原图，原样保留
├── 4T8A8682.JPG                    用户提供原图，原样保留
├── logs/                          本次及后续运行日志（自动生成）
├── .venv/                         本地开发环境（不分发）
├── build/                         构建临时产物（不分发）
└── dist/
    └── macos-validation/          仅本机打包验证产物，不是 Windows EXE
```

除四个 legacy 文件、两张用户原图、系统 `.DS_Store` 外，以上项目文件为本次新增。
**没有修改或删除旧脚本，没有修改原图，没有提交 Git（当前目录没有 Git 仓库）。**
开发缓存/模型/构建产物均有 `.gitignore` 规则；大模型不会进入 EXE。

## 3. 功能迁移

| 旧代码 | 新位置 | 变化 |
| --- | --- | --- |
| `dir-rembg.bat` | `processors/remove_bg.py` + `model_store.py` | Python API，单批次只加载一次本地模型 |
| `png-resize.py` | `processors/resize.py` | 原裁剪、旋转、round、LANCZOS、居中算法迁移 |
| `watermark.py` | `processors/watermark.py` | 原合成方式迁移，提前读一次水印，取消硬编码 D 盘路径 |
| 各脚本遍历/保存/错误输出 | `pipeline.py`、`main.py` | 当前层扫描、统一命名、成品原子写入、日志和汇总 |
| 散落的可调参数 | `config.json`、`app_config.py` | 启动时校验；错误不进入处理阶段 |

旧脚本不参与新版业务流程，仅用于回归对照。

## 4. 原行为逐项提取与保留

| 项目 | 原代码/已验证基线 | 新版默认 |
| --- | --- | --- |
| rembg 调用 | BAT 无任何显式选项：`rembg p` | 固定 API 调用，参数显式化 |
| 模型/provider | 2.0.67 CLI 默认 u2net；CPU 环境采用 CPU | u2net / CPUExecutionProvider |
| Alpha matting | false；前景 240、背景 10、腐蚀 10 | 相同 |
| 仅 mask/后处理 | false / false | 相同 |
| rembg bgcolor | p 命令默认 `(0,0,0,0)` | 显式相同；不是自行改为 putalpha |
| 原输入/输出 | 2.0.67 p 会递归扫描、PNG 输出、已有文件跳过；BAT 再附 `_no_bg` | 按新需求只扫描当前层，成品统一 output，无 `_no_bg` |
| 透明裁剪 | 用 `alpha < 10` 排除边界；原 alpha 保留；无主体报错 | 相同 |
| 方向 | 裁剪后高大于宽，逆时针旋转 90°，expand=True | 相同 |
| 主图尺寸 | 最长边 900，另一边 Python `round` | 相同 |
| 主图画布 | RGB 白底 1000×1000 | 同色同尺寸；保存 RGBA PNG，alpha=255 |
| 效果图 | ratio≥1.618 时宽900；否则高600；另一边 round | 相同，包括 1.5～1.618 区间宽度可能超过900 |
| 效果图画布 | RGB 白底 1000×618 | 同色同尺寸；保存 RGBA PNG，alpha=255 |
| 重采样 | Pillow LANCZOS | 同一固定 Pillow 11.3.0 / LANCZOS |
| 图片定位 | `(canvas - image) // 2`，没有额外偏移 | 同样居中，配置 offset 默认 `[0,0]` |
| 背景合成 | RGB 白底 `paste(image, xy, alpha)` | 保持此操作后转 RGBA，避免透明合成舍入变化 |
| 水印文件 | 1000×618，RGBA；alpha 范围0～77 | assets 副本与原文件 SHA-256 相同 |
| 水印可见范围 | alpha bbox `(207,407,794,594)` | 相同 |
| 水印缩放 | 不缩放 | scale=1.0，不做 resize |
| 水印位置/边距 | 左上角 `(0,0)`；额外边距0 | 相同；不随画布自动移位 |
| 水印透明度 | 完全使用 PNG 自带 alpha | opacity=1.0，保留原 alpha |
| 水印合成 | RGB `paste(watermark, (0,0), mask=watermark)` | 白底模式相同；不使用会导致 alpha 平方的 RGBA paste |
| 旧最终格式 | resize PNG 72DPI optimize；watermark JPEG quality95 | PNG 72DPI optimize，不增加有损中间步骤 |
| 旧命名 | `_no_bg_主图.png` / `_no_bg_效果图.png`；水印保留 JPG 名 | 简化为 `原名.png` / `原名_效果图.png` |
| 重复运行 | 各脚本规则不一 | 默认 skip；可配置 overwrite；不会改原图 |

为了保留已验证视觉，默认最终背景**不是透明背景**，这是旧 resize 的明确行为。
透明模式由配置主动开启，内部和成品均正确保留 alpha；实际单图测试已通过。

## 5. 完整数据流及容错

```text
启动 → 外部配置读取/校验 → 水印读取 + 官方模型校验
 → 输入目录（无效则重输）
 → 只扫描当前层 → 为全部成品预分配无冲突名称
 → 首次有待处理项时创建 1 个 CPU rembg session
 → 逐原图加载 → 去背景 → 裁透明边 → 必要时逆时针90°
 → main / golden 各自缩放居中 → 水印 → 最终 PNG 编码
 → 同目录临时文件原子提交至 <输入目录>/output
 → 下一张 → 汇总成功/失败/跳过/部分成品和耗时
```

原图只读；两种构图共用一次去背景，没有 temp1/temp2 中间图片。
临时文件仅服务于最终 PNG 防中断写入，正常/失败退出会清理。
每张图异常隔离；坏文件不会阻止后面的图片。详细 traceback 在日志，不在普通用户终端。
输出目录符号链接/Windows junction 被拒绝，防止 output 实际指回原图或其他目录。
文件名采用大小写无关的预分配；扩展名冲突及 `_效果图` 名冲突不会覆盖。

模型、配置或水印不存在会在处理前报错。JSON 语法、重复键、遗漏键、未知键、类型、范围、
非法枚举和互相矛盾的阈值会提前校验。

## 6. 模型和离线发布

采用方案 B：**官方 U²-Net 权重随目录提供**。
本次实际准备了约176MB（168MiB）的 `models/u2net.onnx`，官方 MD5：
`60024c5c889badc19c04ad937298a77b`。

实现依据：固定版 `BaseSession` 会通过 session 类的 `download_models()` 获得文件路径。
新版继承原 `U2netSession`，仅覆盖这个路径获取方法，返回已校验外部文件；
归一化、预测、mask 缩放等仍执行官方原方法。没有换成存在细节差异的 `u2net_custom`。
session 构造选项复现原 factory 的线程设置，固定 CPU，避免 CUDA 安装依赖。

运行程序不调用 pooch 下载，不依赖用户 `~/.u2net`、`U2NET_HOME` 或当前工作目录。
开发者工具负责下载/导入；出错不会把坏文件当成正式权重，也不会偷偷替换已有坏模型。
真实运行测试将 Python socket 连接禁用后仍成功，证据见 JSON 报告。

rembg 原项目为 MIT；U-2-Net 上游仓库提供 Apache-2.0 许可证及预训练模型链接。
已附原始文本。ONNX 转换下载没有另附独立许可证文件，来源及这个边界已记录在 licenses/README.md，
没有把 rembg 的 MIT 许可证泛化为所有模型的许可。未来替换模型必须重新核对预测逻辑、权重来源和许可。

## 7. PyInstaller 设计

- `EXE(exclude_binaries=True)` + `COLLECT`，`onedir`，控制台入口，不做 onefile/UPX。
- `collect_submodules("rembg.sessions")` 收集 session registry；排除 CLI、Gradio、FastAPI、Torch 等。
- `collect_dynamic_libs("onnxruntime")` 加 ORT C API hidden imports，收集 provider DLL/动态库。
- Pillow、NumPy、SciPy 使用固定版 PyInstaller hooks；`llvmlite` 额外收集本地动态库。
- `copy_metadata("rembg")` 支持冻结后版本校验。
- PyMatting 使用 `module_collection_mode="py"`，提供实际源码用于 Numba JIT/缓存定位；
  runtime hook 把 Numba 缓存放入用户可写位置，不要求安装目录可写。
- 配置、水印和模型完全不进 `Analysis.datas`；构建末尾按相对路径复制到 EXE 旁。
- spec 也直接检查和复制外部资源；build.bat 再负责模型准备、测试、清理和搬移验收。
- 打包记录依赖版本和许可证；不把用户原图或测试成品复制进发布目录。

开发模式 `Path(__file__).parent`，冻结模式 `Path(sys.executable).parent`。
`sys._MEIPASS` 仅由 PyInstaller 管理内部代码/依赖，不作为可编辑资源根。

## 8. 实际测试

### 自动化回归

`python -m unittest discover -s tests -v`：**14 项通过**，其中一项包含4种实际 CLI 启动错误子进程。

- 与未经修改旧 resize 的逐像素比较：横图、竖图、正方形、1.618 分支两侧。
- 实际调用旧 watermark 函数，截取 JPEG 编码前图像，逐像素比对。
- alpha 阈值、半透明边缘、透明模式、非零偏移。
- Windows 大小写规则、同名扩展名、主图/效果图名称冲突。
- 当前层扫描、不重复处理 output、损坏图隔离、原始字节不变、session一次初始化。
- 原子保存、skip、overwrite、模拟磁盘写失败保留旧成品。
- 路径/冻结根目录模拟、资源缺失、JSON错误/类型/范围/重复/遗漏/未知参数。
- 相机 MPO/JPG 的第一帧兼容：真实两张 JPEG 含一张5472×3648主图和一张1620×1080预览，
  已修复初版多帧检查误拒绝这类照片的问题。
- 真实 CLI 子进程：缺失水印、缺失模型、JSON错误、类型错误，均退出码2且终端无 traceback，日志有详情。

### 真实 U²-Net 测试

输入为用户提供的 `4T8A8532.JPG`、`4T8A8682.JPG`，另由第一张生成 PNG 和 WebP 样本，加入1个损坏 PNG。
在中文/空格/带引号路径执行，结果：**5个输入，4成功，1预期失败，8张成品，session初始化1次**。
记录批次耗时约6.62秒（本机一次观测，不代表 Windows 性能）。
重复运行：4张正常原图均跳过，损坏图仍被单独报告，已有成品哈希不变。
额外真实单张 PNG 透明模式：成功，输出 RGBA，alpha 范围0～255。
原始两张 JPEG 的 SHA-256 在前后完全一致，记录于 JSON。

另实际运行 `main.py` 的交互输入：先输入不存在目录，再输入带引号的中文/空格目录；
从 `/private/tmp` 启动，成功重试、完成处理，资源不依赖 CWD，终端无 traceback。

### 新旧视觉对比

真实执行 rembg **CLI p 命令**、旧 BAT 的等价重命名（macOS 无法执行 BAT）、未经修改的 `png-resize.py`。
旧水印代码对 PNG 目录确实处理0张，该格式缺口有独立日志。

| 原图 | rembg RGBA 不同像素 | 主图合成后不同像素 | 效果图合成后不同像素 |
| --- | ---: | ---: | ---: |
| 4T8A8532.JPG | 0 | 0 | 0 |
| 4T8A8682.JPG | 0 | 0 | 0 |

这里的最终比较基准是“旧 resize PNG + 原水印 paste 算法”的 **JPEG 编码前像素**。
另将旧 resize 结果显式转 JPEG quality95，再调用旧 watermark 真正保存 JPEG，测得与新 PNG 的
通道最大差异14～23/255，来自两次 JPEG 有损编码。这个桥接是测试构造的，未声称它就是用户旧人工步骤。

已目视检查两张代表成品，尺寸、位置、水印一致；部分背景残影是同一 U²-Net 基线的结果，
没有在重构中擅自修边、改模型或改透明处理。

### 打包测试

Windows `build.bat` 和 Windows EXE：**尚未执行，当前没有 Windows 环境。**
本机额外完成 macOS PyInstaller onedir 构建及搬移推理验证，最终状态见本报告末尾“交付验证状态”。
源码测试和 macOS 打包都不能替代干净 Windows 机器的最终验证。

## 9. 构建和最终使用

开发者在 Windows Python 3.11 x64 环境双击 `build.bat`。构建失败会明确返回非零；
通过后取得 `dist/ImagePipeline/`，整目录复制给用户。
首次构建需要准备依赖/模型，后续可复用环境和模型；已有官方模型可离线导入。

最终用户：

1. 双击 `ImagePipeline.exe`。
2. 输入需要处理的图片目录。
3. 在该目录的 `output` 文件夹获取结果。

以后直接改外部 config 或替换 assets/watermark.png，无须重建 EXE。

## 10. 官方实现依据

- [rembg 2.0.67 p 命令默认值与遍历行为](https://github.com/danielgatis/rembg/blob/v2.0.67/rembg/commands/p_command.py)
- [U2netSession 预测和权重校验值](https://github.com/danielgatis/rembg/blob/v2.0.67/rembg/sessions/u2net.py)
- [BaseSession 的模型路径机制](https://github.com/danielgatis/rembg/blob/v2.0.67/rembg/sessions/base.py)
- [rembg session factory 线程选项](https://github.com/danielgatis/rembg/blob/v2.0.67/rembg/session_factory.py)
- [rembg 官方 spec 对照](https://github.com/danielgatis/rembg/blob/v2.0.67/rembg.spec)（未引入其 Gradio 依赖）
- [PyInstaller 运行时路径](https://pyinstaller.org/en/stable/runtime-information.html)
- [U-2-Net 上游项目](https://github.com/xuebinqin/U-2-Net)

## 11. 交付验证状态

**macOS PyInstaller 实际构建和搬移验证通过**：

- 生成 `dist/macos-validation/ImagePipeline/`，EXE 对应本机 Mach-O 可执行文件 `ImagePipeline`。
- 复制整个发布目录到独立、含中文和空格的临时目录。
- 从另外的工作目录启动；清除 PYTHONPATH/PYTHONHOME/VIRTUAL_ENV，PATH 仅保留系统目录。
- 发布根目录没有应用 `.py` 源码，依赖/模型/水印/配置均由发布目录提供。
- 实际处理一张生成的 JPG 测试图，输出主图和效果图；原图 SHA-256 不变。
- 再次运行成功，成品数量仍为2，不再次处理 output。
- 首次运行约41.5秒，包含 PyMatting/Numba 首次本地编译和模型加载；源码环境已缓存时的耗时不具可比性。

本验证实质发现并修复了 `cannot cache function '_make_tree': no locator available`：
仅把源码与 PYZ 同时打包不够，需将 PyMatting 改为 **源码模式 `py`**，使函数的源码路径能被 Numba 定位。
最终 smoke 脚本退出码0，日志末行是 `Relocated frozen inference and repeat-run checks passed.`。

本机构建日志仍有若干可选模块/macOS SDK/rpath 提示；当前实际使用的 CPU 抠图路径已运行通过，
不将这个结果扩大为对所有可选依赖或其他平台的保证。

证据：

- `logs/unittest.log`：14项测试通过。
- `tests/artifacts/real-image-report.json`：真实原图哈希、抠图和最终图像逐像素差异。
- `tests/artifacts-real-run.log`：真实批处理执行记录。
- `logs/cli-interactive.log`：独立 CWD 的输入重试与实际处理。
- `logs/pyinstaller-macos.log`：本机实际 PyInstaller 构建。
- `logs/frozen-smoke-macos.log`：复制发布目录后的真实推理及重复运行。

**仍需 Windows 最终执行**：双击 `build.bat`，取得 `dist/ImagePipeline/ImagePipeline.exe`；
在干净 Windows x64 机器断网运行用户原图，核验 ORT DLL/运行库、中文路径、资源定位、成品和双击窗口行为。
没有生成或宣称验证 Windows EXE。
