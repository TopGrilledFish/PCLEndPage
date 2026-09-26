# -*- coding: utf-8 -*-
"""我们自己补的常见节日——跟 ``calendar.py`` 分开放，是为了不被移植脚本冲掉。

``calendar.py`` 是 tools/port_data.py 从上游生成的（那份的条目数还当着"跟上游一致"
的基准，见 doctor.EXPECTED_COUNTS），重跑一次移植就整个重写。所以补的节日写这儿，
由 ``lunar.py`` 合并进内置节日表。几种写法：

    ``month`` / ``day``   公历固定日子
    ``lm`` / ``ld``       农历固定日子；``ld = 0`` 表示"这个月最后一天"
                          （除夕得这么写，腊月是廿九还是三十逐年不同）
    ``term``              节气（见 lunar.py 的 ``_TERM_DAYS``）
    ``rule``              按星期算的（母亲节、感恩节这种，见 ``_RULES``）

``key`` 是词条键后缀，不写就按「月-日」拼（``festival.solar.10-1``）；月日会变的
（节气、按星期算的、除夕的月末）必须自己写，不然键跟着日期漂，翻译就对不上了。

**msg 不要重复节日名**：横幅是「今天是 {name}！{msg}」拼出来的，写成"母亲节快乐"
就会变成"今天是母亲节！母亲节快乐"。
"""
from __future__ import annotations

EXTRA_FESTIVALS = [
    # ---------- 公历固定日子 ----------
    {
        "month": 3,
        "day": 5,
        "name": "学雷锋日",
        "msg": "顺手帮别人一把，不费事。",
    },
    {
        "month": 3,
        "day": 12,
        "name": "植树节",
        "msg": "种一棵树，给世界添点绿！",
    },
    {
        "month": 3,
        "day": 15,
        "name": "消费者权益日",
        "msg": "买东西多留个心眼，该较真就较真。",
    },
    {
        "month": 4,
        "day": 22,
        "name": "世界地球日",
        "msg": "今天对地球好一点。",
    },
    {
        "month": 4,
        "day": 23,
        "name": "世界读书日",
        "msg": "找本书翻翻吧，哪怕就两页。",
    },
    {
        "month": 5,
        "day": 4,
        "name": "青年节",
        "msg": "青春就是用来折腾的！",
    },
    {
        "month": 5,
        "day": 12,
        "name": "护士节",
        "msg": "谢谢每一位白衣天使！",
    },
    {
        "month": 5,
        "day": 20,
        "name": "网络情人节",
        "msg": "520，今天适合说点好听的~",
    },
    {
        "month": 6,
        "day": 5,
        "name": "世界环境日",
        "msg": "少扔一个瓶子也是好的。",
    },
    {
        "month": 7,
        "day": 1,
        "name": "建党节",
        "msg": "七一，不忘初心。",
    },
    {
        "month": 8,
        "day": 1,
        "name": "建军节",
        "msg": "致敬最可爱的人！",
    },
    {
        "month": 9,
        "day": 10,
        "name": "教师节",
        "msg": "谢谢每一位老师！",
    },
    {
        "month": 10,
        "day": 1,
        "name": "国庆节",
        "msg": "祝祖国生日快乐，假期玩得开心！",
    },
    {
        "month": 11,
        "day": 11,
        "name": "光棍节",
        "msg": "购物车冷静一点~",
    },
    {
        "month": 12,
        "day": 1,
        "name": "世界艾滋病日",
        "msg": "多一份了解，少一份歧视。",
    },
    {
        "month": 12,
        "day": 4,
        "name": "国家宪法日",
        "msg": "了解一下自己的权利。",
    },
    # ---------- 按星期算的（rule 见 lunar.py 的 _RULES）----------
    {
        "rule": "mothers_day",
        "key": "solar.mothers_day",
        "name": "母亲节",
        "msg": "记得跟妈妈说句话！",
    },
    {
        "rule": "fathers_day",
        "key": "solar.fathers_day",
        "name": "父亲节",
        "msg": "老爸辛苦了！",
    },
    {
        "rule": "thanksgiving",
        "key": "solar.thanksgiving",
        "name": "感恩节",
        "msg": "谢谢一路陪着你的人！",
    },
    # ---------- 节气 ----------
    {
        "term": "qingming",
        "key": "solar.qingming",
        "name": "清明节",
        "msg": "清明时节雨纷纷，踏青扫墓，别忘了带伞。",
    },
]

EXTRA_LUNAR_FESTIVALS = [
    {
        "lm": 2,
        "ld": 2,
        "name": "龙抬头",
        "msg": "今天剪头发正合适！",
    },
    {
        "lm": 7,
        "ld": 15,
        "name": "中元节",
        "msg": "晚上早点回家~",
    },
    {
        "lm": 12,
        "ld": 23,
        "name": "小年",
        "msg": "北方今天过，南方明天过~",
    },
    {
        "lm": 12,
        "ld": 0,
        "key": "lunar.chuxi",
        "name": "除夕",
        "msg": "年夜饭安排上！",
    },
]
