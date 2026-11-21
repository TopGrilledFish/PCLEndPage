# NewEndPage

用 Go 重写的 PCLEndPage 主页服务（旧版 `pclhome` 的替代实现）。目标：单二进制、低内存（约 14MB RSS）、高并发，主页外观与原 Python 版一致，并在顶部多一条「您现在访问的是新版测试站点」测试横条。

## 结构

| 文件 | 作用 |
| --- | --- |
| `main.go` | 配置加载（JSON）、路由（`/`、`/Custom.xaml`、`/mark*`、版本端点、`/healthz`）、静态资源、其余路径反代旧服务 |
| `render.go` | 两阶段模板替换：构建期 `{{TOKEN}}`、请求期 `__TOKEN__`（含 `<!-- __X__ -->` 整块形态） |
| `content.go` | 首页数据组装：问候语/日期/星期/农历/节日/倒计时/一言/按钮/站点/关于卡 |
| `weather.go` | 天气卡片 + IP 定位（ip-api）、3 小时缓存、分级建议 |
| `saying.go` | 每日一言（uapis 接口 + `var/saying.json` 缓存 + 内置 68 条兜底） |
| `lunar.go` / `lunar_service.go` | 农历换算（1900–2100）与节气接口缓存 |
| `uapi.go` | uapis.cn 多密钥轮换、401/402 冷却 30 分钟、匿名兜底 |
| `i18n.go` | 语言表（缺键回退 en → zh-hans） |
| `assets/` | 从旧版导出的模板片段与数据，`//go:embed` 进二进制 |
| `_deploy/` | 部署脚本：`setup.py` 生成服务器配置、`newendpage.service`、`patch_nginx.py` / `patch_vhosts.sh` 配置 50% 分流 |

## 构建

```bash
go build -trimpath -ldflags "-s -w" -o newendpage .          # 本机
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags "-s -w" -o newendpage.linux .
```

## 部署（50% 分流）

1. 服务器 `/root/NewEndPage/` 放二进制，`config.json` 由 `_deploy/setup.py` 生成（`base_url`、`version`、`uapi_keys` 从旧版 `/root/PCLEndPage/config.json` 取、`legacy_backend`）。
2. `systemctl enable --now newendpage`（监听 `127.0.0.1:8778`，`legacy_backend` 指向旧版 `127.0.0.1:8787`）。
3. nginx 在 http 级加 `split_clients`：50% → `127.0.0.1:8778`，其余 → `127.0.0.1:8787`；两个 end vhost 的 `location /` 改 `proxy_pass http://$pcl_upstream;`。
4. 未实现的路径（`/admin`、`/ai_page.json`、`/images/*` 等）由 Go 服务透明反代回旧版。

## 与旧版的差异

- 顶部新增「您现在访问的是新版测试站点」横条（`Theme="Yellow"` 的 MyHint）。
- 暂未实现旧版的访问验证（每 IP 7 天看图验证）与 `/` 的 PCL2 UA 过滤，测试期后需补齐。
