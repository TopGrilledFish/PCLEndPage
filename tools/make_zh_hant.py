# -*- coding: utf-8 -*-
"""从 zh-hans.json 生成 zh-hant.json。

**只在开发机上跑**，服务端不需要 zhconv 这个依赖——生成好的 zh-hant.json 是
提交进仓库的数据文件。

    python -m pip install zhconv
    python tools/make_zh_hant.py

生成完请顺手看一遍 diff：简繁转换有上下文（"里"要变"裡"还是留着，"後面"和
"皇后"不是一回事），zhconv 大部分能处理对，但术语（比如"默认"该是"默認"还是
"預設"）值得人工过一眼。改文案改 zh-hans.json，然后重跑这个脚本；如果你手工
调过 zh-hant.json，重跑会覆盖掉，所以调完记得把改动同步回这里的 _OVERRIDES
或者直接改 zh-hans。

用法：
    python tools/make_zh_hant.py            # 生成
    python tools/make_zh_hant.py --check    # 只比对，不写盘（体检用）
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "pclhome" / "i18n" / "zh-hans.json"
DST = ROOT / "pclhome" / "i18n" / "zh-hant.json"

# zhconv 的 zh-hant 是通用繁體（不偏台湾也不偏香港）。想要台湾用词改这里，
# 但注意"内存→記憶體""文件→檔案"这类是台湾特有说法，港澳用户看着也别扭，
# 所以保持中性。
VARIANT = "zh-hant"

# 转换后的人工修正：键 -> (原文, 想要的译文)。放这儿是为了重跑不被覆盖。
# 目前为空——zhconv 对我们的文案处理得没问题；以后发现哪句不对，加在这儿。
_OVERRIDES: dict[str, tuple[str, str]] = {}

# 转换后的词级修正：简繁转换按字走，改不了用词。"内存"在内地是内存，
# 港澳台说"記憶體"；这类词列在这儿，转完统一替换。
_PHRASES = (
    ("內存", "記憶體"),
    # AI 提示词里那句"请用简体中文回答"：繁中用户要的是繁體中文回答，
    # 照字面转过去会变成"請用簡體中文回答"，正好把意思转反了。
    ("請用簡體中文回答", "請用繁體中文回答"),
    # zhconv 的 zh-hant 把「湿」转成「溼」（大陆繁体写法），港澳台通行的是「濕」
    ("溼度", "濕度"),
    # 下面几个是 zhconv 转出来的异体字，港澳台并不这么写
    ("喫", "吃"),      # 「吃」的异体，繁体里照样写「吃」
    ("糉", "粽"),
    ("啓", "啟"),      # 台湾写「啟」，「啓」是异体
    ("裏", "裡"),      # 台湾写「裡」
)


def convert(value: str, zhconv) -> str:
    text = zhconv.convert(value, VARIANT)
    for wrong, right in _PHRASES:
        text = text.replace(wrong, right)
    return text


def walk(node, zhconv, overrides: dict, path: str = ""):
    """递归转换：只动字符串和字符串数组，键和 _说明 之类的元数据不碰。"""
    if isinstance(node, str):
        return overrides.get(path, (None, None))[1] or convert(node, zhconv)
    if isinstance(node, list):
        return [walk(item, zhconv, overrides, path + "[]") for item in node]
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key.startswith("_"):
                out[key] = value            # _说明 是给我们看的，不翻
                continue
            out[key] = walk(value, zhconv, overrides, path + "." + key if path else key)
        return out
    return node


def main(argv: list[str]) -> int:
    try:
        import zhconv
    except ImportError:
        print("需要 zhconv：python -m pip install zhconv")
        return 2

    src = json.loads(SRC.read_text(encoding="utf-8"))
    made = walk(src, zhconv, _OVERRIDES)
    made["_说明"] = "由 tools/make_zh_hant.py 从 zh-hans.json 生成，别手改；改简中然后重跑。"

    text = json.dumps(made, ensure_ascii=False, indent=2) + "\n"
    if "--check" in argv:
        current = DST.read_text(encoding="utf-8") if DST.exists() else ""
        same = current == text
        print("zh-hant.json " + ("与生成结果一致" if same else "和生成结果不一样，重跑一下"))
        return 0 if same else 1

    DST.write_text(text, encoding="utf-8")
    zhconv_keys = set(src) - {k for k in src if k.startswith("_")}
    missing = sorted(k for k in zhconv_keys if not made.get(k))
    print("已生成 " + str(DST.relative_to(ROOT)) + "（" + str(len(made)) + " 个键）")
    if missing:
        print("这些键转换后是空的，检查一下：" + "、".join(missing))
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
