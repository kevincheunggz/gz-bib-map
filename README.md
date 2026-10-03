# 广州必比登地图

把米其林指南广州“必比登推介”餐厅全部标在百度地图上的安卓 App。名单每月自动从米其林官网核对一次。

## 安装

在本仓库的 [Releases](../../releases/tag/latest) 下载 `gz-bib-map.apk`，在手机上打开安装（需要允许“安装未知来源应用”）。

第一次打开时会提示填写百度地图 AK。申请方法是登录 [百度地图开放平台控制台](https://lbsyun.baidu.com/apiconsole/key)，创建应用，类型选“浏览器端”，Referer 白名单填 `*`，复制 AK 粘贴到 App 里即可。也可以把 AK 存为仓库的 Actions Secret `BAIDU_AK`，之后打包的 App 会自带 AK。

## 功能

- 全部餐厅显示在百度地图上，红色为 ¥（实惠），深蓝为 ¥¥（中档）
- 按菜系、价位筛选，搜索店名、地址
- 定位后按距离排序，找附近的必比登
- 点“导航”直接跳到百度地图 App，点“电话”直接拨号
- 新上榜的餐厅会标“新”

## 自动更新怎么运作

| 环节 | 位置 |
| --- | --- |
| 每月 1 日抓取米其林官网名单，有变化就提交 `data/restaurants.json` | `.github/workflows/update-data.yml` |
| App 每次打开时通过 jsDelivr 拉取最新名单，离线时用上次的名单 | `web/index.html` |
| 修改 App 代码后自动打包 APK 并发布到 Releases | `.github/workflows/build-apk.yml` |

名单更新不需要重新安装 App。想立刻更新，可以在 Actions 页面手动运行“每月更新餐厅名单”。

抓取脚本有保护机制。如果抓到的餐厅数比上次少了四成以上（通常是网站改版），就不会覆盖名单，Actions 会报错并发邮件提醒。

## 目录

- `web/index.html` 地图页面
- `data/restaurants.json` 餐厅名单
- `scripts/scrape.py` 抓取脚本
- `app/` 安卓外壳（WebView）

餐厅信息来自 [米其林指南](https://guide.michelin.com/sg/zh_CN/guangdong/guangzhou_1026985/restaurants/bib-gourmand)，仅供个人使用。
