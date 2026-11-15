# 写作计划 (`draft_plan.json`) 与四件套输出规范

## 一、`draft_plan.json` 格式规范

智能体完成选题、背景检索与当周事实核实后，在 `<run_dir>/draft_plan.json` 写入以下 JSON 结构，供 `render` 命令生成最终四件套并进行机器核验：

```json
{
  "issue_id": "267",
  "date_range": "2026-10-24~2026-10-30",
  "subtitle": "",
  "coverage_status": "ok",
  "coverage_notes": [
    "输入包含 2026-10-24~2026-10-29 与 2026-10-29~2026-10-30 两份 X 汇总。"
  ],
  "holdout_constraints": {
    "max_issue": 266,
    "exclude_issues": ["267"]
  },
  "entries": [
    {
      "section": "新作",
      "title": "《Happy Weekend》体验版公开",
      "event_type": "体验版",
      "selection_reason": "AirVelocity 公布主机版新 CG 与体验版，并开启 Switch 下载版预约 9 折优惠。",
      "body_paragraphs": [
        "本周，AirVelocity公开了《Happy Weekend》主机版的全新CG，并同步放出了游戏的体验版。",
        "本作原版是2025年6月27日发售的PC游戏，本次为登陆Switch与PS4的移植版，由綾瀬はづき负责原画，内藤自由、モーリー、さんざんまん负责剧本。游戏将推出Switch盒装版、下载版及PS4下载版，预定于2026年11月26日发售，目前Switch下载版预约可享9折优惠。感兴趣的玩家可以前往官网了解详情。"
      ],
      "source_status_ids": ["2115788882256257449"],
      "source_urls": [
        "https://int-creative.com/air/weekend/",
        "https://store-jp.nintendo.com/item/software/D70010000127584",
        "https://x.com/AirVelo2026/status/2115788882256257449"
      ],
      "current_week_facts": [
        "Switch版与PS4版新样品CG及体验版公开",
        "Switch下载版预约享10%折扣（9折）"
      ],
      "verified_fields": {
        "platform": "Switch / PS4",
        "edition": "Switch盒装版、下载版 / PS4下载版",
        "language": "日文",
        "release_date": "2026-11-26",
        "price_tax": "Switch下载版预约10%折扣",
        "verification_sources": [
          "https://int-creative.com/air/weekend/",
          "https://store-jp.nintendo.com/item/software/D70010000127584"
        ]
      },
      "omitted_secondary_fields": [],
      "historical_background_used": [
        {
          "source_issue": "259",
          "work_name": "Happy Weekend",
          "fact_summary": "PC原版2025年6月27日发售，原画綾瀬はづき，剧本内藤自由、モーリー、さんざんまん，主机版2026年11月26日发售"
        }
      ],
      "style_examples_referenced": ["259-01"]
    }
  ],
  "pending_verifications": [
    {
      "topic": "某作品疑似新动态",
      "reason": "推文仅附图片且无文字说明具体更新事项，待人工查看原图确认。",
      "source_urls": ["https://x.com/example/status/1234567890"]
    }
  ],
  "excluded_events": [
    {
      "account": "whisp_news",
      "status_ids": ["2113987654321000000"],
      "summary": "bunny club 线上抽签常规宣传与中奖通知",
      "reason": "无新增作品或周边发布信息，属日常重复宣传与抽奖通知。"
    }
  ]
}
```

## 二、四件套导出产物规范

运行 `python '<skill>/scripts/script.py' render '<run_dir>'` 后，脚本在 `<run_dir>` 中生成以下四个文件：

1. **`周报初稿.docx`**：
   - 栏目顺序固定为：`新作`、`汉化`、`周边`（某栏无条目时自动省略该栏目空标题，确保下游解析器状态机正确激活）。
   - **样式严格遵循 `galgame_news` 解析规范**：
     - 顶部期号标题：26 磅加粗（`w:sz val="52"` + `w:b`）。
     - 栏目标题（`新作` / `汉化` / `周边`）：16 磅加粗（`w:sz val="32"` + `w:b`）。
     - **新闻标题**：**15 磅加粗**（`w:sz val="30"` + `w:b`），不含 `#` 号或 URL，不依赖 `#作者#` 识别标题。
     - **新闻正文段落**：**11 磅常规**（`w:sz val="22"`，非加粗）。
     - **独立来源链接段落**：每条 URL 单独成段，**11 磅常规**（`w:sz val="22"`，非加粗），同时写入 `<w:hyperlink>` 外部关系。
2. **`口播稿.md`**：
   - 与 `周报初稿.docx` 同版正文，按三栏二级标题与新闻三级标题排版，**彻底剥离所有 URL 与编辑元信息**（纯净无注音底稿；需加注音时将 `周报初稿.docx` 或本文件传入 `weekly-ruby`）。
3. **`审阅清单.md`**：
   - 包含四部分：输入覆盖与空文件检查、入选新闻与选题理由（含已省略次要字段）、待核实事项清单、未入选事件与排除理由。
4. **`evidence.json`**：
   - 完整记录每条新闻的来源推文 `status_id`、外部链接、当周事实列表、核实字段、省略字段、所引用的历史背景期次及文风范例编号。
