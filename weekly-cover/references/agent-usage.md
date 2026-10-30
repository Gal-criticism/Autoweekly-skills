# 其他智能体如何调用

本 skill 的执行入口是本地 Python CLI。智能体可以用自己平台的文件读取、终端
和图片查看工具执行，不需要 Codex 专用工具或特定模型。完整复制或导入此文件夹，
不要只传 `SKILL.md`；`agents/openai.yaml` 是可选的 Codex 展示信息。

## 首次执行

1. 从当前 `SKILL.md` 的位置确定 `<skill>` 绝对路径；不要假定 `.codex`、
   `.workbuddy` 或当前工作目录。保留 `scripts/runtime/` 与 `references/`。
2. 找到 Python 3.11+，用该解释器运行 `scripts/doctor.py`。命令输出 JSON；
   退出码 0 表示已发现主要依赖，1 表示还需要配置。它不会下载、安装或启动 PS，
   也不验证字体和 Photoshop COM 是否可连接。
3. 缺少模板时运行 JSON 中 `commands.download` 指定的命令；缺少依赖时安装到
   同一个 Python。模板下载规则见主文档。JSON `photoshop` 是本机候选路径，
   运行 render 时显式传 `--photoshop`，无需修改现有默认配置。
4. 按主文档执行 prepare → render → 查看预览。`--output` 显式指向当前可写工作区。

```powershell
python '<skill>/scripts/doctor.py'
python '<skill>/scripts/doctor.py' --photoshop 'D:/Adobe/Photoshop.exe'
```

如果平台不支持技能导入，也可以在聊天中要求智能体读取这个 skill 的 `SKILL.md`
绝对路径，然后照文档调用脚本。`<skill>`、`<run_dir>` 等是占位符，要替换后执行。

## 调用约定

- 成功结果从 stdout 读取 JSON；错误时检查退出码、stderr 和运行目录中的日志。
  下载脚本的进度写入 stderr，不能仅因 stderr 有内容就判定失败。
- 从 prepare 返回的 `run_dir` 继续执行，不要自行拼接批次目录。路径包含空格或中文时，
  用平台支持的参数数组或正确的 shell 引号。
- 环境变量只在同一进程及其子进程继承。若每次命令启动新终端，应在每个命令环境中
  带上同样配置；也可以显式传 `--template`（角标）和 `--photoshop`。
- 同一批次只提交一次。超时先调用 status 并读结果，不能直接重试 render。
- 无图片查看能力时交付预览供用户检查，不要自动填写 review 或宣称视觉验收通过。

渲染必须在安装了 Photoshop 的 Windows 电脑上执行，使用 Windows PowerShell 5.1
和桌面会话。云端沙箱不会自动获得用户本机的 Photoshop。跨智能体兼容与跨操作系统
是两件事；当前未提供 macOS/Linux 渲染接口。
