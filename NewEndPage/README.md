# NewEndPage

用 Go 重写的 PCLEndPage 主页服务（旧版 `pclhome` 的替代实现）。目标：单二进制、低内存（约 14MB RSS）、高并发，主页外观与原 Python 版一致，并在顶部多一条「您现在访问的是新版测试站点」测试横条。

## 新旧版切换（按 IP 选择，不再随机分流）

- 默认所有访客走旧版；用户在 PCL 的「个性设置」页点「切换到新版测试站点」后，该 IP 才进入新版（记录在 `var/pref.json`），点「返回旧版主页」退出。
- 入口：`/_new` 选择新版、`/_old` 返回旧版；设置页按钮由 Go 反代 `/settings_page.xaml` 时注入（按钮走「打开帮助」，用系统浏览器访问）。
- 新版入口的每个可见页面都有标识：主页与 XAML 页面顶部 `MyHint` 横条，`/verify`、`/admin` 等 HTML 页固定横幅；响应头 `X-New-EndPage: 1/0` 可直接区分新旧。
- 人机验证复用旧版：Go 读旧版 `/root/PCLEndPage/var/verify.json`，未通过验证的 IP 仍返回旧版看图验证卡（每 IP 7 天）；验证通过后新版才渲染主页。

## 结构

| 文件 | 作用 |
| --- | --- |
| `main.go` | 配置加载（JSON）、路由与新旧版决策（`/`、`/Custom.xaml`、`/mark*`、版本端点、`/healthz`、`/_new`、`/_old`）、其余路径反代旧服务并注入新版标识 |
| `pref.go` | 按 IP 记录「选择新版」偏好（JSON 原子写） |
| `verify.go` | 读取旧版 `verify.json`，判断该 IP 是否已通过人机验证 |
| `render.go` | 两阶段模板替换：构建期 `{{TOKEN}}`、请求期 `__TOKEN__`（含 `<!-- __X__ -->` 整块形态） |
| `content.go` | 首页数据组装：问候语/日期/星期/农历/节日/倒计时/一言/按钮/站点/关于卡 |
| `weather.go` | 天气卡片 + IP 定位（ip-api）、3 小时缓存、分级建议 |
| `saying.go` | 每日一言（uapis 接口 + `var/saying.json` 缓存 + 内置 68 条兜底） |
| `lunar.go` / `lunar_service.go` | 农历换算（1900–2100）与节气接口缓存 |
| `uapi.go` | uapis.cn 多密钥轮换、401/402 冷却 30 分钟、匿名兜底 |
| `i18n.go` | 语言表（缺键回退 en → zh-hans） |
| `assets/` | 从旧版导出的模板片段与数据，`//go:embed` 进二进制 |
| `_deploy/` | 部署脚本：`setup.py` 生成服务器配置、`newendpage.service`、`disable_split.py`（关闭 nginx 随机分流，入口统一走 8778）、`update_config.py` |

## 构建

```bash
go build -trimpath -ldflags "-s -w" -o newendpage .          # 本机
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags "-s -w" -o newendpage.linux .
```

## 部署

1. 服务器 `/root/NewEndPage/` 放二进制，`config.json` 由 `_deploy/setup.py` 生成（`base_url`、`version`、`uapi_keys` 从旧版 `/root/PCLEndPage/config.json` 取、`legacy_backend`），再补 `verify_enabled`、`verify_days`、`verify_file`、`pref_file`。
2. `systemctl enable --now newendpage`（监听 `127.0.0.1:8778`，`legacy_backend` 指向旧版 `127.0.0.1:8787`）。
3. 运行 `_deploy/disable_split.py`：删除 nginx 的 `split_clients`/`map`，两个 end vhost 的 `location /` 与 `/admin` 都改为 `proxy_pass http://127.0.0.1:8778;`，新旧版完全由 Go 按 IP 偏好决定。
4. 未实现的路径（`/admin`、`/ai_page.xaml`、`/images/*` 等）由 Go 透明反代回旧版；旧版本继续跑 `pclhome.service`。

## 与旧版的差异

- 顶部新增「您现在访问的是新版测试站点」横条（主页 `MyHint`，HTML 页固定横幅）。
- 访问验证复用旧版（同一份 `verify.json`、同一套看图验证），不再区分新旧实现。
- 访问统计、AI、计算器、后台等仍由旧版提供，Go 只做入口与主页渲染。
