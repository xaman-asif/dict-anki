#!/usr/bin/env python3
"""PyQt6 GUI — dict.cc → Anki flashcard maker."""
import hashlib
import os
import pathlib
import sys
import tempfile

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QDialog, QWidget,
    QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem,
    QComboBox, QGroupBox, QSizePolicy,
    QScrollArea, QButtonGroup, QToolButton, QFrame,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl, QSize
from PyQt6.QtGui import QPixmap, QIcon, QPainter, QFont, QFontMetrics, QBrush, QColor
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from dict_anki import (
    fetch_search, format_german, GENDER_ARTICLE,
    anki, load_config, save_config, tts_audio, store_anki_media,
)

# "German word" now embeds [sound:...] in the field value, same field.
# "Audio" still available for a standalone sound-only field.
FIELD_OPTIONS = ["German word", "English translation", "Image", "Audio", "Skip"]

APP_STYLE = """
    QMainWindow, QDialog, QWidget {
        background-color: #1e1e1e;
        color: #d4d4d4;
    }
    QGroupBox {
        border: 1px solid #3c3c3c;
        border-radius: 6px;
        margin-top: 10px;
        background-color: #252526;
        font-weight: bold;
        color: #cccccc;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: #9cdcfe;
    }
    QListWidget {
        border: 1px solid #3c3c3c;
        border-radius: 4px;
        background-color: #252526;
        color: #d4d4d4;
        alternate-background-color: #2a2a2a;
    }
    QListWidget::item { padding: 5px 8px; }
    QListWidget::item:selected { background-color: #094771; color: #ffffff; }
    QLineEdit {
        border: 1px solid #3c3c3c;
        border-radius: 4px;
        padding: 4px 8px;
        background-color: #3c3c3c;
        color: #d4d4d4;
        font-size: 14px;
    }
    QLineEdit:focus { border-color: #0078d4; }
    QPushButton {
        border: 1px solid #3c3c3c;
        border-radius: 4px;
        padding: 4px 10px;
        background-color: #3c3c3c;
        color: #d4d4d4;
    }
    QPushButton:hover  { background-color: #505050; }
    QPushButton:disabled { color: #555555; }
    QPushButton[class="primary"] {
        background-color: #0078d4;
        color: white;
        font-weight: bold;
        border-color: #005a9e;
    }
    QPushButton[class="primary"]:hover    { background-color: #106ebe; }
    QPushButton[class="primary"]:disabled {
        background-color: #2a2a2a;
        color: #555555;
        border-color: #333333;
    }
    QComboBox {
        border: 1px solid #3c3c3c;
        border-radius: 4px;
        padding: 3px 6px;
        background-color: #3c3c3c;
        color: #d4d4d4;
    }
    QComboBox::drop-down { border: none; width: 18px; }
    QComboBox QAbstractItemView {
        background-color: #252526;
        color: #d4d4d4;
        selection-background-color: #094771;
        border: 1px solid #3c3c3c;
    }
    QScrollArea { border: none; background: transparent; }
    QScrollBar:vertical {
        background: #1e1e1e; width: 8px; border: none;
    }
    QScrollBar::handle:vertical {
        background: #424242; border-radius: 4px; min-height: 20px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar:horizontal {
        background: #1e1e1e; height: 8px; border: none;
    }
    QScrollBar::handle:horizontal {
        background: #424242; border-radius: 4px; min-width: 20px;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QLabel { color: #d4d4d4; }
"""

_IMG_BTN_STYLE = """
    QToolButton {
        border: 2px solid #3c3c3c;
        border-radius: 4px;
        background-color: #2d2d2d;
        font-size: 20px;
        color: #666;
    }
    QToolButton:checked {
        border: 3px solid #0078d4;
        background-color: #0d2d42;
    }
    QToolButton:hover { border-color: #606060; }
"""


def _primary(btn: QPushButton) -> QPushButton:
    btn.setProperty("class", "primary")
    btn.style().unpolish(btn)
    btn.style().polish(btn)
    return btn


class _AutoFitLabel(QLabel):
    """Bold single-line label that shrinks its font to fit the available width.
    Wraps only if the text still doesn't fit at the minimum size."""

    def __init__(self, text: str, max_px: int = 15, min_px: int = 9, parent=None):
        super().__init__(text, parent)
        self._max_px = max_px
        self._min_px = min_px
        f = self.font()
        f.setBold(True)
        f.setPixelSize(max_px)
        self.setFont(f)
        self.setToolTip(text)
        # Don't let the text's natural width force the window wider.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        avail = self.contentsRect().width()
        if avail <= 0:
            return
        f  = QFont(self.font())
        px = self._max_px
        while px > self._min_px:
            f.setPixelSize(px)
            if QFontMetrics(f).horizontalAdvance(self.text()) <= avail:
                break
            px -= 1
        f.setPixelSize(px)
        if f != self.font():
            self.setFont(f)
        wrap = QFontMetrics(f).horizontalAdvance(self.text()) > avail
        if wrap != self.wordWrap():
            self.setWordWrap(wrap)


def _audio_filename_for(text: str) -> str:
    """Deterministic filename: google-{sha1_in_5_groups}.mp3 (same format as HyperTTS)."""
    spoken = text.replace(" | ", ", ")
    h = hashlib.sha1(spoken.encode()).hexdigest()
    return f"google-{h[:8]}-{h[8:16]}-{h[16:24]}-{h[24:32]}-{h[32:40]}.mp3"


# ── Worker threads ────────────────────────────────────────────────────────────

class SearchWorker(QThread):
    """Fetches dict.cc only — results appear immediately."""
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, word: str):
        super().__init__()
        self.word = word

    def run(self):
        try:
            self.finished.emit(fetch_search(self.word))
        except Exception as exc:
            self.error.emit(str(exc))


class ThumbnailLoader(QThread):
    ready = pyqtSignal(int, QPixmap, bytes)

    def __init__(self, images: list[dict]):
        super().__init__()
        self.images = images

    def run(self):
        import requests as _r
        for idx, img in enumerate(self.images):
            try:
                resp = _r.get(img["url"], timeout=5, headers={"User-Agent": "dict-anki/1.0"})
                data = resp.content
                px   = QPixmap()
                px.loadFromData(data)
                if not px.isNull():
                    self.ready.emit(idx, px, data)
            except Exception:
                pass


class AudioWorker(QThread):
    """Generates German TTS for the full formatted string via gTTS."""
    ready = pyqtSignal(bytes)
    error = pyqtSignal(str)

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def run(self):
        data = tts_audio(self.text)
        if data:
            self.ready.emit(data)
        else:
            self.error.emit("gTTS failed — check internet connection")


# ── Preview Dialog ────────────────────────────────────────────────────────────

class PreviewDialog(QDialog):
    def __init__(self, pairs: list[dict], images: list[dict],
                 audio_text: str,
                 deck: str, model: str, field_mapping: dict[str, str],
                 parent=None):
        super().__init__(parent)
        self.pairs         = pairs
        self.images        = images
        self.audio_text    = audio_text   # full formatted string, e.g. "der Tisch | die Tische"
        self.deck          = deck
        self.model         = model
        self.field_mapping = field_mapping

        self._audio_data: bytes | None  = None
        self._tmp_audio:  str   | None  = None
        self._img_data:   list[bytes | None] = [None] * len(images)
        self._img_btns:   list[QToolButton]  = []
        self._field_edits: dict[str, QLineEdit] = {}  # editable field widgets
        self._field_dirty: set[str]             = set()  # fields user has manually edited
        self._thumb_worker: ThumbnailLoader | None = None
        self._audio_worker: AudioWorker     | None = None

        self._player = QMediaPlayer()
        self._aout   = QAudioOutput()
        self._player.setAudioOutput(self._aout)

        self._build_ui()
        self._start_workers()

    # ── construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("Preview & Add")
        self.setMinimumSize(520, 500)

        vbox = QVBoxLayout(self)
        vbox.setSpacing(12)
        vbox.setContentsMargins(18, 18, 18, 18)

        hdr = _AutoFitLabel(self.pairs[0]["german"] if self.pairs else "", max_px=15)
        hdr.setStyleSheet("color: #9cdcfe;")
        vbox.addWidget(hdr)

        # ── Image picker ─────────────────────────────────────────────────
        if self.images:
            img_box = QGroupBox("Choose Image")
            img_vbox = QVBoxLayout(img_box)
            img_vbox.setContentsMargins(10, 10, 10, 10)
            img_vbox.setSpacing(6)

            scroll = QScrollArea()
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFixedHeight(124)
            scroll.setWidgetResizable(False)

            container = QWidget()
            n = len(self.images)
            container.setFixedSize(n * 138 + 8, 108)
            hlay = QHBoxLayout(container)
            hlay.setSpacing(8)
            hlay.setContentsMargins(4, 4, 4, 4)

            self._img_group = QButtonGroup(self)
            self._img_group.setExclusive(True)

            for idx, img in enumerate(self.images):
                btn = QToolButton()
                btn.setCheckable(True)
                btn.setFixedSize(128, 100)
                btn.setToolTip(img.get("description", ""))
                btn.setText("…")
                btn.setStyleSheet(_IMG_BTN_STYLE)
                self._img_group.addButton(btn, idx)
                hlay.addWidget(btn)
                self._img_btns.append(btn)

            scroll.setWidget(container)
            img_vbox.addWidget(scroll)

            self._img_caption = QLabel()
            self._img_caption.setStyleSheet("color: #888; font-size: 11px;")
            img_vbox.addWidget(self._img_caption)

            self._img_group.idClicked.connect(self._on_img_clicked)
            if self._img_btns:
                self._img_btns[0].setChecked(True)
                self._img_caption.setText(self.images[0].get("description", ""))

            vbox.addWidget(img_box)

        # ── Audio ─────────────────────────────────────────────────────────
        audio_row = QHBoxLayout()
        audio_row.setContentsMargins(0, 0, 0, 0)
        self._play_btn = QPushButton("🔊  Play")
        self._play_btn.setMinimumHeight(30)
        self._play_btn.setFixedWidth(90)
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._on_play)
        self._audio_lbl = QLabel("Generating audio…")
        self._audio_lbl.setStyleSheet("color: #888; font-size: 11px;")
        audio_row.addWidget(self._play_btn)
        audio_row.addSpacing(8)
        audio_row.addWidget(self._audio_lbl)
        audio_row.addStretch()
        vbox.addLayout(audio_row)

        # ── Card / field preview ──────────────────────────────────────────
        prev_box  = QGroupBox("Card Preview")
        prev_vbox = QVBoxLayout(prev_box)
        prev_vbox.setContentsMargins(12, 10, 12, 10)
        prev_vbox.setSpacing(8)

        if len(self.pairs) == 1:
            english = self.pairs[0]["english"]
            cards_lbl = QLabel(
                f"<b style='color:#9cdcfe'>{self.pairs[0]['german']}</b>"
                + (f"  →  {english}" if english else "")
            )
        else:
            lines = "  |  ".join(f"<b>{p['english']}</b>" for p in self.pairs if p["english"])
            cards_lbl = QLabel(f"{len(self.pairs)} cards: {lines}")
        cards_lbl.setWordWrap(True)
        prev_vbox.addWidget(cards_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #3c3c3c; margin: 2px 0;")
        prev_vbox.addWidget(sep)

        field_form = QFormLayout()
        field_form.setSpacing(6)
        field_form.setContentsMargins(0, 0, 0, 0)
        for field, mapping in self.field_mapping.items():
            if mapping == "Skip":
                continue
            edit = QLineEdit(self._field_value(mapping))
            edit.setStyleSheet(
                "font-family: monospace; font-size: 11px; color: #ce9178;"
                "background: #2d2d2d; border: 1px solid #3c3c3c;"
                "border-radius: 3px; padding: 2px 5px;"
            )
            # Mark field dirty when the user types (textEdited fires only on user input,
            # not on programmatic setText calls)
            edit.textEdited.connect(lambda _, f=field: self._field_dirty.add(f))
            field_form.addRow(
                f"<span style='color:#858585'>{field}:</span>", edit
            )
            self._field_edits[field] = edit
        prev_vbox.addLayout(field_form)
        vbox.addWidget(prev_box)

        vbox.addStretch(1)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #3c3c3c;")
        vbox.addWidget(sep2)

        bot = QHBoxLayout()
        bot.setContentsMargins(0, 4, 0, 0)
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet("color: #888; font-size: 11px;")
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumHeight(34)
        cancel_btn.clicked.connect(self.reject)
        self._add_btn = QPushButton("Add to Anki")
        self._add_btn.setMinimumHeight(34)
        self._add_btn.setFixedWidth(120)
        _primary(self._add_btn)
        self._add_btn.clicked.connect(self._on_add)
        bot.addWidget(self._status_lbl, 1)
        bot.addWidget(cancel_btn)
        bot.addSpacing(6)
        bot.addWidget(self._add_btn)
        vbox.addLayout(bot)

    # ── Workers ───────────────────────────────────────────────────────────

    def _start_workers(self):
        if self.images:
            self._thumb_worker = ThumbnailLoader(self.images)
            self._thumb_worker.ready.connect(self._on_thumbnail)
            self._thumb_worker.start()

        self._audio_worker = AudioWorker(self.audio_text)
        self._audio_worker.ready.connect(self._on_audio_ready)
        self._audio_worker.error.connect(self._on_audio_error)
        self._audio_worker.start()

    def _on_thumbnail(self, idx: int, px: QPixmap, data: bytes):
        if idx < len(self._img_btns):
            self._img_data[idx] = data
            btn = self._img_btns[idx]
            btn.setText("")
            scaled = px.scaled(124, 96, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            btn.setIcon(QIcon(scaled))
            btn.setIconSize(QSize(124, 96))
        self._refresh_preview()

    def _on_img_clicked(self, idx: int):
        if idx < len(self.images):
            self._img_caption.setText(self.images[idx].get("description", ""))
        self._refresh_preview()

    def _on_audio_ready(self, data: bytes):
        self._audio_data = data
        self._play_btn.setEnabled(True)
        filename = _audio_filename_for(self.audio_text)
        self._audio_lbl.setText(f"✓  {filename}")
        self._refresh_preview()
        self._on_play()  # auto-play when audio is ready

    def _on_audio_error(self, msg: str):
        self._audio_lbl.setText(f"⚠  {msg}")
        self._audio_lbl.setStyleSheet("color: #f48771; font-size: 11px;")

    # ── Audio playback ────────────────────────────────────────────────────

    def _on_play(self):
        if not self._audio_data:
            return
        if self._tmp_audio is None:
            fd, path = tempfile.mkstemp(suffix=".mp3")
            os.write(fd, self._audio_data)
            os.close(fd)
            self._tmp_audio = path
        self._player.setSource(QUrl.fromLocalFile(self._tmp_audio))
        self._player.play()

    # ── Field preview ─────────────────────────────────────────────────────

    def _selected_img_idx(self) -> int | None:
        if not self.images or not hasattr(self, "_img_group"):
            return None
        cid = self._img_group.checkedId()
        return cid if cid >= 0 else None

    def _img_ref(self) -> str:
        idx = self._selected_img_idx()
        if idx is None:
            return ""
        return f'<img src="dict_anki_img_{self.images[idx]["id"]}.jpg">'

    def _field_value(self, mapping: str) -> str:
        if mapping == "German word":
            german = self.pairs[0]["german"] if self.pairs else ""
            if self._audio_data:
                return f"{german} [sound:{_audio_filename_for(self.audio_text)}]"
            return german
        if mapping == "English translation":
            if len(self.pairs) == 1:
                return self.pairs[0]["english"]
            return " / ".join(p["english"] for p in self.pairs if p["english"])
        if mapping == "Image":
            ref = self._img_ref()
            return ref if ref else "(no image selected)"
        if mapping == "Audio":
            if self._audio_data:
                return f"[sound:{_audio_filename_for(self.audio_text)}]"
            return "(generating…)"
        return ""

    def _refresh_preview(self):
        for field, mapping in self.field_mapping.items():
            if field in self._field_edits and field not in self._field_dirty:
                self._field_edits[field].setText(self._field_value(mapping))

    # ── Add ───────────────────────────────────────────────────────────────

    def _on_add(self):
        self._add_btn.setEnabled(False)
        self._status_lbl.setText("Adding…")
        QApplication.processEvents()

        needs_img   = any(v == "Image" for v in self.field_mapping.values())
        # Audio needed if any field is "Audio" OR "German word" (audio embedded there)
        needs_audio = any(v in ("Audio", "German word") for v in self.field_mapping.values())

        # Store image
        img_filename = None
        if needs_img:
            idx = self._selected_img_idx()
            if idx is not None:
                raw = self._img_data[idx]
                if raw is None:
                    try:
                        import requests as _r
                        raw = _r.get(self.images[idx]["url"], timeout=5,
                                     headers={"User-Agent": "dict-anki/1.0"}).content
                    except Exception:
                        raw = None
                if raw:
                    img_filename = f"dict_anki_img_{self.images[idx]['id']}.jpg"
                    try:
                        store_anki_media(img_filename, raw)
                    except Exception as e:
                        self._status_lbl.setText(f"Image store failed: {e}")
                        self._add_btn.setEnabled(True)
                        return

        # Store audio
        audio_filename = None
        if needs_audio and self._audio_data:
            audio_filename = _audio_filename_for(self.audio_text)
            try:
                store_anki_media(audio_filename, self._audio_data)
            except Exception as e:
                self._status_lbl.setText(f"Audio store failed: {e}")
                self._add_btn.setEnabled(True)
                return

        # Add notes
        single = (len(self.pairs) == 1)
        added, errors = 0, []
        for i, pair in enumerate(self.pairs):
            fields: dict[str, str] = {}
            for f, mapping in self.field_mapping.items():
                if mapping == "Skip":
                    continue
                # Single card: always use the (possibly edited) QLineEdit value.
                # Multi-card: Image/Audio are shared → use QLineEdit; German/English
                # are per-pair → fall back to computed per-pair values.
                if f in self._field_edits and (single or mapping in ("Image", "Audio")):
                    val = self._field_edits[f].text()
                    if val:
                        fields[f] = val
                elif mapping == "German word":
                    german = pair["german"]
                    fields[f] = f"{german} [sound:{audio_filename}]" if audio_filename else german
                elif mapping == "English translation":
                    fields[f] = pair["english"]
                elif mapping == "Image" and img_filename:
                    fields[f] = f'<img src="{img_filename}">'
                elif mapping == "Audio" and audio_filename:
                    fields[f] = f"[sound:{audio_filename}]"
            try:
                anki("addNote", note={
                    "deckName":  self.deck,
                    "modelName": self.model,
                    "fields":    fields,
                    "options":   {"allowDuplicate": False},
                    "tags":      ["dict.cc"],
                })
                added += 1
            except RuntimeError as exc:
                errors.append(str(exc))

        if errors and added == 0:
            self._status_lbl.setText(errors[0])
            self._add_btn.setEnabled(True)
        else:
            self.accept()

    # ── Cleanup ───────────────────────────────────────────────────────────

    def closeEvent(self, event):
        for w in (self._thumb_worker, self._audio_worker):
            if w and w.isRunning():
                w.quit()
                w.wait(800)
        if self._tmp_audio and os.path.exists(self._tmp_audio):
            try:
                os.unlink(self._tmp_audio)
            except Exception:
                pass
        super().closeEvent(event)


# ── Results helpers ──────────────────────────────────────────────────────────

def _make_section_header(title: str) -> QListWidgetItem:
    item = QListWidgetItem(f"  {title}")
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    item.setForeground(QBrush(QColor("#9cdcfe")))
    item.setBackground(QBrush(QColor("#1a1a1a")))
    f = item.font()
    f.setBold(True)
    item.setFont(f)
    return item


def _make_word_info_item(label: str, content: str) -> QListWidgetItem:
    """Checkable word-info row (NOUN/SYNO/ALSO/…). German = content, English = empty."""
    item = QListWidgetItem(f"  {label:<6}  {content}")
    item.setData(Qt.ItemDataRole.UserRole, {"german": content, "english": "", "result_idx": None})
    item.setFlags(
        Qt.ItemFlag.ItemIsEnabled |
        Qt.ItemFlag.ItemIsSelectable |
        Qt.ItemFlag.ItemIsUserCheckable
    )
    item.setCheckState(Qt.CheckState.Unchecked)
    item.setForeground(QBrush(QColor("#dcdcaa")))
    item.setToolTip(content)
    f = item.font()
    f.setFamily("monospace")
    item.setFont(f)
    return item


# ── Main Window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config        = load_config()
        self._search_data: dict = {}
        self._worker: SearchWorker | None = None
        self.field_combos: dict[str, QComboBox] = {}

        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(600)
        self._debounce.timeout.connect(self._do_search)

        self._build_ui()
        self._populate_anki()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("dict.cc → Anki")
        self.setMinimumSize(580, 660)

        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setSpacing(12)
        vbox.setContentsMargins(20, 20, 20, 20)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Type a German word to search…")
        self.search_edit.setMinimumHeight(38)
        self.search_edit.textChanged.connect(self._on_text_changed)
        vbox.addWidget(self.search_edit)

        res_lbl = QLabel("Translations")
        res_lbl.setStyleSheet("font-weight: bold; color: #9cdcfe; margin-top: 2px;")
        vbox.addWidget(res_lbl)

        self.results_list = QListWidget()
        self.results_list.setMinimumHeight(200)
        self.results_list.setAlternatingRowColors(True)
        self.results_list.setSpacing(1)
        self.results_list.itemChanged.connect(self._refresh_btn)
        vbox.addWidget(self.results_list)

        anki_box = QGroupBox("Anki")
        af = QFormLayout(anki_box)
        af.setSpacing(8)
        af.setContentsMargins(12, 10, 12, 10)

        self.deck_combo = QComboBox()
        self.deck_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.deck_combo.currentTextChanged.connect(self._refresh_btn)
        af.addRow("Deck:", self.deck_combo)

        self.model_combo = QComboBox()
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        af.addRow("Note type:", self.model_combo)
        vbox.addWidget(anki_box)

        self.map_box  = QGroupBox("Field Mapping")
        self.map_form = QFormLayout(self.map_box)
        self.map_form.setSpacing(8)
        self.map_form.setContentsMargins(12, 10, 12, 10)
        vbox.addWidget(self.map_box)

        vbox.addStretch(1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #3c3c3c;")
        vbox.addWidget(sep)

        bot = QHBoxLayout()
        bot.setContentsMargins(0, 4, 0, 0)
        self.status_lbl = QLabel()
        self.status_lbl.setStyleSheet("color: #858585; font-size: 12px;")
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.setMinimumHeight(36)
        self.preview_btn.setFixedWidth(100)
        self.preview_btn.setEnabled(False)
        _primary(self.preview_btn)
        self.preview_btn.clicked.connect(self._open_preview)
        bot.addWidget(self.status_lbl, 1)
        bot.addWidget(self.preview_btn)
        vbox.addLayout(bot)

    # ── Anki ──────────────────────────────────────────────────────────────

    def _populate_anki(self):
        try:
            decks  = sorted(anki("deckNames"))
            models = sorted(anki("modelNames"))
        except Exception:
            self._set_status("⚠  Anki not connected — open Anki and restart")
            return

        saved_deck  = self.config.get("deck",  "")
        saved_model = self.config.get("model", "")

        self.deck_combo.blockSignals(True)
        self.deck_combo.addItems(decks)
        if saved_deck in decks:
            self.deck_combo.setCurrentText(saved_deck)
        self.deck_combo.blockSignals(False)

        self.model_combo.blockSignals(True)
        self.model_combo.addItems(models)
        if saved_model in models:
            self.model_combo.setCurrentText(saved_model)
        self.model_combo.blockSignals(False)

        self._on_model_changed(self.model_combo.currentText())

    def _on_model_changed(self, model: str):
        while self.map_form.rowCount():
            self.map_form.removeRow(0)
        self.field_combos.clear()
        if not model:
            return
        try:
            fields = anki("modelFieldNames", modelName=model)
        except Exception:
            return
        saved = self.config.get("mappings", {}).get(model, {})
        for field in fields:
            combo = QComboBox()
            combo.addItems(FIELD_OPTIONS)
            combo.setCurrentText(saved.get(field, "Skip"))
            self.map_form.addRow(field + ":", combo)
            self.field_combos[field] = combo

    # ── Live search ───────────────────────────────────────────────────────

    def _on_text_changed(self, text: str):
        if len(text.strip()) >= 2:
            self._debounce.start()
        else:
            self._debounce.stop()
            self._stop_workers()
            self.results_list.clear()
            self._search_data = {}
            self._set_status("")
            self._refresh_btn()

    def _stop_workers(self):
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(300)

    def _do_search(self):
        word = self.search_edit.text().strip()
        if not word:
            return
        self._stop_workers()
        self.results_list.clear()
        self._search_data = {}
        self._refresh_btn()
        self._set_status(f"Searching '{word}'…")

        self._worker = SearchWorker(word)
        self._worker.finished.connect(self._on_results)
        self._worker.error.connect(self._on_err)
        self._worker.start()

    def _on_results(self, data: dict):
        self._search_data = data
        word_info = data.get("word_info", [])
        results   = data.get("results", [])

        if not word_info and not results:
            self._set_status("No results found.")
            return

        self.results_list.blockSignals(True)

        if word_info:
            self.results_list.addItem(_make_section_header("Word Info"))
            for wi in word_info:
                self.results_list.addItem(_make_word_info_item(wi["type"], wi["content"]))

        first_checked = False
        if results:
            self.results_list.addItem(_make_section_header("Translations"))
            for idx, r in enumerate(results):
                german  = format_german(r["german_word"], r["gender"], None)
                english = r["english"]
                item = QListWidgetItem(f"  {german}  →  {english}")
                item.setData(Qt.ItemDataRole.UserRole, {
                    "german": german, "english": english, "result_idx": idx,
                })
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if not first_checked
                    else Qt.CheckState.Unchecked
                )
                first_checked = True
                self.results_list.addItem(item)

        self.results_list.blockSignals(False)
        self._refresh_btn()

        n         = len(results)
        img_count = len(data.get("images", []))
        extras    = [f"{img_count} image{'s' if img_count > 1 else ''}"] if img_count else []
        suffix    = f"  ·  {', '.join(extras)}" if extras else ""
        self._set_status(f"{n} result{'s' if n != 1 else ''}{suffix}")

    def _on_err(self, msg: str):
        self._set_status(f"Error: {msg}")

    # ── Preview ───────────────────────────────────────────────────────────

    def _open_preview(self):
        word_infos, translations = [], []
        for i in range(self.results_list.count()):
            item = self.results_list.item(i)
            stored = item.data(Qt.ItemDataRole.UserRole)
            if stored and item.checkState() == Qt.CheckState.Checked:
                if stored["result_idx"] is None:
                    word_infos.append(stored)
                else:
                    translations.append(stored)
        if not word_infos and not translations:
            return

        if word_infos:
            # Word info supplies the German side; checked translations supply the English.
            english  = " / ".join(t["english"] for t in translations if t["english"])
            selected = [{"german": word_infos[0]["german"], "english": english}]
        else:
            selected = [{"german": t["german"], "english": t["english"]} for t in translations]

        deck    = self.deck_combo.currentText()
        model   = self.model_combo.currentText()
        mapping = {f: c.currentText() for f, c in self.field_combos.items()}

        self.config["deck"]  = deck
        self.config["model"] = model
        self.config.setdefault("mappings", {})[model] = mapping
        save_config(self.config)

        dlg = PreviewDialog(
            pairs        = selected,
            images       = self._search_data.get("images", []),
            audio_text   = selected[0]["german"],
            deck         = deck,
            model        = model,
            field_mapping= mapping,
            parent       = self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            n = len(selected)
            self._set_status(f"✓  Added {n} card{'s' if n > 1 else ''} to '{deck}'")
            self.results_list.blockSignals(True)
            for i in range(self.results_list.count()):
                item = self.results_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole):  # skip section headers
                    item.setCheckState(Qt.CheckState.Unchecked)
            self.results_list.blockSignals(False)
            self._refresh_btn()

    def _refresh_btn(self):
        has_checked = False
        for i in range(self.results_list.count()):
            item = self.results_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) and item.checkState() == Qt.CheckState.Checked:
                has_checked = True
                break
        self.preview_btn.setEnabled(has_checked and bool(self.deck_combo.currentText()))

    def _set_status(self, msg: str):
        self.status_lbl.setText(msg)

    def closeEvent(self, event):
        self._stop_workers()
        super().closeEvent(event)


# ── App icon ─────────────────────────────────────────────────────────────────

def _render_icon_pixmap(size: int = 256) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # Dark rounded background
    p.setBrush(QBrush(QColor("#1e1e1e")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(0, 0, size, size, size // 5, size // 5)
    # ♾ symbol
    p.setPen(QColor("#d4d4d4"))
    f = QFont()
    f.setPixelSize(int(size * 0.72))
    p.setFont(f)
    p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "♾")
    p.end()
    return px


def _install_app_icon(px: QPixmap):
    """Save icon PNG into XDG hicolor tree so .desktop entries can reference it."""
    icon_dir = pathlib.Path.home() / ".local/share/icons/hicolor/256x256/apps"
    icon_dir.mkdir(parents=True, exist_ok=True)
    px.save(str(icon_dir / "dict-anki.png"), "PNG")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("dict-anki")
    app.setApplicationDisplayName("dict.cc → Anki")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)

    icon_px = _render_icon_pixmap(256)
    app_icon = QIcon(icon_px)
    app.setWindowIcon(app_icon)
    _install_app_icon(icon_px)

    win = MainWindow()
    win.setWindowIcon(app_icon)
    win.show()
    win.search_edit.setFocus()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
