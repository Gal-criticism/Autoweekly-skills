# 本地输入格式

Python 3.11+，Pillow、fontTools；读取 XLSX 另需 openpyxl。渲染依赖 Windows 上的 AE、工程使用的字体和效果、Voukoder。媒体核验需要 FFmpeg/FFprobe。

本地 config JSON（示例路径需改为实际绝对路径）：

```json
{
  "template":"E:/templates/片尾.aep",
  "ae":"C:/Program Files/Adobe/Adobe After Effects 2022/Support Files/AfterFX.com",
  "aerender":"C:/Program Files/Adobe/Adobe After Effects 2022/Support Files/aerender.exe",
  "voukoder":"C:/Program Files/Adobe/Common/Plug-ins/7.0/MediaCore/VoukoderR2.aex",
  "bridge_image":"E:/templates/春季片尾.png",
  "ffmpeg":"E:/tools/ffmpeg.exe",
  "ffprobe":"E:/tools/ffprobe.exe",
  "fixed_roles":["设计"],
  "script_timeout":300,
  "render_timeout":1800
}
```

`fixed_roles` 需有用户授权。可选 `reference_queue_index` 指定参考队列。文字区域默认最大宽 420、高 120 源坐标像素，并根据已启用姓名槽位的间距缩小；可用 `text_max_width`、`text_max_height` 按模板实际区域配置。更换模板后重新 inspect。

task JSON 同时用于 sync-roster 和 prepare，选一种名单来源：

```json
{
  "week_id":"250",
  "config":"E:/work/config.json",
  "profile":"E:/work/inspect/profile.json",
  "allowed_roots":["E:/work/图包"],
  "roster_snapshot":"E:/work/roster-250.json",
  "xlsx_roster":{
    "path":"C:/Users/example/Downloads/周报工作表.xlsx",
    "sheet_name":"周报工作表",
    "header_row":1,
    "issue_column":"周报期数",
    "roles":{
      "后期":"后期","时轴":"时轴","轴校":"轴校","文案":"文案",
      "素材汇总":"素材汇总","新闻采集":"新闻采集","配音":"配音",
      "修音":"修音","制作辅助":"制作辅助"
    },
    "member_separators":{"新闻采集":["、"],"文案":["、"],"素材汇总":[" "]}
  }
}
```

分隔符只适用于已确定惯例的列。未配置时整个单元格保留为一个成员字符串，不自动按空格、逗号或换行拆人名。固定设计栏目不需要映射至空表格列。

Feishu 来源替换上述 `xlsx_roster`：

```json
{
  "feishu_roster":{
    "wiki_url":"https://example.feishu.cn/wiki/NODE",
    "table_id":"tblEXAMPLE",
    "view_id":"vewEXAMPLE",
    "issue_field_id":"fldISSUE",
    "roles":{"后期":"fldEDITOR","文案":"fldWRITER"},
    "expected_schema_sha256":"使用首次实际探测并确认的schema哈希"
  }
}
```

首次可省略 `expected_schema_sha256`，完成实际字段绑定后保存。API 来源探测配置是包含 `wiki_url`、`table_id` 的 JSON，调用 `inspect-source --source-config` 返回字段 ID；要先检查完整字段再生成 roles。

同步命令会把 task 的 week_id 写入 API 查询的 issue 参数。凭据只从进程环境读取，不写入 task、config、快照、日志或分发包。该示例是结构说明，不证明对任何实际飞书表有权限。

批次状态依次为 prepared、applying、rendering、rendered、verified。任意失败保留具体阶段、错误和提交回执；查看 status，不重复提交同一批次。机器验证失败可能显示 verification_failed。visual-review.json 必须另记 performed、覆盖的栏目/时刻和观察结果。
