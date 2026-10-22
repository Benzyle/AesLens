#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AesLens美学分析工具 · 启动台（launcher）。

本地 HTTP 服务，提供：作业管理（新建 / 继续 / 重命名）、模型选择、参数配置、
启动 / 停止分析、进度查看、报告生成与查看。

用法：python launcher.py  →  浏览器打开 http://127.0.0.1:8081
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (LAUNCHER_LOG, REGISTRY, SERVER_STATE, WEB_DIR,  # noqa: E402
                    WORK_ROOT, job_dir, load_registry, model_dir,
                    resolve_model_files, resolve_work_dir, save_registry)

UI_HTML = os.path.join(WEB_DIR, "launcher.html")
RUN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_analysis.py")
MAKE_REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_report.py")
LOG_FILE = LAUNCHER_LOG
PORT = 8081

_WIN = os.name == "nt"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if _WIN else 0
NEWGROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if _WIN else 0
PY = (sys.executable if sys.executable and os.path.exists(sys.executable)
      else ("python" if _WIN else "python3"))

MODEL_NAMES = {"unipercept": "UniPercept-abliterated",
               "unipercept_std": "UniPercept", "artimuse": "ArtiMuse"}

job = {"proc": None, "job_id": None, "model": None, "started": None, "done0": 0}
job_lock = threading.Lock()
SERVER_INSTANCE = None


def server_healthy():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def read_registry():
    return load_registry()


# ---------------- 作业加载 / 移除（work/ 目录 ↔ 注册表） ----------------
# 结论文件名形如 <model_id>_conclusions.json，与 common.job_conclusions_path 一致
_CONCL_RE = re.compile(r"^(unipercept|unipercept_std|artimuse)_conclusions\.json$")


def _job_dir_of(entry):
    """注册条目对应的输出目录（兼容显式 dir 字段与按名反推两种形态）。"""
    return os.path.abspath(resolve_work_dir(entry))


def _read_job_models(full):
    """读取作业目录里各模型的结论条数 {model_id: n}。"""
    models = {}
    try:
        entries = os.listdir(full)
    except OSError:
        return models
    for fn in entries:
        m = _CONCL_RE.match(fn)
        if not m:
            continue
        try:
            with open(os.path.join(full, fn), "r", encoding="utf-8") as f:
                models[m.group(1)] = len(json.load(f).get("records", []))
        except Exception:
            models[m.group(1)] = 0
    return models


def scan_work_dirs():
    """扫描 work/ 下可作为作业加载的目录。

    判定标准：含任一 <model>_conclusions.json 或 report.html。
    返回目录名、各模型结论条数、是否有报告、是否已在注册表中、修改时间。
    """
    registered = {_job_dir_of(j) for j in read_registry().get("jobs", [])}
    out = []
    if not os.path.isdir(WORK_ROOT):
        return out
    try:
        names = sorted(os.listdir(WORK_ROOT))
    except OSError:
        return out
    for name in names:
        full = os.path.join(WORK_ROOT, name)
        if not os.path.isdir(full) or name.startswith("."):
            continue
        models = _read_job_models(full)
        has_report = os.path.isfile(os.path.join(full, "report.html"))
        if not models and not has_report:
            continue  # 不像作业目录（空目录 / 残留目录等）
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M")
        except OSError:
            mtime = ""
        out.append({"folder": name, "models": models, "has_report": has_report,
                    "registered": os.path.abspath(full) in registered,
                    "mtime": mtime})
    return out


def load_job_from_work(folder):
    """把 work/ 下已存在的作业目录纳入注册表（加载已完成作业）。

    folder 传 work/ 下的目录名或绝对路径（须在 work/ 内）。返回 (条目, 错误信息)。
    """
    name = os.path.basename(os.path.abspath(folder).rstrip("\\/"))
    full = os.path.abspath(os.path.join(WORK_ROOT, name))
    if os.path.dirname(full) != os.path.abspath(WORK_ROOT) or not os.path.isdir(full):
        return None, "目录不存在或不在 work/ 下"
    models = _read_job_models(full)
    if not models and not os.path.isfile(os.path.join(full, "report.html")):
        return None, "该目录下没有结论文件或报告，不像是一个作业"
    reg = read_registry()
    for j in reg.get("jobs", []):
        if _job_dir_of(j) == full:
            return None, "该作业已在列表中（无需重复加载）"
    now = datetime.now()
    entry = {"id": "job-" + now.strftime("%Y%m%d-%H%M%S"), "name": name,
             "folder": None, "dir": full, "models": list(models.keys()),
             "created": now.strftime("%Y-%m-%d %H:%M:%S"),
             "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
             "status": "loaded", "counts": dict(models)}
    reg["jobs"].append(entry)
    save_registry(reg)
    return entry, None


def remove_job(jid, delete_files=False):
    """从注册表移除作业（不再显示）。delete_files=True 时连 work/ 下的目录一起删。

    返回 (成功?, 错误信息)。正在分析的作业不允许移除。
    """
    reg = read_registry()
    j = next((x for x in reg.get("jobs", []) if x["id"] == jid), None)
    if not j:
        return False, "作业不存在"
    with job_lock:
        running = (job["proc"] is not None and job["proc"].poll() is None
                   and job.get("job_id") == jid)
    if running:
        return False, "该作业正在分析中，请先停止再移除"
    target = _job_dir_of(j)
    reg["jobs"] = [x for x in reg["jobs"] if x["id"] != jid]
    save_registry(reg)
    if delete_files and os.path.isdir(target):
        # 防御：只允许删 work/ 直属目录，避免注册表被改坏时误伤
        if os.path.dirname(target) == os.path.abspath(WORK_ROOT):
            shutil.rmtree(target, ignore_errors=True)
    return True, None


def list_dir(path):
    """列出目录下的子文件夹（供启动台内置文件夹浏览器）。path 为空时返回磁盘列表。"""
    if not path:
        drives = []
        for d in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            p = d + ":\\"
            if os.path.exists(p):
                drives.append({"name": p, "path": p})
        return {"path": "", "parent": None, "dirs": drives, "is_root": True}
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return {"path": path, "parent": None, "dirs": [], "error": "目录不存在或无法访问"}
    dirs = []
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            try:
                if os.path.isdir(full) and not name.startswith("$") \
                        and name.lower() != "system volume information":
                    dirs.append({"name": name, "path": full})
            except Exception:
                pass
    except Exception as e:
        return {"path": path, "parent": None, "dirs": [], "error": str(e)}
    dirs.sort(key=lambda x: x["name"].lower())
    _drive, tail = os.path.splitdrive(path.rstrip("\\/"))
    parent = "" if tail in ("", "\\", "/") else os.path.dirname(path.rstrip("\\/"))
    return {"path": path, "parent": parent, "dirs": dirs, "is_root": False}


_MODELS_CACHE = {"ts": 0.0, "data": None}


def get_models(force=False):
    """返回模型列表。带 60s 缓存与「上次成功结果」回退，避免 GPU 高负载时子进程变慢导致列表空白。"""
    now = time.time()
    if not force and _MODELS_CACHE["data"] and now - _MODELS_CACHE["ts"] < 60:
        return _MODELS_CACHE["data"]
    try:
        out = subprocess.run([PY, RUN_SCRIPT, "--list-models"],
                             capture_output=True, text=True, timeout=15,
                             encoding="utf-8", creationflags=NOWIN)
        data = json.loads(out.stdout)
    except Exception as e:
        if _MODELS_CACHE["data"]:
            return _MODELS_CACHE["data"]
        data = {"models": [], "error": str(e)}
    _MODELS_CACHE.update({"ts": now, "data": data})
    return data


def tail_log(n=200):
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return [l.rstrip("\n") for l in f.readlines()[-n:]]
    except Exception:
        return []


def job_status():
    with job_lock:
        running = job["proc"] is not None and job["proc"].poll() is None
        return {"running": running, "job_id": job["job_id"], "model": job["model"],
                "started": job["started"]}


IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")


def get_progress(jid, model):
    """批量分析进度：total=文件夹图片数，done=该模型已写结论数，elapsed/eta 基于本次运行。"""
    reg = read_registry()
    jb = next((x for x in reg.get("jobs", []) if x["id"] == jid), None)
    if not jb:
        return {"total": 0, "done": 0, "running": False, "elapsed": 0, "eta": None, "pct": 0}
    total = 0
    folder = jb.get("folder")
    if folder and os.path.isdir(folder):
        for _root, _dirs, files in os.walk(folder):
            for fn in files:
                if fn.lower().endswith(IMG_EXTS):
                    total += 1
    done = 0
    try:
        p = os.path.join(job_dir(jid), f"{model}_conclusions.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                done = len(json.load(f).get("records", []))
    except Exception:
        pass
    with job_lock:
        running = job["proc"] is not None and job["proc"].poll() is None
        started = job["started"] if job.get("job_id") == jid else None
        done0 = job.get("done0", 0)
    elapsed = 0
    if started:
        try:
            elapsed = max(0.0, (datetime.now() - datetime.strptime(started, "%Y-%m-%d %H:%M:%S")).total_seconds())
        except Exception:
            elapsed = 0
    this_run = max(0, done - done0)
    eta = None
    if running and this_run > 0 and elapsed > 3:
        rate = this_run / elapsed
        if rate > 0:
            eta = max(0.0, (total - done) / rate)
    pct = int(min(100, round(done / total * 100))) if total else 0
    return {"total": total, "done": done, "running": bool(running),
            "elapsed": int(elapsed), "eta": (int(eta) if eta is not None else None), "pct": pct}


def _kill_win_orphans():
    """Windows 兜底：杀掉所有孤儿 run_analysis.py（按命令行过滤，不会误杀本进程）。"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*run_analysis.py*' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, timeout=15, creationflags=NOWIN)


def kill_all_analysis():
    """彻底停止分析：先杀已知子进程树，再兜底杀 llama-server 与所有孤儿 run_analysis.py。
    即使启动台曾重启、丢失了子进程句柄，也能把残留的分析/推理进程清干净。"""
    with job_lock:
        proc = job["proc"]
        job.update({"proc": None, "job_id": None, "model": None, "started": None, "done0": 0})
    if proc is not None and proc.poll() is None:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10, creationflags=NOWIN)
        except Exception:
            pass
    if not _WIN:
        return
    # 兜底 1：推理服务
    try:
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                       capture_output=True, timeout=10, creationflags=NOWIN)
    except Exception:
        pass
    # 兜底 2：任何孤儿 run_analysis.py
    try:
        _kill_win_orphans()
    except Exception:
        pass
    # 清理 server_state，避免下次 ensure_server 误判为「已在运行」
    try:
        if os.path.exists(SERVER_STATE):
            os.remove(SERVER_STATE)
    except Exception:
        pass


def generate_report(job_id=None):
    """生成指定作业的报告，返回 work/<作业名>/report.html 路径。"""
    if not job_id:
        return None, "请先选择一个作业"
    cmd = [PY, MAKE_REPORT, "--job", job_id]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                       encoding="utf-8", creationflags=NOWIN)
    except Exception as e:
        return None, str(e)
    p = os.path.join(job_dir(job_id), "report.html")
    if not os.path.exists(p):
        return None, "报告生成失败（未产生文件）"
    return p, None


# ---------------- 系统监控 ----------------
_STATS_CACHE = {"ts": 0.0, "data": None}
_STATS_PS = r'''
$cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$os = Get-CimInstance Win32_OperatingSystem
$totalMem = [math]::Round($os.TotalVisibleMemorySize/1MB,2)
$freeMem = [math]::Round($os.FreePhysicalMemory/1MB,2)
$usedMem = [math]::Round($totalMem-$freeMem,2)
$disks = @()
foreach ($d in (Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3")) {
  $disks += [pscustomobject]@{drive=$d.DeviceID; total=[math]::Round($d.Size/1GB,2); used=[math]::Round(($d.Size-$d.FreeSpace)/1GB,2); free=[math]::Round($d.FreeSpace/1GB,2)}
}
$gpuUtil=$null; $vramUsed=$null; $vramTotal=$null; $gpuName=$null
try {
  $raw = & nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>$null | Out-String
  $line = ($raw -split "`n" | Where-Object { $_.Trim() -ne "" } | Select-Object -First 1).Trim()
  $parts = $line -split ',' | ForEach-Object { $_.Trim() }
  if ($parts.Count -ge 3) {
    $gpuUtil=[int]$parts[0]; $vramUsed=[math]::Round([double]$parts[1]/1024,2); $vramTotal=[math]::Round([double]$parts[2]/1024,2)
  }
  $nm = & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Out-String
  $nmLine = ($nm -split "`n" | Where-Object { $_.Trim() -ne "" } | Select-Object -First 1).Trim()
  if ($nmLine) { $gpuName=$nmLine }
} catch {}
$obj = [pscustomobject]@{cpu=$cpu; mem_total=$totalMem; mem_used=$usedMem; mem_free=$freeMem; disks=$disks; gpu_util=$gpuUtil; vram_used=$vramUsed; vram_total=$vramTotal; gpu_name=$gpuName; time=(Get-Date -Format 'HH:mm:ss')}
$obj | ConvertTo-Json -Compress
'''


def pick_folder_dialog():
    """弹出 Windows 原生文件夹选择对话框（STA 线程要求），返回所选绝对路径或 None。"""
    ps = (
        'Add-Type -AssemblyName System.Windows.Forms\n'
        '$dlg = New-Object System.Windows.Forms.FolderBrowserDialog\n'
        '$dlg.Description = "选择图片文件夹"\n'
        '$dlg.ShowNewFolderButton = $true\n'
        'try { $r = $dlg.ShowDialog() } catch { exit 1 }\n'
        'if ($r -eq [System.Windows.Forms.DialogResult]::OK) { $dlg.SelectedPath }\n'
    )
    try:
        out = subprocess.run(["powershell", "-STA", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=600,
                             creationflags=NOWIN)
        path = (out.stdout or "").strip()
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass
    return None


def collect_stats():
    """只返回后台线程已采集的缓存，绝不在请求线程里发起可能阻塞的 PowerShell 调用。
    （GPU 被 llama-server 卡住时 nvidia-smi 有时会挂死，逐请求调用会堆积线程把启动台拖死。）"""
    if _STATS_CACHE["data"]:
        return _STATS_CACHE["data"]
    return {"error": "stats warming up", "time": time.strftime("%H:%M:%S")}


def _stats_loop():
    """单线程后台采集，每 2 秒刷新一次缓存；即使某次 nvidia-smi 卡住，也只影响本线程，不会拖垮 HTTP 服务。"""
    while True:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", _STATS_PS],
                                 capture_output=True, text=True, timeout=8,
                                 creationflags=NOWIN)
            data = json.loads(out.stdout.strip())
            _STATS_CACHE.update({"ts": time.time(), "data": data})
        except Exception:
            if not _STATS_CACHE["data"]:
                _STATS_CACHE.update({"ts": time.time(),
                                     "data": {"error": "stats unavailable",
                                              "time": time.strftime("%H:%M:%S")}})
        time.sleep(2)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass

    def _send_file(self, path, ctype, download=False):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except Exception:
            self._send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        if download:
            self.send_header("Content-Disposition", "attachment")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send_file(UI_HTML, "text/html; charset=utf-8")
        elif path == "/api/models":
            q = parse_qs(urlparse(self.path).query)
            force = q.get("force", ["0"])[0] in ("1", "true", "yes")
            self._send_json(get_models(force=force))
        elif path == "/api/status":
            reg = read_registry()
            self._send_json({
                "inference_server": {"running": server_healthy()},
                "job": job_status(),
                "jobs": reg.get("jobs", []),
            })
        elif path == "/api/log":
            q = parse_qs(urlparse(self.path).query)
            n = int(q.get("n", ["200"])[0])
            self._send_json({"lines": tail_log(n)})
        elif path == "/api/jobs":
            self._send_json({"jobs": read_registry().get("jobs", [])})
        elif path == "/api/report":
            q = parse_qs(urlparse(self.path).query)
            jid = q.get("job", [""])[0]
            p, err = generate_report(jid)
            if not p or not os.path.exists(p):
                self._send_json({"error": err or "报告生成失败"}, 500)
                return
            self._send_file(p, "text/html; charset=utf-8")
        elif path == "/api/stats":
            self._send_json(collect_stats())
        elif path == "/api/progress":
            q = parse_qs(urlparse(self.path).query)
            jid = q.get("job", [""])[0]
            model = q.get("model", ["unipercept_std"])[0]
            self._send_json(get_progress(jid, model))
        elif path == "/api/browse":
            q = parse_qs(urlparse(self.path).query)
            self._send_json(list_dir(q.get("path", [""])[0]))
        elif path == "/api/workdirs":
            self._send_json({"dirs": scan_work_dirs()})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            payload = {}

        if path == "/api/job/new":
            name = payload.get("name") or "新建作业"
            folder = payload.get("folder")
            model = payload.get("model", "unipercept_std")
            if not folder or not os.path.isdir(folder):
                self._send_json({"ok": False, "error": "文件夹路径无效"}, 400)
                return
            # --create-only：只建作业、不开始分析——分析由用户点「开始分析」触发。
            # （run_analysis 的 --new-job 单独用时是「建完立即分析」的 CLI 语义。）
            out = subprocess.run([PY, RUN_SCRIPT, "--new-job", name, "--folder", folder,
                                  "--model", model, "--create-only"],
                                 capture_output=True, text=True, timeout=30, encoding="utf-8",
                                 creationflags=NOWIN)
            # stdout 理论上是单行 JSON；防御性取第一行非空内容解析，
            # 避免万一夹带了日志行就整体解析失败、把一大坨内容糊给出错调用方
            first = ""
            for line in (out.stdout or "").splitlines():
                if line.strip():
                    first = line.strip()
                    break
            try:
                res = json.loads(first)
            except Exception:
                blob = ((out.stdout or "") + (out.stderr or "")).strip()
                self._send_json({"ok": False, "error": blob[-1500:]}, 500)
                return
            self._send_json({"ok": True, **res})

        elif path == "/api/pick-folder":
            if not _WIN:
                self._send_json({"ok": False, "path": None,
                                 "error": "文件夹选择对话框仅支持 Windows"}, 400)
                return
            folder = pick_folder_dialog()
            if folder:
                self._send_json({"ok": True, "path": folder})
            else:
                self._send_json({"ok": False, "path": None, "error": "未选择或选择失败"})

        elif path == "/api/job/rename":
            jid = payload.get("id")
            new_name = payload.get("name")
            if not jid:
                self._send_json({"ok": False, "error": "缺少作业 id"}, 400)
                return
            out = subprocess.run([PY, RUN_SCRIPT, "--job", jid, "--rename-only", new_name or ""],
                                 capture_output=True, text=True, timeout=30, encoding="utf-8",
                                 creationflags=NOWIN)
            try:
                res = json.loads(out.stdout)
            except Exception:
                self._send_json({"ok": False,
                                 "error": (out.stdout + out.stderr) or "重命名失败"}, 500)
                return
            self._send_json({"ok": bool(res.get("ok"))})

        elif path == "/api/job/load":
            entry, err = load_job_from_work(payload.get("folder") or "")
            if not entry:
                self._send_json({"ok": False, "error": err or "加载失败"}, 400)
                return
            self._send_json({"ok": True, "job": entry})

        elif path == "/api/job/remove":
            jid = payload.get("id")
            if not jid:
                self._send_json({"ok": False, "error": "缺少作业 id"}, 400)
                return
            ok, err = remove_job(jid, delete_files=bool(payload.get("delete_files")))
            if not ok:
                self._send_json({"ok": False, "error": err}, 400)
                return
            self._send_json({"ok": True, "id": jid})

        elif path == "/api/start":
            with job_lock:
                if job["proc"] is not None and job["proc"].poll() is None:
                    self._send_json({"ok": False, "error": "已有分析任务在进行中"}, 409)
                    return
            jid = payload.get("job_id")
            model = payload.get("model", "unipercept_std")
            workers = int(payload.get("workers", 2))
            limit = int(payload.get("limit", 0))
            if not jid:
                self._send_json({"ok": False, "error": "缺少 job_id"}, 400)
                return
            reg = read_registry()
            j = next((x for x in reg.get("jobs", []) if x["id"] == jid), None)
            if not j:
                self._send_json({"ok": False, "error": "作业不存在"}, 404)
                return
            if not j.get("folder"):
                self._send_json({"ok": False,
                                 "error": "该作业是从 work/ 文件夹加载的，未记录图片目录，"
                                          "无法继续分析（可查看 / 重新生成报告）；"
                                          "需要分析请新建作业"}, 400)
                return
            if model not in MODEL_NAMES:
                self._send_json({"ok": False, "error": f"未知模型: {model}"}, 400)
                return
            # 模型文件运行时解析：按本机实际放置的量化档位找文件，不限定 Q8_0
            files = resolve_model_files(model)
            if not files["gguf"] or not files["mmproj"]:
                self._send_json({"ok": False,
                                 "error": f"模型 {MODEL_NAMES[model]} 文件缺失：请在 "
                                          f"{model_dir(model)} 放置 <模型名>-<量化档位>.gguf "
                                          f"与 mmproj-*.gguf（命名规则见 docs/models.md）"}, 400)
                return

            args = [PY, RUN_SCRIPT, "--job", jid, "--model", model, "--workers", str(workers)]
            if limit > 0:
                args += ["--limit", str(limit)]
            os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            logf = open(LOG_FILE, "a", encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT, env=env,
                                    creationflags=NEWGROUP)
            # 记录开始时的已完成数，用于计算「本次运行」的进度速率与剩余时间
            d0 = 0
            try:
                p0 = os.path.join(job_dir(jid), f"{model}_conclusions.json")
                if os.path.exists(p0):
                    with open(p0, "r", encoding="utf-8") as f:
                        d0 = len(json.load(f).get("records", []))
            except Exception:
                d0 = 0
            with job_lock:
                job.update({"proc": proc, "job_id": jid, "model": model,
                            "started": time.strftime("%Y-%m-%d %H:%M:%S"), "done0": d0})
            self._send_json({"ok": True, "pid": proc.pid, "job_id": jid, "model": model})

        elif path == "/api/stop":
            # 幂等：无论当前是否「认为」有任务，都彻底清理分析/推理进程，保证一定能停下
            kill_all_analysis()
            self._send_json({"ok": True, "msg": "已停止分析任务与推理服务"})

        elif path == "/api/shutdown":
            self._send_json({"ok": True, "msg": "启动台正在关闭"})
            threading.Thread(target=self._delayed_shutdown, daemon=True).start()

        else:
            self._send_json({"error": "not found"}, 404)

    def _delayed_shutdown(self):
        time.sleep(0.2)
        try:
            kill_all_analysis()
        except Exception:
            pass
        if SERVER_INSTANCE:
            SERVER_INSTANCE.shutdown()


def _reset_stale_running():
    """启动时清理上次会话遗留的「running」状态（进程已被关闭，状态不应再显示为运行中）。"""
    try:
        reg = read_registry()
        changed = False
        for j in reg.get("jobs", []):
            if j.get("status") == "running":
                j["status"] = "stopped"
                changed = True
        if changed:
            save_registry(reg)
    except Exception:
        pass


def _open_browser_when_ready(url, retries=120):
    """等待本服务真正可访问后再打开浏览器，避免端口未就绪导致打不开页面。"""
    import webbrowser
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=2):
                pass
            break
        except Exception:
            time.sleep(0.3)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    if not os.path.exists(UI_HTML):
        print(f"缺少 UI 文件: {UI_HTML}")
        sys.exit(1)
    _reset_stale_running()
    global SERVER_INSTANCE
    try:
        SERVER_INSTANCE = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        # 端口已被占用通常意味着已有一个启动台实例在运行：直接退出，让 .bat 打开已有实例即可
        print(f"检测到 {PORT} 端口已被占用，可能已有启动台实例在运行。")
        print(f"请直接访问 http://127.0.0.1:{PORT}（或先运行「停止服务.bat」再重启）。")
        print(f"（详情：{e}）")
        sys.exit(0)
    url = f"http://127.0.0.1:{PORT}"
    print(f"启动台已启动: {url}")
    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    if _WIN:
        threading.Thread(target=_stats_loop, daemon=True).start()
    try:
        SERVER_INSTANCE.serve_forever()
    finally:
        print("启动台已关闭")


if __name__ == "__main__":
    main()
