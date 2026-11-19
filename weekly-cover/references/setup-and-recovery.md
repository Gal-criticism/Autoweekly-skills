# 首次安装、几何规则与平台恢复（weekly-cover）

## 首次使用：下载母版并连接 Photoshop

1. **运行环境**：Python 3.11+（需安装 `Pillow`）以及本机 Photoshop。优先使用用户配置、已运行 Photoshop 路径或本机安装路径，不能假设示例盘符存在。
2. **下载封面母版**：未配置本地模板或路径不存在时运行：

```powershell
python '<skill>/scripts/download_templates.py'
```

- 下载固定 `templates-v1` Release 中的 3 个封面母版（约 212 MiB），校验文件大小、SHA256 与 PSD 画布尺寸。
- 默认存放目录：`%LOCALAPPDATA%/Autoweekly/templates/templates-v1`（缺 `LOCALAPPDATA` 时使用 `~/.cache/Autoweekly/templates/templates-v1`）。
- 模板选择优先级：单模板环境变量 > 存在的旧版本机母版 > 默认缓存。下载到自定义目录时用 `--directory '<目录>'` 并设置 `AUTOWEEKLY_TEMPLATE_DIR`；校验失败的现存文件保留并报错，不得覆盖用户母版。
- 模板不含字体安装包；缺字体时告知用户缺失的 PostScript 字体名，安装后继续。

详细图层契约见 [references/templates.md](templates.md)，通用智能体说明见 [references/agent-usage.md](agent-usage.md)。

## 人脸遮挡与模板几何

- **T3（4:3）**：CG 通常为 16:9，必须采用 cover（等比放大覆盖全画布后裁切边缘），不得用 contain 留出上下空带，也不得拉伸变形。默认居中裁切，目视检查人脸与右上「VOL」标题；需要避让时调整 `scripts/runtime/cover/model.py` 中 `DEFAULT_TEMPLATES[3]["box"]` 后重新 `prepare`，不改模板母版。
- **T1（16:10）与 T2（文章横幅）**：槽位比例接近 16:9，保留原摆放。
- 「新闻速递」文字覆盖人物下半身属于模板固定设计。

## Windows 与 macOS 平台差异及安全前提

- **Windows**：通过 PowerShell 5.1 STA 辅助进程调用 Photoshop COM `DoJavaScriptFile`，`render` / `refine` 均支持 `--photoshop '<绝对路径>'` 与 `--timeout`（默认 300 秒）。
- **macOS**：支持 Photoshop 原生 JSX，但 CLI 不自动拉起应用或申请 AppleScript 权限：
  1. `prepare` 创建独立批次（显式传 `--issue`、日期与 `--output`）。
  2. `cover.py render '<run_dir>'` 校验素材、重建引擎并独占登记，返回 `awaiting_photoshop` 与 `run_mac.jsx` 路径（此时尚未渲染）。
  3. 在 Photoshop「文件 → 脚本 → 浏览」执行返回的 JSX 一次。
  4. 运行 `cover.py verify '<run_dir>'` 并目视检查。可选 `refine` 同样返回 `refine_run_mac.jsx`，执行后运行 `verify '<run_dir>' --refined`。
- **安全前提**：只使用本机 `prepare` 创建的独立批次；入口总是从已安装引擎重建 JSX，不信任批次内原有脚本；状态未知或报错时先查 `status` 和回执，不重复提交或覆盖旧输出。
