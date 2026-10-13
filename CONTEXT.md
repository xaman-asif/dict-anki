# dict-anki — Project Context

A PyQt6 desktop app that looks up German words on dict.cc, enriches them with
Wiktionary plurals, generates TTS audio with gTTS, shows Shutterstock image
thumbnails for selection, and adds the finished card to Anki via AnkiConnect.

---

## File Structure

```
dict-anki/
├── app.py           # PyQt6 GUI — all UI, workers, dialogs
├── dict_anki.py     # Backend — scraping, API calls, formatting
├── dict-anki        # Linux launcher (bash script, chmod +x)
├── build.ps1        # Windows build script → dist\dict-anki.exe
├── requirements.txt # pip dependencies
├── CONTEXT.md       # this file
├── .gitignore       # ignores venv/, build/, dist/, *.spec, icon.ico
├── dist/            # (generated) dict-anki.exe — double-click to run
└── venv/            # Python virtualenv (Linux; empty on the Windows machine)
```

---

## Backend — `dict_anki.py`

### External services

| Service | Purpose | How accessed |
|---|---|---|
| dict.cc | German↔English translations, Shutterstock images | HTTP scraping via `requests` |
| Wiktionary API | German noun plurals | `en.wiktionary.org/w/api.php` |
| Google TTS (gTTS) | German pronunciation audio | `gtts` library (calls Google Translate TTS) |
| AnkiConnect | Add notes, store media in Anki | HTTP POST to `localhost:8765` |

### Key constants

```python
ANKI_URL    = "http://localhost:8765"
CONFIG_PATH = Path.home() / ".dict_anki.json"   # persisted user settings
DICT_CC_URL = "https://www.dict.cc/"
GENDER_ARTICLE = {"m": "der", "f": "die", "n": "das"}
```

### Important functions

#### `_get_session() → requests.Session`
Returns a singleton `requests.Session` with browser-like headers. **Critical**: the
session performs a warm-up GET to `https://www.dict.cc/` before the first search.
Without this, dict.cc returns only a short "limited result set" (~3 entries) instead
of the full 50+ rows.

#### `fetch_search(word) → dict`
Single entry point for a dict.cc lookup. Returns:
```python
{
  "results":    [...],   # list of {german_word, english, gender, entry_id}
  "word_info":  [...],   # list of {type, content} — NOUN/VERB/SYNO/ALSO/… rows
  "images":     [...],   # list of {id, description, url}
  "audio_id":   int|None,
  "audio_word": str,     # the original search term
}
```

#### `_parse_results(html, word) → list[dict]`
- Finds `<tr id="trN">` rows from dict.cc HTML
- Reads `c1Arr` JS array for clean English (avoids qualifier noise like
  `{adj} [attr.] [e.g. chair, lamp]`)
- Reads `idArr` JS array for entry IDs
- Extracts gender from `{m}`, `{f}`, `{n}` markers in the German column
- Skips entries ending with `-` (compound prefix forms like "Schreibtisch-")
- Sorts: exact match first, then nouns, then others

#### `_extract_word_info(html) → list[dict]`
Parses dict.cc's word-info rows (`NOUN`, `VERB`, `ADJ`, `SYNO`, `ALSO`, …) — `<tr>`
elements with no `id` and an unclassed first `<td>`. Numbered labels collapse
(`NOUN1` → `NOUN`); hidden content (`display:none`) and `javascript:` expand
links are stripped. `content` is the German text, e.g. `der Tisch | die Tische`.

#### `_extract_images(html) → list[dict]`
Parses the `display_shutterstock_images_multi("word","de",[...])` JS call.
Each image has `{id, description, url}`.

#### `_find_audio_id(html, word) → int|None`
Finds the dict.cc entry ID (from `idArr`) for the exact German word match in
`c2Arr`. Retained in the data but **not used for audio** — all audio is
generated via gTTS (see `tts_audio`).

#### `get_plural(word) → str|None`
**Currently unused by the GUI** (plurals now come from the dict.cc NOUN word-info
row instead). Calls `en.wiktionary.org/w/api.php` with `action=parse&prop=text` to get
rendered HTML, finds the `inflection-table-de` table, reads the Nominative
row at column index 5 (plural nominative).

#### `format_german(word, gender, plural) → str`
Produces the display string:
- `"der Tisch | die Tische"` (noun with plural)
- `"der Tisch"` (noun, no plural found yet)
- `"die Tische"` (plural-only noun, gender=`"pl"`)
- `"laufen"` (verb/adjective — no article)

#### `tts_audio(text) → bytes|None`
Generates MP3 audio via gTTS. Replaces ` | ` with `, ` so
`"der Tisch | die Tische"` is spoken as `"der Tisch, die Tische"`.
Returns raw MP3 bytes or `None` on failure. dict.cc audio was dropped in
favour of this — gTTS speaks the full formatted string including the plural.

#### `store_anki_media(filename, data) → str`
Encodes bytes to base64 and calls AnkiConnect `storeMediaFile`.

#### `anki(action, **params)`
Generic AnkiConnect caller. Raises `RuntimeError` if AnkiConnect returns an error.

---

## Frontend — `app.py`

### Dark theme

Full VS Code-inspired dark theme applied via `app.setStyleSheet(APP_STYLE)`:

| Role | Colour |
|---|---|
| Window / root background | `#1e1e1e` |
| Panel / group box background | `#252526` |
| Input / button background | `#3c3c3c` |
| Section header background | `#1a1a1a` |
| Primary text | `#d4d4d4` |
| Accent / section header text | `#9cdcfe` |
| Field value text (monospace) | `#ce9178` |
| Accent button (Add to Anki) | `#0078d4` |
| Selected list item | `#094771` |

### Threading model

All network/blocking work is off the main thread via `QThread` subclasses:

| Worker | Input | Output (signal) | Blocking call |
|---|---|---|---|
| `SearchWorker` | search word | `finished(dict)` | `fetch_search()` — dict.cc scrape |
| `ThumbnailLoader` | list of image dicts | `ready(int, QPixmap, bytes)` per image | HTTP GET for each thumbnail |
| `AudioWorker` | formatted German string | `ready(bytes)` | `tts_audio()` — gTTS network call |

### Search flow (live search)

```
User types ≥2 chars
  → 600ms QTimer debounce
  → SearchWorker starts (fetches dict.cc only — no Wiktionary)
  → results appear immediately in two sections
```

### Results list

The single `QListWidget` holds two sections, each introduced by
`_make_section_header(title)` (non-checkable, accent colour `#9cdcfe`, darker
background):

1. **Word Info** — one checkable row per `word_info` entry, built by
   `_make_word_info_item(label, content)`: monospace, yellow (`#dcdcaa`),
   shown as `NOUN    der Tisch | die Tische`. Unchecked by default.
2. **Translations** — one checkable row per result: `der Tisch  →  table`.
   The first translation is pre-checked.

Every checkable row stores `{"german", "english", "result_idx"}` in `UserRole`:
- Word Info rows: `german = content`, `english = ""`, `result_idx = None`
- Translation rows: `result_idx` = index into `results`

`result_idx is None` is how the code tells Word Info rows from translations.
Header items have `UserRole = None` and are skipped in all checkbox loops.

### Config persistence

`~/.dict_anki.json` stores last-used deck, model, and per-model field mappings:
```json
{
  "deck": "German",
  "model": "Basic",
  "mappings": {
    "Basic": {"Front": "German word", "Back": "English translation"}
  }
}
```

### `MainWindow`

- Live search: `textChanged → QTimer(600ms) → SearchWorker`
- Results displayed in two sections (Word Info → Translations)
- First translation is pre-checked
- **Preview** button (bottom-right) opens `PreviewDialog` for checked items

#### How checked rows become cards (`_open_preview`)

| Checked | Result |
|---|---|
| ≥1 Word Info row (+ any translations) | **One card.** German = first checked Word Info row's text; English = checked translations' English joined with ` / ` (empty if none) |
| Translations only | **One card per translation** (German + English of each row) |

Example: check `NOUN der Tisch | die Tische` + `der Tisch → table` →
one card `der Tisch | die Tische` / `table`, and TTS speaks the Word Info text.

### `PreviewDialog`

Opened with `pairs` (the cards built above), image list, and `audio_text`
(the German of the first pair).

Key behaviour:

- **Header**: `_AutoFitLabel` showing the German text. It shrinks the bold font
  from 15px down to 9px to fit the window width (re-fits on resize). Below 9px it
  word-wraps. Horizontal size policy is `Ignored`, so a long word never forces the
  dialog wider. Hovering shows the full text.
- **Empty English values are skipped** wherever English is joined (field value,
  card summary). Without this, a Word Info row's empty English produced a stray
  ` / table`.
- **On open**: `ThumbnailLoader` and `AudioWorker` start in parallel
- **Auto-play**: audio plays automatically as soon as `AudioWorker` emits `ready`
- **⟳ Regenerate** (next to Play): re-runs TTS on the current text of the field
  mapped to "German word" (e.g. `Front`), with `[sound:…]` tags stripped. It sets
  `audio_text` to that text, so the audio filename changes. If the user edited that
  field, `_sync_sound_tags()` rewrites its `[sound:…]` tag to the new file. Add to
  Anki is disabled while regenerating, so stale audio can't be stored under the new name.
- **Window width follows the fields**: field edits are `_FitLineEdit`s, whose size
  hint is the full text width. `_fit_width()` sizes the dialog to fit them on open
  and grows it (never shrinks it) whenever a field's text changes, capped at 90% of
  the screen and kept on-screen. It measures the edits directly, because the
  layouts' cached size hints lag behind `setText()`.
- **Editable fields**: each mapped field is a `QLineEdit` pre-filled with
  the computed value:
  - `_field_edits: dict[str, QLineEdit]` — the widgets
  - `_field_dirty: set[str]` — fields the user has manually typed in
  - `textEdited` signal (fires only on user input, not `setText`) adds to dirty set
  - `_refresh_preview()` skips dirty fields, so auto-updates (audio loading,
    image selection) never overwrite user edits
- **German word field embeds audio**:
  `der Tisch | die Tische [sound:google-XXXX-XXXX-XXXX-XXXX-XXXX.mp3]`
- **Audio filename** (`_audio_filename_for(text)`):
  `google-{sha1[:8]}-{sha1[8:16]}-{sha1[16:24]}-{sha1[24:32]}-{sha1[32:40]}.mp3`
  where SHA-1 is computed on the spoken text (` | ` → `, `).
  This matches the HyperTTS naming convention.
- **On Add to Anki**:
  - Stores image (if mapped) via `storeMediaFile`
  - Stores audio (if any field maps to "German word" or "Audio") via `storeMediaFile`
  - Single card: uses `QLineEdit` text verbatim for every field
  - Multi-card: Image/Audio fields use `QLineEdit` text (shared); German/English
    fall back to per-pair values

### App icon

`_render_icon_pixmap(size)` draws ♾ on a dark rounded rectangle using `QPainter`.
`_install_app_icon(px)` saves it to
`~/.local/share/icons/hicolor/256x256/apps/dict-anki.png` on every startup.
Both `app` and `win` get `setWindowIcon()` so it appears in both the taskbar
and the window title bar.

### Desktop integration

`~/.local/share/applications/dict-anki.desktop` is installed once (manually or
by re-running the setup commands). The app sets:

```python
app.setApplicationName("dict-anki")
app.setApplicationDisplayName("dict.cc → Anki")
```

so the WM_CLASS matches the `.desktop` file's `StartupWMClass=dict-anki`.

---

## Running the App

### Requirements

- Python 3.10+
- Anki open with **AnkiConnect** add-on installed (code: `2055492159`)
- Internet connection (dict.cc, Wiktionary, Google TTS)

### First-time setup

```bash
cd /home/opi/Programming/dict-anki
python -m venv venv
venv/bin/pip install -r requirements.txt
```

### Run

**Windows**: double-click `dist\dict-anki.exe` (build it first with `build.ps1`,
see *Packaging → Windows*). From source: `python app.py`.

**Linux**:
```bash
./dict-anki          # launcher script
venv/bin/python app.py  # direct
```

On Linux the app is registered at `~/.local/share/applications/dict-anki.desktop` and
appears in the app launcher / taskbar search.

To reinstall the desktop entry and icon after moving the project:

```bash
# regenerate icon PNG
QT_QPA_PLATFORM=offscreen venv/bin/python -c "
from PyQt6.QtWidgets import QApplication; from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPainter, QFont, QBrush, QColor
import pathlib, sys
app = QApplication(sys.argv)
size = 256; px = QPixmap(size, size); px.fill(Qt.GlobalColor.transparent)
p = QPainter(px); p.setRenderHint(QPainter.RenderHint.Antialiasing)
p.setBrush(QBrush(QColor('#1e1e1e'))); p.setPen(Qt.PenStyle.NoPen)
p.drawRoundedRect(0,0,size,size,size//5,size//5)
p.setPen(QColor('#d4d4d4')); f=QFont(); f.setPixelSize(int(size*.72)); p.setFont(f)
p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, '♾'); p.end()
d = pathlib.Path.home()/'.local/share/icons/hicolor/256x256/apps'
d.mkdir(parents=True, exist_ok=True); px.save(str(d/'dict-anki.png'), 'PNG')
"
# reinstall .desktop
cat > ~/.local/share/applications/dict-anki.desktop << 'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=dict.cc → Anki
Comment=German flashcard creator using dict.cc
Exec=/home/opi/Programming/dict-anki/dict-anki
Icon=dict-anki
Terminal=false
Categories=Education;Languages;
StartupNotify=true
StartupWMClass=dict-anki
EOF
update-desktop-database ~/.local/share/applications/
```

---

## Packaging as Executable

### Linux

#### Option A — Current approach (venv + shell script)
The `dict-anki` launcher script already works as an executable:
```bash
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/app.py" "$@"
```
**Pros**: zero extra build step, easy to update source.
**Cons**: requires the venv present alongside the script; not portable to
another machine without Python.

To make it launchable from anywhere, symlink into `~/bin/`:
```bash
mkdir -p ~/bin
ln -sf /home/opi/Programming/dict-anki/dict-anki ~/bin/dict-anki
# ensure ~/bin is on PATH in ~/.bashrc / ~/.zshrc
```

#### Option B — PyInstaller standalone bundle
Produces a directory (or single binary) with Python and all dependencies
bundled — works on any Linux machine with compatible glibc.

```bash
venv/bin/pip install pyinstaller
venv/bin/pyinstaller \
    --name dict-anki \
    --windowed \
    --onedir \
    --collect-all PyQt6 \
    --hidden-import gtts \
    app.py
# Output: dist/dict-anki/dict-anki
```

Single-file variant (slower startup, simpler distribution):
```bash
venv/bin/pyinstaller --name dict-anki --windowed --onefile \
    --collect-all PyQt6 --hidden-import gtts app.py
# Output: dist/dict-anki
```

---

### Windows

Run on a **Windows machine** (cross-compilation is not supported by PyInstaller).
`build.ps1` does everything:

```powershell
pip install -r requirements.txt pyinstaller   # once
powershell -ExecutionPolicy Bypass -File build.ps1
# Output: dist\dict-anki.exe  (single file, ~50 MB) — double-click to run
```

What `build.ps1` does:
1. Renders a multi-size `icon.ico` (16–256 px, PNG entries) with
   `app._save_icon_ico()`, using the same drawing as the in-app window icon.
   It must run on the **native** platform plugin. Under `QT_QPA_PLATFORM=offscreen`
   there are no fonts, so ♾ renders as an empty box (this was the earlier wrong exe icon).
2. Runs `python -m PyInstaller --windowed --onefile --icon icon.ico --name dict-anki app.py`.

Set `$env:PYTHON` to use a specific interpreter (default: `python` on PATH).
Last built with Python 3.14.6, PyQt6 6.11.0, PyInstaller 6.22.3.

**Notes:**
- `--windowed` suppresses the console window
- `--onefile` gives one self-contained `.exe`. Copy or shortcut it anywhere.
  Startup takes a few seconds because it unpacks to `%TEMP%` on each launch.
  For faster startup, swap in `--onedir` and distribute the whole
  `dist\dict-anki\` folder.
- `--collect-all PyQt6` is **not** needed: PyInstaller's PyQt6 hooks already
  bundle `plugins\multimedia\ffmpegmediaplugin.dll` + FFmpeg DLLs for `QMediaPlayer`
- gTTS makes outbound HTTPS requests — Windows Defender may prompt on first run
- Rebuild after every source change. The exe bundles a snapshot of the code.
- `_install_app_icon()` still writes `~/.local/share/icons/...` on startup.
  On Windows this does nothing useful but is harmless.

---

## Known Quirks

| Issue | Root cause | Fix applied |
|---|---|---|
| dict.cc returns only 3 results | Session not warmed up | `_get_session()` does a warm-up GET before the first search |
| German vote count "6512" in word | `<div style="float:right">` inside `<td>` | All `<div>` children removed before `get_text()` |
| Wiktionary returned empty | Missing `User-Agent` header | Added `headers={"User-Agent": "dict-anki/1.0"}` |
| dict.cc audio broken / unreliable | URL pattern changed; content-type checks failed | Replaced entirely with gTTS; always generates audio from the formatted string |
| Search slow | Wiktionary called serially for every noun before showing results | `SearchWorker` fetches dict.cc only; Wiktionary lookup dropped (plural comes from the Word Info NOUN row) |
| Extra ` / ` in preview English | Word Info rows carry `english = ""`, which was joined with the translation | Word Info + translations merge into one card; empty English values are skipped when joining |
| Long German text overflowed preview | Fixed 15px header label | `_AutoFitLabel` shrinks font to fit, wraps below 9px |
| Long Front value cut off in preview | Dialog stuck at minimum width | `_FitLineEdit` + `_fit_width()` grow the dialog to show the full field |
| Exe icon was an empty box | Icon rendered under `offscreen` (no fonts) | `build.ps1` renders on the native platform |

---

## Dependencies

| Package | Version constraint | Purpose |
|---|---|---|
| `requests` | any | HTTP for dict.cc, Wiktionary, AnkiConnect |
| `beautifulsoup4` | any | HTML parsing for dict.cc and Wiktionary |
| `PyQt6` | ≥6.4 | GUI, threading, audio playback |
| `gtts` | ≥2.3 | German TTS via Google Translate |
