#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键安装：配置环境、下载 llama.cpp 与模型、自检。

用法（在项目根目录运行）：
  python setup.py                       # 交互式安装（推荐）
  python setup.py --yes                 # 无交互：默认模型，全部自动确认
  python setup.py --models all         # 安装全部三个模型
  python setup.py --models unipercept,artimuse
  python setup.py --skip-llama          # 跳过 llama.cpp 下载
  python setup.py --force               # 忽略已有文件，重新下载/转换
  python setup.py --mirror hf           # 模型改从 huggingface.co 下载（默认 hf-mirror.com）
  python setup.py --cn                  # pip 改用清华镜像（国内加速）
  python setup.py --system              # 不建虚拟环境，直接装入当前 Python
  python setup.py --keep-intermediates  # 保留 ArtiMuse 转换中间产物（F16 与 safetensors）

量化档位：
  python setup.py --quant Q4_K_M        # 主模型换档位（默认 Q8_0；显存不够时选低档位）
                                            # 可选：BF16 Q8_0 Q6_K Q5_K_M Q4_K_M Q4_0
                                            #       IQ4_XS Q3_K_M IQ3_M IQ2_M IQ2_XXS
  档位不设死：仓库提供完整量化梯，安装时选或事后把任意档位文件按命名放入 models/，
  运行时自动识别（多档位并存取质量最高）；详见 docs/models.md「量化档位」。

模型说明（体积按 Q8_0 计）：
  unipercept_std  默认模型（UniPercept）。社区已转好 GGUF，直接下载，约 8.2 GB
  unipercept      UniPercept-abliterated。社区已转好 GGUF，直接下载，约 8.2 GB
  artimuse        细粒度美学模型。上游只有 safetensors，需下载 15.2 GB 后本地转换量化；
                  额外安装 CPU 版 PyTorch（约 2 GB），全程约 30-60 分钟
                  （ArtiMuse 没有预置量化档位可选，选它时跳过档位选择）
  all             以上全部（最终约 24.5 GB；ArtiMuse 转换期间磁盘峰值约 42 GB）

安装流程：先选择模型，再选择量化档位（仅 UniPercept 系需要；只选 ArtiMuse 时跳过）。

下载均支持断点续传与自动重试，中断后重新运行本脚本即可继续。

已通过其他途径拿到模型文件：按 docs/models.md 的位置说明直接放入 models/，再运行本脚本，
已存在的文件会自动跳过——本地文件齐全时完全不访问模型站（离线也能继续装）。

Windows 下也可以直接在资源管理器里双击本文件运行（需已安装 Python 3.9+ 并勾选
Add to PATH）；双击运行时结束后会等待按键再关闭窗口，不会一闪就没。
在终端（PowerShell / cmd）里运行输出更易复制，效果相同。
"""
import argparse
import glob
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.error
import urllib.request

# 本文件位于项目根目录，公共模块在 scripts/ 下：把 scripts/ 加进导入路径
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "scripts"))
from common import (LLAMA_DIR, MODEL_STEMS, MODELS_DIR, ROOT,  # noqa: E402
                    SCRIPTS_DIR, find_quant_variant, model_dir)

IS_WIN = os.name == "nt"
HF = "https://huggingface.co"
HF_MIRROR = "https://hf-mirror.com"
LLAMA_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
UA = {"User-Agent": "aesthetics-setup/1.0"}
GB = 1 << 30
PIP_INDEX_TUNA = "https://pypi.tuna.tsinghua.edu.cn/simple"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

# 量化档位参考（体积为 8B 主模型近似值，来自社区仓库实测；显存建议 = 体积 + 2 GB 余量）。
# 跑不动高档位时选低档位即可，运行时按 models/ 下实际文件解析，不限定单一档位。
QUANT_MAIN_GB = {"BF16": 14.2, "Q8_0": 7.5, "Q6_K": 5.8, "Q5_K_M": 5.1, "Q4_K_M": 4.4,
                 "Q4_0": 4.1, "IQ4_XS": 3.9, "Q3_K_M": 3.5, "IQ3_M": 3.3,
                 "IQ2_M": 2.6, "IQ2_XXS": 2.1}
QUANT_GUIDE = [
    ("BF16", "14.2 GB", "无损精度，需 16 GB 以上显存"),
    ("Q8_0", " 7.5 GB", "默认档位，质量/体积均衡，约需 10 GB 显存"),
    ("Q6_K", " 5.8 GB", "接近 Q8_0 质量"),
    ("Q5_K_M", " 5.1 GB", "画质轻微下降"),
    ("Q4_K_M", " 4.4 GB", "推荐低配档位，8 GB 显存可跑"),
    ("Q4_0", " 4.1 GB", "略逊于 Q4_K_M"),
    ("IQ4_XS", " 3.9 GB", "性价比档位"),
    ("Q3_K_M", " 3.5 GB", "明显有损，仅建议显存紧张时使用"),
    ("IQ3_M", " 3.3 GB", "明显有损"),
    ("IQ2_M", " 2.6 GB", "大幅有损，仅供体验"),
    ("IQ2_XXS", " 2.1 GB", "极限压缩，质量显著下降"),
]


def normalize_quant(q):
    """校验并归一化档位名（大写）。未知档位给出警告但仍放行——
    仓库以后可能新增档位，运行时同样能解析。"""
    q = (q or "").strip().upper()
    if not q or not re.match(r"^[A-Z0-9_]+$", q):
        raise SetupError(
            f"无效的量化档位：{q!r}\n"
            f"  可选档位：{', '.join(QUANT_MAIN_GB)}（也可填仓库里存在的其他档位名）")
    if q not in QUANT_MAIN_GB:
        warn(f"档位 {q} 不在已知列表中，将按该名字下载；"
             f"若仓库没有此档位会报错，可选：{', '.join(QUANT_MAIN_GB)}")
    return q


def gguf_files_for(mid, quant):
    """生成「仓库内文件名 → 本地路径」映射：主模型按所选档位，mmproj 固定首选词干。"""
    stem = MODEL_STEMS[mid]["stem"]
    main = "%s-%s.gguf" % (stem, quant)
    files = {main: os.path.join(model_dir(mid), main)}
    mp = "mmproj-%s-BF16.gguf" % MODEL_STEMS[mid]["mmproj_stems"][0]
    files[mp] = os.path.join(model_dir(mid), mp)
    return files


def setup_files_for(mid, quant, explicit):
    """本次实际要下载的文件映射。

    显式指定 --quant 时按用户意图下载所选档位；用默认档位时，若本机已通过
    其他途径放置了别的档位，则尊重本地文件、只补 mmproj，避免重复下载大文件
    （低配用户放个 Q4_K_M 就够了，不该被强制再拉 7.5 GB 的 Q8_0）。
    """
    files = gguf_files_for(mid, quant)
    if not explicit:
        have_q, have_path = find_quant_variant(model_dir(mid), MODEL_STEMS[mid]["stem"])
        if have_q and have_q.upper() != quant.upper():
            info(f"本机已有 {os.path.basename(have_path)}（{have_q} 档），"
                 f"跳过 {quant} 下载；运行时将自动使用 {have_q}"
                 f"（想改用 {quant} 可加 --quant {quant}）")
            files = {k: v for k, v in files.items()
                     if os.path.basename(k).startswith("mmproj")}
    return files


# 模型 id → 安装信息。files 在安装时按档位动态生成（gguf_files_for）；
# convert=True 走本地转换路线（ArtiMuse）。
MODEL_CATALOG = {
    "unipercept": {
        "title": "UniPercept-abliterated",
        "repo": "AX1Y2JP/UniPercept-abliterated-GGUF",
        "convert": False,
    },
    "unipercept_std": {
        "title": "UniPercept（默认模型）",
        "repo": "AX1Y2JP/UniPercept-GGUF",
        "convert": False,
    },
    "artimuse": {
        "title": "ArtiMuse（细粒度美学）",
        "repo": "Thunderbolt215215/ArtiMuse",
        "convert": True,  # 转换路线：先整仓下载 safetensors，再本地转换
        "outputs": {
            "mmproj": os.path.join(MODELS_DIR, "artimuse", "mmproj-ArtiMuse-F16.gguf"),
        },
        "f16": os.path.join(MODELS_DIR, "artimuse", "ArtiMuse-F16.gguf"),
        "hf_dir": os.path.join(MODELS_DIR, "artimuse", "hf"),
    },
}

IMPORT_NAMES = {"Pillow": "PIL", "torch": "torch", "safetensors": "safetensors",
                "numpy": "numpy", "gguf": "gguf"}


class SetupError(Exception):
    """安装过程中的致命错误（已附带可操作提示）。"""


# ---------------------------------------------------------------- 输出辅助

def step(msg):
    print("\n" + "=" * 62)
    print("==> " + msg)
    print("=" * 62)


def info(msg):
    print("    " + msg)


def warn(msg):
    print("[WARN] " + msg)


def hsize(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def fmt_dur(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec} 秒"
    return f"{sec // 60} 分 {sec % 60} 秒"


def pause_before_exit():
    """双击运行（Windows 控制台）时停留一下，避免窗口一闪即关看不清结果。

    判定条件：Windows 且 stdin 是控制台（isatty）。从终端里运行、或 stdin
    被重定向/管道时不会停留，不影响脚本化使用。
    """
    if IS_WIN and sys.stdin.isatty():
        try:
            input("\n按回车键退出 ...")
        except (EOFError, KeyboardInterrupt, OSError):
            pass


# ---------------------------------------------------------------- 网络基础

def _open(url, headers=None, timeout=120):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def _get_json(url, timeout=60):
    with _open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(8 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _progress(tag, done, total, speed):
    if total:
        pct = done * 100.0 / total
        line = (f"\r  {tag}: {done / GB:.2f}/{total / GB:.2f} GB "
                f"({pct:.1f}%) {speed / (1 << 20):.1f} MB/s")
    else:
        line = f"\r  {tag}: {done / GB:.2f} GB {speed / (1 << 20):.1f} MB/s"
    sys.stdout.write(line[:112])
    sys.stdout.flush()


# ---------------------------------------------------------------- 下载

def download_file(url, dest, expect_size=None, expect_sha256=None,
                  label=None, retries=6):
    """带断点续传 / 自动重试 / 进度显示 / 大小与 SHA256 校验的下载。"""
    tag = label or os.path.basename(dest)
    tmp = dest + ".part"
    attempt = 0
    while True:
        attempt += 1
        pos = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        headers = dict(UA)
        if pos:
            headers["Range"] = f"bytes={pos}-"
        try:
            with _open(url, headers=headers) as r:
                if pos and r.getcode() != 206:
                    pos = 0  # 服务端忽略了 Range，只能完整重下
                total = expect_size or int(r.headers.get("Content-Length") or 0) or None
                done = pos
                t0 = time.time()
                shown = 0.0
                mode = "ab" if pos else "wb"
                os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
                with open(tmp, mode) as f:
                    while True:
                        chunk = r.read(8 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if now - shown >= 1.0:
                            shown = now
                            _progress(tag, done, total, (done - pos) / max(now - t0, 1e-6))
                sys.stdout.write("\n")
                sys.stdout.flush()
                if total and done != total:
                    raise IOError(f"下载不完整：{done} / {total} 字节")
                break
        except urllib.error.HTTPError as e:
            if e.code == 416 and os.path.exists(tmp):
                info(f"{tag}: 本地临时文件已超过远程大小，重新下载")
                os.remove(tmp)
                continue
            if e.code in (403, 404, 401):
                raise SetupError(
                    f"下载 {tag} 失败：HTTP {e.code} {e.reason}\n  URL: {url}\n"
                    f"  文件不存在或没有访问权限。")
            if attempt >= retries:
                raise SetupError(
                    f"下载 {tag} 失败（重试 {retries} 次后仍不成功）：HTTP {e.code}\n"
                    f"  URL: {url}\n  可稍后重新运行本脚本，支持断点续传。")
            wait = min(5 * (2 ** (attempt - 1)), 90)
            print(f"\n[WARN] {tag} 第 {attempt} 次尝试失败：HTTP {e.code}；"
                  f"{wait}s 后重试（断点续传）")
            time.sleep(wait)
        except (urllib.error.URLError, IOError, OSError, TimeoutError) as e:
            if attempt >= retries:
                raise SetupError(
                    f"下载 {tag} 失败（重试 {retries} 次后仍不成功）：{e}\n  URL: {url}\n"
                    f"  可稍后重新运行本脚本，支持断点续传。")
            wait = min(5 * (2 ** (attempt - 1)), 90)
            print(f"\n[WARN] {tag} 第 {attempt} 次尝试失败：{e}；{wait}s 后重试（断点续传）")
            time.sleep(wait)

    if expect_sha256:
        info(f"校验 {tag} 的 SHA256 ...")
        got = sha256_file(tmp)
        if got != expect_sha256:
            os.remove(tmp)
            raise SetupError(
                f"{tag} 校验失败：SHA256 不匹配（下载损坏，或仓库文件已更新）。\n"
                f"  期望 {expect_sha256}\n  实际 {got}\n"
                f"  已删除损坏的临时文件，请重新运行本脚本。")
    os.replace(tmp, dest)
    info(f"完成：{tag}（{hsize(os.path.getsize(dest))}）")


# ---------------------------------------------------------------- HuggingFace

def hf_base(mirror):
    return HF_MIRROR if mirror else HF


def hf_tree(repo, mirror, timeout=60):
    """仓库文件清单：{路径: {size, sha256}}。sha256 取自 LFS 指针，可用于完整性校验。"""
    url = f"{hf_base(mirror)}/api/models/{repo}/tree/main?recursive=true"
    try:
        data = _get_json(url, timeout=timeout)
    except Exception as e:
        raise SetupError(
            f"查询仓库文件清单失败：{repo}\n  {e}\n"
            f"  请检查网络；大陆用户默认走 hf-mirror.com，可加/去 --mirror hf 切换。\n"
            f"  若已通过其他途径取得模型文件，可按 docs/models.md 的位置说明直接放入\n"
            f"  models/ 后重跑本脚本——已存在的文件会自动跳过，无需联网。")
    out = {}
    for e in data:
        if e.get("type") == "file" and e.get("path"):
            lfs = e.get("lfs") or {}
            out[e["path"]] = {"size": e.get("size") or 0,
                              "sha256": lfs.get("oid")}
    if not out:
        raise SetupError(f"仓库 {repo} 的文件清单为空，请检查仓库 id 是否正确。")
    return out


def hf_tree_or_none(repo, mirror, timeout=20):
    """hf_tree 的宽容版：查询失败返回 None，供「本地已放好文件」的离线场景使用。"""
    try:
        return hf_tree(repo, mirror, timeout=timeout)
    except SetupError:
        return None


def hf_download_files(repo, files, mirror, force):
    """files: {仓库内文件名: 本地路径}。

    已存在且大小一致的文件默认跳过。访问不了模型站时，只要本地文件齐全，
    也能完全不联网完成本步骤（离线放置场景）。
    """
    base = hf_base(mirror)
    if not files:
        return

    # 1) 本地扫描：分开「已放好」与「待下载」两组
    present, need = {}, {}
    for remote, local in files.items():
        if os.path.exists(local) and not force:
            present[remote] = local
        else:
            need[remote] = local

    # 2) 有待下载的才必须查清单；没有的话尝试查一次做核对，查不到就按本地文件继续
    if need:
        tree = hf_tree(repo, mirror)
    else:
        tree = hf_tree_or_none(repo, mirror)
        if tree is None:
            warn("无法连接模型站核对文件清单，将按本地已有文件继续（未经联网校验）")

    # 3) 逐个核对已存在的文件
    for remote, local in present.items():
        name = os.path.basename(local)
        size = os.path.getsize(local)
        expect = (tree or {}).get(remote, {}).get("size") or 0
        if expect and size != expect:
            warn(f"已存在的 {name} 大小与仓库不符"
                 f"（本地 {hsize(size)} / 仓库 {hsize(expect)}），将重新下载")
            need[remote] = local
            continue
        if size <= 0:
            warn(f"已存在的 {name} 大小为 0，将重新下载")
            need[remote] = local
            continue
        info(f"已存在，跳过：{name}（{hsize(size)}）")
        part = local + ".part"
        if os.path.exists(part):
            os.remove(part)  # 最终文件已就位，残留的续传临时文件只是占地方

    if not need:
        return
    if tree is None:
        names = "、".join(os.path.basename(p) for p in need.values())
        raise SetupError(
            f"无法连接模型站，且本地文件不完整（{names} 缺失或为空）。\n"
            f"  请补齐后重跑，或恢复网络后重跑；已下载部分支持断点续传。")

    # 4) 下载
    for remote, local in need.items():
        if remote not in tree:
            raise SetupError(
                f"仓库 {repo} 中找不到 {remote}\n"
                f"  仓库结构可能已变化，请对照 {base}/{repo} 检查。")
        meta = tree[remote]
        name = os.path.basename(local)
        info(f"下载 {name}（约 {hsize(meta['size'])}）")
        download_file(f"{base}/resolve/main/{remote}", local,
                      expect_size=meta["size"] or None,
                      expect_sha256=meta["sha256"], label=name)


# ---------------------------------------------------------------- 虚拟环境 / pip

def venv_python(venv_dir):
    sub = "Scripts" if IS_WIN else "bin"
    exe = "python.exe" if IS_WIN else "python"
    return os.path.join(venv_dir, sub, exe)


def ensure_venv(venv_dir):
    py = venv_python(venv_dir)
    if os.path.exists(py):
        info(f"虚拟环境已存在：{venv_dir}")
        return py
    info(f"创建虚拟环境：{venv_dir}")
    r = subprocess.run([sys.executable, "-m", "venv", venv_dir])
    if r.returncode != 0 or not os.path.exists(py):
        raise SetupError(
            "创建虚拟环境失败。请确认：\n"
            "  1. 已安装 Python 3.9+ 并勾选 Add to PATH（Windows）\n"
            "  2. Debian/Ubuntu 可能需要先 sudo apt install python3-venv\n"
            "  3. 或加 --system 参数直接装入当前 Python")
    # 升级 pip：venv 自带的 pip 版本可能过旧，装不上部分新 wheel
    subprocess.run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
                   capture_output=True)
    return py


def pip_install(py, packages, index=None):
    cmd = [py, "-m", "pip", "install", "--progress-bar", "off"]
    if index:
        cmd += ["--index-url", index]
    cmd += list(packages)
    src = f"（镜像 {index}）" if index else ""
    info(f"pip install {' '.join(packages)} {src}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise SetupError(
            f"依赖安装失败：{' '.join(packages)}\n"
            f"  大陆网络可加 --cn 换清华源重试；也可能是磁盘不足或权限问题。")


def ensure_packages(py, packages, index=None, torch_cpu=False):
    """按需安装：已可 import 的跳过。torch_cpu=True 时 torch 走 CPU 专用源。"""
    need = []
    for p in packages:
        mod = IMPORT_NAMES.get(p, p)
        r = subprocess.run([py, "-c", f"import {mod}"],
                           capture_output=True)
        if r.returncode != 0:
            need.append(p)
    if torch_cpu and "torch" in need:
        info("安装 CPU 版 PyTorch（约 200 MB，safetensors → GGUF 转换必需；"
             "首次安装需要几分钟，请耐心等待）...")
        pip_install(py, ["torch"], index=TORCH_CPU_INDEX)
        need.remove("torch")
    if need:
        pip_install(py, need, index=index)
    if not need:
        info("依赖已齐备，无需安装")


# ---------------------------------------------------------------- llama.cpp

def latest_nightly_tag():
    """返回最新 nightly 的 tag（形如 b11819）；失败返回 None。"""
    try:
        releases = _get_json(f"{LLAMA_API}?per_page=30")
        for rel in releases:
            tag = rel.get("tag_name", "")
            if tag.startswith("b") and tag[1:].isdigit():
                return tag
    except Exception as e:
        warn(f"查询 llama.cpp nightly 版本号失败（不影响二进制下载）：{e}")
    return None


def install_llama_cpp(py, force):
    exe = "llama-server.exe" if IS_WIN else "llama-server"
    target = os.path.join(LLAMA_DIR, exe)
    if os.path.exists(target) and not force:
        info(f"llama.cpp 已就绪：{target}")
        return
    info("下载 llama.cpp 预编译二进制（Vulkan 后端）...")
    script = os.path.join(SCRIPTS_DIR, "fetch_llama_cpp.py")
    r = subprocess.run([py, script])
    if r.returncode != 0 or not os.path.exists(target):
        raise SetupError(
            "llama.cpp 下载失败。\n"
            "  可手动到 https://github.com/ggml-org/llama.cpp/releases 下载 nightly 的\n"
            f"  win-vulkan-x64 包，解压到 {LLAMA_DIR}\\（见 docs/llama-cpp.md）。")


def fetch_llama_source(tag, dest, force):
    """从 llama.cpp 源码包中抽取转换脚本：convert_hf_to_gguf.py + conversion/ + gguf-py/。

    与二进制同一个 nightly tag，保证转换脚本与推理引擎版本匹配。
    """
    marker = os.path.join(dest, "convert_hf_to_gguf.py")
    if os.path.exists(marker) and not force:
        info(f"转换脚本已存在：{dest}")
        return dest
    if tag:
        urls = [f"https://codeload.github.com/ggml-org/llama.cpp/tar.gz/refs/tags/{tag}",
                f"https://github.com/ggml-org/llama.cpp/archive/refs/tags/{tag}.tar.gz"]
    else:
        urls = ["https://codeload.github.com/ggml-org/llama.cpp/tar.gz/refs/heads/master",
                "https://github.com/ggml-org/llama.cpp/archive/refs/heads/master.tar.gz"]
    tmp = tempfile.mkdtemp(prefix="llama_src_")
    try:
        tpath = os.path.join(tmp, "src.tar.gz")
        last_err = None
        for url in urls:
            try:
                download_file(url, tpath,
                              label=f"llama.cpp 源码（转换脚本，{tag or 'master'}）")
                break
            except SetupError as e:
                last_err = e
                if os.path.exists(tpath):
                    os.remove(tpath)
        else:
            raise SetupError(
                f"下载 llama.cpp 源码包失败：{last_err}\n"
                f"  可手动 git clone https://github.com/ggml-org/llama.cpp 后参考 "
                f"docs/models.md 的转换步骤。")
        info("解压转换脚本 ...")
        os.makedirs(dest, exist_ok=True)
        picked = 0
        with tarfile.open(tpath) as tf:
            members = []
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                name = m.name[2:] if m.name.startswith("./") else m.name
                rel = name.split("/", 1)[1] if "/" in name else ""
                if (rel == "convert_hf_to_gguf.py"
                        or rel.startswith("conversion/")
                        or rel.startswith("gguf-py/gguf/")):
                    members.append((m, rel))
            for m, rel in members:
                target = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tf.extractfile(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                picked += 1
        if not os.path.exists(marker):
            raise SetupError(
                "解压后未找到 convert_hf_to_gguf.py，llama.cpp 仓库结构可能已变化。\n"
                "  可手动 git clone https://github.com/ggml-org/llama.cpp 后参考 "
                "docs/models.md 的转换步骤。")
        info(f"转换脚本就绪：{dest}（{picked} 个文件）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest


# ---------------------------------------------------------------- ArtiMuse 转换

def run_convert(py, conv_dir, hf_dir, outfile, mmproj=False):
    script = os.path.join(conv_dir, "convert_hf_to_gguf.py")
    cmd = [py, script, hf_dir, "--outfile", outfile, "--outtype", "f16"]
    if mmproj:
        cmd.append("--mmproj")
    what = "视觉投影（mmproj）" if mmproj else "主模型"
    info(f"转换{what}：{os.path.basename(outfile)}")
    info("（8B 模型转换约 10-25 分钟，期间请保持通电与空闲内存）")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=conv_dir)
    if r.returncode != 0:
        raise SetupError(
            f"转换{what}失败（退出码 {r.returncode}），详见上方输出。\n"
            f"  常见原因：内存不足、safetensors 下载不完整、磁盘空间不够。")
    info(f"转换完成，耗时 {fmt_dur(time.time() - t0)}")


def install_artimuse(py, mirror, force, keep_intermediates, pip_index, llama_tag,
                     quant="Q8_0"):
    cat = MODEL_CATALOG["artimuse"]
    out_main = os.path.join(MODELS_DIR, "artimuse", "ArtiMuse-%s.gguf" % quant)
    out_mmproj = cat["outputs"]["mmproj"]
    if os.path.exists(out_main) and os.path.exists(out_mmproj) and not force:
        info(f"ArtiMuse {quant} 已存在，跳过（--force 可重建）")
        return
    # 本机已放置其他档位且 mmproj 就绪时尊重本地文件，避免再花 30-60 分钟重转
    have_q, have_path = find_quant_variant(model_dir("artimuse"), "ArtiMuse")
    if (not force and have_q and have_q.upper() != quant.upper()
            and os.path.exists(out_mmproj)):
        info(f"本机已有 {os.path.basename(have_path)}（{have_q} 档）且 mmproj 就绪，"
             f"跳过 {quant} 转换量化；运行时将自动使用 {have_q}")
        return

    # 1) 转换工具链（CPU 版 PyTorch + safetensors/numpy/gguf）
    ensure_packages(py, ["torch", "safetensors", "numpy", "gguf"],
                    index=pip_index, torch_cpu=True)

    # 2) 上游 safetensors：能联网就对照仓库清单下载；连不上且本地已放好，就离线继续
    hf_dir = cat["hf_dir"]
    tree = hf_tree_or_none(cat["repo"], mirror)
    files = {}
    if tree is not None:
        skip = {".gitattributes"}
        files = {p: os.path.join(hf_dir, p) for p in tree if p not in skip}
        total = sum(tree[p]["size"] for p in files)
        if total > 25 * GB:
            raise SetupError(
                f"仓库 {cat['repo']} 待下载内容达 {hsize(total)}，超出预期（约 15.2 GB）。\n"
                f"  请人工核对 {hf_base(mirror)}/{cat['repo']} 后重试。")
        info(f"下载上游 safetensors：{cat['repo']}（{len(files)} 个文件，共 {hsize(total)}）")
    else:
        shards = sorted(glob.glob(os.path.join(hf_dir, "*.safetensors")))
        if not shards:
            raise SetupError(
                f"无法连接模型站获取 {cat['repo']} 的文件清单，本地也没有已放置的 safetensors。\n"
                f"  请先通过网络其他途径取得上游权重（4 个 safetensors 分片约 15.2 GB，\n"
                f"  外加 config/tokenizer 等小文件），放入：{hf_dir}\n"
                f"  （目录结构见 {hf_base(mirror)}/{cat['repo']}），再重跑本脚本。")
        total = sum(os.path.getsize(p) for p in shards)
        warn(f"无法连接模型站，按本地已放置的文件继续：{len(shards)} 个 safetensors 分片，"
             f"共 {hsize(total)}")
        warn("请确认分片齐全（上游约 15.2 GB）且 config/tokenizer 等小文件都在；"
             "不齐的话后续转换会失败")
    hf_download_files(cat["repo"], files, mirror, force)

    # 3) 抽取与二进制同版本的转换脚本
    conv_dir = fetch_llama_source(llama_tag, os.path.join(ROOT, "tools", "convert"), force)

    # 4) 主模型转 F16
    f16 = cat["f16"]
    if os.path.exists(f16) and not force:
        info(f"F16 中间产物已存在，跳过转换：{os.path.basename(f16)}")
    else:
        run_convert(py, conv_dir, hf_dir, f16)

    # 5) 视觉投影转 F16（转换器可能自动加 mmproj- 前缀，用临时名再归位）
    tmp_mmproj = os.path.join(MODELS_DIR, "artimuse", "ArtiMuse-mmproj-tmp.gguf")
    for stale in (tmp_mmproj, os.path.join(MODELS_DIR, "artimuse",
                                           "mmproj-ArtiMuse-mmproj-tmp.gguf")):
        if os.path.exists(stale):
            os.remove(stale)
    run_convert(py, conv_dir, hf_dir, tmp_mmproj, mmproj=True)
    produced = None
    for cand in (tmp_mmproj,
                 os.path.join(MODELS_DIR, "artimuse", "mmproj-ArtiMuse-mmproj-tmp.gguf")):
        if os.path.exists(cand):
            produced = cand
            break
    if produced is None:  # 兜底：目录下最新的 mmproj*.gguf
        cands = sorted(glob.glob(os.path.join(MODELS_DIR, "artimuse", "mmproj*.gguf")),
                       key=os.path.getmtime)
        produced = cands[-1] if cands else None
    if produced is None:
        raise SetupError("mmproj 转换后未找到输出文件，请检查上方转换日志。")
    if os.path.abspath(produced) != os.path.abspath(out_mmproj):
        if os.path.exists(out_mmproj):
            os.remove(out_mmproj)
        os.replace(produced, out_mmproj)
    info(f"视觉投影就绪：{os.path.basename(out_mmproj)}")

    # 6) 量化主模型到所选档位
    if os.path.exists(out_main) and not force:
        info(f"{quant} 已存在，跳过量化：{os.path.basename(out_main)}")
    else:
        quant_exe = os.path.join(LLAMA_DIR, "llama-quantize.exe" if IS_WIN else "llama-quantize")
        if not os.path.exists(quant_exe):
            raise SetupError(
                f"未找到 {quant_exe}\n  量化需要 llama.cpp 工具，请先完成 llama.cpp 安装"
                f"（不要加 --skip-llama），或见 docs/llama-cpp.md 手动放置。")
        info(f"量化主模型到 {quant}（约 5-10 分钟）：{os.path.basename(out_main)}")
        t0 = time.time()
        r = subprocess.run([quant_exe, f16, out_main, quant])
        if r.returncode != 0 or not os.path.exists(out_main):
            raise SetupError("量化失败，详见上方输出。")
        info(f"量化完成，耗时 {fmt_dur(time.time() - t0)}")

    # 7) 清理中间产物
    if not keep_intermediates:
        freed = 0
        for path in (f16, hf_dir):
            if os.path.exists(path):
                if os.path.isdir(path):
                    freed += sum(os.path.getsize(os.path.join(b, f))
                                 for b, _d, fs in os.walk(path) for f in fs)
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    freed += os.path.getsize(path)
                    os.remove(path)
        info(f"已清理中间产物，释放 {hsize(freed)}（--keep-intermediates 可保留）")
    else:
        info(f"保留中间产物：{f16} 与 {hf_dir}")


# ---------------------------------------------------------------- 自检

def self_check(py, selected):
    print("\n" + "=" * 62)
    print("==> 自检")
    print("=" * 62)
    ok = True

    # 模型文件与 import 链
    script = os.path.join(SCRIPTS_DIR, "run_analysis.py")
    try:
        r = subprocess.run([py, script, "--list-models"],
                           capture_output=True, encoding="utf-8", errors="replace")
        data = json.loads(r.stdout)
    except Exception as e:
        warn(f"--list-models 自检失败：{e}")
        ok = False
        data = {"models": []}
    for m in data.get("models", []):
        mark = "OK" if m["available"] else "--"
        if m["id"] in selected:
            mark = "OK" if m["available"] else "缺失"
        quant = ("（%s）" % m["quant"]) if m.get("quant") else ""
        info(f"[{mark}] {m['id']:<16} {m['name']}{quant}"
             + ("" if m["id"] in selected else "（未安装，可选）"))
    for mid in selected:
        m = next((x for x in data.get("models", []) if x["id"] == mid), None)
        if m is None or not m["available"]:
            warn(f"模型 {mid} 未就绪")
            ok = False

    # llama-server
    exe = os.path.join(LLAMA_DIR, "llama-server.exe" if IS_WIN else "llama-server")
    if os.path.exists(exe):
        try:
            r = subprocess.run([exe, "--version"], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=60)
            ver = (r.stdout or r.stderr or "").strip().splitlines()
            info(f"llama-server: {ver[0] if ver else '已安装'}")
        except Exception as e:
            warn(f"llama-server --version 执行失败：{e}")
    else:
        warn(f"未找到 {exe}（推理引擎未安装）")
        ok = False

    return ok


# ---------------------------------------------------------------- 交互

def ask(prompt, default=""):
    try:
        s = input(prompt).strip()
    except EOFError:
        return default
    return s or default


def menu_quant():
    print("\n选择主模型量化档位（显存不足或磁盘紧张时选低档位；"
          "安装后也可随时替换 models/ 里的文件，程序自动识别）：")
    for i, (q, size, note) in enumerate(QUANT_GUIDE, 1):
        print("  %2d) %-8s %s —— %s" % (i, q, size, note))
    s = ask("> ", "2").strip().upper()
    if s.isdigit():
        idx = int(s)
        if 1 <= idx <= len(QUANT_GUIDE):
            return QUANT_GUIDE[idx - 1][0]
        raise SetupError(f"无效的档位序号：{s}（可选 1-{len(QUANT_GUIDE)}）")
    return normalize_quant(s)


def menu_models():
    print("\n选择要安装的模型（可多选，逗号分隔；直接回车 = 仅默认模型）：")
    print("  1) UniPercept             —— 默认模型，直接下载 GGUF，约 7.5 GB（Q8_0 档）")
    print("  2) UniPercept-abliterated —— 直接下载 GGUF，约 7.5 GB（Q8_0 档）")
    print("  3) ArtiMuse              —— 下载 15.2 GB safetensors 后本地转换量化，")
    print("                              另需 CPU 版 PyTorch 约 2 GB，全程约 30-60 分钟")
    print("  4) 全部三个              —— 最终约 24.5 GB（均按 Q8_0 计，换低档位同比缩小）")
    s = ask("> ", "1")
    picks = set()
    for tok in s.replace("，", ",").split(","):
        tok = tok.strip()
        if tok in ("1", ""):
            picks.add("unipercept_std")
        elif tok == "2":
            picks.add("unipercept")
        elif tok == "3":
            picks.add("artimuse")
        elif tok == "4":
            picks = set(MODEL_CATALOG)
        elif tok in MODEL_CATALOG:
            picks.add(tok)
    return picks or {"unipercept_std"}


def resolve_quant(selected, explicit, yes):
    """确定量化档位，返回 (档位, 是否向用户展示档位信息)。

    先选模型后再选档位：显式 --quant 优先；只选 ArtiMuse 时它没有预置量化
    档位，跳过询问（固定用 Q8_0 做本地转换量化）；否则走交互菜单，
    --yes 无交互时取默认 Q8_0。
    """
    if explicit is not None:
        return normalize_quant(explicit), True
    if all(mid == "artimuse" for mid in selected):
        return "Q8_0", False
    if yes:
        return "Q8_0", True
    return menu_quant(), True


def parse_models(s):
    s = (s or "").strip().lower()
    if not s:
        return {"unipercept_std"}
    if s == "all":
        return set(MODEL_CATALOG)
    if s == "default":
        return {"unipercept_std"}
    picks = set()
    for tok in s.replace("，", ",").split(","):
        tok = tok.strip()
        if tok not in MODEL_CATALOG:
            raise SetupError(
                f"未知模型 id：{tok}\n  可选：{', '.join(MODEL_CATALOG)} 或 all")
        picks.add(tok)
    return picks


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(
        description="一键安装：环境 + llama.cpp + 模型 + 自检")
    ap.add_argument("--models", default=None,
                    help="安装哪些模型：unipercept / unipercept_std / artimuse / all，"
                         "逗号分隔；默认仅 unipercept")
    ap.add_argument("--quant", default=None,
                    help="主模型量化档位（默认 Q8_0）。显存跑不动高档位时选低档位，"
                         "如 Q4_K_M / IQ4_XS；可选："
                         + ", ".join(QUANT_MAIN_GB))
    ap.add_argument("--yes", "-y", action="store_true", help="无交互，全部自动确认")
    ap.add_argument("--skip-llama", action="store_true", help="跳过 llama.cpp 下载")
    ap.add_argument("--force", action="store_true", help="忽略已有文件，重新下载/转换")
    ap.add_argument("--mirror", choices=["hf-mirror", "hf"], default="hf-mirror",
                    help="模型下载源，默认 hf-mirror（大陆可用）")
    ap.add_argument("--cn", action="store_true", help="pip 使用清华大学镜像")
    ap.add_argument("--system", action="store_true",
                    help="不创建虚拟环境，直接装入当前 Python")
    ap.add_argument("--keep-intermediates", action="store_true",
                    help="保留 ArtiMuse 转换中间产物（F16 与 safetensors）")
    args = ap.parse_args()

    if sys.version_info < (3, 9):
        raise SetupError(f"需要 Python 3.9+，当前 {platform.python_version()}")

    mirror = args.mirror == "hf-mirror"
    print("=" * 62)
    print(" 一键安装")
    print("=" * 62)
    info(f"Python      : {platform.python_version()}（{sys.executable}）")
    info(f"系统        : {platform.system()} {platform.release()} {platform.machine()}")
    info(f"项目目录    : {ROOT}")
    info(f"模型下载源  : {hf_base(mirror)}")

    # ---- 选择模型（先选模型，再选量化档位）----
    if args.models:
        selected = parse_models(args.models)
    elif args.yes:
        selected = {"unipercept_std"}
    else:
        selected = menu_models()

    # ---- 量化档位（仅 UniPercept 系需要；ArtiMuse 无预置量化档位，跳过选择）----
    quant, show_quant = resolve_quant(selected, args.quant, args.yes)
    if show_quant:
        info(f"量化档位    : {quant}（主模型约 "
             f"{QUANT_MAIN_GB.get(quant, 7.5):.1f} GB，显存建议 "
             f"{QUANT_MAIN_GB.get(quant, 7.5) + 2:.0f} GB 以上）")
    else:
        info("量化档位    : 跳过（ArtiMuse 无预置量化档位，转换时按 Q8_0 量化）")

    # ---- pip 源 ----
    pip_index = PIP_INDEX_TUNA if args.cn else None
    if not args.yes and not args.system:
        s = ask("\npip 下载源：1) 官方 PyPI   2) 清华大学镜像（国内更快）[1/2] > ", "1")
        pip_index = PIP_INDEX_TUNA if s == "2" else None

    # ---- 磁盘预算 ----
    main_gb = QUANT_MAIN_GB.get(quant, 7.5)
    need_gb = sum(main_gb + 0.7 for _ in selected)  # 主模型 + mmproj
    if "artimuse" in selected:
        need_gb += 31.9  # 转换峰值：safetensors 15.2 + F16 15.2 + 余量
        need_gb += 2.0   # CPU 版 PyTorch 等转换工具链
    if not args.skip_llama:
        need_gb += 0.2
    try:
        free = shutil.disk_usage(ROOT).free
        info(f"磁盘        : 需要约 {need_gb:.1f} GB，可用 {free / GB:.1f} GB")
        if free < need_gb * GB:
            warn(f"可用空间可能不足（需要约 {need_gb:.1f} GB）")
            if not args.yes:
                if ask("仍要继续吗？[y/N] > ", "n").lower() not in ("y", "yes"):
                    raise SetupError("已取消。请清理空间或减少安装的模型后重试。")
    except OSError:
        pass

    # ---- 环境 ----
    if args.system:
        py = sys.executable
        info(f"使用当前 Python：{py}")
    else:
        venv_dir = os.path.join(ROOT, ".venv")
        if not args.yes:
            s = ask("\n依赖安装方式：1) 创建独立虚拟环境 .venv（推荐）"
                    "   2) 直接装入当前 Python [1/2] > ", "1")
            if s == "2":
                py = sys.executable
                info(f"使用当前 Python：{py}")
            else:
                py = ensure_venv(venv_dir)
        else:
            py = ensure_venv(venv_dir)

    # ---- 1. Python 依赖 ----
    step("1/4 安装 Python 依赖")
    pip_install(py, ["-r", os.path.join(ROOT, "requirements.txt")], index=pip_index)

    # ---- 2. llama.cpp ----
    step("2/4 安装推理引擎（llama.cpp）")
    llama_tag = None
    if args.skip_llama:
        info("按参数要求跳过 llama.cpp 下载")
    else:
        install_llama_cpp(py, args.force)
        llama_tag = latest_nightly_tag()
        if llama_tag:
            info(f"当前 nightly：{llama_tag}")

    # ---- 3. 模型 ----
    step("3/4 安装模型")
    for mid in ("unipercept", "unipercept_std", "artimuse"):
        if mid not in selected:
            continue
        cat = MODEL_CATALOG[mid]
        tag = cat["title"] if cat["convert"] else f"{cat['title']}（{quant}）"
        print(f"\n--- {mid}：{tag}---")
        if cat["convert"]:
            install_artimuse(py, mirror, args.force, args.keep_intermediates,
                             pip_index, llama_tag, quant)
        else:
            files = setup_files_for(mid, quant, args.quant is not None)
            hf_download_files(cat["repo"], files, mirror, args.force)

    # ---- 4. 自检 ----
    step("4/4 自检")
    ok = self_check(py, selected)

    # ---- 总结 ----
    print("\n" + "=" * 62)
    if ok:
        print(" 安装完成")
    else:
        print(" 安装结束（存在未就绪项，请查看上方 [缺失]/[WARN]）")
    print("=" * 62)
    info("llama.cpp : " + LLAMA_DIR)
    for mid in ("unipercept", "unipercept_std", "artimuse"):
        if mid in selected:
            cat = MODEL_CATALOG[mid]
            tag = cat["title"] if cat["convert"] else f"{cat['title']}（{quant}）"
            info(f"模型      : {mid} -> {tag}")
    print()
    info("更换量化档位：把 <模型名>-<档位>.gguf 放入 models/ 后重跑本脚本会自动识别；")
    info("也可直接手动放置任意档位文件，程序运行时自动解析（详见 docs/models.md）")
    print()
    if IS_WIN:
        info("启动：双击 run.bat，或 .venv\\Scripts\\python scripts\\launcher.py")
    else:
        info("启动：.venv/bin/python scripts/launcher.py")
    info("然后浏览器打开 http://127.0.0.1:8081")
    pause_before_exit()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    try:
        main()
    except SetupError as e:
        print(f"\n[ERROR] {e}")
        pause_before_exit()
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n已取消。下载支持断点续传，重新运行本脚本即可继续。")
        pause_before_exit()
        sys.exit(130)
    except Exception:
        # 兜底：双击运行时若命中未预期错误，窗口同样会停留，方便看清堆栈
        traceback.print_exc()
        print("\n[ERROR] 安装过程中出现未预期的错误，请附上以上信息反馈。")
        pause_before_exit()
        sys.exit(1)
