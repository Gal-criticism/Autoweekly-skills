"""Cover templates, tint derivation, and job schema."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
from template_assets import template_path


_SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


DEFAULT_TEMPLATES = {
    1: {
        "key": "t1_16x9",
        "source": template_path(
            "WEEKLY_COVER_T1_SOURCE", "E:/114514/周报视频_封面模板_16-9.psd", "weekly-cover-t1-16x9.psd"
        ),
        "size": [2400, 1500],
        "layers": [2026, 2025],
        # The 16:9 template's CG slot already matches a 16:9 CG, so this box
        # performs a 1:1 fit without any crop.
        "box": [-264, 0, 2400, 1500],
        "hide": [],
        "has_date": False,
    },
    2: {
        "key": "t2_wide",
        "source": template_path(
            "WEEKLY_COVER_T2_SOURCE",
            "E:/114514/周报视频_封面模板_V100_文章_1.2-2.psd",
            "weekly-cover-t2-banner.psd",
        ),
        "size": [3525, 1500],
        "layers": [2394, 2393],
        "box": [732, -23, 3525, 1550],
        "hide": [],
        "has_date": True,
    },
    3: {
        "key": "t3_43",
        "source": template_path(
            "WEEKLY_COVER_T3_SOURCE", "E:/114514/周报模板_视频封面_1.3 (1).psd", "weekly-cover-t3-4x3.psd"
        ),
        "size": [2400, 1800],
        "layers": [135, 136],
        # Cover the entire 4:3 canvas with an aspect-preserving crop; never
        # contain a wide CG and leave empty bands above or below it.
        "box": [0, 0, 2400, 1800],
        "hide": [1012, 1013],
        "has_date": False,
    },
}


# Layer ids shared by every template (inspected from the master PSDs).
TINT_LAYER = 919
PERIOD_LAYER = 939
DATE_LAYER = 185


# T2's base gradient is purple-pink. Shift it toward blue so it sits well
# under cold-blue CG art; the values were tuned on the 2026-10-30 trial.
T2_REFINE = {
    "layers": [1340, 1226, 920],
    "hue": -90,
    "saturation": -30,
    "lightness": 8,
    "fills": [1247, 1013],
}


def resolve_templates(choice):
    """Return a shallow copy of the selected template specs."""
    if choice == "all":
        return {index: dict(spec) for index, spec in DEFAULT_TEMPLATES.items()}
    try:
        index = int(choice)
    except (TypeError, ValueError):
        raise ValueError("--template must be 1, 2, 3, or all") from None
    if index not in DEFAULT_TEMPLATES:
        raise ValueError("--template must be 1, 2, 3, or all")
    return {index: dict(DEFAULT_TEMPLATES[index])}


def default_period(today=None):
    """Return Saturday through Friday for today's Monday-based Shanghai week."""
    if today is None:
        today = datetime.now(_SHANGHAI).date()
    elif isinstance(today, datetime):
        today = today.astimezone(_SHANGHAI).date() if today.tzinfo else today.date()
    elif not isinstance(today, date):
        raise ValueError("today must be a date or datetime")
    end = today + timedelta(days=4 - today.weekday())
    return end - timedelta(days=6), end


def parse_period(args):
    """Resolve a (start, end) date pair from CLI arguments."""
    if args.date:
        try:
            left, right = [part.strip() for part in args.date.split("-", 1)]
            start = date.fromisoformat(left.replace(".", "-"))
            end = date.fromisoformat(right.replace(".", "-"))
        except (ValueError, AttributeError):
            raise ValueError("--date must be YYYY.MM.DD - YYYY.MM.DD") from None
    elif args.period_start or args.period_end:
        if not (args.period_start and args.period_end):
            raise ValueError("--period-start and --period-end must be supplied together")
        start = date.fromisoformat(args.period_start)
        end = date.fromisoformat(args.period_end)
    else:
        start, end = default_period()
    if start > end:
        raise ValueError("period start must not be after period end")
    return start, end


def format_period(start, end):
    return f"{start.strftime('%Y.%m.%d')} - {end.strftime('%Y.%m.%d')}"


def tint_for(path):
    """Derive a light, low-saturation background tint from CG edge colours."""
    image = Image.open(path).convert("RGB")
    width, height = image.size
    pixels = []
    for x in range(0, width, 24):
        pixels.append(image.getpixel((x, 4)))
        pixels.append(image.getpixel((x, height - 5)))
    for y in range(0, height, 24):
        pixels.append(image.getpixel((4, y)))
    average = [sum(channel[i] for channel in pixels) // len(pixels) for i in range(3)]
    return [min(255, int(channel + (255 - channel) * 0.72)) for channel in average]


def expected_size(template_index):
    return tuple(DEFAULT_TEMPLATES[int(template_index)]["size"])
