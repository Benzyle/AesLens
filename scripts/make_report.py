#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取某个作业的多模型结论与缩略图，生成自包含的单文件 HTML 报告。

产物：work/<作业名>/report.html（内嵌缩略图与 Chart.js，可离线打开、直接分享）。

用法：python make_report.py --job <作业 id>
"""
import argparse
import base64
import html
import json
import os
import re
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CHARTJS, job_dir  # noqa: E402

# 模型 id → 报告里显示的名字（顺序即顶部切换按钮的顺序，默认模型排最前）
MODEL_FILES = [
    ("unipercept_std", "UniPercept"),
    ("unipercept", "UniPercept-abliterated"),
    ("artimuse", "ArtiMuse"),
]

DIMS = [
    ("composition_design", "构图与设计", "Composition & Design"),
    ("visual_elements_structure", "视觉元素与结构", "Visual Elements & Structure"),
    ("technical_execution", "技术执行", "Technical Execution"),
    ("originality_creativity", "原创性与创意", "Originality & Creativity"),
    ("theme_communication", "主题与表达", "Theme & Communication"),
    ("emotion_viewer_response", "情感与观众反应", "Emotion & Viewer Response"),
    ("overall_gestalt", "整体格式塔", "Overall Gestalt"),
    ("comprehensive_evaluation", "综合评价", "Comprehensive Evaluation"),
]
OPT_DIMS = ["构图与设计", "视觉元素与结构", "技术执行", "原创性与创意",
            "主题与表达", "情感与观众反应", "整体格式塔", "综合评价"]


def load_models(job_id):
    models = []
    jdir = job_dir(job_id)
    for mid, name in MODEL_FILES:
        path = os.path.join(jdir, f"{mid}_conclusions.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        records = [r for r in data.get("records", []) if r.get("id")]
        if not records:
            continue
        models.append({"id": mid, "name": name, "meta": data.get("meta", {}),
                       "records": records})
    return models


def load_thumbs(models, job_id):
    """读取缩略图并转 base64 data URI 内嵌进 HTML。"""
    thumbs = {}
    for m in models:
        for rec in m["records"]:
            if rec["id"] in thumbs:
                continue
            p = os.path.join(job_dir(job_id), "thumbs", rec["id"] + ".jpg")
            if os.path.exists(p):
                with open(p, "rb") as f:
                    thumbs[rec["id"]] = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
    return thumbs


# ---------------- NSFW 判定 ----------------
# 依据 booru 类原始文件名里直接带的标签（naked/nipples/pussy/...），命中即判 NSFW。
NSFW_TOKENS = {
    "naked", "nude", "nipples", "nipple", "pussy", "penis", "sex", "uncensored",
    "anus", "cum", "masturbation", "topless", "nopan", "bottomless", "penetration",
    "creampie", "cumshot", "fellatio", "paizuri", "ahegao", "bondage", "bdsm",
}
NSFW_SUBSTR = ("pussy_juice", "panty_pull", "pubic_hair", "no_bra", "breast_grab",
               "uncensored", "penetration", "creampie")


def _name_tokens(name):
    return set(t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t)


def is_nsfw_name(name):
    low = (name or "").lower()
    if any(k in low for k in NSFW_SUBSTR):
        return True
    return bool(_name_tokens(name) & NSFW_TOKENS)


def annotate_nsfw(models, job_id=None, log_fn=None):
    """按文件名标签为每条记录打 nsfw 标记。job_id 仅为兼容调用签名保留。"""
    stats = {"hit": 0, "none": 0, "results": {}}
    for m in models:
        for rec in m["records"]:
            if rec["id"] in stats["results"]:   # 同一作品在多个模型里重复出现，只统计一次
                rec["nsfw"] = stats["results"][rec["id"]]
                continue
            rec["nsfw"] = is_nsfw_name(rec.get("original_name") or rec.get("id") or "")
            stats["results"][rec["id"]] = rec["nsfw"]
            stats["hit" if rec["nsfw"] else "none"] += 1
    if log_fn:
        log_fn(f"NSFW 判定完成（文件名标签）：共 {stats['hit'] + stats['none']} 条，"
               f"判定隐藏 {stats['hit']} 条")
    return stats


CSS = """
:root {
  --bg: #f6f7fb; --card: #ffffff; --ink: #1e2235; --ink2: #5a6072; --line: #e6e8f0;
  --accent: #4f46e5; --accent2: #7c6cf0; --gold: #d97706; --silver: #64748b; --bronze: #b45309;
  --good: #059669; --bad: #dc2626;
  --shadow: 0 1px 3px rgba(30,34,53,.07), 0 4px 14px rgba(30,34,53,.05);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--ink); font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif; font-size: 14px; line-height: 1.6; }
.wrap { max-width: 1200px; margin: 0 auto; padding: 24px 20px 60px; }
header.hero { background: linear-gradient(135deg, #4338ca 0%, #6d28d9 55%, #7c3aed 100%); border-radius: 16px; padding: 28px 30px; color: #fff; box-shadow: var(--shadow); margin-bottom: 18px; }
header.hero h1 { font-size: 24px; letter-spacing: .5px; }
header.hero .sub { margin-top: 8px; font-size: 13px; opacity: .85; }
header.hero .chips { margin-top: 14px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
header.hero .chip { background: rgba(255,255,255,.16); border: 1px solid rgba(255,255,255,.25); border-radius: 999px; padding: 3px 12px; font-size: 12px; }
.model-switch { display: inline-flex; background: rgba(255,255,255,.14); border: 1px solid rgba(255,255,255,.3); border-radius: 999px; padding: 3px; gap: 2px; }
.model-switch button { border: none; background: transparent; color: rgba(255,255,255,.75); border-radius: 999px; padding: 5px 18px; font-size: 13px; cursor: pointer; font-weight: 500; }
.model-switch button.active { background: #fff; color: var(--accent); }
nav.tabs { display: flex; gap: 8px; margin-bottom: 18px; position: sticky; top: 0; z-index: 50; background: var(--bg); padding: 10px 0; }
nav.tabs button { border: 1px solid var(--line); background: var(--card); color: var(--ink2); border-radius: 10px; padding: 8px 18px; font-size: 14px; cursor: pointer; font-weight: 500; }
nav.tabs button.active { background: var(--accent); border-color: var(--accent); color: #fff; }
section { display: none; }
section.active { display: block; animation: fade .25s ease; }
@keyframes fade { from { opacity: 0; transform: translateY(4px);} to { opacity: 1; transform: none;} }
.grid { display: grid; gap: 14px; }
.cards { grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); margin-bottom: 18px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; box-shadow: var(--shadow); }
.card .label { font-size: 12px; color: var(--ink2); }
.card .value { font-size: 26px; font-weight: 700; margin-top: 4px; }
.card .value small { font-size: 13px; font-weight: 500; color: var(--ink2); }
.card .extra { font-size: 12px; color: var(--ink2); margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.panel { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; box-shadow: var(--shadow); margin-bottom: 18px; }
.panel h2 { font-size: 16px; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
.panel h2::before { content: ""; width: 4px; height: 16px; background: var(--accent); border-radius: 2px; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; color: var(--ink2); font-weight: 500; padding: 8px 10px; border-bottom: 2px solid var(--line); white-space: nowrap; }
td { padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: middle; }
tr.clickable { cursor: pointer; }
tr.clickable:hover { background: #f3f4ff; }
.scorebar { height: 6px; border-radius: 3px; background: #eef0f7; overflow: hidden; min-width: 70px; }
.scorebar > div { height: 100%; background: linear-gradient(90deg, var(--accent2), var(--accent)); }
.rank-badge { display: inline-flex; width: 22px; height: 22px; border-radius: 7px; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; background: #eef0f7; color: var(--ink2); }
.rank-badge.r1 { background: #fef3c7; color: var(--gold); }
.rank-badge.r2 { background: #f1f5f9; color: var(--silver); }
.rank-badge.r3 { background: #ffedd5; color: var(--bronze); }
.thumb-strip { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 14px; }
@media (max-width: 700px) { .thumb-strip { grid-template-columns: repeat(2, 1fr); } }
.thumb-cell { position: relative; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: #fff; cursor: pointer; transition: transform .15s ease, box-shadow .15s ease; }
.thumb-cell:hover { transform: translateY(-3px); box-shadow: 0 6px 20px rgba(79,70,229,.18); }
.thumb-cell img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: #f1f2f6; }
.thumb-cell .meta { padding: 7px 9px; }
.thumb-cell .meta .rk { font-size: 11px; font-weight: 700; color: var(--accent); }
.thumb-cell .meta .sc { font-size: 15px; font-weight: 700; }
.thumb-cell .meta .nm { font-size: 11px; color: var(--ink2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.seg { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
.seg button { border: 1px solid var(--line); background: #fff; color: var(--ink2); border-radius: 999px; padding: 5px 14px; font-size: 12.5px; cursor: pointer; }
.seg button.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.sort-sel { border: 1px solid var(--line); background: #fff; color: var(--ink2); border-radius: 10px; padding: 7px 12px; font-size: 13px; cursor: pointer; }
.sort-wrap { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; font-size: 12px; color: var(--ink2); flex-wrap: wrap; }
.chart-box { position: relative; height: 380px; }
.chart-box.tall { height: 460px; }
.hist-meta { font-size: 12.5px; color: var(--ink2); margin: -4px 0 10px; }
.hist-meta b { color: var(--accent); font-size: 13px; }
.legend-note code { background: #f1f2f6; border-radius: 4px; padding: 0 4px; font-size: 11px; color: #4b5163; }
/* ---------- 横轴切换 ---------- */
.axis-switch { margin: 2px 0 16px; }
.axis-sub { font-size: 11.5px; color: var(--ink2); margin-top: 8px; }
.modal-mask { position: fixed; inset: 0; background: rgba(20,22,40,.55); display: none; align-items: center; justify-content: center; z-index: 100; padding: 24px; }
.modal-mask.open { display: flex; }
.modal { background: #fff; border-radius: 16px; max-width: 880px; width: 100%; max-height: 92vh; overflow: auto; padding: 0; position: relative; }
.modal .close { position: absolute; top: 12px; right: 14px; border: none; background: #f1f2f6; width: 30px; height: 30px; border-radius: 8px; cursor: pointer; font-size: 15px; color: var(--ink2); z-index: 8; }
.modal .mhead { position: sticky; top: 0; z-index: 6; display: flex; align-items: flex-start; gap: 18px; background: #fff; padding: 22px 24px 12px; margin-bottom: 0; border-bottom: 1px solid var(--line); }
.modal .mhead img { flex: 0 0 auto; width: auto; max-width: 220px; max-height: 260px; border-radius: 12px; border: 1px solid var(--line); background: #f1f2f6; object-fit: contain; }
.modal .mhead .info { flex: 1; min-width: 0; }
.modal .mbody { padding: 14px 24px 24px; }
.nav-arrow { position: absolute; top: 50%; transform: translateY(-50%); width: 46px; height: 46px; border-radius: 50%; border: none; background: rgba(255,255,255,.92); color: #4338ca; font-size: 28px; line-height: 1; cursor: pointer; z-index: 102; box-shadow: 0 3px 12px rgba(0,0,0,.22); display: flex; align-items: center; justify-content: center; }
.nav-arrow:hover { background: #fff; }
.nav-arrow.left { left: 14px; }
.nav-arrow.right { right: 14px; }
@media (max-width: 1020px) { .nav-arrow { display: none; } }
.nsfw-toggle { display: inline-flex; align-items: center; gap: 6px; background: rgba(255,255,255,.16); border: 1px solid rgba(255,255,255,.25); border-radius: 999px; padding: 3px 12px; font-size: 12px; cursor: pointer; user-select: none; }
.nsfw-toggle input { cursor: pointer; }
.nsfw-badge { position: absolute; top: 6px; right: 6px; background: rgba(220,38,38,.92); color: #fff; font-size: 10px; font-weight: 700; border-radius: 6px; padding: 1px 6px; letter-spacing: .5px; }
.modal .mhead h3 { font-size: 18px; margin-bottom: 6px; }
.modal .mhead .kv { font-size: 12.5px; color: var(--ink2); margin-bottom: 3px; }
.modal .final-big { font-size: 34px; font-weight: 800; color: var(--accent); }
.mmodel { display: inline-flex; background: #f1f2f6; border-radius: 999px; padding: 3px; gap: 2px; margin-bottom: 14px; }
.mmodel button { border: none; background: transparent; color: var(--ink2); border-radius: 999px; padding: 5px 16px; font-size: 12.5px; cursor: pointer; font-weight: 500; }
.mmodel button.active { background: var(--accent); color: #fff; }
.mmodel button:disabled { opacity: .4; cursor: not-allowed; }
.dim-row { border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; margin-bottom: 10px; }
.dim-row .dh { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px; }
.dim-row .dh .dn { font-weight: 600; }
.dim-row .dh .den { font-size: 11.5px; color: var(--ink2); font-weight: 400; margin-left: 8px; }
.dim-row .dh .ds { font-size: 18px; font-weight: 700; color: var(--accent); }
.dim-row .de { font-size: 13px; color: #3a4055; white-space: pre-wrap; }
.dim-row .de .sc-line { font-weight: 700; color: var(--ink); }
.desc-box { margin-top: 14px; background: #eef2ff; border: 1px solid #c7d2fe; border-radius: 12px; padding: 12px 14px; }
.desc-box .desc-title { font-weight: 700; font-size: 13px; color: #4338ca; margin-bottom: 6px; }
.desc-box .desc-title .desc-en { font-weight: 400; font-size: 11px; color: #8a90a5; margin-left: 6px; }
.desc-box .desc-text { font-size: 13px; color: #3a4055; line-height: 1.7; white-space: pre-wrap; }
.opt-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 700px) { .opt-grid { grid-template-columns: 1fr; } }
.opt-item { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: #fbfbfe; }
.opt-item .on { font-weight: 600; font-size: 13px; margin-bottom: 3px; color: var(--accent); }
.opt-item .ot { font-size: 12.5px; color: #3a4055; }
.legend-note { font-size: 12px; color: var(--ink2); margin-top: 8px; }
.cmp-chip { display: inline-block; border-radius: 999px; padding: 2px 12px; font-size: 12px; font-weight: 600; background: #eef2ff; color: var(--accent); margin-left: 8px; }
footer { text-align: center; color: var(--ink2); font-size: 12px; margin-top: 30px; }
"""

APP_JS = r"""
const DIMS = __DIMS_JSON__;
const FINAL = {key: "final_score", cn: "综合得分", en: "Comprehensive"};
const MODELS = __MODELS_JSON__;
const THUMBS = __THUMBS_JSON__;
const OPT_DIMS = __OPT_DIMS_JSON__;

const $ = (s, el) => (el || document).querySelector(s);
const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function fmt(v, d=1){ return v==null ? "N/A" : Number(v).toFixed(d); }
function thumbOf(id){ return THUMBS[id] || ""; }

let curModel = MODELS.length ? MODELS[0].id : null;
let sortMode = "score_desc";
let hideNsfw = false;
function M(){ return MODELS.find(m => m.id === curModel); }
function recs(){ const m = M(); let arr = m ? m.records.slice() : []; if (hideNsfw) arr = arr.filter(r => !r.nsfw); return arr; }

/* ---- pixiv 投稿 ID 解析 ----
   文件名格式 {id}_{page}-{title}，如 123456789_p0-标题。
   pixiv 的 ID 严格按投稿时间递增，所以比文件系统时间可靠（批量下载会把 mtime 全刷成下载时刻）。 */
function pixivOf(r){
  if (r._pid !== undefined) return r._pid;
  const name = r.original_name || "";
  const id = r.id || "";
  let m = /^(\d+)_p(\d+)/.exec(name) || /^(\d+)_p(\d+)/.exec(id);
  r._pid = m ? { num: parseInt(m[1], 10), page: parseInt(m[2], 10) } : null;
  return r._pid;
}
function byPixiv(a, b){
  const pa = pixivOf(a), pb = pixivOf(b);
  if (!pa && !pb) return 0;
  if (!pa) return 1;              // 非 pixiv 命名的排到最后
  if (!pb) return -1;
  if (pa.num !== pb.num) return pa.num - pb.num;
  return pa.page - pb.page;
}
function byName(a, b){
  return (a.original_name || a.id).localeCompare(b.original_name || b.id, "zh-Hans-CN", {numeric: true, sensitivity: "base"});
}
function applyOrder(arr){
  const A = arr.slice();
  switch (sortMode){
    case "pid_asc":    A.sort(byPixiv); break;
    case "pid_desc":   A.sort((a,b)=> byPixiv(b,a)); break;
    case "name_asc":   A.sort(byName); break;
    case "name_desc":  A.sort((a,b)=> byName(b,a)); break;
    case "score_asc":  A.sort((a,b)=> (a.final_score==null?1e9:a.final_score) - (b.final_score==null?1e9:b.final_score)); break;
    default:           A.sort((a,b)=> (b.final_score==null?-1e9:b.final_score) - (a.final_score==null?-1e9:a.final_score));
  }
  return A;
}
function sortedRecs(){ return applyOrder(recs().filter(r => r.final_score != null)); }
/* 按指定维度 + 当前排序方式排序（修复：切换维度时也遵循升序/降序/时间） */
function orderByMetric(key){
  if (key === "final_score") return sortedRecs();
  const arr = validRecords(key);
  if (sortMode === "score_desc" || sortMode === "score_asc") {
    const asc = sortMode === "score_asc";
    return arr.sort((a,b)=> asc ? metricOf(a,key) - metricOf(b,key) : metricOf(b,key) - metricOf(a,key));
  }
  return applyOrder(arr);
}
function dimDef(key){ if(key==="final_score") return FINAL; const d = DIMS.find(d=>d.key===key); return d || {key, cn:key, en:key}; }
function metricOf(r, key){ return key==="final_score" ? r.final_score : (r.scores ? r.scores[key] : null); }
function validRecords(key){ return recs().filter(r => metricOf(r, key) != null); }

/* ---------- 模型切换 ---------- */
function renderModelSwitch(){
  const box = $("#global-model-switch");
  box.innerHTML = "";
  MODELS.forEach(m => {
    const b = document.createElement("button");
    b.textContent = m.name;
    if (m.id === curModel) b.classList.add("active");
    b.onclick = () => { curModel = m.id; onModelSwitch(); };
    box.appendChild(b);
  });
  const m = M();
  const n = recs().length;
  $("#hero-sub").textContent = m ? `${m.name} · ${n} 张作品 · 8 维度评分与文字点评` : "";
  $("#chip-count").textContent = `已分析 ${n} 张` + (hideNsfw ? "（已隐藏 NSFW）" : "");
  $("#chip-model").textContent = `当前模型：${m ? m.name : "-"}`;
}
function refreshDataSections(){
  renderOverview();
  rankBuilt = false; trendBuilt = false;
  if ($("#sec-rank").classList.contains("active")) ensureRankBuilt();
  if ($("#sec-trend").classList.contains("active")) ensureTrendBuilt();
}
function onModelSwitch(){
  renderModelSwitch();
  refreshDataSections();
}

/* ---------- tabs ---------- */
function initTabs(){
  $$("nav.tabs button").forEach(b => b.addEventListener("click", () => {
    $$("nav.tabs button").forEach(x => x.classList.remove("active"));
    $$("section").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    $("#sec-" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "rank") ensureRankBuilt();
    if (b.dataset.tab === "trend") ensureTrendBuilt();
  }));
}

/* ---------- overview ---------- */
let histChart = null;
const HIST_BINS = [
  [0,60,  "0-59"],
  [60,65, "60-64"],
  [65,70, "65-69"],
  [70,75, "70-74"],
  [75,80, "75-79"],
  [80,85, "80-84"],
  [85,90, "85-89"],
  [90,95, "90-94"],
  [95,100.01, "95-100"]
];
function histColors(n){
  // 由低到高分档着色：低分偏冷灰 → 高分偏品牌紫
  const palette = ["#cbd0e0","#b7bfd6","#a3b0cc","#8f9fc4","#7d8bbd","#6c76bf","#5f5fc4","#4f46e5","#4338ca"];
  return palette.slice(0, n);
}
function renderHistogram(finals){
  const counts = HIST_BINS.map(() => 0);
  finals.forEach(r => {
    const v = +r.final_score;
    for (let i = 0; i < HIST_BINS.length; i++) {
      if (v >= HIST_BINS[i][0] && v < HIST_BINS[i][1]) { counts[i]++; break; }
    }
  });
  const n = finals.length;
  const maxC = Math.max.apply(null, counts.concat([1]));
  // 众数区间（用于摘要文字）
  let modeIdx = 0;
  counts.forEach((c, i) => { if (c > counts[modeIdx]) modeIdx = i; });
  const modeTxt = n ? `${HIST_BINS[modeIdx][2]} 分（${counts[modeIdx]} 张，${(counts[modeIdx]/n*100).toFixed(0)}%）` : "N/A";
  const el = $("#hist-mode");
  if (el) el.textContent = modeTxt;

  const labels = HIST_BINS.map(b => b[2]);
  const colors = histColors(HIST_BINS.length);
  const pct = counts.map(c => n ? +(c / n * 100).toFixed(1) : 0);

  if (histChart) histChart.destroy();
  const canvas = $("#hist");
  if (!canvas) return;
  histChart = new Chart(canvas, {
    type: "bar",
    data: { labels: labels, datasets: [{
      label: "作品数",
      data: counts,
      backgroundColor: colors.map(c => c + "cc"),
      hoverBackgroundColor: colors,
      borderColor: colors,
      borderWidth: 1,
      borderRadius: 6,
      barPercentage: 0.88,
      categoryPercentage: 0.92
    }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      layout: { padding: { top: 6 } },
      scales: {
        x: { grid: { display: false }, ticks: { color: "#3a4055", font: { size: 12 } },
             title: { display: true, text: "综合得分区间", color: "#8a90a5", font: { size: 11 } } },
        y: { beginAtZero: true, suggestedMax: Math.max(1, Math.ceil(maxC * 1.18)),
             ticks: { precision: 0, color: "#8a90a5", font: { size: 11 } },
             grid: { color: "#eef0f6" },
             title: { display: true, text: "作品数（张）", color: "#8a90a5", font: { size: 11 } } }
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: {
          label: ctx => ` ${ctx.parsed.y} 张（占 ${pct[ctx.dataIndex]}%）`
        } },
        datalabels: false
      },
      animation: { duration: 500 }
    },
    plugins: [{
      id: "barValueLabels",
      afterDatasetsDraw(chart){
        const ctx = chart.ctx, meta = chart.getDatasetMeta(0);
        ctx.save();
        ctx.font = "600 11px -apple-system, 'Segoe UI', sans-serif";
        ctx.fillStyle = "#4b5163";
        ctx.textAlign = "center";
        meta.data.forEach((bar, i) => {
          const v = counts[i];
          if (!v) return;
          ctx.fillText(v, bar.x, bar.y - 6);
        });
        ctx.restore();
      }
    }]
  });
}
function renderOverview(){
  const finals = validRecords("final_score");
  const total = recs().length;
  const avgFinal = finals.length ? finals.reduce((s,r)=>s+r.final_score,0)/finals.length : null;
  const best = finals.slice().sort((a,b)=>b.final_score-a.final_score)[0];
  const worst = finals.slice().sort((a,b)=>a.final_score-b.final_score)[0];
  $("#ov-total").innerHTML = total + '<small> 张</small>';
  $("#ov-avg").innerHTML = fmt(avgFinal);
  $("#ov-best").innerHTML = fmt(best && best.final_score) + '<small> ' + esc(best ? best.id : "") + '</small>';
  $("#ov-worst").innerHTML = fmt(worst && worst.final_score) + '<small> ' + esc(worst ? worst.id : "") + '</small>';

  renderHistogram(finals);

  const rows = DIMS.map(d => {
    const v = validRecords(d.key).sort((a,b)=>metricOf(b,d.key)-metricOf(a,d.key));
    if (!v.length) return "";
    const avg = v.reduce((s,r)=>s+metricOf(r,d.key),0)/v.length;
    const max = v[0], min = v[v.length-1];
    return `<tr class="clickable" data-id="${esc(max.id)}">
      <td><b>${esc(d.cn)}</b><br><span style="font-size:11px;color:#8a90a5">${esc(d.en)}</span></td>
      <td><b style="font-size:15px">${fmt(avg)}</b></td>
      <td>${fmt(metricOf(max,d.key))} <span class="legend-note">${esc(max.id)}</span></td>
      <td>${fmt(metricOf(min,d.key))} <span class="legend-note">${esc(min.id)}</span></td>
      <td><div class="scorebar"><div style="width:${avg}%"></div></div></td></tr>`;
  }).join("");
  $("#dim-table tbody").innerHTML = rows;
  $$("#dim-table tr.clickable").forEach(tr => tr.addEventListener("click", () => openModal(tr.dataset.id)));
}

/* ---------- rankings ---------- */
let rankBuilt = false, rankMetric = "final_score";
function buildRanking(){
  const seg = $("#rank-seg");
  const metrics = [{key:"final_score", cn:"综合得分"}].concat(DIMS);
  seg.innerHTML = "";
  metrics.forEach(m => {
    if (m.key !== "final_score" && !recs().some(r => metricOf(r, m.key) != null)) return;
    const b = document.createElement("button");
    b.textContent = m.cn;
    b.dataset.key = m.key;
    if (m.key === rankMetric) b.classList.add("active");
    b.addEventListener("click", () => {
      rankMetric = m.key;
      $$("#rank-seg button").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      renderRanking();
    });
    seg.appendChild(b);
  });
  renderRanking();
  rankBuilt = true;
}
function ensureRankBuilt(){ if(!rankBuilt) buildRanking(); }
const SORT_LABEL = {
  score_desc: "得分高→低", score_asc: "得分低→高",
  pid_desc: "ID 新→旧", pid_asc: "ID 旧→新",
  name_desc: "文件名 Z→A", name_asc: "文件名 A→Z"
};
const SCORE_SORTS = ["score_desc", "score_asc"];
function renderRanking(){
  const def = dimDef(rankMetric);
  const sorted = orderByMetric(rankMetric);
  const orderName = SORT_LABEL[sortMode] || sortMode;
  const isScoreSort = SCORE_SORTS.indexOf(sortMode) >= 0;
  const metricName = rankMetric === "final_score" ? "综合得分" : def.cn;
  // 只有按得分排序时才叫「排行」，其余排序方式本质是浏览列表
  const headName = isScoreSort ? `${metricName} 排行` : `${metricName} · ${orderName}`;
  $("#rank-title").textContent = `${M().name} · ${headName}（共 ${sorted.length} 张）`;
  const sel = $("#sort-sel"); if (sel) sel.value = sortMode;
  // TOP 横条只在按得分排序时有意义
  const strip = $("#rank-top5");
  if (strip) strip.style.display = isScoreSort ? "" : "none";
  const top5 = isScoreSort ? sorted.slice(0,5) : [];
  if (strip) {
    strip.innerHTML = top5.map((r,i) => `
    <div class="thumb-cell" data-id="${esc(r.id)}">
      ${r.nsfw?'<span class="nsfw-badge">18+</span>':''}
      <img src="${thumbOf(r.id)}" alt="">
      <div class="meta">
        <div class="rk">TOP ${i+1}</div>
        <div class="sc">${fmt(metricOf(r,rankMetric))}</div>
        <div class="nm" title="${esc(r.original_name)}">${esc(r.id)}</div>
      </div>
    </div>`).join("");
    $$("#rank-top5 .thumb-cell").forEach(c => c.addEventListener("click", () => openModal(c.dataset.id)));
  }
  // 序号列：按得分排序时显示名次徽章，其他排序方式只显示流水号
  $("#rank-table tbody").innerHTML = sorted.map((r,i) => {
    const idxCell = isScoreSort
      ? `<span class="rank-badge ${i<3?"r"+(i+1):""}">${i+1}</span>`
      : `<span style="color:#8a90a5;font-size:12px">${i+1}</span>`;
    return `
    <tr class="clickable" data-id="${esc(r.id)}">
      <td>${idxCell}</td>
      <td><img src="${thumbOf(r.id)}" style="width:44px;height:44px;object-fit:cover;border-radius:8px;border:1px solid var(--line)"></td>
      <td><b>${esc(r.id)}</b>${r.nsfw?' <span style="background:#dc2626;color:#fff;font-size:10px;border-radius:6px;padding:1px 5px">18+</span>':''}<br><span style="font-size:11px;color:#8a90a5">${esc(r.original_name)}</span></td>
      <td>${esc(r.file_date)}</td>
      <td><b style="font-size:15px;color:var(--accent)">${fmt(metricOf(r,rankMetric))}</b></td>
      <td style="min-width:110px"><div class="scorebar"><div style="width:${Math.max(0,Math.min(100,metricOf(r,rankMetric)||0))}%"></div></div></td>
    </tr>`;
  }).join("");
  $$("#rank-table tbody tr").forEach(tr => tr.addEventListener("click", () => openModal(tr.dataset.id)));
}

/* ---------- trends ---------- */
let trendBuilt = false, trendMetric = "final_score", trendChart = null;
let trendAxis = "time";   // time | pid
const TREND_AXES = [
  {key:"time", cn:"得分–时间"},
  {key:"pid",  cn:"得分–ID"}
];
function buildTrend(){
  // 横轴切换（分段按钮）
  const axisSeg = $("#axis-seg");
  if (axisSeg) {
    axisSeg.innerHTML = "";
    TREND_AXES.forEach(a => {
      const b = document.createElement("button");
      b.textContent = a.cn;
      if (a.key === trendAxis) b.classList.add("active");
      b.addEventListener("click", () => {
        trendAxis = a.key;
        $$("#axis-seg button").forEach(x => x.classList.remove("active"));
        b.classList.add("active");
        syncAxisLabels();
        renderTrend();
      });
      axisSeg.appendChild(b);
    });
  }
  syncAxisLabels();
  const seg = $("#trend-seg");
  const metrics = [{key:"final_score", cn:"综合得分"}].concat(DIMS);
  seg.innerHTML = "";
  metrics.forEach(m => {
    if (m.key !== "final_score" && !recs().some(r => metricOf(r, m.key) != null)) return;
    const b = document.createElement("button");
    b.textContent = m.cn;
    if (m.key === trendMetric) b.classList.add("active");
    b.addEventListener("click", () => {
      trendMetric = m.key;
      $$("#trend-seg button").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      renderTrend();
    });
    seg.appendChild(b);
  });
  renderTrend();
  trendBuilt = true;
}
/* 同步横轴下方说明文字 */
function syncAxisLabels(){
  const sub = document.getElementById("axis-sub");
  const onPid = trendAxis === "pid";
  if (sub) sub.textContent = onPid
    ? "当前：按 pixiv 投稿 ID（ID 越大越新），更能反映真实创作顺序"
    : "当前：按文件修改时间；批量下载可能使时间失真";
}
function ensureTrendBuilt(){ if(!trendBuilt) buildTrend(); }

/* 把有序列表切成若干段求平均，用于 ID/时间横轴下的「同期平均」 */
function segmentAverages(list, vals, segCount){
  const n = list.length;
  if (!n) return [];
  const size = Math.max(1, Math.ceil(n / segCount));
  const out = new Array(n);
  for (let s = 0; s < n; s += size){
    const end = Math.min(n, s + size);
    let sum = 0, cnt = 0;
    for (let i = s; i < end; i++){ if (vals[i] != null){ sum += vals[i]; cnt++; } }
    const avg = cnt ? +(sum / cnt).toFixed(1) : null;
    for (let i = s; i < end; i++) out[i] = avg;
  }
  return out;
}
function renderTrend(){
  const def = dimDef(trendMetric);
  const usePid = trendAxis === "pid";
  $("#trend-title").textContent = `${M().name} · ${def.cn}${usePid ? "随投稿 ID 变化" : "随时间变化"}`;

  let list = recs().filter(r => metricOf(r, trendMetric) != null);
  let labels, avgData, note;

  if (usePid) {
    list = list.filter(r => pixivOf(r) != null).sort(byPixiv);   // 按投稿 ID 升序
    const pids = list.map(r => pixivOf(r).num);
    labels = list.map(r => String(pixivOf(r).num));
    const vals0 = list.map(r => metricOf(r, trendMetric));
    avgData = segmentAverages(list, vals0, 12);
    note = "横轴为 pixiv 投稿 ID（严格按投稿时间递增，比文件时间可靠）；ID 越大越新。";
  } else {
    list = list.slice().sort((a,b)=> (a.file_datetime||"") < (b.file_datetime||"") ? -1 : 1);
    labels = list.map(r => (r.file_date||"").slice(5));
    const byDate = {};
    list.forEach(r => { (byDate[r.file_date] = byDate[r.file_date] || []).push(metricOf(r, trendMetric)); });
    const avgByDate = {};
    Object.keys(byDate).forEach(d => { avgByDate[d] = +(byDate[d].reduce((a,b)=>a+b,0)/byDate[d].length).toFixed(1); });
    avgData = list.map(r => avgByDate[r.file_date]);
    note = "横轴为文件修改时间（按时间排序，同一天的作品共享平均分）。";
  }
  const vals = list.map(r => metricOf(r, trendMetric));
  const avgLabel = usePid ? "分段平均" : "同期平均";
  const el = $("#trend-note"); if (el) el.textContent = note;

  const cfg = {
    type: "line",
    data: { labels, datasets: [
      { label: "单张得分", data: vals, borderColor: "#4f46e5", backgroundColor: "rgba(79,70,229,.08)",
        borderWidth: 2, tension: .25, pointRadius: list.length > 120 ? 0 : 3, pointHoverRadius: 6, fill: true },
      { label: avgLabel, data: avgData, borderColor: "#d97706", borderDash: [6,5], borderWidth: 2,
        tension: .3, pointRadius: 0, fill: false }
    ]},
    options: { responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { color: "#8a90a5", maxTicksLimit: 24, maxRotation: 0, autoSkip: true },
             grid: { color: "#eef0f7" },
             title: { display: true, text: usePid ? "pixiv 投稿 ID" : "文件日期", color: "#8a90a5", font: { size: 11 } } },
        y: { min: 0, max: 100, ticks: { color: "#8a90a5", stepSize: 20 }, grid: { color: "#eef0f7" } }
      },
      plugins: { legend: { labels: { color: "#3a4055", usePointStyle: true } },
        tooltip: { callbacks: { afterBody: (items) => {
          const i = items[0].dataIndex, r = list[i];
          const p = pixivOf(r);
          return "文件: " + r.id + "\n原文件名: " + r.original_name +
                 (p ? "\n投稿 ID: " + p.num + "  第 " + (p.page + 1) + " 张" : "") +
                 "\n文件日期: " + (r.file_date || "-");
        } } } },
      onClick: (e, els) => { if (els.length) openModal(list[els[0].index].id); }
    }
  };
  if (trendChart) trendChart.destroy();
  trendChart = new Chart($("#trend-chart"), cfg);
}

/* ---------- modal ---------- */
let modalModel = null, modalOrder = [], modalIdx = -1;
function currentOrderIds(){
  const arr = ($("#sec-rank") && $("#sec-rank").classList.contains("active"))
    ? orderByMetric(rankMetric) : sortedRecs();
  return arr.map(r => r.id);
}
function openModal(id){
  modalModel = curModel;
  modalOrder = currentOrderIds();
  modalIdx = modalOrder.indexOf(id);
  if (modalIdx < 0) { modalOrder = [id]; modalIdx = 0; }
  renderModal(id);
  $("#modal-mask").classList.add("open");
}
function navModal(dir){
  if (!modalOrder.length) return;
  modalIdx = (modalIdx + dir + modalOrder.length) % modalOrder.length;
  renderModal(modalOrder[modalIdx]);
}
function renderModal(id){
  const m = MODELS.find(x => x.id === modalModel);
  const r = m ? m.records.find(x => x.id === id) : null;
  if (!r) { closeModal(); return; }
  const imgSrc = "";
  const mbtns = MODELS.map(mm => {
    const has = mm.records.some(x => x.id === id);
    return `<button data-mid="${mm.id}" class="${mm.id===modalModel?'active':''}" ${has?'':'disabled'}>${esc(mm.name)}${has?'':'（无数据）'}</button>`;
  }).join("");

  const other = MODELS.find(mm => mm.id !== modalModel);
  let cmp = "";
  if (other) {
    const or = other.records.find(x => x.id === id);
    if (or && or.final_score != null) {
      cmp = `<span class="cmp-chip">${esc(other.name)} 综合 ${fmt(or.final_score)}</span>`;
    }
  }

  const keys = r.scores ? Object.keys(r.scores) : [];
  let dimsHtml = "";
  keys.forEach(k => {
    const d = dimDef(k);
    const sc = r.scores[k];
    const ev = (r.evaluations && r.evaluations[k]) || "";
    const lines = ev.split("\n").filter(Boolean);
    const scLine = lines.length && /score|评分|得分/i.test(lines[0]) ? `<div class="sc-line">${esc(lines[0])}</div>` : "";
    const rest = scLine ? lines.slice(1).join("\n") : lines.join("\n");
    dimsHtml += `<div class="dim-row">
      <div class="dh"><div><span class="dn">${esc(d.cn)}</span><span class="den">${esc(d.en)}</span></div>
      <div class="ds">${fmt(sc)}</div></div>
      <div class="de">${scLine}${esc(rest)}</div></div>`;
  });

  const descHtml = r.description ? `
    <div class="desc-box">
      <div class="desc-title">画面描述 <span class="desc-en">Image Description</span></div>
      <div class="desc-text">${esc(r.description)}</div>
    </div>` : "";

  let optHtml = "";
  if (r.optimization && Object.keys(r.optimization).length) {
    const items = OPT_DIMS.filter(d => r.optimization[d]).map(d =>
      `<div class="opt-item"><div class="on">${esc(d)}</div><div class="ot">${esc(r.optimization[d])}</div></div>`).join("");
    if (items) {
      optHtml = `<div class="dim-row"><div class="dh"><div><span class="dn">优化方向</span><span class="den">Optimization Suggestions</span></div></div>
        <div class="opt-grid">${items}</div></div>`;
    }
  } else if (r.optimization_text) {
    optHtml = `<div class="dim-row"><div class="dh"><div><span class="dn">优化方向</span><span class="den">Optimization Suggestions</span></div></div>
      <div class="de">${esc(r.optimization_text)}</div></div>`;
  }

  $("#modal-body").innerHTML = `
    <div class="mhead">
      <button class="close" id="modal-close">✕</button>
      <img src="${thumbOf(id)}" onerror="this.src='${imgSrc}'">
      <div class="info">
        <h3>${esc(r.original_name)} ${r.nsfw?'<span style="background:#dc2626;color:#fff;font-size:11px;border-radius:6px;padding:1px 6px;vertical-align:middle">18+</span>':''} ${cmp}</h3>
        <div class="kv">编号：<b>${esc(r.id)}</b>　文件日期：${esc(r.file_datetime)}</div>
        <div class="kv">尺寸：${r.image.width} × ${r.image.height}　大小：${(r.image.size_bytes/1048576).toFixed(1)} MB</div>
        <div class="kv">分析时间：${esc(r.analysed_at)}　模型：${esc(m.name)}</div>
        <div style="margin-top:10px">综合得分<br><span class="final-big">${fmt(r.final_score)}</span><span style="color:var(--ink2);font-size:12px"> / 100</span></div>
        ${descHtml}
      </div>
    </div>
    <div class="mbody">
      <div class="mmodel" id="modal-model-switch">${mbtns}</div>
      ${dimsHtml}
      ${optHtml}
    </div>`;
  $("#modal-close").addEventListener("click", closeModal);
  $$("#modal-model-switch button").forEach(b => {
    if (b.disabled) return;
    b.addEventListener("click", () => {
      modalModel = b.dataset.mid;
      curModel = modalModel;
      modalOrder = currentOrderIds();
      modalIdx = modalOrder.indexOf(id);
      if (modalIdx < 0) { modalOrder = [id]; modalIdx = 0; }
      renderModal(id);
      renderModelSwitch();
      refreshDataSections();
    });
  });
}
function closeModal(){ $("#modal-mask").classList.remove("open"); }
$("#modal-mask").addEventListener("click", e => { if (e.target.id === "modal-mask") closeModal(); });

/* ---------- boot ---------- */
initTabs();
renderModelSwitch();
renderOverview();
const sortSel = document.getElementById("sort-sel");
if (sortSel) {
  sortSel.value = sortMode;
  sortSel.addEventListener("change", () => {
    sortMode = sortSel.value;
    if ($("#sec-rank").classList.contains("active")) renderRanking();
  });
}

/* ---------- 左右箭头 / 键盘 / NSFW 开关 ---------- */
const navPrev = document.getElementById("nav-prev");
const navNext = document.getElementById("nav-next");
if (navPrev) navPrev.addEventListener("click", () => navModal(-1));
if (navNext) navNext.addEventListener("click", () => navModal(1));
document.addEventListener("keydown", e => {
  if (!$("#modal-mask").classList.contains("open")) return;
  if (e.key === "ArrowLeft") { e.preventDefault(); navModal(-1); }
  else if (e.key === "ArrowRight") { e.preventDefault(); navModal(1); }
  else if (e.key === "Escape") closeModal();
});
const nsfwToggle = document.getElementById("nsfw-toggle");
if (nsfwToggle) {
  nsfwToggle.addEventListener("change", () => {
    hideNsfw = nsfwToggle.checked;
    renderModelSwitch();
    refreshDataSections();
  });
}
"""


def build_html(models, thumbs):
    dims_json = json.dumps([{"key": k, "cn": cn, "en": en} for k, cn, en in DIMS], ensure_ascii=False)
    models_json = json.dumps(
        [{"id": m["id"], "name": m["name"], "meta": m.get("meta", {}), "records": m["records"]}
         for m in models], ensure_ascii=False).replace("</", "<\\/")
    thumbs_json = json.dumps(thumbs, ensure_ascii=False).replace("</", "<\\/")
    opt_json = json.dumps(OPT_DIMS, ensure_ascii=False)
    app_js = (APP_JS
              .replace("__DIMS_JSON__", dims_json)
              .replace("__MODELS_JSON__", models_json)
              .replace("__THUMBS_JSON__", thumbs_json)
              .replace("__OPT_DIMS_JSON__", opt_json))
    if not os.path.exists(CHARTJS):
        raise SystemExit(f"缺少 Chart.js 运行时：{CHARTJS}\n"
                         f"该文件用于把图表内联进报告，请从 "
                         f"https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.js "
                         f"下载后放到该路径。")
    with open(CHARTJS, "r", encoding="utf-8") as f:
        chartjs = f.read()

    all_dates = [r.get("file_date", "") for m in models for r in m["records"] if r.get("file_date")]
    date_range = f"{min(all_dates)} ~ {max(all_dates)}" if all_dates else "-"

    # 页脚按实际运行的模型与量化档位生成（档位不写死，兼容历史结论文件）
    model_tags = " · ".join(
        "{}-{}".format(m["meta"].get("model") or m["name"], m["meta"]["quant"])
        if m.get("meta", {}).get("quant") else (m["meta"].get("model") or m["name"])
        for m in models)

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AesLens美学分析报告</title>
<script>{chartjs}</script>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header class="hero">
  <h1>AesLens美学分析报告 <small style="font-size:.5em;font-weight:500;color:rgba(255,255,255,.8);vertical-align:middle">v1.0</small></h1>
  <div class="sub" id="hero-sub"></div>
  <div class="chips">
    <span class="model-switch" id="global-model-switch"></span>
    <span class="chip" id="chip-count">-</span>
    <span class="chip">文件日期 {date_range}</span>
    <span class="chip" id="chip-model">-</span>
    <label class="nsfw-toggle" title="勾选后隐藏 NSFW 作品"><input type="checkbox" id="nsfw-toggle"> 隐藏 NSFW</label>
    <span class="chip">生成于 {html.escape(datetime.now().strftime("%Y-%m-%d %H:%M"))}</span>
  </div>
</header>

<nav class="tabs">
  <button class="active" data-tab="overview">总览</button>
  <button data-tab="rank">得分排行</button>
  <button data-tab="trend">时间趋势</button>
</nav>

<section id="sec-overview" class="active">
  <div class="grid cards">
    <div class="card"><div class="label">已分析作品</div><div class="value" id="ov-total">-</div></div>
    <div class="card"><div class="label">平均综合得分</div><div class="value" id="ov-avg">-</div></div>
    <div class="card"><div class="label">最高综合得分</div><div class="value" id="ov-best">-</div></div>
    <div class="card"><div class="label">最低综合得分</div><div class="value" id="ov-worst">-</div></div>
  </div>
  <div class="two-col">
    <div class="panel"><h2>综合得分分布</h2>
      <div class="hist-meta">区间 <b id="hist-mode">-</b> 作品最多　·　按综合得分每 5 分一档统计</div>
      <div class="chart-box"><canvas id="hist"></canvas></div>
      <div class="legend-note">直方图展示当前模型下全体作品综合得分的分布情况；柱子越高说明落在该分数段的作品越多。开启「隐藏 NSFW」后统计随之刷新。</div></div>
    <div class="panel"><h2>各维度统计（平均 / 最高 / 最低）</h2>
      <table id="dim-table"><thead><tr><th>维度</th><th>平均分</th><th>最高分</th><th>最低分</th><th></th></tr></thead><tbody></tbody></table>
      <div class="legend-note">点击行可查看对应最高分作品的详细评价。</div></div>
  </div>
</section>

<section id="sec-rank">
  <div class="panel">
    <h2 id="rank-title">得分排行</h2>
    <div class="sort-wrap"><span>排序方式：</span>
      <select id="sort-sel" class="sort-sel">
        <optgroup label="按得分">
          <option value="score_desc">得分 高→低</option>
          <option value="score_asc">得分 低→高</option>
        </optgroup>
        <optgroup label="按投稿 ID">
          <option value="pid_desc">投稿 ID 新→旧</option>
          <option value="pid_asc">投稿 ID 旧→新</option>
        </optgroup>
        <optgroup label="按文件名">
          <option value="name_asc">文件名 A→Z</option>
          <option value="name_desc">文件名 Z→A</option>
        </optgroup>
      </select>
      <span class="legend-note">得分排序对该维度生效；ID / 文件名排序只改变浏览顺序，不改动得分列。</span>
    </div>
    <div class="seg" id="rank-seg"></div>
    <div class="thumb-strip" id="rank-top5"></div>
    <table id="rank-table"><thead><tr><th style="width:36px">#</th><th style="width:52px"></th><th>作品</th><th style="width:96px">文件日期</th><th style="width:70px">得分</th><th style="width:120px"></th></tr></thead><tbody></tbody></table>
    <div class="legend-note">点击任意作品查看完整信息、八维度得分、画面描述、优化方向与详细评价。</div>
  </div>
</section>

<section id="sec-trend">
  <div class="panel">
    <h2 id="trend-title">得分随时间变化</h2>
    <div class="axis-switch">
      <div class="seg" id="axis-seg"></div>
      <div class="axis-sub" id="axis-sub">左：按文件时间　·　右：按 pixiv 投稿 ID</div>
    </div>
    <div class="seg" id="trend-seg"></div>
    <div class="chart-box tall"><canvas id="trend-chart"></canvas></div>
    <div class="legend-note" id="trend-note">横轴为文件日期（按时间排序）；实线为单张作品得分，虚线为同期平均分。点击数据点可查看该作品详情。可切换查看综合得分或任一维度的变化。</div>
    <div class="legend-note">提示：批量下载的文件修改时间往往被统一刷成下载时刻，此时拖到右侧「得分–ID」更能反映真实的创作先后顺序；投稿 ID 越大越新。</div>
  </div>
</section>

<footer>{model_tags} · llama.cpp (Vulkan)<br><span style="font-size:11px;opacity:.85">Powered by 书生·妙析 (<a href="https://github.com/thunderbolt215/ArtiMuse" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;border-bottom:1px solid rgba(255,255,255,.5)">ArtiMuse</a>) &amp; <a href="https://github.com/thunderbolt215/UniPercept" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;border-bottom:1px solid rgba(255,255,255,.5)">UniPercept</a></span><br><span style="color:var(--ink2)">枋芗町美术学院 © 2026 | Created by 苄尘</span></footer>
</div>

<div class="modal-mask" id="modal-mask">
  <button class="nav-arrow left" id="nav-prev" title="上一个（←）">‹</button>
  <div class="modal" id="modal-body"></div>
  <button class="nav-arrow right" id="nav-next" title="下一个（→）">›</button>
</div>

<script>{app_js}</script>
</body>
</html>"""
    return html_doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True, help="作业 id（报告写入 work/<作业名>/report.html）")
    args = ap.parse_args()
    models = load_models(args.job)
    if not models:
        print("没有可用的结论数据")
        return
    thumbs = load_thumbs(models, args.job)
    annotate_nsfw(models, args.job, log_fn=lambda s: print(s))
    doc = build_html(models, thumbs)
    jdir = job_dir(args.job)
    os.makedirs(jdir, exist_ok=True)
    jpath = os.path.join(jdir, "report.html")
    with open(jpath, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"HTML 报告已生成: {jpath} ({len(doc)/1048576:.1f} MB, "
          f"模型 {len(models)} 个, 内嵌 {len(thumbs)} 张缩略图)")


if __name__ == "__main__":
    main()

