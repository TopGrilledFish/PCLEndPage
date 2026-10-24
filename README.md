# PCL End Page

PCL2（Plain Craft Launcher 2）的联网自定义主页，用 Python 写成，只用标准库，不装任何第三方包就能跑。

在线示例：<https://end.g-fish.dpdns.org>

注意：此项目部分代码由人工智能完成。

## 这是什么

PCL2 2.10.4 起支持「联网更新」自定义主页：客户端启动时向一个地址要 `Custom.xaml`，然后整页渲染出来。
本项目就是一个自己实现的服务器，负责按请求现算内容、渲染 XAML 再返回给客户端，顺带把 AI 分析页、计算器、后台管理这些周边功能也包了。

上游是 [wlasfjdskfj/pcl-homepage](https://github.com/wlasfjdskfj/pcl-homepage)（Cloudflare Pages + Functions 的 JS 版）。本项目重写成了 Python 单进程服务，功能按自己的需求扩展过。

## 功能

### 主页

- 问候语、日期、星期，以及农历日期与节气
- 节日横幅、自定义节日、倒计时（可在后台配置）
- 每日一言（默认走接口，取不到就用内置文案；也可自己写一份按日期轮换）
- 天气：按访客 IP 定位城市，自动适配温度单位，带风向湿度与一句建议
- 必应每日壁纸做横幅背景
- 常用网站列表（13 个 Minecraft 相关站点，图标可本地镜像）

### 按访客区分

同一个地址，不同访客看到的内容不完全一样：

- 语言：简体中文 / 繁体中文 / 英文，按地区自动选择
- 温度单位：摄氏度 / 华氏度
- 主题配色

### 额外页面

| 入口 | 说明 |
| --- | --- |
| `/ai_page.json` | AI 分析：把日志贴进去，交给大模型找问题。支持访客自带密钥，按 IP 限速 |
| `/calc.json` | 计算器：伤害、下界坐标、区块、距离、要塞、游戏刻、经验、盔甲、堆叠、颜色值、UUID、种子，共 12 个 |
| `/settings_page.json` | 个性设置页 |

### 后台

访问 `/admin`，输入 `config.json` 里的 `admin_token` 登录。可以看访问统计（7 天走势、地区分布、最近访客），管封禁名单与白名单、维护模式，改公告 / 每日一言 / 节日 / 倒计时，清缓存，改站点名称与天气城市，跑系统体检，看服务日志，直接编辑 `store.json`。

## 快速开始

### 直接使用

PCL 主页预设里已经上架，也可以手动填地址：进入 `设置 -> 个性化 -> 主页`，选择「联网更新」，地址填：

```
https://end.g-fish.dpdns.org
```

### 自己跑一份

需要 Python 3.10 或更新版本，没有其他依赖。

```bash
git clone https://github.com/TopGrilledFish/PCLEndPage.git
cd PCLEndPage

# 不建配置也能跑，默认监听 127.0.0.1:8787
python run.py serve

# 想改配置就复制一份模板
cp config.example.json config.json
```

然后用 PCL 把主页地址指向 `http://127.0.0.1:8787` 即可。

### 常用命令

| 命令 | 作用 |
| --- | --- |
| `python run.py` | 等同于 `python -m pclhome serve` |
| `python -m pclhome serve --host 0.0.0.0 --port 8787` | 指定监听地址与端口 |
| `python -m pclhome serve --log-file var/server.log` | 同时把日志写进文件 |
| `python -m pclhome generate` | 只重新生成静态的 `Custom.xaml` |
| `python -m pclhome doctor` | 体检：本地资源、渲染、i18n，附带线上探测 |
| `python -m pclhome doctor --offline` | 只做本地体检，不联网 |
| `python -m pclhome version` | 打印版本号 |

## 配置

配置优先级：默认值 < `config.json` < 环境变量。所有环境变量都带 `PCLHOME_` 前缀，比如 `PCLHOME_PORT=9000`、`PCLHOME_ADMIN_TOKEN=xxx`。

`config.example.json` 里每一项都带说明，常用的是这些：

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `base_url` | 空 | 对外地址。留空则按请求的 Host 推导；反代或多域名时建议写死 |
| `host` / `port` | `127.0.0.1` / `8787` | 监听地址与端口 |
| `admin_token` | `admin` | 后台登录令牌，部署到公网前务必改掉 |
| `enable_ai` | `false` | 是否开启 AI 分析页 |
| `ai_api_base` | `https://api.deepseek.com/v1` | 任何 OpenAI 兼容接口都行 |
| `ai_api_key` | 空 | 内置密钥（访客也可自带） |
| `ai_model` | `deepseek-flash` | 模型名 |
| `ai_rate_limit` / `ai_rate_window` | `3` / `hour` | 每个 IP 的调用上限与窗口 |
| `enable_weather` | `true` | 天气卡片 |
| `weather_city` | 空 | 留空按访客 IP 定位，填了就固定城市 |
| `enable_geo` | `true` | IP 归属地查询（定位城市用） |
| `enable_wallpaper` / `enable_saying` / `enable_lunar` | `true` | 壁纸 / 每日一言 / 农历 |
| `uapi_key` | 空 | uapis.cn 的密钥，只走请求头，不会出现在日志的 URL 里 |
| `guard_clients` | `true` | 拦截爬虫与非 PCL 客户端（只影响主页接口） |
| `trust_proxy_headers` | `true` | 位于 nginx / CDN 之后时保留真实 IP |

改完配置重启服务生效。部分内容类配置（公告、每日一言、节日、倒计时）在后台改就行，不用碰文件。

## 部署

本质上就是一个 HTTP 服务，用 systemd 托管最省事：

```ini
[Unit]
Description=PCL End Page
After=network.target

[Service]
WorkingDirectory=/root/PCLEndPage
ExecStart=/usr/bin/python3 run.py serve --log-file var/server.log
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

保存为 `/etc/systemd/system/pclhome.service`，然后：

```bash
systemctl daemon-reload
systemctl enable --now pclhome
```

前面套 nginx 反代时，记得把 `base_url` 写成对外域名，并且**不要把项目目录暴露成静态目录**——`config.json` 里有密钥。根目录只该放出 `Custom.xaml` 和 `static/`。

## 目录结构

```
run.py                  便捷入口，等价于 python -m pclhome
config.example.json     配置模板（带说明）
pclhome/
  __main__.py           命令行入口：serve / generate / doctor / version
  server.py             HTTP 服务、路由、客户端拦截、模板缓存
  render.py             请求期占位符替换，把数据填进主页模板
  templates/            主页模板 Custom.xaml.tpl
  generate.py           构建期生成静态 Custom.xaml（{{TOKEN}} 在这里替换）
  data/                 内置文案、节日、农历表、计算器素材
  i18n/                 简体中文（母版）/ 繁体 / 英文 文案表
  admin.py, admin_page.py   后台接口与界面
  ai.py                  AI 分析页
  calc.py                计算器页
  settings.py            个性设置页
  weather.py, geo.py     天气与 IP 定位
  saying.py, lunar.py, lunartime.py   每日一言、农历、节气
  store.py               运行时数据（公告、封禁、统计）
  doctor.py              体检
static/                  图标、音乐等静态资源，对外可见
var/                     缓存、日志、数据库（不纳入版本库）
```

## 排错

先跑体检，大部分问题它会直接指出来：

```bash
python -m pclhome doctor --offline
```

- 主页没变化：`Custom.xaml` 是生成出来的文件，改了模板要重新 `python -m pclhome generate`
- 客户端一直显示旧页面：PCL 会缓存，重启客户端或清掉它的主页缓存
- 后台打不开：确认 `admin_token`、确认访问的是 `/admin`
- 日志：默认只输出到控制台，加 `--log-file var/server.log` 才会落盘

## 协议

本仓库使用 MIT 协议。

上游是 <https://github.com/wlasfjdskfj/pcl-homepage>，所以借鉴本仓库的代码或样式时：

- 在代码文件头部标注原作者，并使用同样的 MIT 协议
- 如果做的是 PCL 主页，还要在主页文件里标出来，建议放在尾部「特别鸣谢」中。标注内容的颜色和字号要保证可读，上下文不能出现冒犯性或容易引起反感的内容

欢迎接着 Fork，有需要可以前往我的个人主页获取联系方式。
