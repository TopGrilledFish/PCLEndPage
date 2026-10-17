# 网站图标

首页「功能网站」卡片的图标。文件按 **站点域名** 命名（`www.` 去掉、点换成横线），
扩展名用 `.png` / `.ico` / `.jpg`（PCL 用的是 WPF 解码器，**不支持 WebP 和 SVG**）。

| 站点 | 文件名 |
|---|---|
| Minecraft Wiki | `zh-minecraft-wiki.png` |
| MineBBS | `minebbs-com.png` |
| Modrinth | `modrinth-com.png` |
| CurseForge | `curseforge-com.png` |
| MC 百科 | `mcmod-cn.png` |
| MC 官网 | `minecraft-net.png` |
| NameMC | `namemc-com.png` |
| mclo.gs | `mclo-gs.png` |

## 图标是怎么来的

`python run.py generate` 会按 `config.json` 里每个站点的 `icon` 地址，尽力把图标下载到本目录
（下载时带浏览器 UA 和 Referer，并且只接受上面说的三种格式）。**下得到就用本地文件**
（页面指向 `/images/icons/xxx.png`，没有防盗链、没有格式坑、缓存 30 天）；
**下不到就退回远程地址**，由 PCL 自己去取。

## 有站点显示不出图标怎么办

因为防盗链或 Cloudflare 拦截，这几个站**经常抓不到**，需要你自己放一个：

* **Minecraft Wiki** —— 官方只对浏览器放行，命令行抓会 403
* **MineBBS** —— 只提供 WebP，WPF 解不了
* **CurseForge** —— 有防盗链，不带 Referer 会 403

把任意一张 PNG 命名为上表里的文件名丢进本目录，重新 `python run.py generate` 就会生效
（已有的本地文件不会被覆盖）。也可以直接改 `config.json` 里对应站点的 `icon`，
换成一个能访问的图片地址。

## 计算器图标

「计算器」列表页也是一行一个图标，文件名是 **`calc-<计算器 id>.png`**（id 见
`pclhome/calc.py` 里 `CALCS` 的 `id` 字段），同样本地这份优先。

图取自中文 Minecraft Wiki「计算器」页上各工具自己那个图标（Wiki 自带的 120px 缩略图）。
其中两个原图不是方的，直接塞进列表项那个方形 Logo 位会被 Uniform 缩放压成一条细线，
所以裁掉了多余部分：

| 文件 | 原图 | 处理 |
|---|---|---|
| `calc-chunk.png` | `Chunk.png` 120×600 | 取顶部那个完整的等距区块方块 |
| `calc-uuid.png` | `Steve_JE5.png` 120×270 | 取 Steve 的头部 |

要换图标，把方形 PNG 按上面的文件名丢进本目录即可；找不到本地文件时页面会退回
`calc.py` 里 `_wiki_icon()` 拼出来的 Wiki 远程地址——Wiki 按 UA 拦非浏览器请求，
这个兜底多半也拉不到，所以**图标还是要放本地**。
