# PCLEndPage

PCL2 / PCL-CE 联网主页服务器。

- 当前线上版本：[NewEndPage](NewEndPage/)（Go 单二进制，主页渲染 + AXAML 适配 + 管理后台 + 人机验证 + 统计）
- 旧 Python 版本：已归档到 [legacy-python](legacy-python/)，仅作参考

## 线上

- 网址：https://end.g-fish.dpdns.org
- PCL2 原版主页：`/Custom.xaml`（WPF XAML）
- PCL-CE 主页：`/Custom-CE.xaml`（Avalonia AXAML）
- 管理后台：`/admin`（密码 + TOTP 两步验证）

## 构建

```bash
cd NewEndPage
go build -trimpath -ldflags "-s -w" -o newendpage .
```

部署细节见 [NewEndPage/README.md](NewEndPage/README.md)。

## 目录

- `NewEndPage/`：Go 版服务（主页渲染、AXAML 适配、管理后台、人机验证、统计）
- `legacy-python/`：旧 Python 版（归档，仅作参考；服务器上已由 Go 版接管）
