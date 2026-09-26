# -*- coding: utf-8 -*-
"""我们自己补的中国节日——跟 ``calendar.py`` 分开放，是为了不被移植脚本冲掉。

``calendar.py`` 是 tools/port_data.py 从上游生成的（那份的条目数还当着"跟上游一致"
的基准，见 doctor.EXPECTED_COUNTS），重跑一次移植就整个重写。所以补的节日写这儿，
由 ``lunar.py`` 合并进内置节日表。字段跟上游那份一样：

    公历节日    ``month`` / ``day``
    农历节日    ``lm`` / ``ld``；``ld = 0`` 表示"这个月最后一天"（除夕得这么写，
                腊月是廿九还是三十逐年不同，写死一个数就会有一半年份差一天）
    节气        ``term``（见 lunar.py 里的 ``_TERM_DAYS``）

``key`` 是词条键后缀，不写就按「月-日」拼（``festival.solar.10-1``）；月日不固定的
（清明按节气算、除夕按月末算）必须自己写，不然键会跟着日期漂，翻译也就对不上了。
"""
from __future__ import annotations

EXTRA_FESTIVALS = [
    {
        "month": 3,
        "day": 12,
        "name": "植树节",
        "msg": "种一棵树，给世界添点绿！",
    },
    {
        "month": 5,
        "day": 4,
        "name": "青年节",
        "msg": "青年节快乐，青春就是用来折腾的！",
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
        "msg": "教师节快乐，谢谢每一位老师！",
    },
    {
        "month": 10,
        "day": 1,
        "name": "国庆节",
        "msg": "祝祖国生日快乐，假期玩得开心！",
    },
    {
        "term": "qingming",
        "key": "solar.qingming",
        "name": "清明节",
        "msg": "清明时节雨纷纷，扫墓踏青，别忘了带伞。",
    },
]

EXTRA_LUNAR_FESTIVALS = [
    {
        "lm": 12,
        "ld": 0,
        "key": "lunar.chuxi",
        "name": "除夕",
        "msg": "除夕快乐，年夜饭安排上！",
    },
]
