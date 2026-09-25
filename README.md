# PCL2 个性化主页 · Python 复刻版

对 [wlasfjdskfj/pcl-homepage](https://github.com/wlasfjdskfj/pcl-homepage)（MIT）的 Python 重写，
一个为 [PCL2 启动器](https://github.com/Meloong-Git/PCL) 打造的联网自定义主页：
必应每日壁纸 + 实时天气 + 每日一言 + 节日提醒 + 功能网站导航。
内容由服务端按访问者与日期算好，再作为 XAML 发给 PCL。

只依赖 Python 标准库，**不需要 pip install**，也不需要 Node / Cloudflare / 任何账号。
（开发与验证环境：Python 3.14.5 / Windows 11；代码未用 3.10 以后的语法，理论上下限更低。）

```
python run.py serve        # 启动，浏览器打开 http://127.0.0.1:8787/Custom.xaml
python run.py doctor       # 体检：数据完整性、算法保真度、渲染结果
```

---

## 一、上游项目是怎么运作的

上游把一件事拆成了**两个阶段**，这是理解整个项目的关键：

```
构建期（GitHub Actions，每 12 小时）
  scripts/generate.py
    ├─ 抓必应每日壁纸
    ├─ 填 templates/Custom.xaml.tpl 的 {{TOKEN}}
    └─ 产出 Custom.xaml
                    ↓ 提交进仓库，部署到 Cloudflare Pages
请求期（Cloudflare Pages Functions 中间件）
  functions/_middleware.js
    ├─ 读 CF-Connecting-IP 拿到访问者 IP
    ├─ 用 IP + 日期做哈希，决定今天的人品分 / 运势 / 挑战 / 种子
    ├─ 查天气、农历节日、公告，拼出几段 XAML
    ├─ 把静态文件里的 __TOKEN__ 全部换掉
    └─ 加 no-store 头返回给 PCL
```

**为什么非要两段？** 因为 PCL 只是去下载一个 XAML 文件，它不会执行任何逻辑。
想让"每个人看到的内容不同"，就必须有人在服务端把文件改好再发出去——
静态托管做不到，于是有了中间件。构建期负责"变得慢的东西"（壁纸、图标），
请求期负责"因人而异的东西"。

XAML 的语法和可用控件见 PCL 自带的教学文件。

## 二、复刻版做了什么

同样的两段结构，换成 Python 落地：

```
构建期   python -m pclhome generate     →  Custom.xaml
请求期   python -m pclhome serve        →  按访问者与日期生成后返回
```

| 上游 | 复刻版 |
|---|---|
| `scripts/generate.py` | `pclhome/generate.py` |
| `functions/_middleware.js`（路由 + 组装） | `pclhome/server.py`（HTTP、路由、来源守卫）+ `pclhome/render.py`（占位符替换） |
| `functions/_lib/content.js` 等数据 | `pclhome/data/*.py`（脚本移植，逐字保留） |
| `functions/_lib/xaml.js` | `pclhome/xaml.py` |
| `functions/_lib/lunar.js` | `pclhome/lunar.py` |
| `functions/_lib/weather.js` | `pclhome/weather.py` |
| `functions/admin.js`（1636 行） | `pclhome/admin.py`（保留运维核心） |
| Cloudflare KV | `pclhome/store.py`（JSON 文件，键名与上游一致） |
| Cloudflare D1（访问统计） | `pclhome/store.py`（本地 SQLite，同样保留 7 天） |
| `scripts/doctor.py` | `pclhome/doctor.py` |

### 核心算法是逐位对齐的

"同一 IP 同一天内容固定"靠一个 djb2 变体哈希。JS 的位移会截断成有符号 32 位、
加法却按双精度算，两边很容易出现细微差异，所以复刻版**拿上游 JS 的真实输出当基准向量**：

```python
hash_code("1.2.3.4|2026-09-25|score")        # → 1638887361
hash_code("测试IP|2026-12-31|quote")          # → 1791733359
```

农历换算同理（`solar2lunar` / `lunarToSolar` 在 27 个日期上比对过，含 2033 闰月年、
2100 边界）。这些基准都写进了 `python -m pclhome doctor`，改坏了会立刻报 FAIL。

## 三、快速开始

```bash
# 1. 生成静态 XAML（--offline 跳过必应壁纸、图标下载等外部请求）
python run.py generate --offline

# 2. 启动服务
python run.py serve
#    → http://127.0.0.1:8787/Custom.xaml

# 3. 让 PCL 用上它
#    PCL2 → 设置 → 个性化 → 自定义主页 → 填入：
#    http://127.0.0.1:8787/Custom.xaml
```

PCL 会自己去请求 `Custom.xaml.version`，复刻版每次都返回新时间戳，
所以内容变了 PCL 会自己重新下载，不需要手动清缓存。

启动后可以打开 <http://127.0.0.1:8787/admin> 进后台改公告、开维护模式、看访问统计。
启动日志会把当前生效的设置打一遍，之后每个请求一行明细（详见「八、部署 → 日志」）。

### 配置

全部配置项都有默认值，需要改时把 `config.example.json` 复制成 `config.json`：

```json
{
  "base_url": "https://home.example.com",
  "admin_token": "换一个",
  "weather_city": ""
}
```

`weather_city` 留空（默认）就是**每个访客看自己城市的天气**；填上城市名则所有人都看
那一个城市。相关项还有 `enable_geo`（设为 `false` 就完全不查 IP 归属地）和
`geo_cache_hours`（IP→城市 的缓存时长，默认 24 小时）。

环境变量优先级更高：`PCLHOME_BASE_URL`、`PCLHOME_PORT`、`PCLHOME_ADMIN_TOKEN`、
`PCLHOME_WEATHER_CITY`、`PCLHOME_GEO`、`PCLHOME_GUARD` 等（完整列表见
`pclhome/config.py` 的 `ENV_MAP`）。

## 四、页面构成

主页现在有两张卡片，从上到下：

### 1. 欢迎

* **必应每日壁纸**（构建期抓取）铺满的横幅，压着问候语（`{user}` 由 PCL 填入玩家 ID）、
  今天的大号日期 + 星期 + **一行农历**（`农历八月十五`，当天有节气时补 ` · 秋分`）、
  右上角节日倒计时胶囊、底部**每日一言**
* **天气卡片**：温度 + 天气 + 城市 + 风力湿度 + 一句应景的建议
* **三个功能按钮**：内存优化 / 清理垃圾 / 刷新数据（PCL 内置事件）

### 2. AI 日志分析

粘一个日志链接，AI 帮你看崩溃原因（配了 `ai_api_key` 才会出现这张卡）。

* 输入框 + **用站长密钥分析**（每 IP 每天 `ai_daily_limit` 次，按北京时间重置）
* **用我的密钥分析**：粘一次自己的密钥存下来，该 IP 不再受限。
  密钥只存在服务器内存里，重启即失效，日志里打码
* 结果直接显示在卡片里（AI 要跑 10～30 秒，所以先弹窗说"已提交"，
  点确定刷新主页看结果）

**为什么输入框只能填链接，不能直接粘日志原文**：PCL 的事件参数是把输入框原文
**原样拼进 URL** 的（`StringFormat='...?q={0}'`，不做 URL 编码）。崩溃日志里全是
换行和空格，拼出来的请求行是非法的，PCL 那边直接失败。链接又短又没有空白字符，
才塞得进去。所以正常用法是先把日志传到 **mclo.gs**（下面「功能网站」里就有），
再粘链接。单行的短报错文本也能直接粘。

### 3. 功能网站

八个常用站点的列表项，点一下用系统浏览器打开，每项用**该站自己的图标**：

| 站点 | 地址 |
|---|---|
| Minecraft Wiki | zh.minecraft.wiki |
| MineBBS | minebbs.com |
| Modrinth | modrinth.com |
| CurseForge | curseforge.com/minecraft |
| MC 百科 | mcmod.cn |
| MC 官网 | minecraft.net |
| NameMC | namemc.com |
| mclo.gs | mclo.gs |

图标由构建期尽力镜像到本地（原因和补救办法见 `static/images/icons/README.md`）。

### 页面上的变量

* **按 IP + 北京时间固定**：问候语副标题（按时段分池，同一个人一天内不变）
* **按天固定**：每日一言（全站同一句）、节日横幅与倒计时胶囊
* **按访问者位置变化**：天气卡片（按访客 IP 定位到城市，结果按城市缓存 1 小时）、
  AI 卡片的剩余次数与上次分析结果（按 IP）
* **构建期固定**：壁纸、功能网站列表与图标、以及上面所有静态文案

## 五、数据源

| 内容 | 来源 | 说明 |
|---|---|---|
| 每日一言 | `uapis.cn/api/v1/saying/random?mode=daily&source=caoxingyu-sentence` | `mode=daily` 保证**一整天所有人拿到同一句**；限定中文语料，否则会蹦出英文名言 |
| 实时天气 | `uapis.cn/api/v1/misc/weather` | 先用访客 IP 查城市再带上 `?adcode=` / `?city=`，所以**每个访客看到自己城市的天气**。接口本身只能按*调用方* IP 定位（调用方永远是服务器），也没有 `?ip=` 参数，详见下一行 |
| IP → 城市 | `whois.pconline.com.cn`（首选）、`ip-api.com`（备胎） | 接口只认调用方 IP，伪造 `X-Forwarded-For` 等七种头实测全都不认，所以只能自己换。结果按 IP 缓存 24 小时；查不到（内网地址、境外 IP、两个源都挂）就退回按服务器 IP 定位 |
| 农历 | `uapis.cn/api/v1/misc/lunartime` | 中文月日 + 当天节气，按日期缓存；接口不可用时退回本地农历换算（`lunar.py`，无节气） |
| 横幅壁纸 | 必应每日壁纸 | 构建期抓一次 |
| AI 日志分析 | DeepSeek（可换成任意 OpenAI 兼容接口） | 站长内置密钥每 IP 每天限次；访客也可自带密钥。见 `ai.py` |
| 网站图标 | 各站 favicon | 构建期尽力镜像到本地 |
| 农历 / 节日 / 题库 | 本地数据 | 随复刻版一起，离线也能跑 |

任何一个外部接口挂掉都不会让页面出问题：一言有三层兜底（后台自定义 → 接口 → 内置 68 条），
天气拿不到就显示一条黄色提示条，图标下不到就退回远程地址。

## 六、与上游的差异

### 修掉的上游 bug

1. **公历节日从未生效**。上游 `getBeijingDate()` 把 `month/day` 返回成字符串，
   而节日表里是数字，`f.month === date.month` 全等比较恒为 false，
   于是元旦、圣诞、Minecraft 生日这些一个都触发不了。复刻版统一用 int。
2. **跨年倒计时算不出来**。上游 `date.year + 1` 是字符串拼接（`"2026" + 1` → `"20261"`），
   `Date.UTC("20261", …)` 得到 NaN，已经过去的节日会被静默丢弃。复刻版显式求"下一次发生日"。
3. **属性转义不全**。XAML 里以 `{` 开头的属性值会被当作标记扩展（`{DynamicResource}`、`{user}`），
   上游的 `escapeXaml` 只转义 `& < >`，后台文案里出现花括号就会整页白屏。
   复刻版统一用 `escape_attr`（额外转义引号与花括号）。
   *必应壁纸地址里的 `&` 也是同一类问题——上游在 Python 里手工 `replace("&","&amp;")`，
   复刻版改由模板的 `|url` 过滤器统一处理。*
4. **上游有一批"算了却没出口"的值**。`_middleware.js` 一直在算人品分数、今日运势、
   随机挑战、今日种子，但改版删掉版本卡时把这些占位符一并删了，只剩计算没有返回值。
   复刻版一度把它们接回页面（一张「今日运势」卡片），现已按需求整张移除——
   代码路径清干净了，数据仍留在 `pclhome/data/` 里，想接回来随时可以。
5. **服务器列表是死代码**。上游的 `templates/server_item.tpl` 从未被任何模板引用。

### 按需求做的定制

1. **版面精简**：移除了「我的日历」「你的信息」「更多」三张卡片，
   以及幸运数字、幸运颜色、公网 IP、玩家 ID 展示；
   三个功能按钮从「你的信息」搬到了「欢迎」卡片，说明文字去掉。
2. **「功能网站」卡片**取代「你的信息」，八个站点用各自的图标。
3. **「今日运势」整张卡片移除**（人品分数、宜忌、随机挑战、今日种子全不要），
   相关代码路径一并清理，`config.json` 里的 `extra_cards` 开关也已删除。
4. **面板与彩蛋整块移除**：`panel.xaml` / `panel.json` / 独立彩蛋网站
   （连同每日一题、画板、复制指令工具箱）都不再生成，主页上的入口也一并删掉。
   上游对应文件仍在仓库里，需要时可以取回。
5. **每日一言改成 uapis.cn 接口**，全站同一句（上游是按 IP 分发的本地 68 条）。
6. **天气改成 uapis.cn 接口**，展示风力等级与湿度（上游显示 km/h）。
   接口只按*调用方* IP 定位，而调用方永远是服务器，所以先用访客 IP 查城市
   （`geo.py`）再带参查询，做到**每个访客看自己城市的天气**。
7. **日期下面加一行农历**（uapis.cn 的 lunartime），接口挂了退回本地换算。
8. **来源守卫可关闭**。上游一律拦截非 PCL/非浏览器 UA，本机用 curl 调试很不方便；
    `PCLHOME_GUARD=0` 可临时关掉（默认仍是开启）。
9. **后台大幅精简**。上游 1636 行里有一半在调 Cloudflare GraphQL 查用量、批量读写 KV，
    单机部署用不上。复刻版只留维护/封禁/公告/一言/节日/统计/重新生成。
10. **不分发第三方素材**。上游的配图（第三方摄影）与音乐（商业音乐）都没有随本项目分发。

## 七、项目结构

```
pcl-homepage-py/
├── run.py                    便捷入口（等价 python -m pclhome）
├── config.example.json       配置示例
├── pclhome/
│   ├── __main__.py           CLI：serve / generate / doctor / version
│   ├── config.py             配置（默认值 + config.json + 环境变量）
│   ├── generate.py           构建期：填模板产出静态 XAML
│   ├── render.py             请求期：把 __TOKEN__ 换成本次请求的内容
│   ├── server.py             HTTP 服务、路由、来源守卫、封禁/维护
│   ├── admin.py              管理后台（页面 + API）
│   ├── personalize.py        哈希、北京时间、确定性抽取
│   ├── saying.py             每日一言（uapis.cn + 按天缓存 + 三层兜底）
│   ├── lunartime.py          日期下面那行农历（uapis.cn + 本地换算兜底）
│   ├── log.py                日志：时间戳 + 级别 + 可选写文件
│   ├── ai.py                 AI 日志分析（抓日志 + 调接口 + 配额 + 卡片/弹窗）
│   ├── weather.py            实时天气（按城市缓存，多级兜底）
│   ├── geo.py                访客 IP → 城市（天气定位用，两个源 + 24 小时缓存）
│   ├── sites.py              功能网站：图标镜像 + 列表项渲染
│   ├── lunar.py              农历换算、节日、倒计时
│   ├── xaml.py               XAML 转义与组件
│   ├── store.py              配置存储（JSON）+ 访问统计（SQLite）
│   ├── doctor.py             体检
│   ├── data/                 文案与题库（由上游移植，勿手改）
│   └── templates/            页面结构（唯一事实来源：Custom.xaml.tpl）
├── static/
│   ├── images/icons/         网站图标（构建期抓取，见该目录 README）
│   └── music/                音乐（自备）
├── tools/                    移植工具（仅同步上游内容时用）
└── var/                      运行时数据：store.json / stats.db / 缓存（已 gitignore）
```

**占位符两套，别混**：`{{TOKEN}}` 由构建期 `generate.py` 填（壁纸地址、功能网站列表），
`__TOKEN__` 由请求期 `render.py` 填（日期、天气、一言、节日）。
模板里 `<!-- __TOKEN__ -->` 这种注释形态表示"这一整块运行时插入"，没内容时连注释一起消失。

## 八、部署

本机自用 `python run.py serve` 就够了。要让别人也能用，把它放到一台有公网地址的机器上：

```bash
PCLHOME_HOST=0.0.0.0 PCLHOME_PORT=8787 PCLHOME_ADMIN_TOKEN=换成强令牌 python run.py serve
```

* **一定要改 `admin_token`**，`doctor` 在监听非回环地址而令牌仍是默认值时会直接报 FAIL。
* 放在 Nginx / Caddy 后面时，`base_url` 填对外域名，并确保反代传了
  `X-Forwarded-For` 与 `X-Forwarded-Proto`（默认信任这些头，见 `trust_proxy_headers`）。
  天气和"按 IP 个性化"都依赖这个头拿到真实访问者 IP。
* 想让它开机自启，Windows 用任务计划程序、Linux 用 systemd 拉起 `run.py serve` 即可。
* 改完模板或 `config.json` 后，在后台点「重新生成 XAML」，或跑一次
  `python run.py generate`（服务会按文件 mtime 自动重载模板，不用重启）。

### 日志

服务默认每个请求打一行，带上时间、访问者 IP、状态码、字节数、耗时，
以及这次请求**算出了什么**（问候语、一言来源、天气城市、农历、节日、有没有公告）：

```
23:12:29.402 [INFO] [Geo] 112.47.125.174 → 泉州市 350500（pconline）
23:12:29.589 [INFO] [Weather] 泉州市 25° 晴（泉州市（按访问者 IP 112.47.125.174），191ms）
23:12:29.601 [INFO] [HTTP] 112.47.125.174 GET /Custom.xaml → 200 11086B 838ms
                            | 问候=夜深了 一言=api 天气=泉州市 农历=农历八月十五 节日=中秋节 公告=无
23:12:30.041 [INFO] [HTTP] 112.47.125.174 GET /Custom.xaml → 200 11086B 101ms
23:12:30.202 [WARN] [HTTP] 127.0.0.1 GET /Custom.xaml → 200 1175B 3ms
                            | 来源守卫拦截：UA=curl/8.0
```

访客 IP 只在**第一次**出现时查归属地（`[Geo]` 那行），之后 24 小时内都走缓存；
天气按城市缓存 1 小时，所以上面第二条请求只要 101ms。

第二次请求只花了 101ms —— 外部接口的取数结果都缓存着，看耗时就能判断缓存有没有生效。

```bash
python run.py serve                  # 默认：每请求一行 + 关键事件
python run.py serve -v               # 详细：外加 http.server 细节、UA/origin、缓存命中、本地换算
python run.py serve -q               # 安静：只看 WARN 和 ERROR
python run.py serve --log-file var/server.log    # 同时写文件（追加）
```

启动时还会把**当前生效的设置**完整打一遍（监听地址、BASE_URL、来源守卫、
一言/农历/天气的来源与语料、壁纸、功能网站数量、生成物时间、图标是否齐、令牌是否还是默认值），
排查问题时基本看这一段就够了。后台的每次登录、改配置、封禁、重新生成也都会记一条。

### 从上游同步文案

数据是用脚本从上游 JS 里"跑出来"再转写的，不是手抄：

```bash
node tools/dump_upstream.mjs <上游仓库路径> > build/upstream.json
python tools/port_data.py
```

## 九、常见问题

**主页加载不出来？** 先跑 `python run.py doctor`。若 PCL 那边白屏，确认填的地址以
`/Custom.xaml` 结尾，且 `Custom.xaml.version` 能返回一串数字。

**用 curl 测试一直返回"访问被拒绝"？** 来源守卫生效了——换成浏览器的 UA，或
`PCLHOME_GUARD=0`。PCL 自己不受影响。

**内容不更新？** PCL 缓存了主页：关掉 PCL → 删除 `%appdata%\PCL\Cache` → 重开。
复刻版每次都返回新的 `.version` 时间戳，正常情况下 PCL 会自己重新拉。

**天气显示的好像不是我的城市？** 正常情况下这是按**访客自己的 IP** 定位的，所以
先看日志里那两行：

```
[Geo] 112.47.125.174 → 泉州市 350500（pconline）
[Weather] 泉州市 25° 晴（泉州市（按访问者 IP 112.47.125.174），191ms）
```

`[Geo]` 那行说明查到的是哪个城市。如果它显示的不是你在的地方，按下面的顺序排查：

* **看到「服务器本机（内网地址）」** → 访问者 IP 是 `127.0.0.1` 这类内网地址，
  说明请求没过公网（本机调试就会出现这种情况，属正常）。
* **看到「服务器本机（归属地查不到）」** → 两个地理源都没答上来。境外 IP 在
  pconline 会回 `err=noprovince`，只能靠 ip-api 兜底；两个都挂就退回服务器所在地。
  换 `weather_city` 固定城市即可绕开。
* **日志里访问者 IP 就是服务器自己的公网 IP，天气显示机房所在城市** → 这不是
  代码的问题：访问者的流量是**从服务器本机出去的**，服务端只能看到服务器自己。
  最常见的原因是这台服务器同时跑着代理（Xray/v2ray/Clash 节点），而访问者本机
  正把这台服务器的地址也走了代理。查一下 `ps aux | grep -Ei 'xray|v2ray|clash'`，
  有的话在客户端路由里给服务器地址加一条直连规则：

  ```yaml
  rules:
    - IP-CIDR,<服务器IP>/32,DIRECT,no-resolve
  ```

  加完再访问，日志里的 IP 就会变成访问者的真实公网 IP。
* **运营商把出口 IP 落在邻市** → 这是 IP 库本身的精度问题（同一个泉州 IP，
  pconline 说泉州、ip-api 说广州）。换个源未必更好，通常只能接受。
* **想固定成某个城市** → 设 `weather_city`（如 `"北京"`），所有人都看这个。
* **服务器在境外、访客也基本在境外** → 设 `enable_geo: false`，
  省掉每次查询，直接按服务器 IP 定位。

实在取不到就只显示一条黄色提示，页面其余部分照常。

**这会把访客 IP 发给第三方吗？** 会。查归属地必须把 IP 发给 `whois.pconline.com.cn`
（或备用的 `ip-api.com`）。结果按 IP 缓存 24 小时，所以同一个 IP 一天只外发一次。
不想外发就设 `enable_geo: false`。

**某个网站的图标没显示？** 见 `static/images/icons/README.md`。常见原因：
Minecraft Wiki 只对浏览器放行（命令行抓会 403）、MineBBS 只提供 WebP（WPF 解不了）、
CurseForge 有防盗链。放一张 PNG 进那个目录即可。

**一言是英文？** 说明 `saying_source` 被改成空了。填回 `caoxingyu-sentence`
（接口的中文语料），或换成你喜欢的其它 slug。

**想看渲染后的完整 XAML？** 后台点「预览渲染结果」，或
`curl -A "PCL2/2.13.0" http://127.0.0.1:8787/Custom.xaml`。

## 十、协议与致谢

代码以 MIT 协议开源，与上游一致。文案与题库数据移植自上游（MIT），
农历数据表、XAML 页面结构同样来自上游。

* [PCL2](https://github.com/Meloong-Git/PCL) —— 启动器本体，XAML 控件与事件机制都来自它
* [wlasfjdskfj/pcl-homepage](https://github.com/wlasfjdskfj/pcl-homepage) —— 被复刻的上游项目
* [uapis.cn](https://uapis.cn/) —— 每日一言与实时天气接口
* [Minecraft Wiki](https://zh.minecraft.wiki/) · [Mojang](https://www.minecraft.net/) —— 版本信息
* [NewsHomepage](https://github.com/Light-Beacon/PCL2-NewsHomepage) —— 上游主页的设计灵感

> ⚠️ 各站图标版权归各站所有，本项目只在链接旁标注性地引用其 favicon；
> 音乐与配图需自备有使用权的素材。
