# 角标母版规格

`weekly-bgm-overlay` 打开母版 PSD 后按**图层名**定位可编辑文本层，渲染时隐藏
其它所有图层，只保留当前栏目、日期、曲名、作者四层。

## 获取母版

- 文件：`weekly-bgm-overlay-master.psd`
- 画布：2160×1080，RGB，8 bit/通道
- 下载：<https://github.com/GalgameCriticism/Autoweekly-skills/releases/tag/templates-v1>
- SHA256：`ABDD472A0B266611273ABC1D66559C62727565C7F6F74A8336636405DC3A4C7B`
- 环境变量：`WEEKLY_OVERLAY_TEMPLATE`

## 必需图层

按名称精确匹配，同名图层必须唯一。

| 图层名 | 作用 | 字体 | 预留区域 `boundsNoEffects` |
| --- | --- | --- | --- |
| `新作情报` / `汉化情报` / `周边情报` / `简讯` | 四选一的栏目名 | MiSans-Semibold | `[60, 170, 2060, 234]` |
| `【新闻日期】` | 期数范围，如 `2026.10.24 - 2026.10.30` | NotoSerifCJKsc-SemiBold | `[60, 234, 2060, 285]` |
| `【音乐名】` | 曲名 | MiSans-Semibold | `[60, 315, 2060, 388]` |
| `【音乐作者】` | 作者 | MiSans-Regular | `[60, 388, 2060, 445]` |

约束：

- 每个图层必须是 `ArtLayer` 且 `kind === LayerKind.TEXT`，否则直接报错。
- 写入文本后若实际字形超出预留区域，渲染失败，**不做自动缩放或截断**。
- 渲染前会把所有图层隐藏，再显式显示上述四层，背景/装饰层不会导出。

## 字体

渲染前会检查 `app.fonts` 中存在以下 PostScript 名称，缺一即失败：

- `MiSans-Semibold`
- `MiSans-Regular`
- `NotoSerifCJKsc-SemiBold`

字体不随仓库分发，请自行安装（许可与下载以各字体官方说明为准）。

## 用自己的母版替换

图层 ID 不重要，脚本按名字查找。只要满足画布尺寸、色彩模式、图层名、字体和
预留区域，就能直接替换。
