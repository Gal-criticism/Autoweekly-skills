---
name: weekly-cover
description: 用 CG 立绘和三个周报封面模板，通过 Photoshop 原生脚本接口批量生成封面（16:10、横幅、4:3），自动按 CG 边缘主色配色，并按模板几何避开人脸遮挡。
---

# 周报封面

把用户提供的 CG 立绘放进三个周报封面模板，用本机 Photoshop 替换智能对象（Windows 直接调用，macOS 通过脚本菜单执行），
只交付 PNG，每张不超过 5,000,000 字节。自动从 CG 边缘主色推导背景色，并按模板
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

1. 查找可用的 Python 3.11+（封面需要 Pillow）和本机 Photoshop。
   优先使用用户配置、已运行 Photoshop 的可执行路径或本机安装路径，不能假定
   示例中的盘符存在。Windows 原生接口使用 PowerShell 5.1 STA；macOS 使用下述脚本菜单路径。
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
python '<skill>/scripts/cover.py' prepare --cg '<cg1.png>' --cg '<cg2.png>' --template all --issue 267 --output '<工作区>/cover_runs'
```

   `--template` 取 `1`、`2`、`3` 或 `all`。日期覆盖用
   `--date "2026.10.24 - 2026.10.30"`，或 `--period-start 2026-10-24
   --period-end 2026-10-30`。必须用 `--issue 267` 指定期号，用于封面文字与文件命名。
   从返回值记录 `run_dir`。

2. Windows 直接调用 Photoshop 渲染并自动核验（macOS 见下节）：

```powershell
python '<skill>/scripts/cover.py' render '<run_dir>' --photoshop '<Photoshop.exe绝对路径>'
```

   render 使用独立的 Windows PowerShell 5.1 STA 辅助进程，调用一次
   `DoJavaScriptFile`。默认最多等待 300 秒，可用 `--timeout` 缩短；
   Photoshop 路径改变时传 `--photoshop`。渲染后核验并生成 `exports/` 内的最终 PNG，另生成 `contact_sheet.png` 预览。
   不导出 JPG 或交付 PSD；T2 可选校色所需的分层 PSD 仅存于 `working/` 作为中间文件。

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

- T3 是 4:3 画布、CG 通常是 16:9。必须用 cover：将素材等比放大到覆盖整个
  画布，再裁切超出边缘的部分；不得用 contain 留出上下空带，也不得拉伸变形。
  默认居中裁切，目视检查人脸与右上「VOL」标题；需要调整构图时平移素材，
  同时保持素材覆盖画布四边，不以缩小留空来避让。目标框见
  `scripts/runtime/cover/model.py` 的 `DEFAULT_TEMPLATES[3]["box"]`。
- T1、T2 的 CG 槽位比例接近 16:9，无需裁切，保留原摆放。
- 换 CG 后人脸仍被压住时，调整对应模板的 `box`（`[left, top, right, bottom]`）
  后重新 prepare；不要改模板母版内容。
- 「新闻速递」文字必然覆盖人物下半身，这是模板设计；除非移动文字层或
  大幅缩小人物，否则无法完全避免。

## 交付

交付 `verify` 返回的 `exports` 中的 PNG 路径及日期。文件名固定为：

- `xx 期周报视频封面 4-3.png`
- `xx 期周报视频封面 16-10.png`
- `xx 期周报文章封面.png`

`xx` 替换为实际期号；多张 CG 按 `exports/CG1/`、`exports/CG2/` 分目录，文件名不变。
最终文件先无损压缩，仍超 5,000,000 字节时等比缩小后重试；必须核验每张 PNG
格式、实际字节数与清晰度，不能把超限原图作为最终交付。模板原尺寸在工作目录保留。
可附 `contact_sheet.png` 供预览。保留模板母版
与用户原始 CG 不动，所有操作在独立批次副本上执行，不清理历史输出。

## 平台和安全前提

Windows 使用 PowerShell 5.1 + Photoshop COM。macOS 支持 Photoshop 原生 JSX，但 CLI 不自动调用应用或申请自动化权限：

1. 按上文 `prepare` 创建独立批次，显式传 `--issue`、本期期限与 `--output`。
2. 运行 `cover.py render '<run_dir>'`。它校验素材、重建引擎、独占登记批次，返回 `awaiting_photoshop` 与 `run_mac.jsx` 路径；**此时尚未渲染**。
3. 在 Photoshop「文件 → 脚本 → 浏览」选择返回的 JSX，执行一次。具备电脑操作工具的智能体可代为操作；不要求用户改系统保护或授予 AppleScript 自动化权限。
4. 运行 `cover.py verify '<run_dir>'`，再目视检查图片。脚本报错、已有执行标记或状态未知时先看回执，不重复执行、删除标记或覆盖旧输出；需要修正时创建新批次。
5. 可选 `refine` 同样返回一次性 `refine_run_mac.jsx`，执行后用 `verify '<run_dir>' --refined` 核验。

`doctor` 区分 `render_supported` 与 `direct_invocation_supported`；Mac 的脚本菜单路径可用不代表已渲染或字体检查通过。

只使用本机 prepare 生成的独立批次。渲染入口从已安装的引擎重建 JSX，不信任批次携带的可执行脚本。不要手动打开本批模板再启动渲染；保留已有文档、输出和提交回执。未知结果先检查状态，不重复提交。
