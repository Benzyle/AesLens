#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型文件预检（run.bat 启动前调用）。

按 common.resolve_model_files 报告各模型的就绪情况与实际识别到的量化档位——
档位不写死，models/ 下放任意档位都能识别。本脚本只打印信息，永远返回 0，
就绪与否由启动台 / run_analysis.py 做实际校验。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import model_dir, resolve_model_files  # noqa: E402

DEFAULT_MODEL = "unipercept_std"


def main():
    for mid in ("unipercept", "unipercept_std", "artimuse"):
        f = resolve_model_files(mid)
        if f["gguf"] and f["mmproj"]:
            print(f"[OK] {mid}: {f['quant']}（{os.path.basename(f['gguf'])}）")
        elif mid == DEFAULT_MODEL:
            print(f"[WARN] 默认模型未就绪：{model_dir(mid)} 下缺少可识别的模型文件")
            print("[WARN] 请先运行一键安装：python setup.py")
            print("[WARN] 或按 docs/models.md 手动放置（任意量化档位均可识别）")
        else:
            print(f"[--] {mid}: 未安装（可选）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
