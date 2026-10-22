#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 llama.cpp 预编译二进制（默认 Windows x64 + Vulkan 后端）到 tools/llama-vulkan/。

llama.cpp 的 GitHub Release（nightly，tag 形如 b11815）提供官方预编译包。
本脚本查询最新 nightly，挑选匹配当前平台的 zip，下载并解压到 tools/llama-vulkan/。

用法：
  python scripts/fetch_llama_cpp.py              # 下载最新 nightly 的 Windows Vulkan x64 包
  python scripts/fetch_llama_cpp.py --build 11815
  python scripts/fetch_llama_cpp.py --backend cpu
  python scripts/fetch_llama_cpp.py --list       # 只列出可用资产，不下载

说明：llama.cpp 的 Windows 二进制只在 nightly 构建里发布（正式 Release 不带二进制），
所以这里查的是 prerelease 列表中的最新一个。
"""
import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
UA = {"User-Agent": "aesthetics-fetch-llama-cpp", "Accept": "application/vnd.github+json"}

# 平台 → 资产名中的后缀片段
BACKENDS = ("vulkan", "cpu", "cuda-12.4", "cuda-13.4", "rocm", "sycl", "openvino")


def _platform_slug():
    """返回资产名里的平台段，如 win-vulkan-x64。"""
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64"}.get(machine, machine)
    if sys.platform.startswith("win"):
        return "win", arch
    if sys.platform == "darwin":
        return "macos", arch
    return "ubuntu", arch


def _asset_name(build, os_name, backend, arch):
    return f"llama-b{build}-bin-{os_name}-{backend}-{arch}.zip"


def _get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _latest_build():
    """取最新 nightly 的 build 号（tag 形如 b11815）。"""
    releases = _get_json(f"{API}?per_page=30")
    for rel in releases:
        tag = rel.get("tag_name", "")
        if tag.startswith("b") and tag[1:].isdigit():
            return int(tag[1:]), rel
    raise SystemExit("未在最近的 release 中找到 nightly 构建（tag 形如 bNNNNN）")


def _find_asset(build, rel, os_name, backend, arch):
    want = _asset_name(build, os_name, backend, arch)
    for a in rel.get("assets", []):
        if a.get("name") == want:
            return a
    return None


def _download(url, dest, total=None):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r  下载中 {done / 1048576:.1f} / {total / 1048576:.1f} MB ({pct}%)",
                          end="", flush=True)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"下载失败：HTTP {e.code} {e.reason}\n  URL: {url}")
    except urllib.error.URLError as e:
        raise SystemExit(
            f"下载失败：{e.reason}\n  URL: {url}\n"
            f"  请检查网络 / 代理设置；也可以手动下载后解压到 "
            f"tools/llama-vulkan/（见 docs/llama-cpp.md）。")
    finally:
        if total:
            print()


def _extract_zip(zip_path, dest):
    """解压到 dest，兼容「包内有一层顶层目录」与「文件平铺」两种结构。"""
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in names if "/" in n}
        prefix = (tops.pop() + "/") if len(tops) == 1 else ""
        tmp = tempfile.mkdtemp(prefix="llama_x_")
        try:
            z.extractall(tmp)
            src_root = os.path.join(tmp, prefix) if prefix else tmp
            for name in sorted(names):
                rel_name = name[len(prefix):] if prefix else name
                src = os.path.join(src_root, rel_name)
                if not os.path.exists(src):
                    continue
                target = os.path.join(dest, rel_name)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(src, target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", type=int, default=None,
                    help="指定 llama.cpp nightly build 号（默认最新）")
    ap.add_argument("--backend", default="vulkan", choices=BACKENDS,
                    help="推理后端，默认 vulkan（AMD/Intel/NVIDIA 通用）")
    ap.add_argument("--dest", default=None,
                    help="解压目标目录，默认 tools/llama-vulkan/")
    ap.add_argument("--list", action="store_true", help="只列出可用资产")
    args = ap.parse_args()

    os_name, arch = _platform_slug()
    if args.list:
        build, rel = _latest_build()
        print(f"最新 nightly: b{build}（{rel.get('published_at', '')}）  平台段: {os_name}-{arch}")
        names = [a["name"] for a in rel.get("assets", []) if f"-{os_name}-" in a["name"]]
        for n in sorted(names):
            print("  " + n)
        return

    if args.build:
        build = args.build
        # 指定 build 时需要逐页找对应 release
        rel = None
        page = 1
        while page <= 6 and rel is None:
            for r in _get_json(f"{API}?per_page=30&page={page}"):
                if r.get("tag_name") == f"b{build}":
                    rel = r
                    break
            page += 1
        if rel is None:
            raise SystemExit(f"未找到 tag 为 b{build} 的 release")
    else:
        build, rel = _latest_build()

    asset = _find_asset(build, rel, os_name, args.backend, arch)
    if asset is None:
        want = _asset_name(build, os_name, args.backend, arch)
        raise SystemExit(
            f"b{build} 中没有资产 {want}。\n"
            f"请用 --list 查看该 build 实际提供的 {os_name} 资产，"
            f"或换 --backend / --build 重试。")

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dest = args.dest or os.path.join(root, "tools", "llama-vulkan")
    dest = os.path.abspath(dest)

    print(f"llama.cpp nightly b{build}  资产: {asset['name']} "
          f"({asset['size'] / 1048576:.1f} MB)")
    print(f"目标目录: {dest}")

    tmp = tempfile.mkdtemp(prefix="llama_dl_")
    try:
        zpath = os.path.join(tmp, asset["name"])
        _download(asset["browser_download_url"], zpath, asset.get("size"))
        print("解压中...")
        _extract_zip(zpath, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    exe = os.path.join(dest, "llama-server.exe")
    if os.path.exists(exe):
        print(f"完成。llama-server.exe 已就绪：{exe}")
    else:
        found = []
        for base, _dirs, files in os.walk(dest):
            for fn in files:
                if fn.startswith("llama-server"):
                    found.append(os.path.join(base, fn))
        print("完成，但未在目标目录根下找到 llama-server.exe。")
        for f in found:
            print("  找到: " + f)
        if not found:
            print("  请检查解压结果。")
        sys.exit(1)


if __name__ == "__main__":
    main()
