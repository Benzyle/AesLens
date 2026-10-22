#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UniPercept / ArtiMuse 美学评价流水线（作业制 · 多模型）。

设计要点：
  * 作业(job)：每次分析归属一个作业，结果按模型存于 work/<作业名>/<model>_conclusions.json，
    缩略图存于 work/<作业名>/thumbs/，源图片【不会被移动或修改】。
  * 不移动文件：通过 --folder 指定任意图片目录，图片留在原地，仅生成缩略图到作业目录。
  * 支持的图片格式：png/jpg/jpeg/webp/bmp/gif/tif/tiff。
  * 评分：两模型均输出 0-100 整数；综合得分 = 各维度得分的算术平均，
    因此综合得分必落在各维度得分区间内，逻辑自洽。

用法：
  python run_analysis.py --list-models
  python run_analysis.py --new-job "作业名" --folder "D:/图片目录" --model unipercept_std --workers 2
  python run_analysis.py --new-job "作业名" --folder "D:/图片目录" --create-only  # 仅新建作业不分析
  python run_analysis.py --job <job_id> --model artimuse --workers 2
  python run_analysis.py --job <job_id> --rename "新名字"
  python run_analysis.py --job <job_id> --rename-only "新名字"   # 仅重命名并迁移目录
  python run_analysis.py --job <job_id> --supplement            # 仅补齐缺失的画面描述/优化方向
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (LLAMA_DIR, LOG_FILE, MODEL_STEMS, SERVER_LOG, SERVER_STATE,  # noqa: E402
                    WORK_ROOT, find_job, job_conclusions_path,
                    job_dir, job_thumbs_dir, load_registry, model_dir,
                    resolve_model_files, save_registry, touch_job)

SERVER = "http://127.0.0.1:8080"

_WIN = os.name == "nt"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if _WIN else 0
NEWGROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if _WIN else 0

# 支持的图片格式（小写扩展名）
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif",
        ".tif": "image/tiff", ".tiff": "image/tiff"}

# ---------------- 维度定义 ----------------
DIMS_CN = ["构图与设计", "视觉元素与结构", "技术执行", "原创性与创意",
           "主题与表达", "情感与观众反应", "整体格式塔"]
DIMS_EN = ["Composition & Design", "Visual Elements & Structure", "Technical Execution",
           "Originality & Creativity", "Theme & Communication", "Emotion & Viewer Response",
           "Overall Gestalt"]
DIM_KEYS = ["composition_design", "visual_elements_structure", "technical_execution",
            "originality_creativity", "theme_communication", "emotion_viewer_response",
            "overall_gestalt"]

DESCRIPTION_PROMPT = ("请以专业美学评论家的视角，用中文详细描述这张图片的画面内容，"
                      "包括主体、场景构图、色彩、光影、氛围与风格等要素（150 字左右）。")

OPTIMIZATION_PROMPT = ("请针对以下 8 个维度，分别给出这张图片具体的优化建议（每条 1-2 句中文）：\n"
                       "1. 构图与设计\n2. 视觉元素与结构\n3. 技术执行\n4. 原创性与创意\n"
                       "5. 主题与表达\n6. 情感与观众反应\n7. 整体格式塔\n8. 综合评价\n"
                       "请严格按以下格式逐条输出：\n维度名：建议内容")

# ArtiMuse token-as-score 字母映射（仅作兜底解析使用）
LETTER_MAP = {}
for i in range(26):
    LETTER_MAP[f"a{chr(97+i)}"] = i
for i in range(25):
    LETTER_MAP[f"c{chr(97+i)}"] = 26 + i
for i in range(25):
    LETTER_MAP[f"d{chr(97+i)}"] = 51 + i
for i in range(25):
    LETTER_MAP[f"e{chr(97+i)}"] = 76 + i

# 模型定义。gguf / mmproj 不在此处硬编码：运行时按本机 models/ 下实际放置的
# 量化档位解析（find_quant_variant / resolve_model_files），用户换档位无需改代码。
MODELS = {
    "unipercept": {
        "id": "unipercept",
        "name": "UniPercept-abliterated",
        "desc": "统一感知级图像理解模型（abliterated，量化档位按本机文件自动解析）",
        "score_mode": "numeric",
        "aspect_keys": DIM_KEYS,
        "aspect_cn": DIMS_CN,
        "aspect_en": DIMS_EN,
        "system": "你是一位专业的图像美学评价专家，必须始终使用简体中文回答。",
    },
    "unipercept_std": {
        "id": "unipercept_std",
        "name": "UniPercept",
        "desc": "统一感知级图像理解模型（原版，未 abliterate，量化档位按本机文件自动解析）",
        "score_mode": "numeric",
        "aspect_keys": DIM_KEYS,
        "aspect_cn": DIMS_CN,
        "aspect_en": DIMS_EN,
        "system": "你是一位专业的图像美学评价专家，必须始终使用简体中文回答。",
    },
    "artimuse": {
        "id": "artimuse",
        "name": "ArtiMuse",
        "desc": "细粒度图像美学评估模型（量化档位按本机文件自动解析）",
        "score_mode": "letter",  # 首选数值；数值解析失败时用字母映射兜底
        "aspect_keys": DIM_KEYS + ["comprehensive_evaluation"],
        "aspect_cn": DIMS_CN + ["综合评价"],
        "aspect_en": DIMS_EN + ["Comprehensive Evaluation"],
        "system": "你是一位专业的图像美学评价专家，必须始终使用简体中文回答。",
    },
}
DEFAULT_MODEL = "unipercept_std"

# ---------------- 日志 ----------------
log_lock = threading.Lock()


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    with log_lock:
        try:
            os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        try:
            print(line, flush=True)
        except Exception:
            pass


# ---------------- Server 管理 ----------------
def server_healthy():
    try:
        with urllib.request.urlopen(SERVER + "/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def free_vram_gb():
    """查询当前空闲显存（GB）；查询失败或非 NVIDIA 平台返回 None。"""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=6,
                             encoding="utf-8", errors="ignore",
                             creationflags=NOWIN)
        vals = [int(x.strip()) for x in out.stdout.split() if x.strip().isdigit()]
        return max(vals) / 1024.0 if vals else None
    except Exception:
        return None


def kill_servers():
    if _WIN:
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                       capture_output=True, creationflags=NOWIN)
    time.sleep(2)


def read_server_state():
    try:
        with open(SERVER_STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ensure_server(model_id):
    cfg = MODELS[model_id]
    files = resolve_model_files(model_id)
    if not files["gguf"] or not files["mmproj"]:
        d = model_dir(model_id)
        raise RuntimeError(
            f"模型文件缺失：{cfg['name']}\n"
            f"  主模型：{d} 下未找到 {MODEL_STEMS[model_id]['stem']}-<档位>.gguf\n"
            f"  mmproj：同目录下未找到 mmproj-*.gguf\n"
            f"  请先运行 setup.py 安装模型；若已通过其他途径取得文件，"
            f"按 docs/models.md 的命名规则放入即可（任意量化档位均可识别）。")
    if files["mmproj_fallback"]:
        log(f"提示：mmproj 使用兜底文件 {files['mmproj_name']}"
            f"（与首选名不同；UniPercept 两仓库 mmproj 权重一致，可正常使用）")
    state = read_server_state()
    # 文件路径也参与比对：用户替换了量化档位文件后要重启服务，不能复用旧 server
    if (state.get("model") == model_id
            and state.get("gguf") == os.path.abspath(files["gguf"])
            and server_healthy()):
        log(f"llama-server 已在运行（{cfg['name']} {files['quant']}）")
        return
    log(f"启动 llama-server（{cfg['name']} {files['quant']}）...")
    # 显存预检：不足时模型会回退到 CPU，速度骤降，这里明确提示
    _free = free_vram_gb()
    try:
        _need = os.path.getsize(files["gguf"]) / (1024.0 ** 3) + 1.5
    except Exception:
        _need = 9.5
    if _free is not None and _free < _need:
        log(f"⚠️ 空闲显存仅 {_free:.1f}GB，而 {cfg['name']} {files['quant']} 约需 {_need:.1f}GB："
            f"模型可能回退到 CPU，速度会大幅下降。若正在运行 ComfyUI 等占用显存的程序，"
            f"请先关闭它们，再点「停止任务」后重新开始分析。")
    exe = os.path.join(LLAMA_DIR, "llama-server.exe" if _WIN else "llama-server")
    if not os.path.exists(exe):
        raise RuntimeError(
            f"未找到推理引擎可执行文件：{exe}\n"
            f"  请先运行一键安装（python setup.py）下载 llama.cpp，"
            f"或按 docs/llama-cpp.md 手动放置后重试。")
    kill_servers()
    args = [exe, "-m", files["gguf"], "--mmproj", files["mmproj"],
            "-ngl", "99", "-c", "16384", "-np", "2",
            "--port", "8080", "--host", "127.0.0.1", "-fa", "on"]
    os.makedirs(os.path.dirname(SERVER_LOG), exist_ok=True)
    logf = open(SERVER_LOG, "w", encoding="utf-8")
    proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT,
                            creationflags=NEWGROUP)

    def _server_log_tail(n=8):
        try:
            with open(SERVER_LOG, "r", encoding="utf-8", errors="ignore") as f:
                return "".join(f.readlines()[-n:]).strip()
        except Exception:
            return ""

    for i in range(150):
        time.sleep(2)
        # 进程已退出（多为显存不足 / 模型异常）时立即报错，而不是干等
        if proc.poll() is not None:
            tail = _server_log_tail()
            raise RuntimeError(
                f"llama-server 启动后立即退出（code={proc.returncode}）。"
                f"常见原因：显存不足（ComfyUI 等程序占用 GPU）或模型文件异常。"
                f"请关闭占用显存的程序后重试。server.log 尾部：{tail}")
        if server_healthy():
            with open(SERVER_STATE, "w", encoding="utf-8") as f:
                json.dump({"model": model_id, "gguf": os.path.abspath(files["gguf"]),
                           "pid": proc.pid,
                           "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, f)
            log(f"llama-server 就绪（{cfg['name']} {files['quant']}, {i*2}s）")
            return
        if i and i % 15 == 0:
            log(f"  llama-server 加载中… 已等待 {i*2}s（如长时间无进展，可能是显存不足）")
    raise RuntimeError("llama-server 启动超时（300s 内未就绪）。"
                       "请检查显存是否被其他程序（如 ComfyUI）占用，或先关闭它们再试。")


# ---------------- 模型查询 ----------------
def query_model(b64_img, prompt, system, max_tokens=1024, mime="image/png", retries=3):
    payload = {
        "model": "local",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_img}"}},
                {"type": "text", "text": prompt},
            ]},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                SERVER + "/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"query failed after {retries} tries: {last_err}")


def parse_score(text):
    """从模型输出解析 0-100 整数（任意整数，不限于 5 的倍数）。"""
    if not text:
        return None
    patterns = [
        r"[Ss]core\s*[:：]?\s*(\d{1,3}(?:\.\d+)?)\s*(?:/\s*100)?",
        r"(\d{1,3}(?:\.\d+)?)\s*/\s*100",
        r"(?:评分|得分|分数)\s*[:：]?\s*(\d{1,3}(?:\.\d+)?)",
        r"(\d{1,3}(?:\.\d+)?)\s*分(?!钟)",
    ]
    best = None
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                v = float(m.group(1))
            except Exception:
                continue
            if 0 <= v <= 100:
                iv = int(round(v))
                if best is None or abs(iv - v) < abs(best - v):
                    best = iv
    return best


def parse_letter_score(text):
    m = re.search(r"\b([acde][a-z])\b", text or "")
    if m and m.group(1) in LETTER_MAP:
        return LETTER_MAP[m.group(1)]
    return None


def score_dimension(b64, cn, en, cfg):
    """返回 (分数:int|None, 原文:str)。优先数值，ArtiMuse 数值失败时退回字母映射。"""
    prompt = (f"请从「{cn}」维度评价这张图片的美学质量。请给出 0 到 100 之间"
              f"的整数分数（100 为最佳，可以是任意整数，不必是 5 的倍数，例如 73、88、91）。"
              "第一行格式为：Score: <数字>/100。然后用 3-5 句中文详细阐述你的评价。"
              "切记：全部回答使用简体中文。")
    text = query_model(b64, prompt, cfg["system"], max_tokens=1024)
    sc = parse_score(text)
    if sc is None and cfg["score_mode"] == "letter":
        sc = parse_letter_score(text)
    return sc, text


OPT_DIMS = ["构图与设计", "视觉元素与结构", "技术执行", "原创性与创意",
            "主题与表达", "情感与观众反应", "整体格式塔", "综合评价"]


def parse_optimization(text):
    if not text:
        return {}
    result = {}
    for d in OPT_DIMS:
        m = re.search(rf"维度名\s*[:：]\s*{re.escape(d)}\s*\n+\s*建议内容\s*[:：]\s*(.+?)"
                      rf"(?=\n\s*\n|\n\s*维度名\s*[:：]|$)", text, re.S)
        if m:
            result[d] = " ".join(m.group(1).split())
    if len(result) < 4:
        result = {}
        for d in OPT_DIMS:
            m = re.search(rf"(?:^|\n)\s*(?:\d+\s*[.、]\s*)?{re.escape(d)}\s*[:：]\s*(.+?)(?=\n|$)", text)
            if m:
                result[d] = m.group(1).strip()
    return result if len(result) >= 4 else {}


# ---------------- 作业管理 ----------------
def set_job_name(jid, new_name):
    """重命名作业并把其输出目录一并迁移到新名字的子文件夹。"""
    job = find_job(jid)
    if not job:
        return False
    old_dir = job_dir(jid)
    job["name"] = new_name
    new_dir = job_dir(jid)
    if old_dir != new_dir and os.path.isdir(old_dir):
        try:
            shutil.move(old_dir, new_dir)
            log(f"已重命名迁移目录 -> {new_dir}")
        except Exception as e:
            log(f"重命名迁移失败: {e}")
    touch_job(job)
    return True


def create_job(name, folder, models):
    reg = load_registry()
    jid = "job-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    job = {"id": jid, "name": name or jid, "folder": folder,
           "models": list(models), "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "status": "created",
           "counts": {m: 0 for m in models}}
    reg["jobs"].append(job)
    save_registry(reg)
    d = job_dir(jid)
    os.makedirs(os.path.join(d, "thumbs"), exist_ok=True)
    return job


def touch_job(job):
    reg = load_registry()
    for j in reg["jobs"]:
        if j["id"] == job["id"]:
            j.update({"updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      "folder": job["folder"], "name": job["name"], "models": job["models"],
                      "status": job["status"], "counts": job["counts"]})
    save_registry(reg)


# ---------------- 结论读写 ----------------
def load_conclusions(jid, model_id):
    p = job_conclusions_path(jid, model_id)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"meta": {}, "records": []}


def build_meta(cfg, old_meta, folder):
    meta = {"model": cfg["name"], "model_id": cfg["id"], "language": "zh-CN",
            "quant": resolve_model_files(cfg["id"])["quant"],
            "folder": folder,
            "dimensions": [{"key": k, "en": en, "cn": cn}
                           for k, en, cn in zip(cfg["aspect_keys"], cfg["aspect_en"], cfg["aspect_cn"])],
            "score_scale": "0-100（整数）",
            "final_score_rule": "各维度得分的算术平均（必落在维度得分区间内）"}
    meta["created"] = old_meta.get("created", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    meta["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return meta


def merge_records(old, new):
    by_id = {r["id"]: r for r in old}
    for r in new:
        by_id[r["id"]] = r
    return sorted(by_id.values(), key=lambda r: (r.get("file_datetime", ""), r["id"]))


def save_conclusions(jid, cfg, records, old_meta, folder):
    with open(job_conclusions_path(jid, cfg["id"]), "w", encoding="utf-8") as f:
        json.dump({"meta": build_meta(cfg, old_meta, folder), "records": records},
                  f, ensure_ascii=False, indent=1)


# ---------------- 单张图处理 ----------------
def process_image(source_path, rec_id, cfg, jid, only_supplement=False, existing=None):
    from PIL import Image
    fname = os.path.basename(source_path)
    ext = os.path.splitext(fname)[1].lower()
    mime = MIME.get(ext, "image/png")
    with open(source_path, "rb") as f:
        raw = f.read()
    b64 = base64.b64encode(raw).decode("ascii")
    system = cfg["system"]

    if only_supplement and existing:
        rec = dict(existing)
        log(f"补充评价: {fname} -> {rec['id']}")
        if not rec.get("description"):
            rec["description"] = query_model(b64, DESCRIPTION_PROMPT, system, max_tokens=800, mime=mime).strip()
            log(f"  [{fname}] 画面描述完成 ({len(rec['description'])} 字)")
        if not rec.get("optimization"):
            if rec.get("optimization_text"):
                rec["optimization"] = parse_optimization(rec["optimization_text"])
                log(f"  [{fname}] 优化方向本地重解析 ({len(rec['optimization'])} 条)")
            if not rec.get("optimization"):
                opt_text = query_model(b64, OPTIMIZATION_PROMPT, system, max_tokens=1200, mime=mime).strip()
                rec["optimization_text"] = opt_text
                rec["optimization"] = parse_optimization(opt_text)
                log(f"  [{fname}] 优化方向完成 ({len(rec['optimization'])} 条)")
        return rec

    log(f"开始分析: {fname}")
    scores, evaluations = {}, {}
    for key, en, cn in zip(cfg["aspect_keys"], cfg["aspect_en"], cfg["aspect_cn"]):
        sc, text = score_dimension(b64, cn, en, cfg)
        scores[key] = sc
        evaluations[key] = text.strip()
        log(f"  [{fname}] {cn}: {sc if sc is not None else 'N/A'}")

    # 综合得分 = 各维度得分的算术平均（逻辑自洽：必落在维度区间内）
    valid = [v for v in scores.values() if isinstance(v, (int, float))]
    final = round(sum(valid) / len(valid)) if valid else None

    description = query_model(b64, DESCRIPTION_PROMPT, system, max_tokens=800, mime=mime).strip()
    log(f"  [{fname}] 画面描述完成 ({len(description)} 字)")
    opt_text = query_model(b64, OPTIMIZATION_PROMPT, system, max_tokens=1200, mime=mime).strip()
    optimization = parse_optimization(opt_text)
    log(f"  [{fname}] 优化方向完成 ({len(optimization)} 条)")

    im = Image.open(source_path)
    im.load()
    w, h = im.size
    im_rgb = im.convert("RGB")
    mtime = datetime.fromtimestamp(os.path.getmtime(source_path))
    date_str = mtime.strftime("%Y-%m-%d")

    # 缩略图写入作业目录（不改动源文件夹）
    os.makedirs(job_thumbs_dir(jid), exist_ok=True)
    thumb_path = os.path.join(job_thumbs_dir(jid), rec_id + ".jpg")
    try:
        thumb = im_rgb.copy()
        thumb.thumbnail((400, 400))
        thumb.save(thumb_path, "JPEG", quality=72)
    except Exception as e:
        log(f"  [{fname}] 缩略图生成失败: {e}")

    rec = {
        "id": rec_id,
        "original_name": fname,
        "source_path": os.path.abspath(source_path),
        "file_date": date_str,
        "file_datetime": mtime.strftime("%Y-%m-%d %H:%M:%S"),
        "analysed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "image": {"width": w, "height": h, "size_bytes": len(raw)},
        "scores": scores,
        "final_score": final,
        "evaluations": evaluations,
        "comprehensive": evaluations.get("comprehensive_evaluation", ""),
        "description": description,
        "optimization_text": opt_text,
        "optimization": optimization,
    }
    log(f"完成: {fname} -> {rec_id}  综合得分 {final}")
    return rec


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=list(MODELS.keys()))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--supplement", action="store_true", help="仅补充缺失的画面描述/优化方向")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--no-server", action="store_true", help="不自动管理 llama-server")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--new-job", default=None, help="新建作业：作业名")
    ap.add_argument("--create-only", action="store_true",
                    help="仅新建作业并退出，不开始分析（启动台「新建作业」用；"
                         "分析由用户点「开始分析」触发）")
    ap.add_argument("--folder", default=None, help="图片目录（源文件不会移动或修改）")
    ap.add_argument("--job", default=None, help="继续 / 操作指定作业 id")
    ap.add_argument("--rename", default=None, help="重命名当前作业（需配合 --job）")
    ap.add_argument("--rename-only", default=None, help="仅重命名作业并迁移目录（不启动分析）")
    args = ap.parse_args()

    if args.list_models:
        out = []
        for mid, cfg in MODELS.items():
            f = resolve_model_files(mid)
            out.append({"id": mid, "name": cfg["name"], "desc": cfg["desc"],
                        "quant": f["quant"], "mmproj_name": f["mmproj_name"],
                        "available": bool(f["gguf"] and f["mmproj"]),
                        "score_mode": cfg["score_mode"], "default": mid == DEFAULT_MODEL})
        print(json.dumps({"models": out, "default": DEFAULT_MODEL}, ensure_ascii=False))
        return

    if args.rename_only:
        if not args.job:
            log("!! --rename-only 需要配合 --job")
            sys.exit(2)
        ok = set_job_name(args.job, args.rename_only)
        print(json.dumps({"ok": bool(ok)}, ensure_ascii=False))
        return

    os.makedirs(WORK_ROOT, exist_ok=True)

    # 新建作业
    if args.new_job:
        if not args.folder:
            log("!! 新建作业需要 --folder 指定图片目录")
            sys.exit(2)
        job = create_job(args.new_job, os.path.abspath(args.folder), [args.model])
        print(json.dumps({"ok": True, "id": job["id"], "name": job["name"],
                          "folder": job["folder"]}, ensure_ascii=False))
        if args.create_only:
            return  # 仅创建：启动台新建作业后由用户点「开始分析」触发分析
        args.job = job["id"]  # 新建后立即开始分析

    # 选择作业
    job = None
    if args.job:
        job = find_job(args.job)
        if not job:
            log(f"!! 找不到作业: {args.job}")
            sys.exit(2)
        if args.rename:
            set_job_name(args.job, args.rename)
            job = find_job(args.job)
        if args.folder:
            job["folder"] = os.path.abspath(args.folder)
        if args.model not in job["models"]:
            job["models"].append(args.model)
        job["status"] = "running"
        touch_job(job)
    elif args.folder and not args.new_job:
        job = create_job("新建作业", os.path.abspath(args.folder), [args.model])
        args.job = job["id"]

    if job is None:
        log("!! 未指定作业或图片目录（请用 --job <id> 或 --new-job <名> --folder <目录>）")
        sys.exit(2)

    cfg = MODELS[args.model]
    files = resolve_model_files(args.model)
    if not files["gguf"] or not files["mmproj"]:
        d = model_dir(args.model)
        stem = MODEL_STEMS[args.model]["stem"]
        log(f"!! 模型文件缺失：{d} 下未找到 {stem}-<档位>.gguf 或 mmproj-*.gguf")
        log("   请先运行 setup.py 安装模型；若已通过其他途径取得文件，")
        log("   按 docs/models.md 的命名规则放入即可（任意量化档位均可识别）")
        sys.exit(2)

    if not os.path.isdir(job["folder"]):
        log(f"!! 图片目录不存在: {job['folder']}")
        sys.exit(2)

    if not args.no_server:
        ensure_server(args.model)

    data = load_conclusions(job["id"], args.model)
    records = data.get("records", [])
    old_meta = data.get("meta", {})
    analyzed_paths = {os.path.normcase(os.path.abspath(r["source_path"]))
                      for r in records if r.get("source_path")}
    analyzed_names = {r.get("original_name") for r in records if not r.get("source_path")}

    # 补充模式
    if args.supplement:
        todo = [r for r in records if not r.get("description") or not r.get("optimization")]
        log(f"补充模式：{len(todo)}/{len(records)} 条记录需要补充")
        if not todo:
            log("无需补充")
            if not args.no_report:
                gen_report(job["id"])
            return
        results, errors = [], []
        idx = [0]
        lock = threading.Lock()

        def sup_worker():
            while True:
                with lock:
                    if idx[0] >= len(todo):
                        return
                    rec = todo[idx[0]]
                    idx[0] += 1
                sp = rec.get("source_path") or os.path.join(job["folder"], rec["original_name"])
                if not os.path.exists(sp):
                    log(f"!! 源文件缺失，跳过补充: {rec['original_name']}")
                    errors.append(rec["id"])
                    continue
                try:
                    new_rec = process_image(sp, rec["id"], cfg, job["id"],
                                            only_supplement=True, existing=rec)
                    with lock:
                        results.append(new_rec)
                        save_conclusions(job["id"], cfg, merge_records(records, results),
                                        old_meta, job["folder"])
                except Exception as e:
                    log(f"!! 补充失败 {rec['id']}: {e}")
                    errors.append(rec["id"])

        threads = [threading.Thread(target=sup_worker) for _ in range(min(args.workers, 2))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        job["status"] = "done"
        job["counts"][args.model] = len(merge_records(records, results))
        touch_job(job)
        log(f"补充完成 {len(results)} 条，失败 {len(errors)} 条")
        if not args.no_report:
            gen_report(job["id"])
        return

    # 正式分析：递归扫描所选目录及其所有子文件夹中的图片（支持嵌套目录）
    all_files = []
    for root_dir, _subdirs, files in os.walk(job["folder"]):
        for fn in files:
            if fn.lower().endswith(IMAGE_EXTS):
                all_files.append(os.path.join(root_dir, fn))
    new_files = [p for p in all_files
                 if os.path.normcase(os.path.abspath(p)) not in analyzed_paths
                 and os.path.basename(p) not in analyzed_names]
    new_files.sort(key=lambda p: (os.path.getmtime(p), os.path.basename(p)))
    if args.limit:
        new_files = new_files[:args.limit]

    if not new_files:
        log("没有新文件需要分析")
        if not args.no_report:
            gen_report(job["id"])
        return

    log(f"[{cfg['name']}] 作业「{job['name']}」待分析 {len(new_files)} 个文件（共 {len(records)} 已存在）")

    existing_ids = {r["id"] for r in records}
    id_map = {}
    for path in new_files:
        stem = os.path.splitext(os.path.basename(path))[0]
        rid = stem
        i = 2
        while rid in existing_ids:
            rid = f"{stem}_{i}"
            i += 1
        existing_ids.add(rid)
        id_map[path] = rid

    results, errors = [], []
    idx = [0]
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                if idx[0] >= len(new_files):
                    return
                path = new_files[idx[0]]
                idx[0] += 1
            try:
                rec = process_image(path, id_map[path], cfg, job["id"])
                with lock:
                    results.append(rec)
                    save_conclusions(job["id"], cfg, merge_records(records, results),
                                    old_meta, job["folder"])
            except Exception as e:
                log(f"!! 失败 {os.path.basename(path)}: {e}")
                errors.append((path, str(e)))

    threads = [threading.Thread(target=worker) for _ in range(min(args.workers, 2))]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dt = time.time() - t0

    total = len(merge_records(records, results))
    job["status"] = "done"
    job["counts"][args.model] = total
    job["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    touch_job(job)

    log(f"[{cfg['name']}] 本轮完成 {len(results)} 张，失败 {len(errors)} 张，耗时 {dt/60:.1f} 分钟")
    for p, e in errors:
        log(f"  失败文件: {p} ({e})")
    if not args.no_report:
        gen_report(job["id"])
    log("全部结束")


def gen_report(job_id=None):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_report.py")
    if os.path.exists(script):
        py = sys.executable if sys.executable and os.path.exists(sys.executable) else "python"
        cmd = [py, script]
        if job_id:
            cmd += ["--job", job_id]
        subprocess.run(cmd, check=False, creationflags=NOWIN)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(130)
    except RuntimeError as e:
        # 已知业务错误（模型文件缺失 / llama-server 启动失败等）：
        # 异常消息本身已带处理建议，直接打印一行，不喷 traceback 吓到用户
        print(f"\n[ERROR] {e}")
        sys.exit(1)
