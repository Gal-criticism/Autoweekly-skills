"""Japanese proper-name extraction, VNDB lookup, local Hepburn kana fallback, and safe 1:1 ruby rewrite."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

MD_STAR_RE = re.compile(r"\*+")
MULTI_SPACE_RE = re.compile(r"[ \t\u3000]+")
KANA_RE = re.compile(r"[\u3041-\u3093\u30A1-\u30F4\u30FC]")
KATA_RUN_RE = re.compile(r"[\u30A1-\u30F4\u30FC]{2,}")
HIRA_RUN_RE = re.compile(r"[\u3041-\u3093]{2,}")
KANJI_RE = re.compile(r"[\u4E00-\u9FA5\u3005\u3006\u30F5\u30F6]")
HANZI_RE = re.compile(r"[\u4E00-\u9FA5]")
LATIN_RE = re.compile(r"[A-Za-z]")
SPACE_RE = re.compile(r"\s+")

JP_TOKEN_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9]*[\u30FB\uFF65])?"
    r"[\u4E00-\u9FA5\u3005\u3006\u30F5\u30F6\u3041-\u3093\u30A1-\u30F4\u30FCA-Za-z0-9\u2020]{1,24}"
    r"(?:[\u30FB\uFF65][\u4E00-\u9FA5\u3005\u3006\u30F5\u30F6\u3041-\u3093\u30A1-\u30F4\u30FCA-Za-z0-9\u2020]{1,24})*"
)

CN_BOUNDARY_WORDS = [
    "负责", "剧本", "原案", "原画", "脚本", "音乐", "美术", "主题曲", "程序", "演出",
    "官网", "玩家", "作品", "发售", "预约", "更新", "详细", "信息", "感兴趣",
    "本周", "本作", "新作", "公布", "登陆", "平台", "预计", "演唱", "前往",
    "获取", "同人", "社团", "全年龄", "宣布", "代理", "发行", "版本", "讲述",
    "支持", "简体中文", "繁体中文", "英语", "商店", "页面", "目前", "已经",
    "公开", "持续", "关注", "生活", "收到", "邀请", "回乡", "度过", "重新",
    "见到", "故事", "语言", "三种", "绘制", "制作", "体验版", "角色", "声优",
    "一张", "贺图", "预告", "剪影", "内容", "包括", "同步", "正式", "查看",
    "献唱", "配音", "担任", "作词", "作曲", "编曲", "旗下", "主机版", "移植版",
    "协助", "参与", "开发", "推出", "合作", "共同", "带来", "表示",
    "将升级", "升级为", "采用", "材质", "进行", "再版", "无关", "相关",
]

CN_ROLE_WORDS = [
    "原案", "脚本", "剧本", "原画", "音乐", "美术", "主题曲", "片头曲", "片尾曲",
    "程序", "演出", "SD原画", "作词", "作曲", "编曲", "监督", "企划", "企划原案",
    "绘制", "作画", "演唱", "献唱", "配音", "声优",
]

CN_CONNECTORS = ["由", "和", "与", "及", "以及", "还有", "等", "等人"]
CN_BAD_CHARS = set("了在是把被从到及其的")
CN_PURE_BLOCK = [
    "一张", "贺图", "玩家", "官网", "作品", "发售", "预约", "更新", "详细",
    "信息", "感兴趣", "本周", "本作", "新作", "宣布", "代理", "发行", "版本",
]

CN_DIRTY_RE = re.compile(
    r"(负责|剧本|原案|原画|脚本|音乐|美术|官网|玩家|发售|预约|更新|本作|本周|绘制|作画|由|和|与|及|以及|等人|等)"
)

KATA_MAP = {
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o",
    "カ": "ka", "キ": "ki", "ク": "ku", "ケ": "ke", "コ": "ko",
    "サ": "sa", "シ": "shi", "ス": "su", "セ": "se", "ソ": "so",
    "タ": "ta", "チ": "chi", "ツ": "tsu", "テ": "te", "ト": "to",
    "ナ": "na", "ニ": "ni", "ヌ": "nu", "ネ": "ne", "ノ": "no",
    "ハ": "ha", "ヒ": "hi", "フ": "fu", "ヘ": "he", "ホ": "ho",
    "マ": "ma", "ミ": "mi", "ム": "mu", "メ": "me", "モ": "mo",
    "ヤ": "ya", "ユ": "yu", "ヨ": "yo",
    "ラ": "ra", "リ": "ri", "ル": "ru", "レ": "re", "ロ": "ro",
    "ワ": "wa", "ヲ": "o", "ン": "n",
    "ガ": "ga", "ギ": "gi", "グ": "gu", "ゲ": "ge", "ゴ": "go",
    "ザ": "za", "ジ": "ji", "ズ": "zu", "ゼ": "ze", "ゾ": "zo",
    "ダ": "da", "ヂ": "ji", "ヅ": "zu", "デ": "de", "ド": "do",
    "バ": "ba", "ビ": "bi", "ブ": "bu", "ベ": "be", "ボ": "bo",
    "パ": "pa", "ピ": "pi", "プ": "pu", "ペ": "pe", "ポ": "po",
    "キャ": "kya", "キュ": "kyu", "キョ": "kyo",
    "シャ": "sha", "シュ": "shu", "ショ": "sho",
    "チャ": "cha", "チュ": "chu", "チョ": "cho",
    "ニャ": "nya", "ニュ": "nyu", "ニョ": "nyo",
    "ヒャ": "hya", "ヒュ": "hyu", "ヒョ": "hyo",
    "ミャ": "mya", "ミュ": "myu", "ミョ": "myo",
    "リャ": "rya", "リュ": "ryu", "リョ": "ryo",
    "ギャ": "gya", "ギュ": "gyu", "ギョ": "gyo",
    "ジャ": "ja", "ジュ": "ju", "ジョ": "jo",
    "ビャ": "bya", "ビュ": "byu", "ビョ": "byo",
    "ピャ": "pya", "ピュ": "pyu", "ピョ": "pyo",
    "ファ": "fa", "フィ": "fi", "フェ": "fe", "フォ": "fo", "フュ": "fyu",
    "ティ": "ti", "ディ": "di", "トゥ": "tu", "ドゥ": "du",
    "ウェ": "we", "ウィ": "wi", "ウォ": "wo",
    "ヴァ": "va", "ヴィ": "vi", "ヴェ": "ve", "ヴォ": "vo",
    "チェ": "che", "シェ": "she", "ジェ": "je",
    "ッ": "", "ー": "",
    "ァ": "a", "ィ": "i", "ゥ": "u", "ェ": "e", "ォ": "o",
    "ャ": "ya", "ュ": "yu", "ョ": "yo", "ヴ": "vu",
}

HIRA_TO_KATA = str.maketrans(
    "ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをんゔヵヶ",
    "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶ",
)

KANJI_READING = {
    "着替": "kisekae",
    "着せ替え": "kisekae",
    "科学": "kagaku",
    "書店": "shoten",
    "本屋": "honya",
}


def has_kana(s: str) -> bool:
    return bool(KANA_RE.search(s or ""))


def soft_norm(s: str) -> str:
    return SPACE_RE.sub(" ", (s or "").strip().replace("\u3000", " "))


def _clean_span(s: str) -> str:
    return (s or "").strip().strip(" ，,。．.；;、·:：!！?？\"'“”‘’()（）[]【】<>《》")


def _kata_to_hira(s: str) -> str:
    out = []
    for ch in s or "":
        o = ord(ch)
        if 0x30A1 <= o <= 0x30F6:
            out.append(chr(o - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def _match_key(s: str) -> str:
    return _kata_to_hira(soft_norm(s)).replace(" ", "").lower()


def _starts_with_any(text: str, i: int, words: list[str]) -> str | None:
    for w in sorted(words, key=len, reverse=True):
        if text.startswith(w, i):
            return w
    return None


def _ends_with_any(text: str, i: int, words: list[str]) -> str | None:
    for w in sorted(words, key=len, reverse=True):
        length = len(w)
        if i - length + 1 >= 0 and text[i - length + 1 : i + 1] == w:
            return w
    return None


def _looks_jp_name(s: str, allow_single_kanji: bool = False, allow_pure_kanji: bool = True) -> bool:
    s = _clean_span(s)
    if not s or len(s) > 40:
        return False
    if len(s) < 2 and not (allow_single_kanji and s == "硯"):
        return False
    for w in CN_BOUNDARY_WORDS + CN_ROLE_WORDS + CN_CONNECTORS + CN_PURE_BLOCK:
        if w and w in s:
            return False
    if any(ch in CN_BAD_CHARS for ch in s):
        return False
    if not (has_kana(s) or KANJI_RE.search(s)):
        return False
    if not has_kana(s):
        if not allow_pure_kanji:
            return False
        if not re.fullmatch(r"[\u4E00-\u9FA5\u3005\u3006]{2,6}", s):
            return False
        if any(ch in s for ch in "一张贺图预告剪影内容包括同步正式查看"):
            return False
    if re.fullmatch(r"[A-Za-z0-9\-]{1,10}", s):
        return False
    if re.search(r"[A-Za-z]", s) and (not has_kana(s)) and not re.search(r"[・･†]", s):
        return False
    if re.search(r"\d", s) and not has_kana(s) and not re.search(r"[\u4E00-\u9FA5]", s):
        return False
    if re.search(r"[和与及]", s):
        return False
    if s.startswith(("由", "和")):
        return False
    return True


def _split_name_list(chunk: str) -> list[str]:
    chunk = _clean_span(chunk)
    if not chunk:
        return []
    chunk = re.sub(r"^由\s*", "", chunk)
    chunk = re.sub(r"(?:等人|等)$", "", chunk)
    parts = re.split(r"[、,，/|]|和|与|及|以及|还有", chunk)
    out: list[str] = []
    for p in parts:
        p = _clean_span(p)
        p = re.sub(r"^(?:本作|本周|由)\s*", "", p)
        if not p:
            continue
        if _looks_jp_name(p, allow_single_kanji=True, allow_pure_kanji=True):
            out.append(p)
            continue
        for tok in JP_TOKEN_RE.findall(p):
            tok = _clean_span(tok)
            if _looks_jp_name(tok, allow_single_kanji=True, allow_pure_kanji=True):
                out.append(tok)
    return out


def _extract_credit_names(text: str) -> list[str]:
    cands: list[str] = []
    role_alt = "|".join(map(re.escape, CN_ROLE_WORDS))
    patterns = [
        rf"由([^。；;\n]{{1,80}}?)负责(?:{role_alt})?",
        rf"由([^。；;\n，,]{{1,40}}?)(?:绘制|作画|作词|作曲|编曲|演唱|献唱|配音)",
        rf"(?:{role_alt})(?:[和与及](?:{role_alt}))*由([^。；;\n]{{1,80}}?)负责",
        rf"([^。；;\n]{{1,80}}?)负责(?:{role_alt})",
        rf"(?:绘制|作画|原画|脚本|剧本)(?:的)?(?:是|为)由?([^。；;\n，,]{{1,40}})",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            chunk = re.sub(rf"^(?:{role_alt})(?:[和与及](?:{role_alt}))*", "", m.group(1))
            cands.extend(_split_name_list(chunk))
    return cands


def _inside_guillemets(text: str, pos: int) -> bool:
    last_open = text.rfind("《", 0, pos)
    if last_open == -1:
        return False
    last_close = text.rfind("》", 0, pos)
    if last_close > last_open:
        return False
    return text.find("》", pos) != -1


def _expand_jp_name(text: str, start: int, end: int) -> str:
    n = len(text)
    left = start
    right = end
    stop_words = CN_BOUNDARY_WORDS + CN_ROLE_WORDS + CN_CONNECTORS

    def stop_left(pos: int) -> bool:
        if pos < 0 or text[pos] in "由和与及" or text[pos] in CN_BAD_CHARS:
            return True
        return bool(_ends_with_any(text, pos, stop_words) or _starts_with_any(text, pos, stop_words))

    def stop_right(pos: int) -> bool:
        if pos >= n or text[pos] in "由和与及" or text[pos] in CN_BAD_CHARS:
            return True
        return bool(_starts_with_any(text, pos, stop_words))

    while left > 0:
        ch = text[left - 1]
        if ch in "・･":
            left -= 1
            continue
        if KANJI_RE.match(ch) or re.match(r"[A-Za-z0-9'’\-.†]", ch):
            if stop_left(left - 1):
                break
            left -= 1
            continue
        break

    while right < n:
        ch = text[right]
        if ch in "・･":
            right += 1
            continue
        if KANA_RE.match(ch) or KANJI_RE.match(ch) or re.match(r"[A-Za-z0-9'’\-.†]", ch):
            if stop_right(right):
                break
            right += 1
            continue
        break
    return _clean_span(text[left:right])


def _normalize_person(s: str) -> list[str]:
    s = _clean_span(s)
    if not s:
        return []
    s = re.sub(r"^由", "", s)
    if any(w in s for w in CN_ROLE_WORDS + ["负责", "本作", "本周"]):
        s2 = s
        for w in CN_ROLE_WORDS + ["负责", "本作", "本周", "由"]:
            s2 = s2.replace(w, " ")
        s = s2
    parts = _split_name_list(s) if re.search(r"[、,，/|和与及]", s) else [s]
    return [
        _clean_span(p)
        for p in parts
        if _looks_jp_name(_clean_span(p), allow_single_kanji=True, allow_pure_kanji=True)
    ]


def extract_jp_candidates(text: str) -> list[str]:
    """Extract intact 《title》 entries and credit-aware Japanese proper names."""
    cleaned = MD_STAR_RE.sub("", (text or "").strip())
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = MULTI_SPACE_RE.sub(" ", cleaned)
    if not cleaned:
        return []

    titles: list[str] = []
    persons: list[str] = []

    for m in re.finditer(r"《([^》]+)》", cleaned):
        title = (m.group(1) or "").strip()
        if not title:
            continue
        if not (has_kana(title) or KANJI_RE.search(title)):
            continue
        if has_kana(title) or re.fullmatch(r"[\u4E00-\u9FA5\u3005\u3006・†A-Za-z0-9 ～~\-]{2,40}", title):
            if (not has_kana(title)) and re.fullmatch(r"[\u4E00-\u9FA5\u3005\u3006・\s～~\-]{2,40}", title):
                if not re.fullmatch(r"[\u4E00-\u9FA5\u3005\u3006・]{2,12}", title):
                    continue
            titles.append(title)

    persons.extend(_extract_credit_names(cleaned))

    for m in KATA_RUN_RE.finditer(cleaned):
        s = _clean_span(m.group(0))
        if _looks_jp_name(s, allow_pure_kanji=False):
            persons.append(s)

    for rx in (KATA_RUN_RE, HIRA_RUN_RE):
        for m in rx.finditer(cleaned):
            if _inside_guillemets(cleaned, m.start()):
                continue
            s = _expand_jp_name(cleaned, m.start(), m.end())
            persons.extend(_normalize_person(s))

    final: list[str] = []
    seen: set[str] = set()
    for c in titles + persons:
        c = (c or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        final.append(c)

    title_set = set(titles)
    titles_final = [c for c in final if c in title_set]
    persons_final = [c for c in final if c not in title_set]

    uniq_persons: list[str] = []
    for c in sorted(set(persons_final), key=lambda x: (-len(x), x)):
        if any(c != k and c in k for k in uniq_persons):
            continue
        uniq_persons.append(c)
    persons_ordered = [c for c in persons_final if c in uniq_persons]
    return titles_final + list(dict.fromkeys(persons_ordered))


def clean_romaji(r: str) -> str:
    r = soft_norm(r)
    if not r:
        return r
    r = re.sub(r"\s*[—–−]\s*", " - ", r)
    r = re.sub(r"\s+-\s+", " - ", r)
    if " - " in r:
        head, tail = r.split(" - ", 1)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,'!?:;&+\-]*", tail.strip(" -") or ""):
            r = head.strip()
    r = re.sub(r"\s*[\(（][^\)\）]*[\)）]\s*$", "", r).strip()
    r = re.sub(r"\s*[\[【][^\]】]*[\]】]\s*$", "", r).strip()
    return soft_norm(r)


def _post_vndb(endpoint: str, payload: dict[str, Any], timeout: float = 12.0) -> tuple[list[dict[str, Any]], bool]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.vndb.org/kana/{endpoint}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Autoweekly-weekly-ruby/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        results = parsed.get("results") if isinstance(parsed, dict) else None
        return (results if isinstance(results, list) else []), False
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return [], True


def query_vndb_romaji(candidates: list[str], timeout: float = 12.0) -> dict[str, Any]:
    """Query VNDB /kana/staff and /kana/vn in batches of 12 with graceful failure fallback."""
    if not candidates:
        return {"hits": [], "misses": [], "service_error": False}

    options: list[tuple[str, str, str]] = []
    any_staff_err = False
    any_vn_err = False

    for i in range(0, len(candidates), 12):
        batch = candidates[i : i + 12]
        filters = ["search", "=", batch[0]] if len(batch) == 1 else ["or"] + [["search", "=", c] for c in batch]
        staff_rows, s_err = _post_vndb("staff", {"filters": filters, "fields": "id,name,original", "results": 25}, timeout=timeout)
        vn_rows, v_err = _post_vndb("vn", {"filters": filters, "fields": "id,title,alttitle", "results": 25}, timeout=timeout)
        any_staff_err = any_staff_err or s_err
        any_vn_err = any_vn_err or v_err

        for item in staff_rows:
            if not isinstance(item, dict):
                continue
            original = soft_norm(item.get("original") or "")
            name = clean_romaji(item.get("name") or "")
            if not original or not name:
                continue
            if has_kana(name) or HANZI_RE.search(name) or not LATIN_RE.search(name):
                continue
            sid = item.get("id") or ""
            for surface in (original, original.replace(" ", "")):
                if surface:
                    options.append((surface, name, f"vndb:staff:{sid}"))

        for item in vn_rows:
            if not isinstance(item, dict):
                continue
            alttitle = soft_norm(item.get("alttitle") or "")
            title = clean_romaji(item.get("title") or "")
            if not alttitle or not title:
                continue
            if has_kana(title) or HANZI_RE.search(title) or not LATIN_RE.search(title):
                continue
            vid = item.get("id") or ""
            options.append((alttitle, title, f"vndb:vn:{vid}"))

    hits: list[dict[str, Any]] = []
    misses: list[str] = []
    seen_surfaces: set[str] = set()

    for cand in candidates:
        cand_key = _match_key(cand)
        best: tuple[str, str, str, int] | None = None
        best_score = -1
        for surface, romaji, source in options:
            surf_key = _match_key(surface)
            if not cand_key or not surf_key:
                sc = 0
            elif cand_key == surf_key:
                sc = 100
            elif cand_key in surf_key or surf_key in cand_key:
                sc = 80
            else:
                sc = 0
            if sc > best_score:
                best_score = sc
                best = (surface, romaji, source, sc)
        if best and best_score >= 80:
            if cand not in seen_surfaces:
                seen_surfaces.add(cand)
                hits.append({"surface": cand, "romaji": best[1], "source": best[2], "score": best[3]})
        else:
            misses.append(cand)

    return {
        "hits": hits,
        "misses": misses,
        "service_error": any_staff_err and any_vn_err and not hits,
    }


def hepburn_kana(s: str) -> str:
    """Convert pure hiragana/katakana (with optional spaces/middle dots/ASCII) to Capitalized Hepburn romaji."""
    if not has_kana(s):
        return ""
    s = (s or "").translate(HIRA_TO_KATA)
    out: list[str] = []
    i = 0
    while i < len(s):
        if i + 1 < len(s):
            two = s[i : i + 2]
            if two in KATA_MAP and two not in ("ッ", "ー") and s[i] != "ッ":
                out.append(KATA_MAP[two])
                i += 2
                continue
        ch = s[i]
        if ch == "ッ":
            if i + 1 < len(s):
                nxt = s[i + 1 : i + 3]
                rom = KATA_MAP.get(nxt) if nxt in KATA_MAP and s[i + 1] != "ッ" else KATA_MAP.get(s[i + 1], "")
                if rom and rom[0].isalpha():
                    out.append(rom[0])
            i += 1
            continue
        if ch == "ー":
            i += 1
            continue
        if ch in KATA_MAP:
            out.append(KATA_MAP[ch])
            i += 1
            continue
        if ch in "・･ 　-—–_":
            out.append(" ")
            i += 1
            continue
        if re.match(r"[A-Za-z0-9]", ch):
            out.append(ch)
            i += 1
            continue
        return ""

    rom = re.sub(r"\s+", " ", "".join(out)).strip()
    if not rom:
        return ""
    for src, dst in (
        ("toripuru", "toripuru "),
        ("pearingu", " pearingu"),
        ("kisekae", "kisekae "),
        ("korabo", " korabo "),
        ("pakku", " pakku"),
        ("bukku", " bukku"),
    ):
        rom = rom.replace(src, dst)
    rom = re.sub(r"\s+", " ", rom).strip()
    return " ".join(w[:1].upper() + w[1:] for w in rom.split(" ") if w)


def mixed_kana_to_romaji(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    tmp = s
    replaced_kanji = False
    for k, v in sorted(KANJI_READING.items(), key=lambda x: -len(x[0])):
        if k in tmp:
            tmp = tmp.replace(k, f" {v} ")
            replaced_kanji = True
    if not (has_kana(s) or replaced_kanji):
        return ""
    if re.search(r"[\u4E00-\u9FA5]", tmp):
        return ""
    out: list[str] = []
    i = 0
    while i < len(tmp):
        ch = tmp[i]
        if ch.isspace() or ch in "・･-—–_":
            out.append(" ")
            i += 1
            continue
        if re.match(r"[A-Za-z0-9]", ch):
            j = i + 1
            while j < len(tmp) and re.match(r"[A-Za-z0-9]", tmp[j]):
                j += 1
            out.append(tmp[i:j])
            i = j
            continue
        if KANA_RE.match(ch):
            j = i + 1
            while j < len(tmp) and KANA_RE.match(tmp[j]):
                j += 1
            rom = hepburn_kana(tmp[i:j])
            if not rom:
                return ""
            out.append(rom)
            i = j
            continue
        return ""
    rom = re.sub(r"\s+", " ", " ".join(out)).strip()
    return " ".join(w[:1].upper() + w[1:] for w in rom.split(" ") if w)


def pure_kana_name(s: str) -> bool:
    s = (s or "").strip()
    if len(s) < 2:
        return False
    body = re.sub(r"[・･ 　\-—–]", "", s)
    return bool(re.fullmatch(r"[\u3041-\u3093\u30A1-\u30F4\u30FC]+", body))


def local_kana_fallback(misses: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    local_hits: list[dict[str, Any]] = []
    remain: list[str] = []
    for m in misses:
        rom = ""
        src = "local:kana"
        if pure_kana_name(m):
            rom = hepburn_kana(m)
        else:
            rom = mixed_kana_to_romaji(m)
            if rom:
                src = "local:mixed"
        if rom and LATIN_RE.search(rom):
            local_hits.append({"surface": m, "romaji": rom, "source": src, "score": 70})
        else:
            remain.append(m)
    return local_hits, remain


def _is_pure_chinese_span(s: str) -> bool:
    s = (s or "").strip()
    if not s or has_kana(s) or LATIN_RE.search(s) or not HANZI_RE.search(s):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fff0-9\s\-—–~～·、，。！？：；（）()【】\[\]《》『』「」]+", s))


def _clean_pairs_for_text(text: str, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for src, roma in pairs:
        src = soft_norm(src).strip("《》\"'“”")
        roma = clean_romaji(roma)
        if not src or not roma or src in seen:
            continue
        if "【" in src or "】" in src or src.startswith(("http://", "https://")) or re.fullmatch(r"\d+", src):
            continue
        if has_kana(roma) or HANZI_RE.search(roma) or not LATIN_RE.search(roma):
            continue
        if CN_DIRTY_RE.search(src):
            continue
        if src.casefold() == roma.casefold() and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .,&'’\-~～]*", src):
            continue
        if (not has_kana(src)) and _is_pure_chinese_span(src):
            if not re.fullmatch(r"[\u4E00-\u9FA5\u3005\u3006・]{2,12}", src):
                continue
        if f"《{src}》" not in text and src not in text:
            continue
        seen.add(src)
        out.append((src, roma))
    out.sort(key=lambda x: len(x[0]), reverse=True)
    return out


def _ranges_overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return not (a1 <= b0 or b1 <= a0)


def _apply_annotations(text: str, pairs: list[tuple[str, str]]) -> tuple[str, list[tuple[str, str]]]:
    occupied: list[tuple[int, int]] = []
    replacements: list[tuple[int, int, str, str, str]] = []

    for src, roma in pairs:
        title = f"《{src}》"
        start = 0
        matched_title = False
        while True:
            i = text.find(title, start)
            if i < 0:
                break
            j = i + len(title)
            if text.startswith("【", j) or any(_ranges_overlap(i, j, a, b) for a, b in occupied):
                start = j
                continue
            occupied.append((i, j))
            replacements.append((i, j, f"《{src}》【{roma}】", src, roma))
            matched_title = True
            start = j
        if matched_title:
            continue

        start = 0
        while True:
            i = text.find(src, start)
            if i < 0:
                break
            j = i + len(src)
            if _inside_guillemets(text, i):
                left = text.rfind("《", 0, i)
                right = text.find("》", j - 1)
                if right != -1 and text[left + 1 : right] != src:
                    start = j
                    continue
            if text.startswith("【", j) or any(_ranges_overlap(i, j, a, b) for a, b in occupied):
                start = j
                continue
            occupied.append((i, j))
            replacements.append((i, j, f"{src}【{roma}】", src, roma))
            start = j

    replacements.sort(key=lambda x: x[0])
    out: list[str] = []
    cursor = 0
    applied_pairs: list[tuple[str, str]] = []
    for i, j, rep, src, roma in replacements:
        if i < cursor:
            continue
        out.append(text[cursor:i])
        out.append(rep)
        cursor = j
        applied_pairs.append((src, roma))
    out.append(text[cursor:])
    return "".join(out), applied_pairs


def validate_one_to_one(original: str, annotated: str, applied_pairs: list[tuple[str, str]]) -> tuple[bool, str]:
    """Verify exact 1:1 ruby preservation and that stripping 【...】 restores original text."""
    if not annotated:
        return False, "empty"
    if annotated.count("【") != annotated.count("】"):
        return False, "bracket_mismatch"
    if re.search(r"《[^》]*【[^】]*】[^》]*》", annotated):
        return False, "ruby_inside_title"

    restored = re.sub(r"【[^】]*】", "", annotated)
    if re.sub(r"\s+", "", restored) != re.sub(r"\s+", "", original):
        return False, "text_mutated"

    rubies = [m.group(1).strip() for m in re.finditer(r"【([^】]+)】", annotated)]
    if len(rubies) != len(applied_pairs):
        return False, f"count_mismatch:{len(rubies)}!={len(applied_pairs)}"

    for roma, (surface, exp_roma) in zip(rubies, applied_pairs):
        surface = soft_norm(surface)
        exp_roma = soft_norm(exp_roma)
        roma = soft_norm(roma)
        if not surface or not exp_roma:
            return False, "empty_pair"
        if CN_DIRTY_RE.search(surface):
            return False, f"dirty_surface:{surface}"
        if roma != exp_roma:
            return False, f"roma_mismatch:{surface}:{roma}!={exp_roma}"
        if f"《{surface}》【{exp_roma}】" not in annotated and f"{surface}【{exp_roma}】" not in annotated:
            return False, f"not_adjacent:{surface}"
    return True, "ok"


def normalize_pairs(raw_pairs: Any) -> list[tuple[str, str]]:
    """Normalize dict or list-of-dicts ruby pairs into clean (surface, romaji) tuples."""
    pairs: list[tuple[str, str]] = []
    if not raw_pairs:
        return pairs
    if isinstance(raw_pairs, dict):
        for k, v in raw_pairs.items():
            if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                pairs.append((k.strip(), v.strip()))
    elif isinstance(raw_pairs, list):
        for item in raw_pairs:
            if isinstance(item, dict):
                src = item.get("surface") or item.get("term") or item.get("src")
                rom = item.get("romaji") or item.get("reading") or item.get("value")
                if isinstance(src, str) and isinstance(rom, str) and src.strip() and rom.strip():
                    pairs.append((src.strip(), rom.strip()))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                pairs.append((str(item[0]).strip(), str(item[1]).strip()))
    return pairs


def resolve_candidates(
    combined_text: str,
    *,
    extra_pairs: Any = None,
    use_vndb: bool = True,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Extract candidates and resolve readings via VNDB -> local kana -> extra_pairs."""
    candidates = extract_jp_candidates(combined_text)
    if use_vndb and candidates:
        vndb_res = query_vndb_romaji(candidates, timeout=timeout)
    else:
        vndb_res = {"hits": [], "misses": list(candidates), "service_error": False}

    vndb_hits = vndb_res["hits"]
    local_hits, remain = local_kana_fallback(vndb_res["misses"])

    supplied = normalize_pairs(extra_pairs)
    supplied_map = {src: rom for src, rom in supplied}

    extra_hits: list[dict[str, Any]] = []
    unresolved_misses: list[str] = []
    for rem in remain:
        if rem in supplied_map:
            extra_hits.append({"surface": rem, "romaji": supplied_map[rem], "source": "agent:ruby_pairs", "score": 90})
        elif has_kana(rem):
            unresolved_misses.append(rem)

    # Allow explicit extra_pairs to override VNDB/local or supply additional surfaces
    merged_map: dict[str, str] = {}
    for h in vndb_hits:
        merged_map[h["surface"]] = h["romaji"]
    for h in local_hits:
        merged_map.setdefault(h["surface"], h["romaji"])
    for src, rom in supplied:
        merged_map[src] = rom

    merged_pairs = list(merged_map.items())
    return {
        "candidates": candidates,
        "vndb_hits": vndb_hits,
        "local_hits": local_hits,
        "extra_hits": extra_hits,
        "unresolved_misses": unresolved_misses,
        "vndb_service_error": vndb_res["service_error"],
        "merged_pairs": merged_pairs,
    }


def _annotate_single(text: str, merged_pairs: list[tuple[str, str]]) -> tuple[str, list[str], bool]:
    if not text:
        return "", [], True
    clean_pairs = _clean_pairs_for_text(text, merged_pairs)
    annotated, applied_pairs = _apply_annotations(text, clean_pairs)
    ok, _ = validate_one_to_one(text, annotated, applied_pairs)
    if not ok:
        return text, [], False
    return (annotated if applied_pairs else text), [f"{s}=>{r}" for s, r in applied_pairs], True


def annotate_text(
    text: str,
    *,
    extra_pairs: Any = None,
    use_vndb: bool = True,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Run extract -> VNDB -> local kana fallback -> extra_pairs -> safe 1:1 ruby rewrite on text."""
    original = (text or "").strip()
    if not original:
        return {
            "original": "",
            "annotated": "",
            "ok": True,
            "reason": "ok",
            "candidates": [],
            "vndb_hits": [],
            "local_hits": [],
            "extra_hits": [],
            "unresolved_misses": [],
            "applied": [],
            "vndb_service_error": False,
        }

    resolved = resolve_candidates(original, extra_pairs=extra_pairs, use_vndb=use_vndb, timeout=timeout)
    clean_pairs = _clean_pairs_for_text(original, resolved["merged_pairs"])
    annotated, applied_pairs = _apply_annotations(original, clean_pairs)
    ok, reason = validate_one_to_one(original, annotated, applied_pairs)
    final_text = annotated if (ok and applied_pairs) else original

    return {
        "original": original,
        "annotated": final_text,
        "ok": ok,
        "reason": reason,
        "candidates": resolved["candidates"],
        "vndb_hits": resolved["vndb_hits"],
        "local_hits": resolved["local_hits"],
        "extra_hits": resolved["extra_hits"],
        "unresolved_misses": resolved["unresolved_misses"],
        "applied": [f"{s}=>{r}" for s, r in applied_pairs],
        "vndb_service_error": resolved["vndb_service_error"],
    }


def annotate_document(
    doc: dict[str, Any],
    *,
    extra_pairs: Any = None,
    use_vndb: bool = True,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Annotate a normalized document {doc_title, entries: [{section, title, body_paragraphs}]} in one batch."""
    entries = doc.get("entries", [])
    chunks: list[str] = []
    for entry in entries:
        t = str(entry.get("title", "")).strip()
        if t:
            chunks.append(t)
        for p in entry.get("body_paragraphs", []):
            p_str = str(p).strip()
            if p_str:
                chunks.append(p_str)

    combined = "\n".join(chunks)
    combined_pairs = normalize_pairs(doc.get("ruby_pairs")) + normalize_pairs(extra_pairs)
    resolved = resolve_candidates(combined, extra_pairs=combined_pairs, use_vndb=use_vndb, timeout=timeout)
    merged_pairs = resolved["merged_pairs"]

    annotated_entries: list[dict[str, Any]] = []
    all_applied: list[str] = []
    all_ok = True

    for entry in entries:
        section = str(entry.get("section", "")).strip()
        clean_title = str(entry.get("title", "")).strip()
        ann_title, app_t, ok_t = _annotate_single(clean_title, merged_pairs)
        if not ok_t:
            all_ok = False
        all_applied.extend(app_t)

        clean_paras = [str(p).strip() for p in entry.get("body_paragraphs", []) if str(p).strip()]
        ann_paras: list[str] = []
        for p in clean_paras:
            ann_p, app_p, ok_p = _annotate_single(p, merged_pairs)
            if not ok_p:
                all_ok = False
            all_applied.extend(app_p)
            ann_paras.append(ann_p)

        annotated_entries.append(
            {
                "section": section,
                "title": ann_title,
                "clean_title": clean_title,
                "body_paragraphs": ann_paras,
                "clean_body_paragraphs": clean_paras,
            }
        )

    return {
        "ok": all_ok,
        "doc_title": str(doc.get("doc_title", "")).strip(),
        "annotated_entries": annotated_entries,
        "candidates": resolved["candidates"],
        "vndb_hits": resolved["vndb_hits"],
        "local_hits": resolved["local_hits"],
        "extra_hits": resolved["extra_hits"],
        "unresolved_misses": resolved["unresolved_misses"],
        "applied": list(dict.fromkeys(all_applied)),
        "vndb_service_error": resolved["vndb_service_error"],
    }
