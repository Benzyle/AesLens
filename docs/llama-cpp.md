# 推理引擎（llama.cpp）部署

流水线通过 llama.cpp 的 `llama-server` 提供 OpenAI 兼容接口，`run_analysis.py` 只跟
`http://127.0.0.1:8080` 通信。

## 一键获取（推荐）

```powershell
python scripts/fetch_llama_cpp.py
```

脚本会：

1. 查询 `ggml-org/llama.cpp` 最新的 nightly release（tag 形如 `b11819`）
2. 挑选匹配当前平台的资产（Windows x64 → `llama-b11819-bin-win-vulkan-x64.zip`）
3. 下载并解压到 `tools/llama-vulkan/`

其他用法：

```powershell
python scripts/fetch_llama_cpp.py --list                  # 只看有哪些资产
python scripts/fetch_llama_cpp.py --build 11815           # 指定 build 号
python scripts/fetch_llama_cpp.py --backend cuda-12.4     # 换 CUDA 后端
python scripts/fetch_llama_cpp.py --dest "D:\llama-vulkan"
```

> llama.cpp 的 Windows 二进制只在 nightly 构建里发布，正式 Release 不带预编译包，
> 所以脚本查的是 prerelease 列表里最新的一个。

## 手动下载

到 <https://github.com/ggml-org/llama.cpp/releases> 找最新的 nightly，
下载对应平台的 zip，解压后把内容（`llama-server.exe` 与一堆 DLL）直接放进 `tools/llama-vulkan/`。

## 后端选择

| 后端 | 适用显卡 | 备注 |
| --- | --- | --- |
| `vulkan` | NVIDIA / AMD / Intel 通用 | 默认。无需装 CUDA，兼容性最好 |
| `cuda-12.4` | NVIDIA（驱动支持 CUDA 12） | 同模型比 Vulkan 快 10-30% |
| `cuda-13.4` | NVIDIA（较新驱动） | |
| `cpu` | 无独显 | 慢一个数量级，只建议试跑 |

切后端只需要重新下载并覆盖 `tools/llama-vulkan/`，代码不用改。

## 启动参数

`run_analysis.py` 的 `ensure_server()` 用下面这组参数拉起 llama-server：

```
llama-server.exe -m <主模型> --mmproj <视觉投影>
  -ngl 99            # 全部层放 GPU
  -c 16384           # 上下文长度
  -np 2              # 并行槽位（配合 --workers 2）
  --port 8080
  --host 127.0.0.1    # 只听本机，不暴露到局域网
  -fa on             # Flash Attention
```

三个模型的 `-m` / `--mmproj` 路径在 `scripts/run_analysis.py` 的 `MODELS` 字典里。

## 验证引擎可用

```powershell
tools\llama-vulkan\llama-server.exe --version
```

然后启动一次分析，日志里出现 `llama-server 就绪（<模型名>, Ns）` 即正常。
server 的完整输出在 `logs/server.log`，启动失败时 `ensure_server()` 会把该文件尾部
一起抛出来，多数是显存不足或模型文件损坏。

## 端口占用

| 端口 | 服务 |
| --- | --- |
| 8080 | llama-server（推理） |
| 8081 | 启动台（Web UI） |

`stop.bat` 会两个一起清。如果 8080 被别的程序占用，llama-server 起不来，
日志里会是「启动后立即退出」。
