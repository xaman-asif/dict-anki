# dict-anki — Project Context

PyQt6 desktop app: look up a German word on dict.cc, pick translations and a
Shutterstock thumbnail, generate pronunciation audio with gTTS, and add the card
to Anki via AnkiConnect.

## Files

| File | Purpose |
|---|---|
| `app.py` | GUI: main window, preview dialog, worker threads, app icon |
| `dict_anki.py` | Backend: dict.cc scraping, gTTS, AnkiConnect, config |
| `install.py` | Installs a one-file executable and registers it with the desktop (Linux/Windows) |

## Running

Requires Python 3.10+, Anki running with the **AnkiConnect** add-on (`2055492159`),
and internet access.

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python app.py
```

## Building

```bash
python install.py
```

- Uses `$PYTHON`, else `venv/`'s Python, else the current one; installs
  `requirements.txt` + PyInstaller if anything is missing.
- Runs PyInstaller `--onefile --windowed` → `dist/dict-anki` or `dist\dict-anki.exe`.
- **Linux**: installs the icon PNG and `~/.local/share/applications/dict-anki.desktop`
  (`Exec=` → `dist/dict-anki`). **Windows**: `dict.cc → Anki` shortcuts on the
  Desktop and in the Start Menu. WScript.Shell's `Save()` turns `→` into an
  invalid `?`, so each `.lnk` is saved under an ASCII temp name and moved into place.
- Entries point into `dist/`: rebuild after source changes, rerun after moving the project.
- PyInstaller can't cross-compile — build on the target OS.

## Backend — `dict_anki.py`

`fetch_search(word)` does one dict.cc request and returns:

```python
{"results":   [{german_word, english, gender}, ...],
 "word_info": [{type, content}, ...],   # NOUN/VERB/SYNO/ALSO/... rows
 "images":    [{id, description, url}, ...]}
```

- `_get_session()` makes a warm-up GET to dict.cc before the first search;
  without it dict.cc returns a ~3-row "limited result set".
- English comes from the page's `c1Arr` JS array (no `{adj} [qualifier]` noise).
  Gender is parsed from `{m}/{f}/{n}`. Compound-prefix entries (`Schreibtisch-`)
  are skipped. Sort: exact match, then nouns.
- `<div>`s inside result cells are removed before reading text (they hold vote counts).
- Word Info rows: `<tr>` without `id` whose first `<td>` has no class;
  `NOUN1` → `NOUN`; hidden (`display:none`) content and `javascript:` links stripped.
- `tts_audio(text)` speaks `text` with ` | ` replaced by `, `
  (`der Tisch | die Tische` → "der Tisch, die Tische").
- `anki(action, **params)` calls AnkiConnect at `localhost:8765`, raises `RuntimeError` on error.
- Config (`~/.dict_anki.json`): last deck, model, and per-model field mappings.
  Saved when Preview is opened.

Plurals come from the Word Info NOUN row (`der Tisch | die Tische`); all audio
comes from gTTS.

## GUI — `app.py`

Network calls run in `QThread`s: `SearchWorker` (dict.cc), `ThumbnailLoader`
(images), `AudioWorker` (gTTS).

### Main window

- Live search: ≥2 characters, 600 ms debounce.
- Results list has two sections: **Word Info** and **Translations**, all rows
  unchecked. Checkable rows store `{german, english, result_idx}` in
  `UserRole`; Word Info rows have `result_idx = None`, `english = ""`. Section
  headers have no `UserRole` data.
- **Ctrl+F** opens a filter box over the list's top-right corner. Within each
  section, rows matching `german + english` move to the top (best first) and the
  rest are dimmed; Esc or a new search restores the original order. Rows are moved,
  not rebuilt, so check states survive. Matching (`_fuzzy_score`) ignores case,
  umlauts and ß, and every query word must match, as a substring, as in-order
  letters within one word (`tsch` → Tisch), or as a close typo (`tabel` → table).
  Because rows move, "first checked Word Info row" in `_open_preview` means first
  in the current order.
- **Copy**: Ctrl+C copies the checked rows, one per line in the current order,
  or the current row when nothing is checked. Right-click offers **Copy checked**
  and **Copy this row**. Translations copy as `der Tisch → table`, Word Info rows
  as their content only.
- Each Anki note field is mapped to one of: German word, English translation,
  Image, Audio, Skip.

Checked rows → cards (`_open_preview`):

| Checked | Cards |
|---|---|
| Any Word Info row | **One** card: German = first checked Word Info row; English = checked translations joined with ` / ` |
| Translations only | **One per translation** |

### Preview dialog

- Starts thumbnail loading and TTS on open; audio auto-plays when ready.
- Right-click a thumbnail → **Copy** puts the image on the clipboard
  (disabled until that thumbnail has loaded).
- Every mapped field is an editable line edit. Fields the user typed in are
  "dirty" and never auto-overwritten (by audio arriving or image selection).
- "German word" field embeds the audio: `der Tisch [sound:google-….mp3]`.
  Audio filename = SHA-1 of the spoken text, as
  `google-{8}-{8}-{8}-{8}-{8}.mp3` (HyperTTS convention).
- **⟳ Regenerate** re-runs TTS on the German word field's current text (sound tags
  stripped). That changes the filename; `_sync_sound_tags()` updates the tag in
  edited fields. Add is disabled until the new audio arrives.
- Header label shrinks 15→9 px to fit, then wraps. The dialog widens (never
  shrinks) to show full field text, capped at 90% of the screen.
- **Add to Anki**: stores image (`dict_anki_img_{id}.jpg`) and audio via
  `storeMediaFile`, then `addNote` with tag `dict.cc`, no duplicates. Single card
  uses the edited field text verbatim. Multi-card uses edited text only for
  Image/Audio; German/English come from each pair.

### Icon and desktop integration

- `_render_icon_pixmap()` draws ♾ on a dark rounded square. It must render on
  the native Qt platform plugin: under `QT_QPA_PLATFORM=offscreen` there are no
  fonts and ♾ comes out as an empty box.
- On every start the app writes the icon to
  `~/.local/share/icons/hicolor/256x256/apps/dict-anki.png` (harmless on Windows).
- `setApplicationName("dict-anki")` makes WM_CLASS match the desktop entry's
  `StartupWMClass=dict-anki`.
