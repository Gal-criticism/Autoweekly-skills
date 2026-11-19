---
name: weekly-bgm-overlay
description: 根据聊天中的 BGM 信息或 BGM.txt，通过 Photoshop 原生脚本接口批量生成新作、汉化、周边和简讯的透明 PNG 辅助角标。
---

# 周报 BGM 角标 (weekly-bgm-overlay)

根据用户提供的栏目、音乐名、作者与日期，通过本机 Photoshop 渲染附带 JSX，导出 2160×1080 透明 PNG 角标、预览图与核验记录。

首次安装、模板下载、macOS 限制及故障恢复见 [references/setup-and-recovery.md](references/setup-and-recovery.md)；多平台智能体调用见 [references/agent-usage.md](references/agent-usage.md)；JSON Schema 见 [references/task-schema.md](references/task-schema.md)。

## 必需输入

- **栏目与 BGM 映射**：聊天直接说明、UTF-8 文本（`BGM.txt`）或 schema v1 JSON。
  - 文本格式：一行多首用分号分隔，作者与歌名以第一个 ` - ` 分隔；`其余` 填充未显式指定的栏目；歌名在上、作者在下。
  - 示例：`新作，汉化部分：折戸伸治 - 夏休みの過ごし方` / `其余：石濱翔 - 奉仕部活動日誌 01`。
  - 歌名含分号或作者含分隔符时改用 `--task '<task.json>'`。
- **日期（可选）**：优先级为聊天指定 > 文件日期 > 自动日期（上海时间，上周六至本周五；周末仍归刚结束一期，由脚本自动计算）。

## 常用命令

以下 `<skill>` 为本 `SKILL.md` 所在目录的绝对路径：

```powershell
# 1. 环境与模板诊断
python '<skill>/scripts/doctor.py'

# 2. 准备独立批次（可选 --start YYYY-MM-DD --end YYYY-MM-DD 或 --task '<task.json>'）
python '<skill>/scripts/overlay.py' prepare --input '<input.txt>' --output '<工作区>/runs'

# 3. Windows 原生调用 Photoshop 渲染并自动核验
python '<skill>/scripts/overlay.py' render '<run_dir>' --photoshop '<Photoshop.exe绝对路径>'

# 4. 查询状态 / 单独复查 / 记录目视检查
python '<skill>/scripts/overlay.py' status '<run_dir>'
python '<skill>/scripts/overlay.py' verify '<run_dir>'
python '<skill>/scripts/overlay.py' review '<run_dir>' --note '<检查结论>'
```

- 缺母版时先运行 `python '<skill>/scripts/download_templates.py'`（详见 [references/setup-and-recovery.md](references/setup-and-recovery.md)）。
- 不要修改母版内容，不要重复提交已生成的 `run_dir`。

## 交付与验收条件

1. **程序核验（`status == "verified"`）**：校验全部 PNG 为 2160×1080 RGBA、透明区域与字体名称/文字边界正确，生成带棋盘底纹的 `preview.jpg`（底纹不导出）并原子写入 `verification.json`。
2. **原子复查与哈希绑定**：重跑 `verify` 不会提前破坏已有 `verification.json`；当批输出 PNG 哈希未变时自动保留已通过的 `visual_review`，任一 PNG 变更则重置为 `pending`。
3. **目视核验必需**：程序无法识别生僻字缺字或语义，必须查看 `preview.jpg` 确认栏目、日期、歌名、作者与换行无误后再运行 `review` 记录结论；未看图时不得宣称最终验收完成。
4. **交付物**：返回全部透明 PNG 路径、`preview.jpg` 及日期；保留母版与历史批次不动。
