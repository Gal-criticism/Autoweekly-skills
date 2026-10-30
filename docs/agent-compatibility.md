# 智能体兼容与迁移说明

本目录中的 `weekly-cover` 与 `weekly-bgm-overlay` 各自带有完整的
`SKILL.md`、`scripts/` 与 `runtime/`。`SKILL.md` 是通用入口：核心流程通过
本地文件、shell 命令、图片查看和 JSON 结果驱动，不依赖 Codex 工具或 Codex
MCP。具备“本地文件读写 + shell + 看图”能力的智能体均可执行，包括
WorkBuddy、Claude Code、Cursor 等。

本文只是增量兼容说明，不改变现有 `SKILL.md` 流程、模板选择、渲染命令和
默认行为。

## 导入与目录

官方结构说明见
[WorkBuddy skill 文档](https://open.workbuddy.cn/docs/skill)，其结构为
`SKILL.md`、`scripts/`、`references/` 与 ZIP 包。本地导入入口见
[WorkBuddy 技能市场说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)，
支持“添加技能 > 上传技能”导入本地包。

- 传入完整技能文件夹或 ZIP；不能只复制 `SKILL.md`，否则 JSX、Python
  runtime、模板下载脚本和参考文档都会缺失。
- 自动发现与导入目录遵循当前平台的技能目录设置；不要猜测 WorkBuddy 的
  默认路径。
- 优先迁移已有目录。若目标位置已有同名技能，保留其中的私有修改；不要直接
  覆盖。
- `agents/openai.yaml` 只是 Codex 侧元信息。其他客户端可忽略；不应把它当作
  通用技能契约。

## 本地导入与市场发布

本仓库交付目标是 WorkBuddy 的本地“上传技能”导入：提供两个独立 ZIP，
`SKILL.md` 位于各自 ZIP 根目录，并附带打包脚本。这不是市场上架流程；市场
发布还需要额外双语元信息，此处默认不处理。

## 平台兼容边界

- 运行环境按目标机要求为 Windows、Photoshop 2023+、Python 3.11+、
  PowerShell 5.1。封面需要 Pillow，角标还需要 numpy。
- 云端或没有本地 shell 的聊天客户端不能直接控制用户 Windows Photoshop；
  这类环境只能准备任务，不能完成原生渲染。
- 平台若不支持技能导入，可让用户把完整技能目录放到本地可读位置，然后要求
  智能体读取该技能的绝对路径 `SKILL.md`，再按其中的脚本与参数执行。

## 运行前诊断与路径传递

执行渲染前可运行：

```powershell
python '<skill>/scripts/doctor.py'
```

该命令只返回 JSON 诊断和 Photoshop 路径建议，不启动 Photoshop、不修改配置、
不写入用户设置。其他智能体应把建议路径显式传给渲染命令：

```powershell
python '<skill>/scripts/cover.py' render '<run_dir>' --photoshop '<Photoshop.exe绝对路径>'
python '<skill>/scripts/overlay.py' render '<run_dir>' --photoshop '<Photoshop.exe绝对路径>'
```

现有模板选择流程保持不变：单模板环境变量 > 作者现存路径 > 默认缓存。
模板下载脚本会下载、校验并缓存；不要覆盖作者或用户的私有母版修改。

## 输出与配置

默认输出流程保持不变。为保证跨智能体行为一致，运行时显式指定工作区内的
`prepare --output` 路径；封面可用 `output/covers`，角标可用
`output/overlays`。环境变量由子进程继承；若平台每次打开独立终端，需在每次命令环境中重新
传入相同配置。所有生成都在独立批次目录中
进行；不要覆盖母版、原始 CG、BGM 输入或用户已有输出。

## 测试边界

当前可声明的是脚本层面的迁移与自测结果；不能宣称 WorkBuddy 客户端 UI、
导入向导或发布流程已通过实测。客户端端到端验收必须在实际客户端中完成后
才能说明。迁移后的第一步是运行 `doctor.py`，再用小批量 prepare/render 做
机器核验，最后保留人工看图检查。

## WorkBuddy 用户示例

用户可以直接要求：

```text
请读取 C:/path/to/weekly-cover/SKILL.md，并按我的 CG 图片使用该流程生成周报封面。
请读取 C:/path/to/weekly-bgm-overlay/SKILL.md，并按我的 BGM 信息使用该流程生成角标。
```

示例中的 `C:/path/to/...` 应替换为实际完整技能目录路径；确保该目录包含
`scripts/` 与 `references/`。
