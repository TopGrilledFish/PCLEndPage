# NewEndPage

用 Go 重写的 PCLEndPage 主页服务（旧版 Python `pclhome` 的替代实现）。单二进制、低内存、高并发，主页外观与原 Python 版一致。旧版代码已归档到仓库根目录的 `legacy-python/`，服务器上不再运行。

## 入口

| 地址 | 客户端 | 格式 |
| --- | --- | --- |
| `/Custom.xaml` | PCL2 原版 | WPF XAML |
| `/Custom-CE.xaml` | PCL-CE | AXAML（顶部有「您现在访问的是 PCL2 CE 兼容版」提示） |
| `/mark/Custom.xaml`、`/mark/Custom-CE.xaml` | 标记版主页 | 同上，日期交给 PCL 替换 |
| `/YYYY/M/D`、`/YYYY/M/D/Custom-CE.xaml` | 日期版主页 | 同上 |
| `/Custom.xaml.version`、`/Custom.xaml.ini`、`/version` | 版本号 | 模板内容 crc32 + 渲染修订号 |
| `/YYYY/M/D/version`、`/YYYY/M/D/Custom.xaml.ini` | 日期版版本号 | `YYYYMMDD` |

PCL2 与 PCL-CE 完全按 URL 区分，不再按 UA 或 IP 标记。

## 功能（全部由 Go 实现）

- 主页：问候语/日期/星期/农历/节日/倒计时/一言/天气/公告/按钮/站点/关于卡，WPF 与 AXAML 两套输出。
- 访问验证：5×7 点阵看图验证码、失败锁定、连续失败升级 Cloudflare Turnstile、每 IP 7 天；PCL-CE 走浏览器验证（AXAML 不允许 `{Binding}`）。
- 计算器：12 个工具（下界/区块/距离/刻/经验/护甲/堆叠/颜色/UUID/种子/要塞/伤害）+ 快捷值按钮。
- 个性设置、语言选择、访问浏览（中国地图、省份排行、地区分布、趋势、最近来访）。
- AI 故障分析：内置密钥/访客自带密钥、token 额度与全站周额度、高峰倍率、多轮追问、思考过程、任务队列与 `ai_log.json` 记录。
- 后台 `/admin`：令牌登录 + TOTP 两步验证；概览、配置、封禁、维护、公告、AI、日志、体检全部自营，日志按 `[WARN]` 红 / `[INFO]` 蓝着色。
- 静态资源：`/images/*`、`/music/*`、`/favicon.png` 直接从 `static_dir` 读取（带缓存标头与目录穿越防护）。

## 结构

| 文件 | 作用 |
| --- | --- |
| `main.go` | 配置、路由、主页渲染入口、静态资源 |
| `render.go` | 两阶段模板替换（`{{TOKEN}}` 构建期、`__TOKEN__` 请求期） |
| `content.go` | 首页数据组装（问候/日期/农历/节日/一言/公告） |
| `axaml.go` | XAML → AXAML 转换（命名空间、`local:`→`pclcontrols:`、SvgIcon、字体等） |
| `verify.go` / `verify_page.go` | 人机验证与看图验证码生成 |
| `calc.go` / `calc_page.go` | 计算器逻辑与页面 |
| `settings.go` / `languages.go` | 个性设置、语言选择 |
| `visit.go` / `visit_page.go` | 访问统计（`visits.json`）与访问浏览页 |
| `ai.go` / `ai_store.go` / `ai_http.go` / `ai_page.go` / `ai_route.go` | AI 分析：额度、密钥、任务、日志、页面 |
| `admin.go` | 后台 |
| `weather.go` / `saying.go` / `lunar.go` / `lunar_service.go` / `uapi.go` | 天气、一言、农历、uapis 密钥轮换 |
| `i18n.go` / `render_test.go` | 语言表与单元测试 |
| `assets/` | 模板、语言包、地图与数据（`//go:embed` 进二进制） |

## 构建

```bash
go build -trimpath -ldflags "-s -w" -o newendpage .
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags "-s -w" -o newendpage.linux .
```

## 部署

1. `/root/NewEndPage/` 放二进制与 `config.json`（关键字段：`base_url`、`version`、`uapi_keys`、`verify_file`、`store_file`、`log_file`、`ai_*`、`admin_token`、`admin_totp_secret`）。
2. `systemctl enable --now newendpage`（监听 `127.0.0.1:8778`）。
3. nginx 的 end vhost 把 `/` 与 `/admin` 都 `proxy_pass http://127.0.0.1:8778;`。
4. 旧版 `pclhome.service` 已停用；`/root/PCLEndPage` 只保留历史数据供查阅，可随时删除。
