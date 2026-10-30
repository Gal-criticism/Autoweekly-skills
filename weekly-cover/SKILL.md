---
name: weekly-cover
description: 用 CG 立绘和三个周报封面模板，通过 Photoshop 原生脚本接口批量生成封面（16:9、横幅、4:3），自动按 CG 边缘主色配色，并按模板几何避开人脸遮挡。
---

# 周报封面

把用户提供的 CG 立绘放进三个周报封面模板，用本机 Photoshop 替换智能对象，
导出 PNG / JPG / 带图层的 PSD。自动从 CG 边缘主色推导背景色，并按模板
几何摆放人物，避免人脸被标题文字或画布裁切遮挡。

## 本机约定

- 模板母版（只读，不改原文件）：
  - T1 16:9：`E:/114514/周报视频_封面模板_16-9.psd`（2400×1500）
  - T2 横幅：`E:/114514/周报视频_封面模板_V100_文章_1.2-2.psd`（3525×1500）
  - T3 4:3：`E:/114514/周报模板_视频封面_1.3 (1).psd`（2400×1800）
- Photoshop：`D:/114514/Adobe/Adobe Photoshop 2023/Photoshop.exe`
- Python：`E:/Anaconda3/python.exe`（需要 Pillow）
- 直接接口：Windows PowerShell 5.1 的 COM `DoJavaScriptFile`，连接现有
  Photoshop，未运行时自动启动。不要换成 PowerShell 7 的 COM 运行时。
- 默认输出：`E:/project/周报图片设计/cover_runs`
- 以上路径均可用环境变量覆盖（未设置时回退到上文本机路径）：
  `WEEKLY_COVER_T1_SOURCE`、`WEEKLY_COVER_T2_SOURCE`、`WEEKLY_COVER_T3_SOURCE`、
  `WEEKLY_COVER_PHOTOSHOP`、`WEEKLY_COVER_OUTPUT`。

## 输入

用户提供一张或多张 CG 立绘（PNG/JPG），以及要套用的模板（默认全部三个）。
一张 CG × 一个模板 = 一张封面。日期用于 T2 横幅的新闻期数，默认按上海
时间的“上周六至本周五”计算。

## 执行

以下 `<skill>` 为这个 SKILL.md 所在目录，全部使用绝对路径。

1. 准备批次（CG × 模板的跨乘积）：

```powershell
& 'E:/Anaconda3/python.exe' '<skill>/scripts/cover.py' prepare --cg '<cg1.png>' --cg '<cg2.png>' --template all --output 'E:/project/周报图片设计/cover_runs'
```

   `--template` 取 `1`、`2`、`3` 或 `all`。日期覆盖用
   `--date "2026.10.24 - 2026.10.30"`，或 `--period-start 2026-10-24
   --period-end 2026-10-30`。从返回值记录 `run_dir`。

2. 直接调用 Photoshop 渲染并自动核验：

```powershell
& 'E:/Anaconda3/python.exe' '<skill>/scripts/cover.py' render '<run_dir>'
```

   render 使用独立的 Windows PowerShell 5.1 STA 辅助进程，调用一次
   `DoJavaScriptFile`。默认最多等待 300 秒，可用 `--timeout` 缩短；
   Photoshop 路径改变时传 `--photoshop`。每张封面导出 PNG、JPG、带图层
   PSD，并生成 `contact_sheet.jpg` 九宫格预览。

3. 可选：T2 横幅底色校色。T2 原始底为紫粉渐变，若 CG 是冷蓝调：

```powershell
& 'E:/Anaconda3/python.exe' '<skill>/scripts/cover.py' refine '<run_dir>'
```

   refine 把 T2 的背景与纯色层按蓝调偏移（色相 -90、饱和 -30、明度 +8），
   输出到 `refined/` 子目录，并重建九宫格。

4. 查询状态 / 单独核验 / 记录目视检查：

```powershell
& 'E:/Anaconda3/python.exe' '<skill>/scripts/cover.py' status '<run_dir>'
& 'E:/Anaconda3/python.exe' '<skill>/scripts/cover.py' verify '<run_dir>'
& 'E:/Anaconda3/python.exe' '<skill>/scripts/cover.py' review '<run_dir>' --note '<检查结论>'
```

   `rendered` 只表示 Photoshop 导出完成，`verified` 才表示尺寸与可读性
   程序核验通过。程序不会识别人脸或美观度；必须目视确认人脸没有被
   标题文字或画布裁切遮挡，且人物整体设计完整。没有完成目视核验时
   明确说明，不能宣称最终验收完成。

## 人脸遮挡与几何

- T3 是 4:3 画布、CG 通常是 16:9。直接 cover 裁切会把人脸放大并压到
  标题文字。默认改为 contain 适配宽度并下移，人脸避开右上「VOL」标题，
  底部落在渐变背景带上。偏移见
  `scripts/runtime/cover/model.py` 的 `DEFAULT_TEMPLATES[3]["box"]`。
- T1、T2 的 CG 槽位比例接近 16:9，无需裁切，保留原摆放。
- 换 CG 后人脸仍被压住时，调整对应模板的 `box`（`[left, top, right, bottom]`）
  后重新 prepare；不要改模板母版内容。
- 「新闻速递」文字必然覆盖人物下半身，这是模板设计；除非移动文字层或
  大幅缩小人物，否则无法完全避免。

## 交付

交付 PNG/JPG/PSD 路径、`contact_sheet.jpg` 九宫格和日期。保留模板母版
与用户原始 CG 不动，所有操作在独立批次副本上执行，不清理历史输出。
