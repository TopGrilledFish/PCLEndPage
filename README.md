PCL2 主页预设

这是 PCL2 主页预设的公开仓库，主页本体就在这儿，想改可以自己拉下去跑。

访问地址 https://end.g-fish.dpdns.org

仓库地址 https://github.com/TopGrilledFish/PCLEndPage

上游仓库 https://github.com/wlasfjdskfj/pcl-homepage

原作者 wlasfjdskfj

本改版删去了大部分无用功能。

![主页](docs/screens/homepage.png)

**功能介绍**

欢迎部分

1. 日期
2. 必应一图
3. 节日提醒
4. 每日一言
5. 天气预报
6. 4 个功能模块

功能网站

1. 将功能完整挪到了主页面外侧
2. 图标进行缓存

![功能网站](docs/screens/websites.png)

**新加特色功能**

1. 添加 AI 故障分析
   - 每小时可免费使用 3 次，模型走 DeepSeek Flash
   - 可以填写自己的密钥无限使用
2. 小工具
   - 一些玩 MC 可能会用到的计算器
3. 个性化设置
   - 支持切换单位
   - 支持切换语言
   - 支持切换色块浓度
   - 支持导出个性化码

![关于卡片](docs/screens/about-card.png)

**使用方法**

1. 打开 PCL2
2. 依次点击 设置 -> 个性化 -> 主页
3. 在「预设」里找本主页；找不到就选「联网更新」，下载地址填 https://end.g-fish.dpdns.org
4. 回到启动页，完成

**自己部署一份**

需要 Python 3.10 以上，没有别的依赖。

```
git clone https://github.com/TopGrilledFish/PCLEndPage.git
cd PCLEndPage
python run.py
```

默认开在 127.0.0.1:8787，PCL 里填这个地址就能看到。配置照 config.example.json 复制一份 config.json 改，不建也能跑。

改了主页模板要重新生成一次 Custom.xaml：`python -m pclhome generate`。出问题先跑 `python -m pclhome doctor --offline`。

放公网就前面挂个 nginx 反代，注意别把项目目录当静态目录整个暴露出去，只放出 Custom.xaml 和 static/。

多说一句，写这个的时候 AI 帮了不少忙，代码里有相当一部分是它和我一起改出来的。

**鸣谢**

- PCL 启动器作者 龙腾猫跃
- 原作者 wlasfjdskfj
- README 的写法参考了 Light-Beacon 的 PCL2-NewsHomepage

**许可**

MIT。

上游是 https://github.com/wlasfjdskfj/pcl-homepage。借鉴这个仓库的代码或样式时，请在文件头标注原作者，并且同样用 MIT。如果做的是 PCL 主页，还要在主页里写明出处，一般放在末尾的「特别鸣谢」里，字要看得清，也别写容易让人反感的内容。

欢迎 Fork，有问题可以去我主页找联系方式。

还有啥想要的可提，不加没啥软用的。
