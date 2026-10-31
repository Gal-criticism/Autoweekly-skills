---
name: weekly-intro
description: 使用本机 After Effects、独立母版副本和 Voukoder 生成并核验周报片头视频。
---

# 周报片头

本技能只制作周报片头视频，不处理周报封面、BGM 角标或其他成片。AEP
母版保持不可变；所有候选图、选择、脚本副本、关键帧和核验记录都写入独立
批次目录。

## 输入

需要用户提供 `week_id` 和 `allowed_roots`。`week_id` 同时作为批次标识和
输出名的前缀；`allowed_roots` 是本批允许读取的本地图片目录。可由智能体
从用户指定的本地资料中选择目录，但不得扩展到未列出的路径。

可选本地配置提供以下键：

- `ae`：After Effects 可执行文件。
- `aerender`：`aerender.exe`。
- `template`：AEP 母版。
- `bridge_image`：模板需要的本地桥接图。
- `ffmpeg`、`ffprobe`：可选的绝对工具路径。
- `voukoder`：可选的本机 Voukoder 模块候选路径。

先运行静态环境检查，不启动 After Effects、不调用媒体子进程、不写文件：

```powershell
python '<skill>/scripts/intro.py' doctor --config '<config.json>'
```

命令按配置、`FFMPEG`/`FFPROBE` 环境变量、PATH 的顺序查找媒体工具，
`imageio_ffmpeg` 只可补充 ffmpeg。显式配置的无效路径会作为错误报告，
不会静默改用其他路径。未配置桥接图时仍可查看 doctor 结果，但状态应为
需要安装配置，而不是就绪。

## 检查母版并准备任务

```powershell
python '<skill>/scripts/intro.py' inspect --config '<config.json>' --output '<工作区>/inspect'
```

输出目录中的 `profile.json` 记录母版结构与本机能力，inspect 结果中的
`profile` 指向该文件。构造 task JSON 时使用 `profile` 和 `config` 两个键，
值均为本地绝对路径。修改模板前先核对实际图层与占位；不要根据模板历史
版本猜测槽位。

```powershell
python '<skill>/scripts/intro.py' prepare --task '<task.json>' --output '<工作区>/runs'
```

prepare 只读取 `allowed_roots` 内可解码的静态图片，并创建唯一 run
目录。该目录包含 `candidates.json`、`candidates/contact_sheet_*.png`、
`profile.json`、`run.json` 和 `output/`；命令结果报告 `slot_count`。
`prepare` 不生成选择模板。智能体必须逐个查看本地 contact sheet，再自行在
run 目录写入与槽位数量完全一致的 `selection.json`：

```json
[
  {"slot_id": "c372:l3", "candidate_id": "C0001", "crop": [0, 100, 1800, 1180]}
]
```

`crop` 是四个整数，使用考虑 EXIF 方向后的源图像素坐标；省略时使用居中 cover crop。
不能复用候选、伪造候选 ID、跨 `allowed_roots` 读取，或在未看图时自动
生成选择。

## 渲染

```powershell
python '<skill>/scripts/intro.py' render '<run_dir>'
```

渲染器在后台使用原生接口和独立批次，不打开、保存、关闭或复用用户当前的
AE 工程。它会复制母版，保留源队列结构，只复制并新增一个队列项，并沿用
源队列中的 Voukoder 配置。若原生接口失败，保留错误和日志并停止；不回退
到 Computer Use 或菜单手工操作。私有插件参数不能被本技能伪造，也不能
宣称未经核验的字段已生效。

成功产物命名为 `<week_id>片头.mp4`，并写入批次目录。只按
`audio_expected` 的 `true`、`false` 或 `null` 检查音轨；`null` 表示未指定，
不能扩写成“保证有/无音频”。

本期 AEP 使用现成母版的副本，并引用批次中的替换素材；保留整个批次目录，
不要只移动一个 AEP 文件后删除其素材。

## 核验与状态

```powershell
python '<skill>/scripts/intro.py' verify '<run_dir>'
python '<skill>/scripts/intro.py' status '<run_dir>'
```

`verify` 通过 ffprobe 检查文件可读性、单一视频流、宽高、精确有理数帧率、
一帧内的时长误差、精确帧数，以及任务明确要求的音轨。任务要求帧数但
ffprobe 无法返回精确帧数时，帧数检查保留 `not_checked` 诊断，整体状态
必须为 `failed`。随后以
ffmpeg 仅抽取约 12 张图片（包含起点和末帧附近），生成带标签的 contact
sheet，且不改写视频。已有 `frames` 输出时拒绝覆盖。

`verified` 只代表机器检查通过，必须单独说明视觉核验状态。智能体查看
`verification.json` 和本地 contact sheet 后，检查构图、人物、裁切、缺帧、
黑场、文字及播放顺序；没有实际看图时写明“未完成视觉核验”，不能把
contact sheet 已生成当成已验收。

已通过或已存在核验回执的批次再次执行 `verify` 会直接拒绝，并保留原核验记录；查询用 `status`。
整条渲染流水线和核验各有独立的排他提交回执，并发重复调用不会改写已提交任务。
原片头的长期关闭图层不占用新闻图名额。存在启用文本层时，运行时通过
fontTools 检查实际 PostScript 字体名，缺字体会停止；不自动安装或替换字体。
独立脚本接入默认等待 300 秒，整片渲染默认 1800 秒，配置可用
`script_timeout`、`render_timeout` 调整。失败后检查状态，另建批次，不盲目重发。

## 打包边界

分发包应自包含 `SKILL.md`、脚本和 `references/task-schema.md`。不得包含
本机 AEP、用户新闻素材、运行批次、日志、缓存或密钥。用户已有 AE 工程
仍归用户所有；本技能不保存或关闭它。

输入、配置和核验字段见 [references/task-schema.md](references/task-schema.md)。
