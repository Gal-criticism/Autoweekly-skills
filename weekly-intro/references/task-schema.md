# 周报片头数据结构

所有路径优先使用绝对路径。输入文件必须是 UTF-8 JSON，运行目录只接受本
批次生成的输出；不要用旧字段推断新模板的槽位。

## 本地配置

```json
{
  "ae": "C:/Program Files/Adobe/Adobe After Effects 2022/Support Files/AfterFX.com",
  "aerender": "C:/Program Files/Adobe/Adobe After Effects 2022/Support Files/aerender.exe",
  "template": "D:/Autoweekly/weekly-intro.aep",
  "bridge_image": "D:/Autoweekly/bridge.png",
  "ffmpeg": "C:/Tools/ffmpeg.exe",
  "ffprobe": "C:/Tools/ffprobe.exe",
  "voukoder": "C:/Program Files/Adobe/Common/Plug-ins/7.0/MediaCore/VoukoderR2.aex"
}
```

`ae`、`aerender`、`template`、`bridge_image` 是 doctor 的必要配置。
`ffmpeg`、`ffprobe`、`voukoder` 可选；工具路径也可由环境变量或 PATH
发现。配置了无效的显式工具路径时，doctor 必须报告错误，不能用隐式发现
结果掩盖该配置。

doctor 返回 `status`、`prerequisites_found`、`setup_required`、`missing`、
`errors` 和逐项 `checks`。系统、Python 3.11+、Pillow 与媒体工具构成
`prerequisites_found`；全部环境与配置可用时 `status` 才是 `ready`。
doctor 是静态检查，不执行子进程，也不启动应用。

## Inspect 与 task

`inspect --config <config> --output <dir>` 的输出目录包含 `profile.json`。
`profile.json` 描述实际母版、槽位、帧尺寸、帧率和预期时长。inspect 结果中的
`profile` 指向该文件。task 至少包含：

```json
{
  "schema_version": 1,
  "week_id": "240",
  "allowed_roots": ["D:/新闻素材/2026-10-30"],
  "profile": "D:/workspace/inspect/profile.json",
  "config": "D:/workspace/config.json"
}
```

`prepare --task <task> --output <dir>` 创建唯一 run 目录，并写入
`candidates.json`、`profile.json`、`run.json`、`output/` 及
`candidates/contact_sheet_*.png`。`prepare` 结果中包含 `slot_count`。它不生成
选择模板；智能体查看 contact sheet 后必须在 run 目录自行创建 `selection.json`。
候选 ID 只在本批次有效；没有看图证据时不得声称选择质量已核验。

## Selection

`selection.json` 是数组，每项对应一个槽位：

```json
[
  {
    "slot_id": "c372:l3",
    "candidate_id": "C0001",
    "crop": [120, 80, 1920, 1160]
  }
]
```

- `slot_id` 必须唯一且完整覆盖全部槽位。
- 槽位 ID 从本批次 `profile.json` 读取，不能照抄示例或旧模板的 ID。
- `candidate_id` 必须来自同一 run，候选和内容不得重复使用。
- `crop` 可省略；提供时为四个整数，表示 EXIF 方向校正后源图的
  `[left, top, right, bottom]`，且需与槽位宽高比误差不超过 1%。
- 渲染前重新核对候选绝对路径仍位于 `allowed_roots`，并核对 SHA256 和
  `mtime_ns`。

## Render 与 run.json

render 只接受已准备且未提交的独立 run。母版只读，渲染使用母版副本；
源队列只复制并新增一个项，Voukoder 配置沿用源队列。输出文件名固定为
`<week_id>片头.mp4`。原生后台接口失败时保留日志和状态，不提供 GUI
回退。

供独立视频核验使用的 `run.json` 至少包含：

```json
{
  "video_path": "D:/workspace/runs/run-001/2026.10.24-2026.10.30片头.mp4",
  "expected": {
    "width": 2160,
    "height": 1080,
    "fps": "60/1",
    "duration": 12.0,
    "frames": 720,
    "audio_expected": false
  }
}
```

`video_path` 可为相对 run 目录的路径。`fps` 必须按有理数精确匹配，
`duration` 允许一帧误差，`frames` 使用 ffprobe 的 `nb_read_frames` 或
`nb_frames` 精确匹配。`audio_expected` 只允许：

- `true`：至少一个音轨。
- `false`：没有音轨。
- `null`：不检查，结果明确记录 `not_checked`。

ffprobe 未返回精确帧数时，`frames` 检查为 `not_checked` 并报告原因；由于
任务要求精确帧数，整体 `status` 必须为 `failed`，不能
静默称为帧数已核验。

## Verify 输出

`verify <run_dir>` 使用参数列表调用 `ffprobe -count_frames`，再仅抽取约
12 张关键帧图片并生成 Pillow contact sheet。ffprobe/ffmpeg 以 UTF-8、
`errors=replace` 解码，并各自设置 120 秒超时。它设置 `frames` 拒绝覆盖，
所有步骤先在批次内的临时目录进行，成功后才落盘。返回及
`verification.json` 包含：

```json
{
  "status": "verified",
  "checks": {},
  "errors": [],
  "video_path": "D:/workspace/runs/run-001/2026.10.24-2026.10.30片头.mp4",
  "frames": ["D:/workspace/runs/run-001/frames/keyframe-01.png"],
  "contact_sheet": "D:/workspace/runs/run-001/frames/contact-sheet.jpg",
  "visual_review": {
    "performed": false,
    "note": "Semantic visual review was not performed."
  },
  "verification_path": "D:/workspace/runs/run-001/verification.json"
}
```

`status: verified` 只表示机器检查通过。`visual_review.performed` 由实际
查看 contact sheet 与视频的流程决定；本命令不会识别画面内容，也不会把
接触表生成等同于视觉验收。
