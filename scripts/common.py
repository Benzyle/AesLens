#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共路径、作业注册表与模型文件解析。

三个脚本（launcher / run_analysis / make_report）共用本模块，
避免各自硬编码项目根目录与重复实现「作业名 → 输出目录」的映射规则。

项目根目录默认取本文件的上级目录（即 scripts/ 的父目录），
也可用环境变量 AESTHETICS_ROOT 覆盖，便于把数据盘与程序分离。

模型文件解析：主模型按量化梯（QUANT_LADDER）在本机 models/ 下找实际档位，
mmproj 按词干互为兜底——量化档位与 mmproj 选择都不硬编码。
"""
import json
import os
import re
import sys
from datetime import datetime

# 文件名常含日文/特殊符号（・ ♡ ー 等），中文 Windows 的 GBK 控制台在 print 时会报
# UnicodeEncodeError 导致流程中断。强制 stdout/stderr 用 UTF-8 并容错。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

ENV_ROOT = "AESTHETICS_ROOT"


def project_root():
    env = os.environ.get(ENV_ROOT)
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = project_root()
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
WEB_DIR = os.path.join(ROOT, "web")
VENDOR_DIR = os.path.join(ROOT, "vendor")
CHARTJS = os.path.join(VENDOR_DIR, "chart.umd.js")
MODELS_DIR = os.path.join(ROOT, "models")
LLAMA_DIR = os.path.join(ROOT, "tools", "llama-vulkan")
WORK_ROOT = os.path.join(ROOT, "work")        # 作业输出（报告 / 结论 / 缩略图）
STATE_DIR = os.path.join(ROOT, "state")       # 注册表等运行时状态
LOGS_DIR = os.path.join(ROOT, "logs")         # 运行日志
REGISTRY = os.path.join(STATE_DIR, "jobs.json")
SERVER_STATE = os.path.join(STATE_DIR, "server_state.json")
LOG_FILE = os.path.join(LOGS_DIR, "analysis_progress.log")
LAUNCHER_LOG = os.path.join(LOGS_DIR, "launcher_job.log")
SERVER_LOG = os.path.join(LOGS_DIR, "server.log")

# 只与本机 llama-server / 自身通信：强制让 127.0.0.1/localhost 绕过任何代理。
# 否则系统代理会把本机请求转出去，表现为 502 / 超时 / 「推理服务检测失败」等假故障。
_NO_PROXY = ",".join(filter(None, [os.environ.get("NO_PROXY", ""),
                                   os.environ.get("no_proxy", ""),
                                   "127.0.0.1", "localhost", "::1"]))
os.environ["NO_PROXY"] = _NO_PROXY
os.environ["no_proxy"] = _NO_PROXY


def safe_folder_name(name):
    """作业名 → 文件夹名：去掉 Windows 非法字符、压缩空白、限长 60。"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', name or 'job').strip().strip('.')
    s = re.sub(r'\s+', ' ', s).strip().replace(' ', '_')
    return s[:60] or 'job'


def load_registry():
    try:
        with open(REGISTRY, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"jobs": []}


def save_registry(reg):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)


def find_job(jid):
    return next((j for j in load_registry().get("jobs", []) if j["id"] == jid), None)


def touch_job(job):
    """把内存中的作业对象（可能已改 name/folder/status/counts）写回注册表。"""
    reg = load_registry()
    for j in reg["jobs"]:
        if j["id"] == job["id"]:
            j.update({"updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      "folder": job["folder"], "name": job["name"], "models": job["models"],
                      "status": job["status"], "counts": job["counts"]})
    save_registry(reg)


def resolve_work_dir(job):
    """作业输出目录：work/<作业名>/；同名作业追加 id 后缀避免互相覆盖。

    注册条目显式带 dir 字段时优先使用它（如「加载作业」把 work/ 下既有目录
    纳入注册表）：按名字反推的目录会经 safe_folder_name 改写（空格→下划线等），
    与真实文件夹名不一定一致，显式 dir 才能保证结论/报告读写落在原目录上。
    """
    d = job.get("dir")
    if d and os.path.isabs(d):
        return d
    base = safe_folder_name(job.get("name") or job["id"])
    d = os.path.join(WORK_ROOT, base)
    others = [j for j in load_registry().get("jobs", [])
              if j["id"] != job["id"] and safe_folder_name(j.get("name", "")) == base]
    if others:
        d = os.path.join(WORK_ROOT, base + "_" + job["id"].split("-")[-1])
    return d


def job_dir(jid):
    job = find_job(jid)
    return resolve_work_dir(job) if job else os.path.join(WORK_ROOT, jid)


# ---------------- 模型文件解析（量化档位不设死） ----------------
# 社区仓库提供完整量化梯；用户显卡跑不动 Q8_0 时，可换用其他档位
#（安装时 setup.py --quant 选择，或手动把任意档位文件放入 models/），
# 运行时按本机实际文件解析，不硬编码单一档位。
QUANT_LADDER = ["BF16", "Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "Q4_0",
                "IQ4_XS", "Q3_K_M", "IQ3_M", "IQ2_M", "IQ2_XXS"]

# 模型 id → 主模型词干 / mmproj 候选词干（按优先级）。
# 两个 UniPercept 仓库的 mmproj 权重逐字节一致（442 个张量实测相同，
# 仅元数据与张量排序不同），因此任意一方缺失时可按候选顺序互为兜底。
MODEL_STEMS = {
    "unipercept": {"stem": "UniPercept-abliterated",
                   "mmproj_stems": ["UniPercept-abliterated", "UniPercept"]},
    "unipercept_std": {"stem": "UniPercept",
                       "mmproj_stems": ["UniPercept", "UniPercept-abliterated"]},
    "artimuse": {"stem": "ArtiMuse", "mmproj_stems": ["ArtiMuse"]},
}


def model_dir(model_id):
    """模型文件所在目录（ArtiMuse 在 models/artimuse/ 子目录）。"""
    return os.path.join(MODELS_DIR, "artimuse") if model_id == "artimuse" else MODELS_DIR


def _quant_rank(token):
    m = re.search(r"(\d+)", token or "")
    return int(m.group(1)) if m else 0


def find_quant_variant(models_dir, stem):
    """在 models_dir 中解析 <stem>-<档位>.gguf 的实际文件。

    返回 (档位, 路径)；找不到返回 (None, None)。
    正则要求 stem 后紧跟 "-" 且档位只含字母/数字/下划线，
    因此 "UniPercept" 不会误匹配 "UniPercept-abliterated-Q8_0.gguf"。
    已知档位按 QUANT_LADDER 质量排序；未知档位（如 Q3_K_L）排在其后，
    按档位名中的数字粗略降序，保证确定性。
    """
    if not os.path.isdir(models_dir):
        return None, None
    try:
        names = os.listdir(models_dir)
    except OSError:
        return None, None
    pat = re.compile(r"^%s-([A-Za-z0-9_]+)\.gguf$" % re.escape(stem), re.IGNORECASE)
    hits = []  # (rank, token, filename)
    for n in names:
        m = pat.match(n)
        if not m:
            continue
        tok = m.group(1)
        if tok.upper() in QUANT_LADDER:
            # 已知档位：按梯序排名（0=BF16 最优，序号越小越优先）
            hits.append((QUANT_LADDER.index(tok.upper()), tok.upper(), n))
        elif re.search(r"\d", tok):  # 非标档位也必须含数字，排除 abliterated 之类词
            # 未知档位排在所有已知档位之后，按档位名中的数字粗略降序
            hits.append((1000 - _quant_rank(tok), tok, n))
    if not hits:
        return None, None
    hits.sort(key=lambda h: (h[0], h[2].lower()))
    return hits[0][1], os.path.join(models_dir, hits[0][2])


def find_mmproj(models_dir, stems):
    """解析 mmproj 文件：依次尝试 精确名 → 词干前缀 → 目录内唯一 mmproj。

    stems 为按优先级排列的词干（如 ["UniPercept", "UniPercept-abliterated"]）。
    返回 (路径, 文件名, 是否兜底)；找不到返回 (None, None, False)。
    """
    if not os.path.isdir(models_dir):
        return None, None, False
    try:
        names = sorted(n for n in os.listdir(models_dir)
                       if n.lower().endswith(".gguf") and n.lower().startswith("mmproj"))
    except OSError:
        return None, None, False
    for stem in stems:  # 1) 精确名 + 常见精度后缀
        for suffix in ("-BF16.gguf", "-F16.gguf", "-F32.gguf", "-Q8_0.gguf"):
            want = ("mmproj-%s%s" % (stem, suffix)).lower()
            for n in names:
                if n.lower() == want:
                    return os.path.join(models_dir, n), n, False
    for stem in stems:  # 2) 词干前缀（跨仓库兜底：abliterated ↔ 原版）
        pre = ("mmproj-%s" % stem).lower()
        for n in names:
            if n.lower().startswith(pre):
                return os.path.join(models_dir, n), n, False
    if len(names) == 1:  # 3) 目录内唯一 mmproj（用户可能改了名）
        return os.path.join(models_dir, names[0]), names[0], True
    return None, None, False


def resolve_model_files(model_id):
    """解析模型实际文件：主模型按量化梯找档位，mmproj 按词干互为兜底。

    返回 {"gguf", "mmproj", "quant", "mmproj_name", "mmproj_fallback"}；
    找不到的文件对应值为 None。
    """
    spec = MODEL_STEMS.get(model_id)
    if not spec:
        return {"gguf": None, "mmproj": None, "quant": None,
                "mmproj_name": None, "mmproj_fallback": False}
    d = model_dir(model_id)
    quant, gguf = find_quant_variant(d, spec["stem"])
    mmproj, mmname, fallback = find_mmproj(d, spec["mmproj_stems"])
    return {"gguf": gguf, "mmproj": mmproj, "quant": quant,
            "mmproj_name": mmname, "mmproj_fallback": fallback}


def job_thumbs_dir(jid):
    return os.path.join(job_dir(jid), "thumbs")


def job_conclusions_path(jid, model_id):
    return os.path.join(job_dir(jid), f"{model_id}_conclusions.json")
