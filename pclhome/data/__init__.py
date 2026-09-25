# -*- coding: utf-8 -*-
"""内容数据（由上游 wlasfjdskfj/pcl-homepage 移植，MIT）。

    text.py      每日一言、彩蛋、问候语、幸运颜色、运势、人品评语
    play.py      随机挑战、MC 种子、每日一题题库
    calendar.py  公历节日、农历节日、1900-2100 农历数据表

这三个文件由 tools/port_data.py 从上游 JS 生成，请勿手工编辑；
要改文案请改上游或直接改这里的数据项，改完跑一次 ``python -m pclhome doctor`` 校验规模。

**当前页面实际用到的是：**

    text.QUOTES           每日一言在接口不可用时的兜底
    text.GREETING_SUBS    问候语副标题（按时段 + IP + 日期）
    calendar.*            节日横幅与倒计时胶囊

其余数据集（COLORS / EGGS / FORTUNE_* / SCORE_COMMENTS / CHALLENGES / SEEDS / QUIZ）
是上游题库的完整移植，随"今日运势"卡片与面板一起下线后暂时没有调用方。
留着是为了随时能把那些功能接回来——要自己用，直接从 ``pclhome.data`` 里取即可。
"""
from . import calendar, play, text

__all__ = ["text", "play", "calendar"]
