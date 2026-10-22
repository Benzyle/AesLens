#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卸载辅助：按需清除缓存及日志 / 模型 / 工具，保留程序本身。

打开后先选择要执行的操作（可多选，逗号分隔）：
  1) 清除缓存及日志   运行日志、作业注册表、.part 下载残留、__pycache__
  2) 卸载模型         models/ 下的 GGUF 模型文件（三个全装约 24.5 GB）
  3) 卸载工具         推理引擎 llama.cpp 与 ArtiMuse 转换脚本（tools/）
  4) 以上全部         再加 .venv 虚拟环境与 work/ 作业输出，恢复到手装后的干净状态
                      （三个单项操作都不删 work/ 里的报告，避免误删分析成果）

不会删除程序本身：scripts/ web/ docs/ vendor/ input/、README、LICENSE、
run.bat / stop.bat / setup.py / uninstall.py 等一律保留。
想连同程序一起彻底卸载＝ 直接删掉整个项目文件夹（README「卸载」一节）。

删除细节：仓库自带的 .gitkeep 占位目录（models/ work/ logs/ state/ 等）卸载后
会保留为空骨架（0 字节），之后重跑 setup.py 即是全新安装；因卸载而空出来的
父目录（如只剩转换脚本时的 tools/）会被顺手清掉。

用法：
  python uninstall.py            # 交互式：先选操作，列出清单，确认后删除
  python uninstall.py --dry-run  # 只列清单和体积，不删任何文件
  python uninstall.py --yes      # 无交互：执行「以上全部」
  python uninstall.py --system   # 追加卸掉装进当前 Python 的 pip 包
                                 #（仅安装时选了「直接装入当前 Python」才需要）

--system 说明：默认安装方式（.venv 虚拟环境）下依赖都装在 .venv/ 里，
删掉 .venv/ 即完全清净，不需要 --system。只有当时选了 --system 或菜单
「2) 直接装入当前 Python」时才要加，而且卸的是系统 Python 里的包——
若这些包其他项目也在用，请先自行确认。

Windows 下可双击本文件运行：结束后会等待按键再关闭窗口。
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "scripts"))
from common import MODELS_DIR, ROOT  # noqa: E402

IS_WIN = os.name == "nt"

# 操作 → 待删目标（相对 ROOT）。顺序即展示顺序；models 放最后突出它是大头。
# work/（作业输出：报告/结论/缩略图）只在「以上全部」里清——三个单项操作都不碰它，
# 避免误删分析成果；要删报告请选 4 或手动删 work/ 下的目录。
SCOPE_TARGETS = {
    "cache": [("logs", "运行日志"),
              ("state", "作业注册表等运行时状态")],
    "models": [("models", "模型文件（GGUF，三个全装约 24.5 GB）")],
    "tools": [("tools/llama-vulkan", "推理引擎二进制（llama.cpp）"),
              ("tools/convert", "ArtiMuse 转换脚本")],
    "output": [("work", "作业输出（报告 / 结论 / 缩略图）")],
    "venv": [(".venv", "Python 虚拟环境（默认安装方式）")],
}

# 菜单：编号 → (scope, 标题, 说明)。选 4 = 上面全部（外加 work/ 作业输出）。
SCOPE_MENU = [
    ("1", "cache", "清除缓存及日志", "运行日志、作业注册表、.part 下载残留与 __pycache__"),
    ("2", "models", "卸载模型", "models/ 下的 GGUF 模型文件（三个全装约 24.5 GB）"),
    ("3", "tools", "卸载工具", "推理引擎 llama.cpp 与 ArtiMuse 转换脚本（tools/）"),
    ("4", "all", "以上全部", "再加 .venv 虚拟环境与 work/ 作业输出，恢复到手装后的干净状态"),
]
ALL_SCOPES = ("cache", "models", "tools", "output", "venv")

# --system 时追加卸载的包：requirements.txt 里的依赖 + ArtiMuse 转换链路的四个包
EXTRA_PIP_PACKAGES = ["torch", "safetensors", "numpy", "gguf"]


def pause_before_exit():
    """双击运行（Windows 控制台）时停留一下，避免窗口一闪即关看不清结果。"""
    if IS_WIN and sys.stdin.isatty():
        try:
            input("\n按回车键退出 ...")
        except (EOFError, KeyboardInterrupt, OSError):
            pass


def warn(msg):
    print("[WARN] " + msg)


def ask(prompt, default=""):
    try:
        s = input(prompt).strip()
    except EOFError:
        return default
    return s or default


def dir_size(path):
    """目录体积（字节）；单个文件取不到或读取失败按 0 计。"""
    total = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def hsize(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def safe_target(rel):
    """把相对路径解析成绝对路径，并确认它确实在 ROOT 之内（防呆）。"""
    p = os.path.abspath(os.path.join(ROOT, rel))
    root = os.path.abspath(ROOT)
    if p != root and not p.startswith(root + os.sep):
        raise SystemExit(f"安全中止：{rel} 解析到项目目录之外（{p}）")
    return p


def remove_target(p):
    """删除目标；原本带 .gitkeep 占位的目录保留为空骨架，便于之后原样重装。"""
    keep = os.path.isfile(os.path.join(p, ".gitkeep"))
    if os.path.isdir(p):
        shutil.rmtree(p)
        if keep:  # 重建 0 字节占位，保持目录骨架（重装时即全新状态）
            os.makedirs(p, exist_ok=True)
            open(os.path.join(p, ".gitkeep"), "wb").close()
    elif os.path.exists(p):
        os.remove(p)


def prune_empty_parents(path):
    """清掉因卸载而空出来的父目录（如 tools/）；ROOT 与目标自身不动。"""
    parent = os.path.dirname(os.path.abspath(path))
    root = os.path.abspath(ROOT)
    while parent and os.path.abspath(parent) != root:
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
            parent = os.path.dirname(parent)
        else:
            break


# ---------------- 缓存清理（logs/state 之外的 .part 与 __pycache__） ----------------

def cache_extras_scan():
    """「清除缓存及日志」附加项：models/ 下 .part 下载残留 + 项目内 __pycache__。

    返回 [(相对路径, 体积)]。.venv 内的 __pycache__ 不碰——删 .venv 时
    整个目录都会走，没必要先翻一遍。
    """
    out = []
    if os.path.isdir(MODELS_DIR):
        for p in glob.glob(os.path.join(MODELS_DIR, "**", "*.part"), recursive=True):
            if os.path.isfile(p):
                try:
                    out.append((os.path.relpath(p, ROOT), os.path.getsize(p)))
                except OSError:
                    pass
    for d in (ROOT, os.path.join(ROOT, "scripts")):
        p = os.path.join(d, "__pycache__")
        if os.path.isdir(p):
            out.append((os.path.relpath(p, ROOT), dir_size(p)))
    return out


def cache_extras_clean():
    """执行附加清理，返回 (释放字节数, [被清理的相对路径])。"""
    freed, done = 0, []
    for rel, size in cache_extras_scan():
        p = os.path.join(ROOT, rel)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                os.remove(p)
            if not os.path.exists(p):
                freed += size
                done.append(rel)
        except OSError:
            pass
    return freed, done


def scan(targets):
    """返回 [(rel, desc, path, size, exists)]；不存在的目标标注出来。"""
    out = []
    for rel, desc in targets:
        p = safe_target(rel)
        if os.path.isdir(p):
            out.append((rel, desc, p, dir_size(p), True))
        elif os.path.exists(p):
            out.append((rel, desc, p, os.path.getsize(p), True))
        else:
            out.append((rel, desc, p, 0, False))
    return out


def menu_scopes():
    """交互选择要执行的操作，返回 scope 集合（空集 = 取消）。"""
    print("\n请选择要执行的操作（可多选，逗号分隔；直接回车 = 取消）：")
    for num, _key, title, desc in SCOPE_MENU:
        print(f"  {num}) {title:<6} —— {desc}")
    s = ask("> ", "")
    keys = set()
    for tok in s.replace("，", ",").replace(" ", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok in ("4", "all", "全部"):
            keys = set(ALL_SCOPES)
            break
        hit = next((k for n, k, _t, _d in SCOPE_MENU if n == tok), None)
        if hit is None:
            warn(f"忽略无法识别的选项：{tok}")
        else:
            keys.add(hit)
    return keys


def pip_uninstall(packages):
    """卸掉系统 Python 里的包（--system 用）。requirements.txt 也一并处理。"""
    req = os.path.join(_HERE, "requirements.txt")
    cmds = []
    if os.path.isfile(req):
        cmds.append([sys.executable, "-m", "pip", "uninstall", "-y", "-r", req])
    if packages:
        cmds.append([sys.executable, "-m", "pip", "uninstall", "-y"] + list(packages))
    ok = True
    for cmd in cmds:
        print("运行：" + " ".join(cmd))
        r = subprocess.run(cmd)
        if r.returncode != 0:
            ok = False
    return ok


def main():
    ap = argparse.ArgumentParser(
        description="卸载辅助：按需清除缓存及日志 / 模型 / 工具，保留程序本身")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="无交互，直接执行「以上全部」")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列清单和体积，不删任何文件")
    ap.add_argument("--system", action="store_true",
                    help="追加卸掉装进当前 Python 的 pip 包"
                         "（仅安装时选了「直接装入当前 Python」才需要）")
    args = ap.parse_args()

    print("=" * 62)
    print(" 卸载辅助")
    print("=" * 62)
    print(f"项目目录 : {ROOT}")

    # ---- 选择操作 ----
    if args.yes:
        scopes = set(ALL_SCOPES)
        print("\n模式     : --yes（无交互，执行「以上全部」）")
    else:
        scopes = menu_scopes()
        if not scopes:
            print("\n未选择任何操作，已取消。")
            pause_before_exit()
            return 0
        titles = [t for _n, k, t, _d in SCOPE_MENU if k in scopes]
        print(f"\n已选操作 : {'、'.join(titles)}")

    # ---- 汇总待删目标 ----
    targets, seen = [], set()
    for key in ALL_SCOPES:
        if key not in scopes:
            continue
        for rel, desc in SCOPE_TARGETS.get(key, []):
            if rel not in seen:
                seen.add(rel)
                targets.append((rel, desc))

    items = scan(targets)
    existing = [it for it in items if it[4]]
    missing = [it for it in items if not it[4]]
    extras = cache_extras_scan() if "cache" in scopes else []
    extra_size = {rel: size for rel, size in extras}

    print("\n将删除以下内容：")
    if existing:
        for rel, desc, _p, size, _e in existing:
            print(f"  [删除] {rel:<22} {hsize(size):>10}  {desc}")
    else:
        print("  （没有可删除的内容）")
    for rel, _desc, _p, _size, _e in missing:
        print(f"  [跳过] {rel:<22} {'不存在':>10}")
    for rel, size in extras:
        print(f"  [删除] {rel:<22} {hsize(size):>10}  缓存残留")
    total = sum(it[3] for it in existing) + sum(extra_size.values())
    print(f"\n合计释放约 {hsize(total)}。")
    print("程序本身（scripts/ web/ docs/ README 等）不会删除；"
          "彻底卸载请直接删掉整个项目文件夹。")
    print("带 .gitkeep 占位的目录（models/ work/ logs/ state/ 等）会保留为空骨架，"
          "之后重跑 python setup.py 即可原样重装。")

    if args.dry_run:
        print("\n--dry-run 结束，未删除任何文件。")
        pause_before_exit()
        return 0
    if not existing and not extras:
        pause_before_exit()
        return 0

    if not args.yes:
        try:
            s = input("\n确认删除以上内容？[y/N] > ").strip().lower()
        except EOFError:
            s = ""
        if s not in ("y", "yes"):
            print("已取消，未删除任何文件。")
            pause_before_exit()
            return 1

    freed = 0
    failed = []
    for rel, desc, p, size, _e in existing:
        try:
            remove_target(p)
            prune_empty_parents(p)
            freed += size
            print(f"已删除 {rel}（释放 {hsize(size)}）")
        except OSError as e:
            failed.append(rel)
            print(f"[WARN] 删除失败 {rel}: {e}\n"
                  f"       可能被其他程序占用（如 llama-server 正在运行），"
                  f"请先运行 stop.bat 再试。")
    if "cache" in scopes:
        extra_freed, extra_done = cache_extras_clean()
        freed += extra_freed
        for rel in extra_done:
            print(f"已清理 {rel}（释放 {hsize(extra_size.get(rel, 0))}）")

    if args.system:
        print("\n卸掉装进当前 Python 的 pip 包（--system）...")
        print("注意：这些包其他项目可能也在用；原本就有的包不会因本脚本安装。")
        if not pip_uninstall(EXTRA_PIP_PACKAGES):
            failed.append("pip 包")

    print("\n" + "=" * 62)
    if failed:
        print(f" 卸载结束（有 {len(failed)} 项失败：{', '.join(failed)}）")
    else:
        print(f" 卸载完成，共释放约 {hsize(freed)}")
    print("=" * 62)
    print("重新安装：python setup.py")
    pause_before_exit()
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n已取消。")
        sys.exit(130)
