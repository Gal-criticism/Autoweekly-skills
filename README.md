# Autoweekly Skills

Two independent Codex skills for producing Galgame weekly-report graphics with
Adobe Photoshop, driven through the native COM / `DoJavaScriptFile` interface
(no Computer Use, no GUI clicking).

| Skill | Purpose |
| --- | --- |
| `weekly-bgm-overlay` | Turn BGM info into transparent PNG helper overlays for the 新作 / 汉化 / 周边 / 简讯 sections. |
| `weekly-cover` | Place CG artwork into three cover templates (16:9, wide banner, 4:3), auto-tint from the CG edge colours, and avoid face occlusion. |

## Layout

```
weekly-bgm-overlay/
  SKILL.md
  agents/openai.yaml
  references/task-schema.md
  scripts/overlay.py
  scripts/runtime/...

weekly-cover/
  SKILL.md
  references/templates.md
  scripts/cover.py
  scripts/runtime/...
```

Each folder is a self-contained skill: point your installer at the folder that
contains `SKILL.md` (e.g. `.../Autoweekly-skills/weekly-cover`).

## Requirements

- Windows with Adobe Photoshop 2023+ installed.
- Python 3 with `Pillow` (`weekly-bgm-overlay` also needs `numpy`).
- Windows PowerShell 5.1 for the COM bridge (do not use PowerShell 7 for COM).
- Your own Photoshop PSD templates (not bundled here).

## Configuring paths

Both skills default to the author's local paths. Override them with environment
variables to run on your machine; each variable falls back to the author default
when unset.

`weekly-cover`

- `WEEKLY_COVER_T1_SOURCE` - 16:9 template PSD
- `WEEKLY_COVER_T2_SOURCE` - wide banner template PSD
- `WEEKLY_COVER_T3_SOURCE` - 4:3 template PSD
- `WEEKLY_COVER_PHOTOSHOP` - path to `Photoshop.exe`
- `WEEKLY_COVER_OUTPUT` - default output directory

`weekly-bgm-overlay`

- `WEEKLY_OVERLAY_TEMPLATE` - overlay master PSD
- `WEEKLY_OVERLAY_PHOTOSHOP` - path to `Photoshop.exe`

## Install

```powershell
git clone https://github.com/GalgameCriticism/Autoweekly-skills.git
Copy-Item -Recurse .\Autoweekly-skills\weekly-cover "$env:USERPROFILE\.codex\skills\weekly-cover"
Copy-Item -Recurse .\Autoweekly-skills\weekly-bgm-overlay "$env:USERPROFILE\.codex\skills\weekly-bgm-overlay"
```

## Notes

- PSD masters, fonts, and client artwork are intentionally not committed.
- Rendering is deterministic and idempotent per run directory; every run writes
  a `job.json` plus verification records.
