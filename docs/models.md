# 模型清单与获取方式

流水线支持三个模型，代码里的 id 与 `models/` 下的实际文件名对应关系如下
（`<档位>` 为量化档位，默认 `Q8_0`；放别的档位也能跑，见「量化档位」）。

| 代码 id | 报告里显示的名字 | 主模型（GGUF） | 视觉投影（mmproj） | 是否必需 |
| --- | --- | --- | --- | --- |
| `unipercept_std` | UniPercept | `models/UniPercept-<档位>.gguf` | `models/mmproj-UniPercept-BF16.gguf` | **默认模型** |
| `unipercept` | UniPercept-abliterated | `models/UniPercept-abliterated-<档位>.gguf` | `models/mmproj-UniPercept-abliterated-BF16.gguf` | 可选 |
| `artimuse` | ArtiMuse | `models/artimuse/ArtiMuse-<档位>.gguf` | `models/artimuse/mmproj-ArtiMuse-F16.gguf` | 可选 |

> 三个模型全装（均按 Q8_0 计）的磁盘占用是 **24.5 GB**（3 × 7.5 主模型 + 3 × 0.6 mmproj）。
> 只装默认模型最低 **8.2 GB** 即可运行；换用低档位后体积同比缩小（见「量化档位」）。

这些文件都可以由 `python setup.py` 自动下载，也可以通过其他途径取得后按上表路径
手动放置——后者见下文「无法访问模型下载站时」。

放好后运行 `python scripts/run_analysis.py --list-models`，三个模型的 `available` 都应为 `true`，
`quant` 字段会显示实际识别到的档位。

## 上游来源

原始权重（safetensors）：

| 模型 | 仓库 | 基座 |
| --- | --- | --- |
| UniPercept | <https://huggingface.co/Thunderbolt215215/UniPercept> | InternVL3-8B |
| ArtiMuse | <https://huggingface.co/Thunderbolt215215/ArtiMuse> | InternVL3 |

社区已转好的 GGUF（`setup.py` 的默认下载来源，均为 apache-2.0）：

| 模型 | GGUF 仓库 |
| --- | --- |
| UniPercept-abliterated | <https://huggingface.co/AX1Y2JP/UniPercept-abliterated-GGUF> |
| UniPercept | <https://huggingface.co/AX1Y2JP/UniPercept-GGUF> |

ArtiMuse 没有社区 GGUF，需要自己转换（见下文「自己转换 GGUF」）。

- UniPercept 论文：<https://arxiv.org/abs/2512.21675>
- ArtiMuse 论文：<https://arxiv.org/abs/2507.14533>
- ArtiMuse 项目主页：<https://github.com/thunderbolt215/ArtiMuse>

## 关于 abliterated 版

`UniPercept-abliterated` 是对 UniPercept 做「拒绝行为移除」（abliteration）后的版本，
输出更少免责声明和拒答，美学评分的措辞更直接。

原版 `UniPercept`（代码 id `unipercept_std`）是本仓库默认模型。两版可用于对照：同样的图，
两个版本的分数分布差异可以直观看到 abliteration 对评分口径的影响。

> **mmproj 说明**：两版 UniPercept 的视觉编码器一致，各自的 mmproj 文件实际可以互换使用——
> 实测两份文件的 442 个张量逐字节相同（仅元数据和张量排序不同），**装一个就够**。
> 社区 GGUF 仓库为两版各自提供了独立文件（`mmproj-UniPercept-abliterated-BF16.gguf` /
> `mmproj-UniPercept-BF16.gguf`），按上表各放各的即可；万一只放了一个，另一个模型会
> 自动拿它兜底，不影响使用。

## 量化档位

主模型不限定 `Q8_0`。社区仓库提供完整量化梯，`setup.py` 安装时可选档位
（`--quant`，或交互菜单里选），也可以事后把任意档位的文件按
`models/UniPercept-abliterated-<档位>.gguf` 的命名放好——**运行时按 `models/` 下实际
文件自动解析，多个档位同时存在时取质量最高的一个**，无需改任何代码。

| 档位 | 主模型体积（约） | 显存建议（约） | 说明 |
| --- | --- | --- | --- |
| `BF16` | 14.2 GB | 16 GB+ | 无损精度 |
| `Q8_0` | 7.5 GB | 10 GB | 默认档位，质量/体积均衡 |
| `Q6_K` | 5.8 GB | 8 GB | 接近 Q8_0 质量 |
| `Q5_K_M` | 5.1 GB | 7.5 GB | 画质轻微下降 |
| `Q4_K_M` | 4.4 GB | 6.5 GB | **推荐低配档位**，8 GB 显卡可跑 |
| `Q4_0` | 4.1 GB | 6 GB | 略逊于 Q4_K_M |
| `IQ4_XS` | 3.9 GB | 6 GB | 性价比档位 |
| `Q3_K_M` | 3.5 GB | 5.5 GB | 明显有损，仅建议显存紧张时使用 |
| `IQ3_M` | 3.3 GB | 5.5 GB | 明显有损 |
| `IQ2_M` | 2.6 GB | 4.5 GB | 大幅有损，仅供体验 |
| `IQ2_XXS` | 2.1 GB | 4 GB | 极限压缩，质量显著下降 |

体积为 8B 主模型的近似值（来自社区仓库实测），显存建议 = 体积 + 约 2 GB 余量
（mmproj 0.6 GB + KV cache）。对美学评分这类细粒度判断，建议至少 `Q4_K_M`；
更低档位可以跑通，但分数稳定性会下降。

用法：

```powershell
# 安装时选档位（交互菜单里也可以选）
python setup.py --quant Q4_K_M

# 或者事后手动放置：把文件命名为对应档位再放进 models/，重跑分析即自动生效
#   models/UniPercept-abliterated-Q4_K_M.gguf
```

两点细节：

- 安装时用默认档位（`Q8_0`）而 `models/` 里已经放了别的档位时，setup.py 会尊重本地文件、
  只补 mmproj，不重复拉 7.5 GB；想强制改回 `Q8_0` 就显式加 `--quant Q8_0`。
- ArtiMuse 的档位在本地转换量化时决定（`setup.py --quant` 同样作用于它）；
  已有 `ArtiMuse-<档位>.gguf` 且 mmproj 就绪时，重跑 setup.py 不会重复转换。

## 自己转换 GGUF

ArtiMuse 没有现成的社区 GGUF。`python setup.py --models artimuse` 会把下面整套流程
（下载 safetensors → 转换 → 量化 → 清理中间产物）自动跑完，还会顺带装好 CPU 版 PyTorch。
手动执行的步骤如下，以 ArtiMuse 为例：

```powershell
# 1. 拉转换脚本（llama.cpp 自带）
git clone --depth 1 https://github.com/ggml-org/llama.cpp
cd llama.cpp

# 2. 主模型转 GGUF（F16）
python convert_hf_to_gguf.py ..\models\artimuse\hf --outfile ArtiMuse-F16.gguf --outtype f16

# 3. 视觉投影单独转（多模态模型必须）
python convert_hf_to_gguf.py ..\models\artimuse\hf --outfile mmproj-ArtiMuse-F16.gguf --outtype f16 --mmproj

# 4. 量化主模型（投影层不量化，保持 F16；档位可换，如 Q4_K_M）
.\llama-quantize.exe ArtiMuse-F16.gguf ArtiMuse-Q8_0.gguf Q8_0
```

要点：

- **视觉投影必须单独转一次**，`--mmproj` 参数不能漏；投影层不量化，否则图像输入会明显掉点。
- 主模型用 `Q8_0` 是质量/体积的平衡点（约 7.5 GB）。显存吃紧可以换 `Q4_K_M`（约 4.4 GB），
  但对美学评分这类细粒度判断，档位越高分数越稳，推荐下限是 `Q4_K_M`。
  量化目标名就是最后一行的参数，可换成上表任意档位。
- `--outtype f16` 而不是 `bf16`：GGUF 不支持 bf16 的 mmproj 直出，转换脚本会报错。

转换产物按上表的路径放好即可（`ArtiMuse-<档位>.gguf`），代码里只认文件名、不认来源。

## 无法访问模型下载站时

模型文件不强制从脚本下载。通过其他途径（朋友分享、网盘、内网镜像等）拿到文件后，
按本文顶部的表格把文件名和目录结构放对，再运行：

```powershell
python setup.py
```

setup.py 会跳过所有已存在的文件——**本地文件齐全时全程不访问模型站，断网也能装完**。
能联网时则会顺手核对文件大小，发现不一致会自动重下覆盖。
（依赖和 llama.cpp 引擎若也没法联网获取，需另行手动放置：见 README「手动分步安装」
与 [docs/llama-cpp.md](llama-cpp.md)。）

几个典型场景：

- **一开始就自己放好了**：放好文件后直接跑 setup.py，选对应模型即可，秒过。
- **下到一半，改用其他途径拿到了完整文件**：删掉中断残留的 `.part` 临时文件
  （`models/` 下与目标文件同名、后缀 `.part` 的那个），把完整文件放成最终文件名，
  重跑同一命令。残留的 `.part` 会被自动清理，不会白占几 GB 空间。
- **ArtiMuse 的 safetensors**：放进 `models/artimuse/hf/`，保持仓库里的目录结构
  （4 个 `*.safetensors` 分片约 15.2 GB，外加 config / tokenizer 等小文件）。
  setup.py 连不上模型站时会按本地已放置的文件继续转换；分片不齐会在转换阶段报错。
- **想保留自己那份不同版本**（比如换了量化等级）：直接把文件按
  `models/UniPercept-abliterated-<档位>.gguf` 的命名放好就行，运行时自动识别、
  多档位并存时取质量最高的；同名同档位的文件若大小与仓库不符，setup.py 会重下覆盖。

## 显存占用

| 阶段 | 占用 |
| --- | --- |
| 主模型权重 | 随档位变化，`Q8_0` 约 7.5 GB / `Q4_K_M` 约 4.4 GB（见「量化档位」表） |
| mmproj BF16/F16 | ~0.64 GB |
| KV cache（`-c 16384`） | ~0.3 GB |
| 合计（Q8_0） | **~8.5 GB** |

`run_analysis.py` 的 `ensure_server()` 会先查一次空闲显存，不足时在日志里给出预警
（模型会回退到 CPU，速度慢一个数量级）。8 GB 显卡建议换 `Q4_K_M` 档位，
并关闭其他占显存的程序（ComfyUI、游戏等）后再跑。
