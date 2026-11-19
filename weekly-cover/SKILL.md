---
name: weekly-cover
description: 用 CG 立绘和三个周报封面模板，通过 Photoshop 原生脚本接口批量生成封面（16:10、横幅、4:3），自动按 CG 边缘主色配色，并按模板几何避开人脸遮挡。
---

# 周报封面 (weekly-cover)

用本机 Photoshop 将 CG 立绘批量套入三个周报封面模板（T1 16:10、T2 文章横幅、T3 4:3），自动按 CG 边缘主色推导背景配色，最终只交付单张 ≤ 5,000,000 字节的 PNG。

首次安装、模板下载、几何裁切细节及 macOS 步骤见 [references/setup-and-recovery.md](references/setup-and-recovery.md)；多平台智能体调用见 [references/agent-usage.md](references/agent-usage.md)；母版契约见 [references/templates.md](templates.md)。

## 必需输入

- **CG 立绘（`--cg`）**：1 张或多张 PNG/JPG（可重复传入，按 `CG × 模板` 笛卡尔积出图）。
- **期号（`--issue`）**：必填整数，用于封面文字与最终文件名。
- **可选参数**：`--template`（`1`、`2`、`3` 或 `all`，默认 `all`）；日期可用 `--date "YYYY.MM.DD - YYYY.MM.DD"` 或 `--period-start` / `--period-end` 覆盖（默认按上海时间取上周六至本周五）。

## 常用命令

以下 `<skill>` 为本 `SKILL.md` 所在目录的绝对路径：

```powershell
# 1. 环境与模板诊断
python '<skill>/scripts/doctor.py'

# 2. 准备独立批次
python '<skill>/scripts/cover.py' prepare --cg '<cg1.png>' --template all --issue 268 --output '<工作区>/cover_runs'

# 3. 渲染并自动核验（Windows 直接调用 Photoshop；macOS 返回一次性 JSX 路径）
python '<skill>/scripts/cover.py' render '<run_dir>' --photoshop '<Photoshop.exe绝对路径>'

# 4. 可选：冷蓝调 CG 对 T2 横幅底色校色（自动同步刷新统一交付清单、exports/ 与预览）
python '<skill>/scripts/cover.py' refine '<run_dir>' --photoshop '<Photoshop.exe绝对路径>'

# 5. 查询状态 / 单独核验 / 记录目视检查
python '<skill>/scripts/cover.py' status '<run_dir>'
python '<skill>/scripts/cover.py' verify '<run_dir>'
python '<skill>/scripts/cover.py' review '<run_dir>' --note '<检查结论>'
```

- 缺母版时先运行 `python '<skill>/scripts/download_templates.py'`（详见 [references/setup-and-recovery.md](references/setup-and-recovery.md)）。

## 交付与验收条件

1. **统一交付清单（`delivery_manifest.json`）**：`verify` 与 `refine` 共享同一份最终交付清单——无论是否校色，`contact_sheet.png` 预览、最终 `exports/` PNG 与 `review` 目视记录均指向同一批活跃版本与文件哈希（T2 校色后三处统一为校色版）。
2. **文件命名与体积上限**：只交付 `exports/` 中的 PNG（每张 ≤ 5,000,000 字节，先无损压缩，超限时等比缩小重试），不交付 JPG 或 PSD：
   - `<issue> 期周报视频封面 16-10.png`
   - `<issue> 期周报文章封面.png`
   - `<issue> 期周报视频封面 4-3.png`
   - 多张 CG 自动分目录存入 `exports/CG1/`、`exports/CG2/`。
3. **目视核验必需**：程序只校验尺寸、体积与可读性；必须查看 `contact_sheet.png` 确认人脸未被标题或边缘裁切遮挡后再执行 `review`。未看图时须明确说明未完成目视验收。
