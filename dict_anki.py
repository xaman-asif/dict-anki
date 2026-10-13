#!/usr/bin/env python3
"""Backend — dict.cc search, gTTS audio, Anki integration."""
import re
import json
import base64
import io
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ANKI_URL    = "http://localhost:8765"
CONFIG_PATH = Path.home() / ".dict_anki.json"
DICT_CC_URL = "https://www.dict.cc/"

GENDER_ARTICLE = {"m": "der", "f": "die", "n": "das"}

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
            "Referer": DICT_CC_URL,
        })
        _session.get(DICT_CC_URL, timeout=5)
    return _session


# ── AnkiConnect ───────────────────────────────────────────────────────────────

def anki(action, **params):
    r = requests.post(
        ANKI_URL,
        json={"action": action, "version": 6, "params": params},
        timeout=5,
    )
    data = r.json()
    if data["error"]:
        raise RuntimeError(data["error"])
    return data["result"]


def store_anki_media(filename: str, data: bytes) -> str:
    """Store bytes in Anki's media folder. Returns the stored filename."""
    b64 = base64.b64encode(data).decode()
    return anki("storeMediaFile", filename=filename, data=b64)


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(config: dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False))


# ── dict.cc ───────────────────────────────────────────────────────────────────

def fetch_search(word: str) -> dict:
    """
    Fetch dict.cc and return:
      results   – list of {german_word, english, gender}
      word_info – list of {type, content}
      images    – list of {id, description, url}
    """
    r = _get_session().get(DICT_CC_URL, params={"s": word}, timeout=10)
    r.raise_for_status()
    html = r.text
    return {
        "results":   _parse_results(html, word),
        "word_info": _extract_word_info(html),
        "images":    _extract_images(html),
    }


def _extract_string_arr(html: str, name: str) -> list[str]:
    """Parse a JS string array, e.g. var c1Arr = new Array("","foo","bar");
    Index 0 is always the empty leading element."""
    m = re.search(rf'var {name}\s*=\s*new Array\(([^;]+)\);', html)
    if not m:
        return []
    return [s.replace("\\'", "'") for s in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))]


def _parse_results(html: str, word: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    # JS array: c1[0]="" c1[1]=first English
    c1 = _extract_string_arr(html, "c1Arr")

    results = []
    for tr in soup.find_all("tr", id=re.compile(r"^tr\d+")):
        row  = int(re.search(r"\d+", tr["id"]).group())
        tds  = tr.find_all("td", class_="td7nl")
        if len(tds) < 2:
            continue

        def clean(td):
            for d in td.find_all("div"):
                d.decompose()
            return re.sub(r"\s+", " ", td.get_text(" ", strip=True))

        en_raw = clean(tds[0])
        de_raw = clean(tds[1])

        if en_raw.startswith("[") or not en_raw or not de_raw:
            continue

        # Use clean English from JS array (no qualifiers); fall back to cleaning HTML text
        en = (c1[row] if row < len(c1) and c1[row] else _clean_english(en_raw))

        # German: extract gender marker, strip it from word text
        gm     = re.search(r"\{(\w+)\}", de_raw)
        gender = gm.group(1) if gm else None
        de     = re.sub(r"\s*\{\w+\}", "", de_raw)
        de     = re.sub(r"\s*\d+$", "", de).strip()

        # Skip compound-prefix-only entries like "Schreibtisch-"
        if de.endswith("-"):
            continue

        results.append({
            "german_word": de,
            "english":     en,
            "gender":      gender,
        })

    return _sort_results(results, word)


def _clean_english(raw: str) -> str:
    """Fallback cleaner: strip {adj}, [qualifiers], domain prefixes."""
    s = re.sub(r"\s*\{[^}]+\}", "", raw)
    s = re.sub(r"\s*\[[^\]]*\]", "", s)
    s = re.sub(r"^\s*\w+\.\s+", "", s.strip())
    return re.sub(r"\s+\d+$", "", s).strip()


def _sort_results(results: list[dict], query: str) -> list[dict]:
    q = query.lower()
    def score(r):
        w = r["german_word"].lower()
        return (w != q, r["gender"] not in GENDER_ARTICLE and r["gender"] != "pl")
    return sorted(results, key=score)


_WORD_INFO_TYPES = frozenset({
    "NOUN", "VERB", "ADJ", "ADV", "PRON", "PREP",
    "SYNO", "ALSO", "PHRA", "CONJ", "NUM",
})


def _extract_word_info(html: str) -> list[dict]:
    """
    Parse NOUN/SYNO/ALSO/VERB/… rows from dict.cc.
    They live in <tr> elements with no id and unclassed <td> cells,
    separate from the regular translation rows.
    Returns list of {"type": "NOUN"|"SYNO"|…, "content": str}.
    """
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []
    seen: set[tuple] = set()
    for tr in soup.find_all("tr"):
        if tr.get("id"):
            continue
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        if tds[0].get("class"):          # skip noline / styled header cells
            continue
        raw_label = tds[0].get_text(strip=True).upper()
        label = re.sub(r"\d+$", "", raw_label)  # NOUN1/NOUN2 → NOUN
        if label not in _WORD_INFO_TYPES:
            continue
        td = tds[1]
        # Remove collapsed content (conjugation tables, extra synonyms)
        for hidden in td.find_all(lambda t: "display:none" in t.get("style", "") or "display: none" in t.get("style", "")):
            hidden.decompose()
        # Remove javascript "..." expand links
        for a in td.find_all("a", href=re.compile(r"javascript")):
            a.decompose()
        content = re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip()
        if content and (label, content) not in seen:
            seen.add((label, content))
            entries.append({"type": label, "content": content})
    return entries


def _extract_images(html: str) -> list[dict]:
    """Parse Shutterstock thumbnails from display_shutterstock_images_multi(...)."""
    m = re.search(
        r'display_shutterstock_images_multi\s*\([^,]+,\s*[^,]+,\s*(\[.*?\])\s*\)',
        html, re.DOTALL,
    )
    if not m:
        return []
    images = []
    for e in re.finditer(r'\[(\d+),\s*\d+,\s*\d+,\s*"([^"]*)",\s*"([^"]+)"', m.group(1)):
        images.append({"id": e.group(1), "description": e.group(2), "url": e.group(3)})
    return images


# ── Format ────────────────────────────────────────────────────────────────────

def format_german(word: str, gender: str | None) -> str:
    article = GENDER_ARTICLE.get(gender or "")
    if gender == "pl":
        return f"die {word}"
    if article:
        return f"{article} {word}"
    return word


# ── Audio ─────────────────────────────────────────────────────────────────────

def tts_audio(text: str) -> bytes | None:
    """Generate German TTS audio for the given text using gTTS.

    Replaces ' | ' with ', ' so 'der Tisch | die Tische' is spoken naturally.
    """
    try:
        from gtts import gTTS
        spoken = text.replace(" | ", ", ")
        tts = gTTS(text=spoken, lang="de")
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        return buf.getvalue()
    except Exception:
        return None
