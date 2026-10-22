# AesLens美学分析工具（Image Aesthetics Evaluator）

本地运行的图片美学评价流水线：用 [llama.cpp](https://github.com/ggml-org/llama.cpp) 加载
[UniPercept](https://huggingface.co/Thunderbolt215215/UniPercept) /
[ArtiMuse](https://huggingface.co/Thunderbolt215215/ArtiMuse) 视觉模型，对文件夹里的图片逐张打分，
生成一个可离线打开、可直接分享的单文件 HTML 报告。

全程本地推理，不调用任何外部 API；源图片**不会被移动或修改**。

---

## 功能

- **三个模型可选**：UniPercept（默认） / UniPercept-abliterated / ArtiMuse
- **8 个维度评分**（0-100 任意整数）+ 逐维度中文点评 + 画面描述 + 8 维度优化方向 + 综合评价
- **作业制管理**：每次分析是一个「作业」，结果按 `work/<作业名>/` 归档，可随时换模型续跑
- **加载 / 移除作业**：启动台里可从 `work/` 文件夹直接加载已完成的作业，也可把作业从列表移除
- **自包含 HTML 报告**：缩略图 base64 内嵌、Chart.js 内联，单文件离线可看
- **启动台 Web UI**：`http://127.0.0.1:8081`，作业管理 / 模型选择 / 进度条 / 实时日志 / 系统资源监控
- **NSFW 过滤**：按 booru 类文件名标签识别，报告内一键隐藏

## 目录结构

```
AesLens美学分析工具/
├── run.bat                  # 一键启动（Windows）
├── stop.bat                 # 一键停止（Windows）
├── setup.py                 # 一键安装（环境 + 引擎 + 模型 + 自检）
├── uninstall.py             # 卸载辅助（清掉模型/引擎/运行数据，保留程序本身）
├── requirements.txt         # Python 依赖（仅 Pillow）
├── README.md
├── scripts/
│   ├── common.py            # 公共路径、作业注册表、模型文件解析（量化档位自动识别）
│   ├── check_models.py      # 模型文件预检（run.bat 启动前调用）
│   ├── launcher.py          # 启动台 HTTP 服务（端口 8081）
│   ├── run_analysis.py      # 分析流水线
│   ├── make_report.py       # 报告生成
│   └── fetch_llama_cpp.py   # 下载 llama.cpp 预编译二进制
├── web/launcher.html        # 启动台 UI
├── vendor/chart.umd.js      # Chart.js v4.5.1 UMD（内联进报告）
├── docs/
│   ├── models.md            # 模型清单与获取/转换方式
│   └── llama-cpp.md         # 推理引擎部署说明
├── models/                  # GGUF 模型文件（setup.py 自动下载，三个全装约 24.5 GB）
├── input/                   # 待评价图片目录（可选，源文件不会被移动）
├── work/                    # 作业输出：report.html / *_conclusions.json / thumbs/
├── logs/                    # 运行日志
├── state/                   # jobs.json 作业注册表等运行时状态
└── tools/llama-vulkan/      # llama.cpp 二进制（setup.py 自动下载）
```

## 快速开始

### 一键安装（推荐）

```powershell
python setup.py
```

交互式引导，自动完成：创建虚拟环境 `.venv` → 安装依赖（Pillow）→ 下载 llama.cpp
预编译二进制 → 下载模型 → 自检。默认只装 `unipercept_std`（显示名 UniPercept）一个模型
（Q8_0 档约 8.2 GB），可在菜单里加选另外两个。

```powershell
python setup.py --yes                  # 无交互，默认模型
python setup.py --models all           # 三个模型全装（约 24.5 GB）
python setup.py --models artimuse      # 只装 ArtiMuse（需本地转换，30-60 分钟）
python setup.py --quant Q4_K_M         # 换量化档位（显存不够时用，约 4.4 GB）
python setup.py --cn                   # pip 走清华镜像（国内加速）
```

主模型不限定 Q8_0：仓库提供 BF16 → IQ2_XXS 共 11 档，安装时用 `--quant` 选，
或事后把任意档位文件按命名规则放进 `models/`，运行时自动识别（详见
[docs/models.md](docs/models.md)「量化档位」）。

交互流程为**先选模型、再选量化档位**；量化档位只有 UniPercept 系需要——选 ArtiMuse 时
它没有预置量化档位，直接跳过（本地转换时按 Q8_0 量化）。

下载支持断点续传与自动重试，中断后重新运行同一条命令即可继续。
全部参数见 `python setup.py --help`。

Windows 用户也可以直接在资源管理器里双击 `setup.py`（需已安装 Python 3.9+
并在安装时勾选 Add to PATH）：脚本结束后会等待按键再关闭窗口，出错时也会停住显示错误，
不会一闪就没。在终端里运行输出更易复制，效果相同。

### 手动分步安装

#### 1. 安装依赖

```powershell
python -m pip install -r requirements.txt
```

只依赖 Pillow（用于生成缩略图）。Python 3.9+。

#### 2. 准备推理引擎

```powershell
python scripts/fetch_llama_cpp.py
```

会下载 llama.cpp 最新的 Windows x64 Vulkan  nightly 包并解压到 `tools/llama-vulkan/`。
非 Windows 平台或想用 CUDA 后端，见 [docs/llama-cpp.md](docs/llama-cpp.md)。

#### 3. 准备模型

模型文件约 24.5 GB（三个全装，均按 Q8_0 计；换低档位可显著缩小），放入 `models/` 即可——
目录结构与获取方式见 [docs/models.md](docs/models.md)。文件名带量化档位
（如 `UniPercept-abliterated-Q4_K_M.gguf`），放哪个档位都能跑，运行时自动识别。
`models/` 已被 `.gitignore` 忽略（仅保留 `.gitkeep` 占位），不会误提交大文件。

放好后用下面命令自检，输出里每个模型的 `available` 为 `true`，
即表示主模型与视觉投影文件都已就位：

```powershell
python scripts/run_analysis.py --list-models
```

#### 4. 启动

```powershell
python scripts/launcher.py
```

浏览器打开 <http://127.0.0.1:8081>：

1. 填作业名 → 「选择图片文件夹」选一个图片目录 → 「新建作业并选中」
2. 选模型、并行数（建议 2）、数量限制
3. 「开始分析」→ 看进度条与实时日志
4. 「查看报告」打开 `work/<作业名>/report.html`

启动台里还可以：点「从 work 文件夹加载作业」把以前分析过的作业找回来（重装、换电脑后尤其有用）；
点作业右侧的 ✕ 把作业从列表移除（文件默认保留在 `work/`，可再次加载；确认时也可选择连同文件一起删除）。

Windows 下也可以直接双击 `run.bat`。

## 命令行用法

```powershell
# 列出模型与文件是否就绪（quant 字段为实际识别到的量化档位）
python scripts/run_analysis.py --list-models

# 新建作业并分析（默认 unipercept_std，即 UniPercept）
python scripts/run_analysis.py --new-job "我的图" --folder "D:\图片" --model unipercept_std --workers 2

# 继续某个作业，换模型分析
python scripts/run_analysis.py --job job-20261022-101530 --model artimuse --workers 2

# 只补齐缺失的画面描述 / 优化方向
python scripts/run_analysis.py --job job-20261022-101530 --supplement

# 重命名作业（输出目录一并迁移）
python scripts/run_analysis.py --job job-20261022-101530 --rename-only "新名字"

# 单独生成报告
python scripts/make_report.py --job job-20261022-101530
```

脚本会自动完成：启动对应模型的 llama-server → 逐图评价 → 写入 `work/<作业名>/` → 生成报告。

## 卸载

默认安装方式（创建 `.venv` 虚拟环境）下，**所有内容都装在项目文件夹内**：
`.venv/`（虚拟环境）、`tools/llama-vulkan/`（推理引擎）、`models/`（模型文件）、
`tools/convert/`（ArtiMuse 转换脚本）、`work/ logs/ state/`（运行数据）。
不写注册表、不创建快捷方式、不开系统服务、不改 PATH——**直接删掉整个文件夹即为完全卸载**。

只想省磁盘、保留程序：运行卸载辅助（项目文件夹里执行）：

```powershell
python uninstall.py            # 交互式：先选操作，列出清单，确认后执行
python uninstall.py --dry-run  # 只列清单和体积，不删任何文件
python uninstall.py --yes      # 无交互：执行「以上全部」
```

打开后可按需要多选（逗号分隔）：

| 操作 | 清理范围 |
| --- | --- |
| 1) 清除缓存及日志 | `logs/`、`state/`、`.part` 下载残留、`__pycache__` |
| 2) 卸载模型 | `models/` 下的 GGUF（三个全装约 24.5 GB） |
| 3) 卸载工具 | `tools/llama-vulkan/`（llama.cpp）与 `tools/convert/`（转换脚本） |
| 4) 以上全部 | 再加 `.venv/` 与 `work/`（作业输出），恢复到手装后的干净状态 |

三个单项操作都**不会删除 `work/` 里的报告与结论**（避免误删分析成果），只有选
「以上全部」才清。程序本身（`scripts/ web/ docs/`、README、`run.bat` 等）一律保留；
仓库自带的 `.gitkeep` 占位目录会保留为空骨架，之后重跑 `python setup.py`
即是全新安装。Windows 下也可以直接双击 `uninstall.py`。

> 唯一例外：安装时若选择了「直接装入当前 Python」（`--system` 或菜单里选 2），
> 依赖会装进系统 Python 的 site-packages，删文件夹前需要先卸掉：
>
> ```powershell
> python uninstall.py --system   # 追加卸掉装进当前 Python 的 pip 包
> ```
>
> 或手动执行：`pip uninstall -y -r requirements.txt` 与
> `pip uninstall -y torch safetensors numpy gguf`（后者仅 ArtiMuse 路线会装）。
> 若这些包原本就有（其它项目在用），可跳过第二条。

## 评价维度

| # | 维度 | English |
| --- | --- | --- |
| 1 | 构图与设计 | Composition & Design |
| 2 | 视觉元素与结构 | Visual Elements & Structure |
| 3 | 技术执行 | Technical Execution |
| 4 | 原创性与创意 | Originality & Creativity |
| 5 | 主题与表达 | Theme & Communication |
| 6 | 情感与观众反应 | Emotion & Viewer Response |
| 7 | 整体格式塔 | Overall Gestalt |
| 8 | 综合评价 | Comprehensive Evaluation |

每张作品包含：8 维度评分、逐维度中文点评、画面描述、8 维度优化方向、综合评价。
综合得分 = 各维度得分的算术平均（必落在各维度得分区间内，逻辑自洽）。

## 报告功能

`work/<作业名>/report.html`：

- 顶部切换模型
- 总览：综合得分分布直方图 / 均值 / 极值
- 得分排行：综合 + 8 维度，支持按得分、投稿 ID、文件名排序
- 时间趋势：可在「得分–时间」与「得分–ID」两条曲线间切换
- **投稿 ID**：从文件名 `{id}_{page}-{title}` 解析 pixiv 作品 ID。批量下载会把文件时间刷成下载时刻，
  此时用 ID 判断创作先后更可靠（ID 越大越新）
- 作品详情：固定头部（缩略图、综合得分、画面描述随滚动固定），左右箭头 / 键盘 ← → 切换作品
- 「隐藏 NSFW」开关，NSFW 作品带 `18+` 徽标

## NSFW 判定

按 booru 类原始文件名里直接携带的标签判断（`naked` / `nipples` / `pussy` / `uncensored` /
`creampie` 等，共 23 个整词 + 8 个子串），命中即判 NSFW 并在报告里可一键隐藏。
规则见 `scripts/make_report.py` 顶部的 `NSFW_TOKENS` / `NSFW_SUBSTR`，可自行增删。

> 这是纯文件名规则，**不做图像内容识别**。pixiv 风格命名（纯数字 ID）的文件不会被命中。

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `AESTHETICS_ROOT` | 覆盖项目根目录（默认取 `scripts/` 的上级目录）。想把程序和数据盘分离时使用 |

## 硬件与性能

- llama-server 监听 `127.0.0.1:8080`，单模型显存需求随档位变化：Q8_0 约 7.5 GB + 1.5 GB 余量，
  Q4_K_M 约 4.4 GB + 1.5 GB 余量（完整对照见 [docs/models.md](docs/models.md)「量化档位」）
- RTX 5070 Ti 上单张图约 15-20 秒（Vulkan 后端，Q8_0）
- 显存不足时模型会回退到 CPU，速度会大幅下降；启动台会提前给出显存预警

## 常见问题

**报告很大、打开慢？**
报告把缩略图和 Chart.js 都内联成了单文件，几百张图的报告通常在 5-20 MB，首次打开略慢属正常。

**端口 8081 被占用？**
说明已有一个启动台在跑。直接访问 <http://127.0.0.1:8081>，或先运行 `stop.bat` 再启动。

**模型文件缺失？**
`python scripts/run_analysis.py --list-models` 里 `available` 为 `false` 的模型就是文件没放对位置，
对照 [docs/models.md](docs/models.md) 检查。

**换电脑 / 重装后，以前的作业不在列表里了？**
作业文件都还在 `work/` 文件夹里。打开启动台，点「从 work 文件夹加载作业」，
选中对应目录点「加载」即可找回（可查看报告、重新生成报告；加载的作业未记录图片目录，不能续跑分析）。

**显存不够，跑不动 Q8_0？**
换低档位即可，两种办法：① 安装时选档位——`python setup.py --quant Q4_K_M`
（约 4.4 GB，8 GB 显卡可跑）；② 事后把任意档位的文件按
`models/UniPercept-abliterated-<档位>.gguf` 的命名放进 `models/`，运行时自动识别。
档位对照表与显存建议见 [docs/models.md](docs/models.md)「量化档位」。

**两个 mmproj 都要装吗？**
不用。两版 UniPercept 的 mmproj 权重实测逐字节相同（442 个张量），装一个就够，
另一个模型会自动拿它兜底。

**访问不了模型下载站（hf-mirror.com / huggingface.co）？**
通过其他途径（朋友分享、网盘、内网镜像等）拿到模型文件后，按 [docs/models.md](docs/models.md)
顶部的表格把文件名和目录结构放对，再运行 `python setup.py`：已存在的文件会自动跳过，
**本地文件齐全时全程不访问模型站，断网也能装完**。下到一半改用其他途径：删掉中断残留的
`.part` 临时文件、放上完整文件，重跑同一命令即可。ArtiMuse 的 safetensors 放
`models/artimuse/hf/`。详见 docs/models.md「无法访问模型下载站时」。

## 致谢

- [llama.cpp](https://github.com/ggml-org/llama.cpp) — 推理引擎
- [UniPercept](https://huggingface.co/Thunderbolt215215/UniPercept) / [ArtiMuse](https://huggingface.co/Thunderbolt215215/ArtiMuse) — 美学评价模型
- [Chart.js](https://www.chartjs.org/) — 报告图表

## 许可

代码以 MIT 许可发布，见 [LICENSE](LICENSE)。
模型权重遵循各自原作者的许可（学术研究用途），商业使用前请查阅对应模型卡。
