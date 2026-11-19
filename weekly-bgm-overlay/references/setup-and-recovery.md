# 首次安装、平台限制与故障恢复（weekly-bgm-overlay）

## 首次使用：下载母版并连接 Photoshop

1. **运行环境**：Python 3.11+（需安装 `Pillow` 与 `numpy`）以及本机 Photoshop。优先使用用户配置、已运行 Photoshop 进程路径或本机安装路径，不能假设示例盘符存在。
2. **下载 BGM 角标母版**：未配置本地模板或路径不存在时运行：

```powershell
python '<skill>/scripts/download_templates.py'
```

- 下载固定 `templates-v1` Release 中的角标母版（1 个文件，约 45 MiB），校验文件大小、SHA256 与 PSD 画布尺寸。
- 默认存放目录：`%LOCALAPPDATA%/Autoweekly/templates/templates-v1`（缺 `LOCALAPPDATA` 时使用 `~/.cache/Autoweekly/templates/templates-v1`）。
- 已校验文件自动复用；失败临时文件自动清理；若现存文件校验失败则保留并报错，应换 `--directory '<目录>'` 下载并设置 `AUTOWEEKLY_TEMPLATE_DIR`，不得覆盖用户母版。
- 模板选择优先级：`WEEKLY_OVERLAY_TEMPLATE` 环境变量 > 存在的旧版本机母版 > 默认缓存。若环境变量指向不存在的文件，请修正变量或显式传 `--template`。
- 模板不含字体安装包；缺少 `MiSans-Semibold`、`MiSans-Regular` 或 `NotoSerifCJKsc-SemiBold` 时，告知用户缺失的 PostScript 字体名，安装后再继续。

详细图层契约见 [references/template.md](template.md)，通用智能体说明见 [references/agent-usage.md](agent-usage.md)。

## 平台与安全前提

- **仅支持 Windows 原生渲染**：依赖 Windows PowerShell 5.1 STA 辅助进程通过 COM `DoJavaScriptFile` 连接 Photoshop（先附着运行对象表，必要时启动指定 `Photoshop.exe`）。
- **macOS 限制**：仅支持模板下载和部分准备，`doctor` 返回 `render_supported=false` 属于明确平台限制，不自动新增 AppleScript、界面模拟或关闭系统保护的回退方案。
- **批次隔离**：只执行本机 `prepare` 生成的独立批次；入口会校验 `run.jsx` 与已安装引擎及 `job.json` 完全一致。不要手动打开本批模板再启动渲染。

## 超时与故障恢复

- `dispatch.json` 在启动前独占创建，`started.json` 标记 JSX 已开始执行；已提交批次拒绝重复提交。
- 单批默认超时 300 秒（可用 `--timeout` 调整）。超时仅结束辅助进程，Photoshop 可能仍在执行，先运行 `status '<run_dir>'` 并查看 `connection.json` 与 `invoke.log`，不得立即重跑或新建重复批次。
- 仅在原生接口失败且日志无法排除弹窗时，才用界面诊断；只有确认从未调用 JSX 的新批次，才可用 Photoshop **文件 → 脚本 → 浏览** 作为备用入口。
- 失败时引用真实 `error`，不能把部分 PNG 当成整批成功。
