# -*- coding: utf-8 -*-
"""PCL2 个性化主页 · Python 复刻版。

一个为 PCL2 启动器（Plain Craft Launcher 2）打造的联网自定义主页：
构建期生成静态 XAML，请求期按访问者 IP + 北京时间做确定性个性化。

模块划分与上游一一对应：

    config        配置
    generate      构建期：填模板 → Custom.xaml
    render        请求期：把 __TOKEN__ 换成本次请求的内容
    server        HTTP 服务、路由、来源守卫
    admin         管理后台
    personalize   哈希 / 北京时间 / 确定性抽取
    lunar         农历与节日
    weather       实时天气
    xaml          XAML 转义与组件
    store         配置存储与访问统计
    data/         文案与题库数据（由上游移植）
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
