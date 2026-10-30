# Task schema v1

```json
{
  "schema_version": 1,
  "date_start": "2026-10-24",
  "date_end": "2026-10-30",
  "items": [
    {"category": "新作情报", "title": "夏休みの過ごし方", "artist": "折戸伸治"},
    {"category": "汉化情报", "title": "夏休みの過ごし方", "artist": "折戸伸治"},
    {"category": "周边情报", "title": "奉仕部活動日誌 01", "artist": "石濱翔"},
    {"category": "简讯", "title": "奉仕部活動日誌 01", "artist": "石濱翔"}
  ]
}
```

All four canonical categories must have one or more tracks. Repeated identical
tracks within one category are rejected. Field values must be nonempty,
single-line strings without control characters. Additional keys are rejected.
Dates accept ISO or YYYY.MM.DD and are normalized to ISO. A manually selected
period need not be exactly seven days, but the start must not exceed the end.
The CLI sorts by category, retaining track order within each category.

Each prepare call creates a unique run directory containing:

- job.json: canonical task, source/staged paths and original SHA256.
- template.psd: independent disk copy, never saved by the renderer.
- run.jsx: self-contained trusted renderer plus safely serialized data.
- started.json: run guard; no blind rerun.
- dispatch.json: exclusive submission guard, transport and helper outcome.
- connection.json: native COM version, startup and document-count evidence.
- invoke.log: helper stdout/stderr, including connection errors.
- result.json: Photoshop status, actual field fonts and bounds, output mapping.
- PNGs: one per category/track; sequential stable filenames.
- verification.json: independent dimensions/alpha/hash/mapping checks.
- preview.jpg: contact sheet; check visually before final acceptance.

Renderer expects exactly one of each named text layer in the template:
新作情报 / 汉化情报 / 周边情报 / 简讯 /
【新闻日期】 / 【音乐名】 / 【音乐作者】.
It enables their ancestors, preserves their inherited effects, and disables
unrelated layers. Text is not automatically shrunk or truncated; overflowing
the reserved area raises a failure. The fixed template is 2160x1080 RGB 8-bit.
