---
name: weekly-ruby
description: 为 Galgame 周报成稿 DOCX、口播底稿 Markdown/TXT 或 draft_plan.json 执行日文专名抽取、VNDB 官方罗马音查询、本地假名规则转换与人工补漏，安全 1:1 回写【Romaji】注音并导出配音用《口播注音稿.md》及注音核验报告。
---

# Galgame 周报口播注音 (weekly-ruby)

本技能专注**口播用日文专名注音后处理**，与负责选材、核实和中文写作的 `weekly-script` 彻底解耦：不管上游是 `weekly-script` 生成的 `周报初稿.docx` / `口播稿.md`，还是人工在 Word 里改好的成稿，都能直接输入本技能生成可直接配音的注音口播稿，不篡改原稿任何中文表述。

## 参考文档

- [日文注音规范与补漏说明](references/ruby-rules.md)：英文借词还原、含假名罗马音规则、纯汉字人名豁免原则、`【】` 位置红线及 `ruby_pairs.json` 格式。

## 支持输入

1. **`周报初稿.docx` 或人工修改后的 Word 成稿（`.docx`）**：自动识别期号、三栏结构（`新作 / 汉化 / 周边`）、15 磅加粗或 `#作者#` 标题与正文段落，自动剔除独立来源链接段落与署名标签。
2. **Markdown / 纯文本口播底稿（`.md` / `.txt`）**：保留 `# / ## / ###` 层级标题并剥离残留 URL。
3. **写作计划（`draft_plan.json`）**：读取 `entries` 与附带的 `ruby_pairs`。

## 命令入口

入口脚本：`python '<skill>/scripts/ruby.py'`（所有路径建议使用绝对路径）。

### 1. 环境诊断 (`doctor`)

```powershell
python '<skill>/scripts/ruby.py' doctor --input '<周报初稿.docx或口播稿.md>'
```

### 2. 提取候选词并创建独立注音批次 (`prepare`)

```powershell
python '<skill>/scripts/ruby.py' prepare --input '<输入文件>' --output '<运行批次根目录>'
```

- 离线环境或留出测试可加 `--offline`（跳过联网查询 VNDB，仅用本地假名罗马音与 `ruby_pairs.json`）。
- 已有自定义词典可传 `--pairs '<ruby_pairs.json>'`。
- `prepare` 会在 `<output>/ruby_<文件名>_<时间戳>` 创建独立运行目录，输出：
  - `input_normalized.json`：剥离链接与编辑元数据后的纯净结构化文本。
  - `candidates.json`：抽取出的候选专名、VNDB 命中结果、本地假名结果，以及 `unresolved_misses`（仍含假名但自动步骤未解决的词条）。
  - `ruby_pairs.json`：预填充模板字典，供智能体或人工补充未命中词条（`"日文原词": "English or Romaji"`）。

### 3. 智能体补漏（若 `unresolved_misses` 非空）

查看 `prepare` 输出中的 `unresolved_misses`。如存在未命中含假名专名（如汉字+假名混写标题或生僻社团名），按 [注音规范](references/ruby-rules.md) 补全准确的英文官方名或罗马音，写入 `<run_dir>/ruby_pairs.json`：

```json
{
  "月に寄りそう乙女の作法3": "Tsuki ni Yorisou Otome no Sahou 3",
  "綾瀬はづき": "Ayase Hazuki"
}
```

### 4. 安全回写并导出注音产物 (`render` & `verify`)

```powershell
python '<skill>/scripts/ruby.py' render '<run_dir>'
python '<skill>/scripts/ruby.py' verify '<run_dir>'
```

- 如在离线环境或希望跳过二次联网查询，传 `--offline`。
- 如除了 `口播注音稿.md` 外还需一份带注音的 Word 文件，传 `--export-docx`（同时生成 `口播注音版.docx`）。
- 如需对同一批次重新渲染，传 `--force`。

`render` 会在 `<run_dir>` 生成并自动校验：
1. `口播注音稿.md`：按栏目与新闻标题排版的纯口播注音稿，所有 URL 与 `#作者#` 已剥离，`【Romaji】` 紧贴原词或 `》` 之后。
2. `注音报告.md`：候选总数、VNDB 命中数、本地假名数、人工/智能体补充数、逐条转换清单及未命中提示。
3. `ruby_evidence.json`：完整映射来源、应用明细与 1:1 校验结果。
4. `verification.json`：`verify` 校验结果；仅当 `"status": "verified"`（确认剥离全部 `【...】` 后与未注音底稿逐字一致、括号成对且未插进 `《》` 内）时可交付。

### 5. 单句/文件快速直出 (`annotate`，可选)

无需创建批次目录时，可直接对单段文字或文件运行一次注音检查：

```powershell
python '<skill>/scripts/ruby.py' annotate --text '本周，あざらしそふと公开了《アマカノ》情报。'
```
