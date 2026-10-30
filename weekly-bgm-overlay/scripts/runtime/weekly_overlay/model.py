"""Deterministic dates, BGM parsing, and schema validation for weekly overlays."""

from datetime import date, datetime, timedelta, timezone
import re
import unicodedata


CATEGORIES = ("新作情报", "汉化情报", "周边情报", "简讯")

_SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
_ALIASES = {
    "新作": "新作情报",
    "新作情报": "新作情报",
    "汉化": "汉化情报",
    "汉化情报": "汉化情报",
    "周边": "周边情报",
    "周边情报": "周边情报",
    "简讯": "简讯",
}
_DATE_PATTERN = r"[0-9]{4}(?:-[0-9]{2}-[0-9]{2}|\.[0-9]{2}\.[0-9]{2})"
_PERIOD_PATTERN = re.compile(
    rf"({_DATE_PATTERN})\s*-\s*({_DATE_PATTERN})"
)
_TASK_KEYS = {"schema_version", "date_start", "date_end", "items"}
_ITEM_KEYS = {"category", "title", "artist"}


def _has_controls(text):
    return any(
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in ("Zl", "Zp")
        for character in text
    )


def _single_line(value, field):
    if not isinstance(value, str) or _has_controls(value) or not value.strip():
        raise ValueError(f"{field} must be nonempty single-line text without controls")
    return value.strip()


def _parse_date(value, field):
    if not isinstance(value, str) or re.fullmatch(_DATE_PATTERN, value) is None:
        raise ValueError(f"{field} must use YYYY-MM-DD or YYYY.MM.DD")
    try:
        return date.fromisoformat(value.replace(".", "-"))
    except ValueError as error:
        raise ValueError(f"{field} is not a valid calendar date") from error


def _period(start, end):
    start = _parse_date(start, "date_start")
    end = _parse_date(end, "date_end")
    if start > end:
        raise ValueError("date_start must not be after date_end")
    return start, end


def default_period(today=None):
    """Return Saturday through Friday for today's Monday-based Shanghai week.

    Inject a date for deterministic operation. Aware datetimes are converted to
    Shanghai; naive datetimes are interpreted as Shanghai wall time.
    """
    if today is None:
        today = datetime.now(_SHANGHAI).date()
    elif isinstance(today, datetime):
        if today.tzinfo is not None:
            today = today.astimezone(_SHANGHAI)
        today = today.date()
    elif not isinstance(today, date):
        raise ValueError("today must be a date or datetime")
    try:
        end = today + timedelta(days=4 - today.weekday())
        return end - timedelta(days=6), end
    except OverflowError as error:
        raise ValueError("today's weekly period is outside the calendar range") from error


def validate_task(task):
    """Return a fresh canonical schema-v1 payload, or raise ValueError.

    Dates accept ISO or dotted endpoints and become ISO. Category names must be
    canonical. Items are sorted by category, preserving track order within it.
    """
    if not isinstance(task, dict) or set(task) != _TASK_KEYS:
        raise ValueError("task must contain exactly schema_version, date_start, date_end, items")
    if type(task["schema_version"]) is not int or task["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    start, end = _period(task["date_start"], task["date_end"])
    if not isinstance(task["items"], list) or not task["items"]:
        raise ValueError("items must be a nonempty list")

    grouped = {category: [] for category in CATEGORIES}
    seen = set()
    for item in task["items"]:
        if not isinstance(item, dict) or set(item) != _ITEM_KEYS:
            raise ValueError("each item must contain exactly category, title, artist")
        category = item["category"]
        if not isinstance(category, str) or category not in grouped:
            raise ValueError("unknown or noncanonical category")
        title = _single_line(item["title"], "title")
        artist = _single_line(item["artist"], "artist")
        identity = (category, title, artist)
        if identity in seen:
            raise ValueError(f"duplicate track in {category}")
        seen.add(identity)
        grouped[category].append(
            {"category": category, "title": title, "artist": artist}
        )
    missing = [category for category in CATEGORIES if not grouped[category]]
    if missing:
        raise ValueError("missing categories: " + ", ".join(missing))
    return {
        "schema_version": 1,
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "items": [item for category in CATEGORIES for item in grouped[category]],
    }


def _parse_tracks(value):
    tracks = []
    seen = set()
    for raw_track in re.split(r"[;；]", value):
        if " - " not in raw_track:
            raise ValueError("each track must use Artist - Title")
        artist, title = raw_track.split(" - ", 1)
        artist = _single_line(artist, "artist")
        title = _single_line(title, "title")
        identity = (artist, title)
        if identity in seen:
            raise ValueError("duplicate track in mapping")
        seen.add(identity)
        tracks.append({"title": title, "artist": artist})
    return tracks


def _parse_categories(label):
    categories = []
    for token in re.split(r"[,，、]", label):
        token = token.strip()
        if token.endswith("部分"):
            token = token[:-2].strip()
        if token not in _ALIASES:
            raise ValueError(f"unknown category label: {token}")
        category = _ALIASES[token]
        if category in categories:
            raise ValueError(f"duplicate category label: {category}")
        categories.append(category)
    return categories


def parse_bgm(text, today=None, date_start=None, date_end=None):
    """Parse BGM mappings into canonical schema v1.

    Explicit endpoint arguments take precedence over an optional date line,
    then the automatic period. Both explicit endpoints must be supplied.
    Repeated category or rest mappings are errors, even when identical.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("BGM text must be a nonempty string")
    if (date_start is None) != (date_end is None):
        raise ValueError("date_start and date_end must be supplied together")
    explicit_period = (
        _period(date_start, date_end) if date_start is not None else None
    )
    mappings = {}
    rest = None
    dated_period = None
    # Resolve fallback mappings only after every explicit category is known.
    for number, line in enumerate(text.split("\n"), start=1):
        if line.endswith("\r"):
            line = line[:-1]
        if _has_controls(line):
            raise ValueError(f"line {number} contains control or line-separator characters")
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[:：]", line, maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"line {number} must be a label: value mapping")
        label, value = (part.strip() for part in parts)
        if label == "日期":
            if dated_period is not None:
                raise ValueError("duplicate date line")
            match = _PERIOD_PATTERN.fullmatch(value)
            if match is None:
                raise ValueError("date line must contain two ISO or dotted endpoints")
            dated_period = _period(*match.groups())
        elif label == "其余":
            if rest is not None:
                raise ValueError("duplicate or conflicting rest mapping")
            rest = _parse_tracks(value)
        else:
            categories = _parse_categories(label)
            tracks = _parse_tracks(value)
            for category in categories:
                if category in mappings:
                    raise ValueError(f"duplicate or conflicting mapping for {category}")
                mappings[category] = tracks

    selected_period = explicit_period or dated_period
    if selected_period is None:
        selected_period = default_period(today)
    items = []
    for category in CATEGORIES:
        tracks = mappings.get(category, rest)
        if not tracks:
            raise ValueError(f"missing tracks for {category}")
        items.extend({"category": category, **track} for track in tracks)
    start, end = selected_period
    return validate_task(
        {
            "schema_version": 1,
            "date_start": start.isoformat(),
            "date_end": end.isoformat(),
            "items": items,
        }
    )


def display_period(task):
    """Format a validated task's period as YYYY.MM.DD - YYYY.MM.DD."""
    canonical = validate_task(task)
    start = canonical["date_start"].replace("-", ".")
    end = canonical["date_end"].replace("-", ".")
    return f"{start} - {end}"
