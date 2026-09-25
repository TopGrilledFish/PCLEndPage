#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 build/upstream.json（由 tools/dump_upstream.mjs 导出）转写成 pclhome/data/*.py。

用法：
    node tools/dump_upstream.mjs <上游仓库路径> > build/upstream.json
    python tools/port_data.py

移植只在需要同步上游内容时执行；生成出的 .py 已随仓库提交，日常运行不依赖本脚本。
"""
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "build" / "upstream.json"
OUT = ROOT / "pclhome" / "data"

HEADER = """# -*- coding: utf-8 -*-
\"\"\"{doc}

本文件由 tools/port_data.py 从上游 wlasfjdskfj/pcl-homepage（MIT）移植生成，请勿手工编辑。
上游内容为中文文案与题库数据，移植时逐字保留，未做改写。
\"\"\"
"""


def lit(value, indent=0):
    """把 JSON 值渲染成易读的 Python 字面量（每项一行）。"""
    pad = " " * indent
    inner = " " * (indent + 4)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, int):
        return repr(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(x, int) for x in value):
            # 农历数据表：每行 10 个，便于比对
            rows = []
            for i in range(0, len(value), 10):
                rows.append(inner + ", ".join("0x%05x" % x for x in value[i:i + 10]))
            return "[\n" + ",\n".join(rows) + ",\n" + pad + "]"
        items = [inner + lit(x, indent + 4) for x in value]
        return "[\n" + ",\n".join(items) + ",\n" + pad + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = []
        for k, v in value.items():
            if isinstance(v, str) and len(json.dumps(v, ensure_ascii=False)) <= 76:
                items.append(inner + json.dumps(k, ensure_ascii=False) + ": " + lit(v, indent + 4))
            else:
                items.append(inner + json.dumps(k, ensure_ascii=False) + ": " + lit(v, indent + 4))
        return "{\n" + ",\n".join(items) + ",\n" + pad + "}"
    raise TypeError("不支持的类型：" + type(value).__name__)


def emit(doc, names, data, path):
    parts = [HEADER.format(doc=doc)]
    for name in names:
        parts.append(name + " = " + lit(data[name]) + "\n")
    text = "\n".join(parts)
    path.write_text(text, encoding="utf-8")
    print("已生成 " + str(path.relative_to(ROOT)) + "（" + str(len(text)) + " 字节）")


def main():
    if not SRC.exists():
        print("缺少 " + str(SRC) + "，请先执行 tools/dump_upstream.mjs", file=sys.stderr)
        return 1
    data = json.loads(SRC.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)

    emit("主页文案数据：每日一言、彩蛋、问候语、幸运颜色、运势、人品评语。",
         ["QUOTES", "EGGS", "GREETING_SUBS", "COLORS",
          "FORTUNE_GOOD", "FORTUNE_BAD", "FORTUNE_TIPS", "SCORE_COMMENTS"],
         data, OUT / "text.py")

    emit("玩法数据：随机挑战、MC 种子、每日一题题库。",
         ["CHALLENGES", "SEEDS", "QUIZ"],
         data, OUT / "play.py")

    emit("节日与农历：公历节日、农历节日、1900-2100 农历数据表。",
         ["FESTIVALS", "LUNAR_FESTIVALS", "LUNAR_INFO"],
         data, OUT / "calendar.py")

    return 0


if __name__ == "__main__":
    sys.exit(main())
