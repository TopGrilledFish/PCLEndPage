# NewEndPage

用 Go 重写的 PCLEndPage 主页服务（旧版 `pclhome` 的替代实现）。目标：单二进制、低内存（约 14MB RSS）、高并发，主页外观与原 Python 版一致。

## 入口

- **PCL2 原版**：主页地址保持 `https://end.g-fish.dpdns.org/Custom.xaml`（WPF XAML）。
- **PCL-CE（Avalonia）**：主页地址改为 `https://end.g-fish.dpdns.org/Custom-CE.xaml`（AXAML：`pclcontrols:` 控件前缀 + `using:PCL.UI.Controls` 命名空间）。
- 两个入口各自独立，服务端不再按 UA/IP 猜测：`/Custom.xaml` 永远 WPF，`/Custom-CE.xaml` 永远 AXAML（含 `/mark/Custom-CE.xaml`、`/YYYY/M/D/Custom-CE.xaml` 与各自的 `.xaml.ini` 版本端点）。
- PCL-CE 的 AXAML 校验器把按钮的几何 `Logo` 当图片地址检查，因此 AXAML 版按钮改用内置图标 `SvgIcon="lucide/..."`（内存优化=按钮禁用，因为 PCL-CE 不支持该事件类型）。
- 人机验证：未通过验证的 IP 访问 `/Custom-CE.xaml` 时返回旧版 AXAML 验证卡（每 IP 7 天，看图验证；无 `{Binding}`，改为「在浏览器中验证」按钮）；通过后渲染新版主页。
- 后台：`/admin` 令牌 + TOTP 两步验证。

## 结构

| 文件 | 作用 |
| --- | --- |
| `main.go` | 配置加载、路由（`/`、`/Custom.xaml`、`/Custom-CE.xaml`、`/mark*`、版本端点、`/healthz`、`/admin`）、未迁移路径反代旧服务 |
| `axaml.go` | WPF XAML → AXAML 转换：命名空间、`local:`→`pclcontrols:`、去属性元素、几何 Logo→`SvgIcon`、不支持事件类型处理 |
| `admin.go` | 新版后台 `/admin`：令牌登录 + TOTP 两步验证、概览/配置/封禁/维护/AI/日志/工具，覆盖旧版 24 个 action |
| `stats.go` | 新版首页渲染计数（`var/newstats.json`） |
| `verify.go` | 读取旧版 `verify.json`，判断该 IP 是否已通过人机验证 |
| `render.go` | 两阶段模板替换：构建期 `{{TOKEN}}`、请求期 `__TOKEN__`（含 `<!-- __X__ -->` 整块形态） |
| `content.go` | 首页数据组装：问候语/日期/星期/农历/节日/倒计时/一言/按钮/站点/关于卡 |
| `weather.go` | 天气卡片 + IP 定位（ip-api）、3 小时缓存、分级建议 |
| `saying.go` | 每日一言（uapis 接口 + `var/saying.json` 缓存 + 内置 68 条兜底） |
| `lunar.go` / `lunar_service.go` | 农历换算（1900–2100）与节气接口缓存 |
| `uapi.go` | uapis.cn 多密钥轮换、401/402 冷却 30 分钟、匿名兜底 |
| `i18n.go` | 语言表（缺键回退 en → zh-hans） |
| `render_test.go` | 模板替换、横条注入、版本端点、TOTP 等单元测试 |
| `assets/` | 从旧版导出的模板片段与数据，`//go:embed` 进二进制 |
| `_deploy/` | 部署脚本：`setup.py`/`update_config.py`/`update_config_admin.py` 生成服务器配置、`newendpage.service`、`verify_new.sh` |

## 构建

```bash
go build -trimpath -ldflags "-s -w" -o newendpage .          # 本机
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags "-s -w" -o newendpage.linux .
```

## 部署

1. 服务器 `/root/NewEndPage/` 放二进制，`config.json` 由 `_deploy/setup.py` 生成（`base_url`、`version`、`uapi_keys` 从旧版 `/root/PCLEndPage/config.json` 取、`legacy_backend`），再补 `verify_enabled`、`verify_days`、`verify_file`、`admin_token`、`admin_session_hours`、`legacy_log_file`、`log_file`、`store_file`。
2. `systemctl enable --now newendpage`（监听 `127.0.0.1:8778`，`legacy_backend` 指向旧版 `127.0.0.1:8787`）。
3. nginx 两个 end vhost 的 `location /` 全部 `proxy_pass http://127.0.0.1:8778;`（无随机分流）。
4. 尚未迁移的路径（`/ai_page.xaml`、`/calc.*`、`/settings_page.xaml`、`/visit_page.xaml`、`/images/*` 等）由 Go 透明反代回旧版 `pclhome.service`，旧版日志在后台展示。

## 与旧版的差异

- 新增 `/Custom-CE.xaml`（AXAML）入口给 PCL-CE，`/Custom.xaml` 保持 WPF 给 PCL2 原版。
- 访问验证复用旧版（同一份 `verify.json`、同一套看图验证），PCL-CE 下用浏览器验证按钮。
- `/admin` 由本服务接管：令牌登录 + TOTP 两步验证；概览、配置、封禁、维护、AI、日志、工具全部可用，旧版日志按 `[WARN]` 红 / `[INFO]` 蓝着色。
- 首页渲染次数记入 `var/newstats.json`（后台概览显示）。
- 版本端点：`/Custom.xaml.version`、`/version`、`/mark/version` 用模板内容 crc32（不再随请求变化）；`/YYYY/M/D/version` 为 `YYYYMMDD`；`.xaml.ini` 返回同一模板版本。
- 其余路径（AI、计算器、访问浏览、图片等）仍由 Go 透明反代回旧版。
