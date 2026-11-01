---
name: weekly-cover
description: 用 CG 立绘和三个周报封面模板，通过 Photoshop 原生脚本接口批量生成封面（16:9、横幅、4:3），自动按 CG 边缘主色配色，并按模板几何避开人脸遮挡。
---

# 周报封面

把用户提供的 CG 立绘放进三个周报封面模板，用本机 Photoshop 替换智能对象，
导出 PNG / JPG / 带图层的 PSD。自动从 CG 边缘主色推导背景色，并按模板
几何摆放人物，避免人脸被标题文字或画布裁切遮挡。

## 通用智能体调用

可以在 WorkBuddy 或其他支持本地文件、终端和看图的智能体中使用。
本目录即完整技能包，调用不依赖 Codex 工具名称、安装目录或模型。
首次在其他平台运行时读 [references/agent-usage.md](references/agent-usage.md)，
并用 `python '<skill>/scripts/doctor.py'` 获取环境与命令路径。以下流程保持一致。

## 首次使用：下载模板并连接 Photoshop

用户要求生成封面或角标时，可以从本仓库的 GitHub Release 下载对应 PSD 母版到
本机，随后通过 Photoshop 原生接口使用。模板下载属于该生成流程的一部分，
无需另让用户手动找文件；执行环境的联网和目录权限仍按实际规则申请。

1. 查找可用的 Python 3.11+（封面需要 Pillow，角标还需要 numpy）和 Photoshop.exe。
   优先使用用户配置、已运行 Photoshop 的可执行路径或本机安装路径，不能假定
   示例中的盘符存在。原生接口使用 Windows PowerShell 5.1 STA。
2. 检查所需模板路径。未配置本地模板或路径不存在时，运行：

```powershell
python '<skill>/scripts/download_templates.py'
```

   下载固定的 `templates-v1` Release；脚本校验文件大小、SHA256 和 PSD 画布尺寸。
   封面下载 3 个文件（约 212 MiB），角标下载 1 个（约 45 MiB）。默认放到
   `%LOCALAPPDATA%/Autoweekly/templates/templates-v1`，缺少 LOCALAPPDATA 时放到
   `~/.cache/Autoweekly/templates/templates-v1`。已校验文件复用，失败临时文件清理，
   校验失败的现存文件保留并报错；此时换目录下载，不能覆盖用户母版。
3. 模板选择优先级：单模板环境变量 > 存在的旧版本机母版 > 默认缓存。
   下载到其他目录时，用 `--directory '<目录>'`，并在后续命令同一环境设置
   `AUTOWEEKLY_TEMPLATE_DIR`；若单模板变量指向不存在的文件，修正该变量或将下载
   结果路径显式传入/设置为对应变量，避免继续使用无效路径。
4. 将 Photoshop.exe 路径通过 `render --photoshop '<绝对路径>'` 传入；封面的 refine
   也支持同一参数。确认模板后执行 prepare，再 render。prepare 会复制母版到独立
   批次，Photoshop 打开副本执行 JSX；下载不等于已经导入、渲染或验收成功。

详细母版下载与图层契约见 [references/templates.md](references/templates.md)。
模板不含字体安装包；缺字体时说明缺失的 PostScript 名称，由用户安装后继续。

## 输入

用户提供一张或多张 CG 立绘（PNG/JPG），以及要套用的模板（默认全部三个）。
一张 CG × 一个模板 = 一张封面。日期用于 T2 横幅的新闻期数，默认按上海
时间的“上周六至本周五”计算。

## 执行

以下 `<skill>` 为这个 SKILL.md 所在目录，全部使用绝对路径。

1. 准备批次（CG × 模板的跨乘积）：

```powershell
python '<skill>/scripts/cover.py' prepare --cg '<cg1.png>' --cg '<cg2.png>' --template all --output '<工作区>/cover_runs'
```

   `--template` 取 `1`、`2`、`3` 或 `all`。日期覆盖用
   `--date "2026.10.24 - 2026.10.30"`，或 `--period-start 2026-10-24
   --period-end 2026-10-30`。从返回值记录 `run_dir`。

2. 直接调用 Photoshop 渲染并自动核验：

```powershell
python '<skill>/scripts/cover.py' render '<run_dir>' --photoshop '<Photoshop.exe绝对路径>'
```

   render 使用独立的 Windows PowerShell 5.1 STA 辅助进程，调用一次
   `DoJavaScriptFile`。默认最多等待 300 秒，可用 `--timeout` 缩短；
   Photoshop 路径改变时传 `--photoshop`。每张封面导出 PNG、JPG、带图层
   PSD，并生成 `contact_sheet.jpg` 九宫格预览。

3. 可选：T2 横幅底色校色。T2 原始底为紫粉渐变，若 CG 是冷蓝调：

```powershell
python '<skill>/scripts/cover.py' refine '<run_dir>' --photoshop '<Photoshop.exe绝对路径>'
```

   refine 把 T2 的背景与纯色层按蓝调偏移（色相 -90、饱和 -30、明度 +8），
   输出到 `refined/` 子目录，并重建九宫格。

4. 查询状态 / 单独核验 / 记录目视检查：

```powershell
python '<skill>/scripts/cover.py' status '<run_dir>'
python '<skill>/scripts/cover.py' verify '<run_dir>'
python '<skill>/scripts/cover.py' review '<run_dir>' --note '<检查结论>'
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

## 平台和安全前提

原生渲染目前仅支持 Windows PowerShell 5.1 + Photoshop COM。macOS 可做下载和部分准备，不能调用现有 render/refine 完成渲染；`doctor` 的 `render_supported=false` 是明确的平台限制，不能用“已安装 Photoshop”替代检查。不自动新增 AppleScript、界面模拟或关闭系统保护的回退方案。

只使用本机 prepare 生成的独立批次。渲染入口从已安装的引擎重建 JSX，不信任批次携带的可执行脚本。不要手动打开本批模板再启动渲染；保留已有文档、输出和提交回执。未知结果先检查状态，不重复提交。
