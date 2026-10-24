import base64
import ctypes
import io
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

# Embedded placeholder image (1x1 pixel red) for when reference image is unavailable
_BASE64_PLACEHOLDER = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="

if not FROZEN:
    _REQUIRED = {"customtkinter": "customtkinter", "edge_tts": "edge-tts", "requests": "requests",
                 "imageio_ffmpeg": "imageio-ffmpeg", "PIL": "pillow"}
    _missing = []
    for _mod, _pkg in _REQUIRED.items():
        try:
            __import__(_mod)
        except ImportError:
            _missing.append(_pkg)
    if _missing:
        _flags = 0x08000000 if sys.platform == "win32" else 0
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *_missing],
            capture_output=True, timeout=600, creationflags=_flags,
        )

import customtkinter as ctk
import requests
from PIL import Image

from core import (analytics, config, health, languages, moods, pipeline, projects,
                  script_gen, video_gen)
from core.config import APP_NAME
from core.ffmpeg_util import CREATE_NO_WINDOW, ffmpeg_exe

BG = "#F2F4FC"
CARD = "#FFFFFF"
CARD_ALT = "#F4F5FB"
BORDER = "#E1E4F2"
ACCENT = "#7C5CFF"
ACCENT_HOVER = "#6847F0"
ACCENT_SOFT = "#EDE9FF"
TEXT = "#1F2437"
MUTED = "#6E7591"
OK_GREEN = "#12A150"
WARN_AMBER = "#C77700"
ERR_RED = "#D64545"

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

GEN_BASE = "https://gen.pollinations.ai"
FAMILY = "Segoe UI"


def _workarea():
    """Logical-pixel work area (screen minus taskbar), or None."""
    try:
        class _RECT(ctypes.Structure):
            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                        ("r", ctypes.c_long), ("b", ctypes.c_long)]
        rc = _RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0):
            return rc.l, rc.t, rc.r, rc.b
    except Exception:
        return None
    return None


def _font(size=13, weight="normal"):
    return ctk.CTkFont(family=FAMILY, size=size, weight=weight)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.cfg = config.load()
        self.log_queue = queue.Queue()
        self.busy = False
        self.last_video = None
        self.ref_image_path = ""
        self._rec = None
        self._suggestions = ["", ""]
        self._thumb_img = None
        self._ref_thumb = None
        self._badge_img = None
        self._resume_project = ""
        self._active_project_dir = ""
        self._recent_sig = None
        self.configure(fg_color=BG)
        self.title(APP_NAME)
        self._set_geometry()
        self._build_header()
        self._build_body()
        self._load_settings_to_ui()
        self.after(200, self._try_load_badge_later)
        self.after(120, self._poll_log_queue)
        self.after(900, self._poll_recent)
        threading.Thread(target=self.run_system_check, daemon=True).start()

    def _badge_path(self):
        """Locate badge.png next to the app (project folder), output folder, or cwd."""
        candidates = [
            config.app_dir() / "badge.png",
            Path(self.cfg.get("output_dir", ".") or ".") / "badge.png",
            Path.cwd() / "badge.png",
        ]
        for c in candidates:
            try:
                if c.exists():
                    return c
            except OSError:
                continue
        return None

    def _try_load_badge_later(self):
        """Try to load badge.png into the top bar after UI is fully built."""
        try:
            badge_path = self._badge_path()
            if badge_path is not None:
                img = Image.open(badge_path)
                img.thumbnail((260, 90))
                self._badge_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                self.badge_label.configure(image=self._badge_img, text="", padx=0)
            else:
                self.badge_label.configure(image=None, text="Made With Pollination")
        except Exception:
            self.badge_label.configure(image=None, text="Made With Pollination")

    def _set_geometry(self, h=900):
        wa = _workarea()
        screen_logical_w = self.winfo_screenwidth()
        # SPI may report physical (post-DPI-awareness) or logical px; normalize to
        # the logical space that geometry() strings use.
        s = (wa[2] / screen_logical_w) if (wa and screen_logical_w) else 1.0
        work_r = int(wa[2] / s) if wa else screen_logical_w
        work_b = int(wa[3] / s) if wa else self.winfo_screenheight()
        w = min(1140, int(work_r * 0.94))
        h = min(h, max(560, work_b - 40))
        x = max(0, (work_r - w) // 2)
        self.geometry(f"{w}x{h}+{x}+0")

    def _fit_to_screen(self, attempt=0):
        self.update_idletasks()
        wa = _workarea()
        if not wa:
            return
        ww, wh = self.winfo_width(), self.winfo_height()
        if ww <= 1 or wh <= 1:
            return
        try:
            lw, lh = map(int, self.geometry().split("+")[0].split("x"))
        except Exception:
            lw, lh = ww, wh
        # winfo metrics and post-init SPI share the same pixel space (physical on
        # DPI-aware Tk); compare raw, no extra scaling.
        limit_b = wa[3] - 8
        limit_r = wa[2] - 8
        bottom = self.winfo_rooty() + wh
        right = self.winfo_rootx() + ww
        if (bottom > limit_b or right > limit_r) and attempt < 6:
            ratio = min(limit_b / max(bottom, 1), limit_r / max(right, 1), 0.98)
            new_h = max(560, int(lh * ratio))
            new_w = max(880, int(lw * ratio))
            if (new_w, new_h) != (lw, lh):
                self.geometry(f"{new_w}x{new_h}+0+0")
                self.after(250, lambda: self._fit_to_screen(attempt + 1))

    def on_analyze(self):
        if getattr(self, "_analyzing", False):
            return
        tagline = self.tagline_entry.get().strip()
        product = self.product_entry.get().strip()
        description = self.desc_box.get("1.0", "end").strip()
        if not tagline:
            self.log("Enter a tagline first, then press Analyze.")
            return
        self._analyzing = True
        self.analyze_btn.configure(state="disabled", text="Checking online...")
        self.insight_score.configure(text="score: ...", text_color=MUTED)
        self.insight_cat.configure(text="")
        self.insight_trend.configure(text="")

        def worker():
            try:
                rec = analytics.analyze(product, description, tagline)
                self._rec = rec
                tag_suggs = rec.get("tagline_suggestions", [])
                self._suggestions = (tag_suggs + ["", ""])[:2]
                score = rec["score"]["score"]
                trend = rec["trend"]
                issues = ", ".join(rec["score"]["issues"][:2])
                color = OK_GREEN if score >= 70 else (WARN_AMBER if score >= 45 else ERR_RED)
                trend_txt = {
                    "rising": f"trend: rising {trend.get('pct', 0):+d}%",
                    "falling": f"trend: falling {trend.get('pct', 0):+d}%",
                    "stable": "trend: stable",
                }.get(trend.get("status"), "trend: no data")
                understanding = rec.get("understanding", "")

                def apply():
                    self.insight_score.configure(text=f"score: {score}/100", text_color=color)
                    self.insight_cat.configure(
                        text=f"category: {rec['category']}" + (f"  ({issues})" if issues else ""),
                        text_color=MUTED)
                    self.insight_trend.configure(
                        text=trend_txt,
                        text_color=OK_GREEN if trend.get("status") == "rising" else MUTED)
                    for i, btn in enumerate(self.sugg_buttons):
                        s = self._suggestions[i]
                        if s:
                            shown = s if len(s) <= 26 else s[:25] + "…"
                            btn.configure(text=f'"{shown}"', state="normal")
                        else:
                            btn.configure(text="no suggestion", state="disabled")
                    self.apply_rec_btn.configure(state="normal")
                    self.log(f"Tagline analysis: {score}/100, category {rec['category']}, {trend_txt}")
                    if understanding:
                        self.log(f"  product understanding: {understanding}")
                    if rec["suggestions"]:
                        self.log(f"  related searches: {', '.join(rec['suggestions'][:5])}")
                self.after(0, apply)
            except Exception as exc:
                self.log(f"Analysis failed: {exc}")
                self.after(0, lambda: self.insight_score.configure(
                    text="score: offline", text_color=WARN_AMBER))
            finally:
                self._analyzing = False
                self.after(0, lambda: self.analyze_btn.configure(
                    state="normal", text="Analyze tagline"))

        threading.Thread(target=worker, daemon=True).start()

    def on_use_suggestion(self, idx):
        s = self._suggestions[idx]
        if not s:
            return
        self.tagline_entry.delete(0, "end")
        self.tagline_entry.insert(0, s)
        self.log(f'Tagline replaced with suggestion: "{s}"')

    def on_apply_recs(self):
        rec = self._rec
        if not rec:
            return
        if rec.get("mood") and rec["mood"] in moods.MOOD_NAMES:
            self.mood_menu.set(rec["mood"])
        if rec.get("style") and rec["style"] in script_gen.STYLES:
            self.style_menu.set(rec["style"])
        self.log(f"Recommendations applied: mood={rec.get('mood')}, style={rec.get('style') or 'unchanged'}")

    def _btn(self, parent, text, primary=False, **kw):
        if primary:
            opts = dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
                        text_color_disabled="#D9CEFF", border_width=0)
        else:
            opts = dict(fg_color=CARD, hover_color=ACCENT_SOFT, border_width=1,
                        border_color=BORDER, text_color=TEXT, text_color_disabled="#9AA3BC")
        opts.update(kw)
        return ctk.CTkButton(parent, text=text, **opts)

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, height=54, border_width=0)
        header.pack(fill="x")
        header.pack_propagate(False)
        accent_bar = ctk.CTkFrame(header, fg_color=ACCENT, width=5, corner_radius=2)
        accent_bar.pack(side="left", fill="y", padx=(18, 14), pady=10)
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", fill="y", pady=6)
        ctk.CTkLabel(titles, text="AI Ad Video Maker", text_color=TEXT,
                     font=_font(18, "bold")).pack(anchor="w")
        ctk.CTkLabel(titles, text="7-10s ads  ·  21 languages  ·  AI script + visuals + voiceover",
                     text_color=MUTED, font=_font(11)).pack(anchor="w")
        self.error_label = ctk.CTkLabel(header, text="", text_color=ERR_RED, font=_font(12, "bold"),
                                        anchor="w")
        self.error_label.pack(side="left", fill="y", padx=18, pady=4)
        pill = ctk.CTkLabel(header, text=" checking...", fg_color=ACCENT_SOFT, corner_radius=14,
                            text_color=MUTED, font=_font(11, "bold"), padx=14, pady=6, width=190)
        pill.pack(side="right", padx=20)
        self.key_pill = pill

    def _build_body(self):
        self.tabview = ctk.CTkTabview(self, fg_color=BG, segmented_button_fg_color=CARD,
                                      segmented_button_selected_color=ACCENT,
                                      segmented_button_selected_hover_color=ACCENT_HOVER,
                                      segmented_button_unselected_color=CARD,
                                      segmented_button_unselected_hover_color=ACCENT_SOFT,
                                      text_color=TEXT, corner_radius=12)
        self.tabview.pack(fill="both", expand=True, padx=16, pady=(6, 6))
        self.tabview.add("Create Ad")
        self.tabview.add("Recent")
        self.tabview.add("Settings")
        self._build_create_tab(self.tabview.tab("Create Ad"))
        self._build_recent_tab(self.tabview.tab("Recent"))
        self._build_settings_tab(self.tabview.tab("Settings"))

    def _card(self, parent, title, accent=False):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14, border_width=1,
                            border_color=BORDER)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=18, pady=(6, 7))
        inner.columnconfigure(1, weight=1)
        inner.columnconfigure(3, weight=1)
        if title:
            ctk.CTkLabel(inner, text=title.upper(),
                         text_color=ACCENT if accent else MUTED, font=_font(11, "bold"),
                         anchor="w").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        return card, inner

    def _label(self, parent, text, **kw):
        opts = dict(text_color=MUTED, font=_font(12), anchor="w")
        opts.update(kw)
        return ctk.CTkLabel(parent, text=text, **opts)

    def _build_create_tab(self, tab):
        columns = ctk.CTkFrame(tab, fg_color="transparent")
        columns.pack(fill="both", expand=True)
        columns.columnconfigure(0, weight=3)
        columns.columnconfigure(1, weight=0)
        columns.rowconfigure(0, weight=1)

        form = ctk.CTkFrame(columns, fg_color="transparent")
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        card_product, inner = self._card(form, "Product")
        card_product.pack(fill="x", pady=(2, 6))
        row = 1
        self._label(inner, "Name *").grid(row=row, column=0, sticky="w", pady=(2, 0))
        self.product_entry = ctk.CTkEntry(inner, placeholder_text="e.g. GlowUp Serum", height=34,
                                          fg_color=CARD_ALT, border_color=BORDER, text_color=TEXT,
                                          font=_font(13), corner_radius=8)
        self.product_entry.grid(row=row, column=1, sticky="ew", padx=(10, 16), pady=(2, 0))
        self._label(inner, "Tagline").grid(row=row, column=2, sticky="w", pady=(2, 0))
        self.tagline_entry = ctk.CTkEntry(inner, placeholder_text="optional - shown at the end",
                                          height=34, fg_color=CARD_ALT, border_color=BORDER,
                                          text_color=TEXT, font=_font(13), corner_radius=8)
        self.tagline_entry.grid(row=row, column=3, sticky="ew", padx=(10, 0), pady=(2, 0))
        row += 1
        self._label(inner, "Description *").grid(row=row, column=0, sticky="nw", pady=(5, 0))
        self.desc_box = ctk.CTkTextbox(inner, height=46, fg_color=CARD_ALT, border_color=BORDER,
                                       border_width=1, text_color=TEXT, font=_font(13),
                                       corner_radius=8)
        self.desc_box.grid(row=row, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=(5, 0))
        row += 1
        self._label(inner, "Reference").grid(row=row, column=0, sticky="nw", pady=(5, 0))
        ref_row = ctk.CTkFrame(inner, fg_color="transparent")
        ref_row.grid(row=row, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=(5, 0))
        ref_row.columnconfigure(0, weight=1)
        self.ref_label = ctk.CTkLabel(ref_row, text="  no image selected (optional)", anchor="w",
                                      fg_color=CARD_ALT, corner_radius=8, height=34,
                                      text_color=MUTED, font=_font(12))
        self.ref_label.grid(row=0, column=0, sticky="ew")
        self._btn(ref_row, "Browse", width=80, height=34,
                  command=self.choose_reference, font=_font(12)).grid(row=0, column=1, padx=(8, 0))
        self._btn(ref_row, "Clear", width=60, height=34, command=self.clear_reference,
                  text_color=MUTED, font=_font(12)).grid(row=0, column=2, padx=(6, 0))
        self._btn(ref_row, "Rotate", width=70, height=34, command=self.rotate_reference,
                  font=_font(12)).grid(row=0, column=3, padx=(6, 0))
        self._btn(ref_row, "Crop", width=60, height=34, command=self.crop_reference,
                  font=_font(12)).grid(row=0, column=4, padx=(6, 0))

        card_style, inner = self._card(form, "Style & Output")
        card_style.pack(fill="x", pady=6)
        row = 1
        self._label(inner, "Language").grid(row=row, column=0, sticky="w", pady=(2, 0))
        self.lang_menu = ctk.CTkOptionMenu(inner, values=languages.LANGUAGE_NAMES, width=220,
                                           fg_color=CARD_ALT, button_color=ACCENT,
                                           button_hover_color=ACCENT_HOVER,
                                           dropdown_fg_color=CARD,
                                           dropdown_hover_color=ACCENT_SOFT,
                                           text_color=TEXT, font=_font(12), corner_radius=8)
        self.lang_menu.grid(row=row, column=1, sticky="w", padx=(10, 16), pady=(2, 0))
        self._label(inner, "Voice").grid(row=row, column=2, sticky="w", pady=(2, 0))
        self.voice_menu = ctk.CTkOptionMenu(inner, values=["Female", "Male"], width=130,
                                            fg_color=CARD_ALT, button_color=ACCENT,
                                            button_hover_color=ACCENT_HOVER,
                                            dropdown_fg_color=CARD,
                                            dropdown_hover_color=ACCENT_SOFT,
                                            text_color=TEXT, font=_font(12), corner_radius=8)
        self.voice_menu.grid(row=row, column=3, sticky="w", padx=(10, 0), pady=(2, 0))
        row += 1
        self._label(inner, "Style").grid(row=row, column=0, sticky="w", pady=(5, 0))
        self.style_menu = ctk.CTkOptionMenu(inner, values=list(script_gen.STYLES.keys()), width=220,
                                            fg_color=CARD_ALT, button_color=ACCENT,
                                            button_hover_color=ACCENT_HOVER,
                                            dropdown_fg_color=CARD,
                                            dropdown_hover_color=ACCENT_SOFT,
                                            text_color=TEXT, font=_font(12), corner_radius=8)
        self.style_menu.grid(row=row, column=1, sticky="w", padx=(10, 16), pady=(5, 0))
        self._label(inner, "Duration").grid(row=row, column=2, sticky="w", pady=(5, 0))
        dur_row = ctk.CTkFrame(inner, fg_color="transparent")
        dur_row.grid(row=row, column=3, sticky="w", padx=(10, 0), pady=(5, 0))
        self.dur_slider = ctk.CTkSlider(dur_row, from_=7, to=10, number_of_steps=3, width=140,
                                        progress_color=ACCENT, button_color=ACCENT,
                                        button_hover_color=ACCENT_HOVER, fg_color=BORDER)
        self.dur_slider.set(self.cfg.get("duration", 8))
        self.dur_slider.pack(side="left")
        self.dur_label = ctk.CTkLabel(dur_row, text=f"{int(self.cfg.get('duration', 8))}s",
                                      text_color=ACCENT, font=_font(13, "bold"), width=40)
        self.dur_label.pack(side="left", padx=(8, 0))
        self.dur_slider.configure(command=lambda v: self.dur_label.configure(text=f"{int(v)}s"))
        row += 1
        self._label(inner, "Mood").grid(row=row, column=0, sticky="w", pady=(5, 0))
        self.mood_menu = ctk.CTkOptionMenu(inner, values=moods.MOOD_NAMES, width=220,
                                           fg_color=CARD_ALT, button_color=ACCENT,
                                           button_hover_color=ACCENT_HOVER,
                                           dropdown_fg_color=CARD,
                                           dropdown_hover_color=ACCENT_SOFT,
                                           text_color=TEXT, font=_font(12), corner_radius=8)
        self.mood_menu.grid(row=row, column=1, sticky="w", padx=(10, 16), pady=(5, 0))
        self._label(inner, "Aspect").grid(row=row, column=2, sticky="w", pady=(5, 0))
        self.aspect_menu = ctk.CTkOptionMenu(
            inner, values=["9:16 (TikTok / Reels / Shorts)", "16:9 (YouTube / Web)"], width=250,
            fg_color=CARD_ALT, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=CARD, dropdown_hover_color=ACCENT_SOFT, text_color=TEXT,
            font=_font(12), corner_radius=8)
        self.aspect_menu.grid(row=row, column=3, sticky="w", padx=(10, 0), pady=(5, 0))
        row += 1
        self._label(inner, "Source").grid(row=row, column=0, sticky="w", pady=(5, 0))
        self.source_menu = ctk.CTkOptionMenu(
            inner, values=["Auto (AI video if key set)", "Fallback (animated AI image)"], width=220,
            fg_color=CARD_ALT, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=CARD, dropdown_hover_color=ACCENT_SOFT, text_color=TEXT,
            font=_font(12), corner_radius=8)
        self.source_menu.grid(row=row, column=1, sticky="w", padx=(10, 16), pady=(5, 0))
        checks = ctk.CTkFrame(inner, fg_color="transparent")
        checks.grid(row=row, column=2, columnspan=2, sticky="w", padx=(10, 0), pady=(5, 0))
        self.captions_var = ctk.BooleanVar(value=True)
        self.music_var = ctk.BooleanVar(value=True)
        for text, var in [("Captions", self.captions_var), ("Music", self.music_var)]:
            ctk.CTkCheckBox(checks, text=text, variable=var, fg_color=ACCENT, border_color=BORDER,
                            checkmark_color="#FFFFFF", hover_color=ACCENT_SOFT, text_color=TEXT,
                            font=_font(12), corner_radius=6).pack(side="left", padx=(0, 18))

        card_ins, inner = self._card(form, "Tagline Insights", accent=True)
        card_ins.pack(fill="x", pady=(6, 6))
        self.analyze_btn = self._btn(inner, "Analyze tagline", primary=True, width=140, height=32,
                                     font=_font(12), corner_radius=8, command=self.on_analyze)
        self.analyze_btn.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.insight_score = ctk.CTkLabel(inner, text="score: -", text_color=MUTED,
                                          font=_font(12, "bold"), anchor="w")
        self.insight_score.grid(row=1, column=1, sticky="w", padx=(14, 0), pady=(2, 0))
        self.insight_cat = ctk.CTkLabel(inner, text="", text_color=MUTED, font=_font(12),
                                        anchor="w")
        self.insight_cat.grid(row=1, column=2, sticky="w", padx=(14, 0), pady=(2, 0))
        self.insight_trend = ctk.CTkLabel(inner, text="", text_color=MUTED, font=_font(12),
                                          anchor="w")
        self.insight_trend.grid(row=1, column=3, sticky="e", padx=(14, 0), pady=(2, 0))
        self.apply_rec_btn = self._btn(inner, "Apply recommendations", width=170, height=30,
                                       text_color=OK_GREEN, font=_font(11),
                                       corner_radius=8, state="disabled",
                                       command=self.on_apply_recs)
        self.apply_rec_btn.grid(row=2, column=0, sticky="w", pady=(5, 0))
        sugg_row = ctk.CTkFrame(inner, fg_color="transparent")
        sugg_row.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(14, 0), pady=(5, 0))
        self.sugg_buttons = []
        for i in range(2):
            btn = self._btn(sugg_row, "run analyze for ideas", width=165, height=30,
                            font=_font(10), corner_radius=8, state="disabled",
                            command=lambda idx=i: self.on_use_suggestion(idx))
            btn.pack(side="left", padx=(0, 8))
            self.sugg_buttons.append(btn)

        action = ctk.CTkFrame(form, fg_color="transparent")
        action.pack(fill="x", pady=(6, 1))
        self.generate_btn = self._btn(action, "  Generate Ad", primary=True, height=40, width=190,
                                      font=_font(14, "bold"), corner_radius=12,
                                      command=self.on_generate)
        self.generate_btn.pack(side="left")
        self.open_btn = self._btn(action, "Open folder", height=40, font=_font(12),
                                  corner_radius=10, command=self.open_output)
        self.open_btn.pack(side="left", padx=(10, 0))
        self.preview_btn = self._btn(action, "Preview", height=40, font=_font(12),
                                     corner_radius=10, command=self.preview_last)
        self.preview_btn.pack(side="left", padx=(8, 0))
        self.status_label = ctk.CTkLabel(action, text="", text_color=MUTED, font=_font(12),
                                         anchor="e")
        self.status_label.pack(side="right", fill="x", expand=True, padx=(16, 0))

        self.progress = ctk.CTkProgressBar(form, fg_color=CARD_ALT, progress_color=ACCENT,
                                           corner_radius=6, height=8)
        self.progress.pack(fill="x", pady=(4, 2))
        self.progress.set(0)

        self.badge_label = ctk.CTkLabel(
            form, text="Made With Pollination", text_color=MUTED, font=_font(12, "bold"))
        self.badge_label.pack(anchor="w", pady=(8, 0))

        side = ctk.CTkFrame(columns, fg_color=CARD, corner_radius=14, width=280,
                            border_width=1, border_color=BORDER)
        side.grid(row=0, column=1, sticky="nsew")
        side.pack_propagate(False)
        ctk.CTkLabel(side, text="PREVIEW", text_color=ACCENT, font=_font(11, "bold")).pack(
            anchor="w", padx=16, pady=(14, 8))
        self.thumb_label = ctk.CTkLabel(side, text="video preview\nappears here", width=236, height=185,
                                        fg_color=CARD_ALT, corner_radius=12, text_color=MUTED,
                                        font=_font(12))
        self.thumb_label.pack(padx=16, pady=(0, 12))
        self.thumb_caption = ctk.CTkLabel(side, text="", text_color=MUTED, font=_font(11),
                                          wraplength=236, justify="left")
        self.thumb_caption.pack(fill="x", padx=16, pady=(0, 6))
        key_row = ctk.CTkFrame(side, fg_color=ACCENT_SOFT, corner_radius=10)
        key_row.pack(fill="x", padx=16, pady=(4, 10))
        ctk.CTkLabel(key_row, text="AI video key", text_color=MUTED, font=_font(11, "bold")).pack(
            anchor="w", padx=10, pady=(5, 0))
        self.side_key_status = ctk.CTkLabel(key_row, text="checking...", text_color=MUTED,
                                            font=_font(11), anchor="w", wraplength=210)
        self.side_key_status.pack(anchor="w", padx=10, pady=(2, 8))
        self._btn(key_row, "Setup key", height=28, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                  text_color="#FFFFFF", border_width=0, font=_font(11), corner_radius=8,
                  command=lambda: self.tabview.set("Settings")).pack(
            anchor="w", padx=10, pady=(0, 10))
        ctk.CTkLabel(side, text="ACTIVITY", text_color=ACCENT, font=_font(11, "bold")).pack(
            anchor="w", padx=16, pady=(0, 4))
        self.log_box = ctk.CTkTextbox(side, height=80, fg_color="#1B2030",
                                      border_color=BORDER,
                                      text_color="#C9D2E8",
                                      font=ctk.CTkFont(family="Consolas", size=11),
                                      corner_radius=10)
        self.log_box.pack(fill="both", expand=True, padx=16, pady=(0, 10))

    def _build_settings_tab(self, tab):
        scroll = ctk.CTkFrame(tab, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        card_key, inner = self._card(scroll, "Pollinations API key", accent=True)
        card_key.pack(fill="x", pady=(2, 8))
        ctk.CTkLabel(inner, text="Free at enter.pollinations.ai - unlocks true AI video models",
                     text_color=MUTED, font=_font(12), anchor="w").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(2, 6))
        key_row = ctk.CTkFrame(inner, fg_color="transparent")
        key_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(2, 0))
        key_row.columnconfigure(0, weight=1)
        self.key_entry = ctk.CTkEntry(key_row, show="*", placeholder_text="sk_...", height=36,
                                      fg_color=CARD_ALT, border_color=BORDER, text_color=TEXT,
                                      font=_font(13), corner_radius=8)
        self.key_entry.grid(row=0, column=0, sticky="ew")
        self._btn(key_row, "Show", width=64, height=36, font=_font(12),
                  command=self.toggle_key_visibility).grid(row=0, column=1, padx=(8, 0))
        side_btns = ctk.CTkFrame(inner, fg_color="transparent")
        side_btns.grid(row=2, column=3, sticky="e", padx=(16, 0), pady=(2, 0))
        self._btn(side_btns, "Save key", primary=True, font=_font(12), corner_radius=8,
                  command=self.save_key).pack(side="left", padx=(0, 8))
        self._btn(side_btns, "Test key", font=_font(12), corner_radius=8,
                  command=self.test_key).pack(side="left", padx=(0, 8))
        self._btn(side_btns, "Get free key", text_color=OK_GREEN, font=_font(12),
                  corner_radius=8,
                  command=lambda: webbrowser.open("https://enter.pollinations.ai/keys")).pack(side="left")
        self.key_status = ctk.CTkLabel(inner, text="", anchor="w", font=_font(12))
        self.key_status.grid(row=3, column=0, columnspan=4, sticky="w", pady=(5, 0))

        card_def, inner = self._card(scroll, "Defaults")
        card_def.pack(fill="x", pady=8)
        self._label(inner, "Video model").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.model_menu = ctk.CTkOptionMenu(inner, values=video_gen.MODEL_LABELS, width=300,
                                            fg_color=CARD_ALT, button_color=ACCENT,
                                            button_hover_color=ACCENT_HOVER,
                                            dropdown_fg_color=CARD,
                                            dropdown_hover_color=ACCENT_SOFT,
                                            text_color=TEXT, font=_font(12), corner_radius=8)
        self.model_menu.grid(row=1, column=1, sticky="w", padx=(12, 16), pady=(2, 0))
        self._label(inner, "Language").grid(row=1, column=2, sticky="w", pady=(2, 0))
        self.deflang_menu = ctk.CTkOptionMenu(inner, values=languages.LANGUAGE_NAMES, width=230,
                                              fg_color=CARD_ALT, button_color=ACCENT,
                                              button_hover_color=ACCENT_HOVER,
                                              dropdown_fg_color=CARD,
                                              dropdown_hover_color=ACCENT_SOFT,
                                              text_color=TEXT, font=_font(12), corner_radius=8)
        self.deflang_menu.grid(row=1, column=3, sticky="w", padx=(12, 0), pady=(2, 0))
        self._label(inner, "Output folder").grid(row=2, column=0, sticky="w", pady=(5, 0))
        out_row = ctk.CTkFrame(inner, fg_color="transparent")
        out_row.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=(5, 0))
        out_row.columnconfigure(0, weight=1)
        self.out_entry = ctk.CTkEntry(out_row, height=36, fg_color=CARD_ALT, border_color=BORDER,
                                      text_color=TEXT, font=_font(12), corner_radius=8)
        self.out_entry.grid(row=0, column=0, sticky="ew")
        self._btn(out_row, "Browse", width=90, height=36, font=_font(12),
                  command=self.browse_output).grid(row=0, column=1, padx=(8, 0))
        self._btn(inner, "Save settings", primary=True, font=_font(12), corner_radius=8,
                  command=self.save_settings).grid(
            row=2, column=3, sticky="e", padx=(16, 0), pady=(5, 0))

        card_sys, inner = self._card(scroll, "System check")
        card_sys.pack(fill="x", pady=8)
        sys_row = ctk.CTkFrame(inner, fg_color="transparent")
        sys_row.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(2, 0))
        self.check_btn = self._btn(sys_row, "Run system check", font=_font(12), corner_radius=8,
                                   command=self.run_system_check)
        self.check_btn.pack(side="left")
        self.sys_status = ctk.CTkLabel(sys_row, text="", text_color=MUTED, font=_font(12),
                                       anchor="w")
        self.sys_status.pack(side="left", padx=14)
        self.sys_detail = ctk.CTkTextbox(inner, height=110, fg_color="#1B2030",
                                         border_color=BORDER,
                                         text_color="#C9D2E8",
                                         font=ctk.CTkFont(family="Consolas", size=11),
                                         corner_radius=10)
        self.sys_detail.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))

    def choose_reference(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Choose a product photo",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp")],
        )
        if not path:
            return
        self._set_ref_image(path)

    def _set_ref_image(self, path, note=""):
        self.ref_image_path = path
        name = Path(path).name if path else "embedded"
        self.ref_label.configure(text=f"  {name}", text_color=TEXT)
        try:
            img = Image.open(path)
            img.thumbnail((220, 220))
            self._ref_thumb = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.thumb_label.configure(image=self._ref_thumb, text="")
            caption = f"Reference image: {name}"
            if note:
                caption += f" ({note})"
            self.thumb_caption.configure(text=caption + "\nwill be the hero of the video")
        except Exception:
            # Try loading badge.png from project/app directory if original path fails
            if "badge" in (path or "").lower():
                try:
                    badge_path = self._badge_path()
                    if badge_path is not None:
                        img = Image.open(badge_path)
                        img.thumbnail((220, 220))
                        self._ref_thumb = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                        self.thumb_label.configure(image=self._ref_thumb, text="")
                        self.thumb_caption.configure(text=f"Reference image: {badge_path.name}\nwill be the hero of the video")
                        return
                except Exception:
                    pass
            # Show text badge name if image cannot be loaded
            self.thumb_label.configure(image=None, text=f"badge: {name}" if name else "no image selected (optional)")
            self.thumb_caption.configure(text="")

    def rotate_reference(self):
        if not self.ref_image_path or not Path(self.ref_image_path).exists():
            self.log("Select a reference image first")
            return
        try:
            img = Image.open(self.ref_image_path)
            img = img.rotate(-90, expand=True)
            self._apply_ref_edit(img, "rotated")
        except Exception as exc:
            self.log(f"Rotate failed: {exc}")

    def crop_reference(self):
        if not self.ref_image_path or not Path(self.ref_image_path).exists():
            self.log("Select a reference image first")
            return
        CropEditor(self, self.ref_image_path, self._on_crop_applied)

    def _on_crop_applied(self, img):
        self._apply_ref_edit(img, "cropped")

    def _apply_ref_edit(self, img, what):
        try:
            dest = Path(tempfile.gettempdir()) / "adv_ref_edited.jpg"
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(dest, quality=92)
        except Exception as exc:
            self.log(f"Image edit failed: {exc}")
            return
        self._set_ref_image(str(dest), note=what)
        self.log(f"Reference image {what} - using edited copy")

    def clear_reference(self):
        self.ref_image_path = ""
        self.ref_label.configure(text="  no image selected (optional)", text_color=MUTED)
        self._ref_thumb = None
        self.thumb_label.configure(image=None, text="video preview\nappears here")
        self.thumb_caption.configure(text="")

    def _show_video_thumb(self, video_path: Path):
        try:
            tmp = Path(tempfile.gettempdir()) / "adv_preview_frame.jpg"
            subprocess.run(
                [ffmpeg_exe(), "-hide_banner", "-ss", "1.5", "-i", str(video_path),
                 "-frames:v", "1", "-q:v", "3", str(tmp)],
                capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW,
            )
            if not tmp.exists():
                return
            img = Image.open(tmp)
            img.thumbnail((236, 340))
            self._thumb_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.thumb_label.configure(image=self._thumb_img, text="")
            self.thumb_caption.configure(text=f"{video_path.name}\n{video_path.stat().st_size // 1024} KB - click Preview to play")
        except Exception:
            pass

    def toggle_key_visibility(self):
        self.key_entry.configure(show="" if self.key_entry.cget("show") else "*")

    def _set_key_pill(self, text, color):
        self.key_pill.configure(text=text, text_color=color)
        self.side_key_status.configure(text=text, text_color=color)

    def _load_settings_to_ui(self):
        self.key_entry.delete(0, "end")
        self.key_entry.insert(0, self.cfg.get("api_key", ""))
        self.model_menu.set(self.cfg.get("default_model", "auto"))
        self.deflang_menu.set(self.cfg.get("default_language", "English"))
        self.out_entry.delete(0, "end")
        self.out_entry.insert(0, self.cfg.get("output_dir", ""))
        self.lang_menu.set(self.cfg.get("default_language", "English"))
        self.aspect_menu.set(
            "9:16 (TikTok / Reels / Shorts)" if self.cfg.get("aspect", "9:16") == "9:16"
            else "16:9 (YouTube / Web)")
        self.captions_var.set(bool(self.cfg.get("captions", True)))
        self.music_var.set(bool(self.cfg.get("music", True)))
        key = self.cfg.get("api_key", "")
        if key:
            self._set_key_pill("AI video enabled", OK_GREEN)
            self.key_status.configure(text="Key saved - AI video enabled", text_color=OK_GREEN)
        else:
            self._set_key_pill("no key - fallback mode", WARN_AMBER)
            self.key_status.configure(text="No key set - keyless fallback mode", text_color=WARN_AMBER)

    def _collect_settings(self) -> dict:
        self.cfg["api_key"] = self.key_entry.get().strip()
        self.cfg["default_model"] = self.model_menu.get()
        self.cfg["default_language"] = self.deflang_menu.get()
        self.cfg["output_dir"] = self.out_entry.get().strip() or self.cfg["output_dir"]
        config.save(self.cfg)
        return self.cfg

    def save_key(self):
        self._collect_settings()
        self.log("API key saved to settings.json")
        self.key_status.configure(text="Saved. Press Test key to verify.", text_color=WARN_AMBER)

    def save_settings(self):
        self._collect_settings()
        self.log("Settings saved")

    def browse_output(self):
        from tkinter import filedialog

        chosen = filedialog.askdirectory(initialdir=self.out_entry.get() or str(Path.home()))
        if chosen:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, chosen)

    def test_key(self):
        key = self.key_entry.get().strip()
        if not key:
            self.key_status.configure(text="No key entered - keyless fallback mode", text_color=WARN_AMBER)
            return
        self.key_status.configure(text="Testing key...", text_color=MUTED)

        def worker():
            try:
                r = requests.get(f"{GEN_BASE}/account/key",
                                 headers={"Authorization": f"Bearer {key}"}, timeout=30)
                if r.status_code == 401:
                    self.after(0, lambda: (
                        self.key_status.configure(text="Invalid key (401)", text_color=ERR_RED),
                        self._set_key_pill("invalid key", ERR_RED)))
                    return
                r.raise_for_status()
                balance = None
                try:
                    b = requests.get(f"{GEN_BASE}/account/balance",
                                     headers={"Authorization": f"Bearer {key}"}, timeout=30)
                    if b.ok:
                        balance = b.json().get("balance")
                except Exception:
                    pass
                msg = "Valid key - AI video enabled"
                if balance is not None:
                    msg += f" ({balance} pollen)"
                self.after(0, lambda: (
                    self.key_status.configure(text=msg, text_color=OK_GREEN),
                    self._set_key_pill(f"key ok - {balance} pollen" if balance is not None else "key ok", OK_GREEN)))
            except Exception as exc:
                self.after(0, lambda: self.key_status.configure(text=f"Test failed: {exc}", text_color=ERR_RED))

        threading.Thread(target=worker, daemon=True).start()

    def open_output(self):
        path = Path(self.cfg.get("output_dir", "")).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    def preview_last(self):
        if self.last_video and self.last_video.exists():
            os.startfile(str(self.last_video))
        else:
            self.log("No video generated yet")

    def run_system_check(self):
        self.check_btn.configure(state="disabled", text="Checking...")

        def worker():
            try:
                results = health.run_checks()
                report = health.format_report(results)
                ok = health.all_ok(results)

                def apply():
                    self.sys_detail.delete("1.0", "end")
                    self.sys_detail.insert("1.0", report)
                    if ok:
                        self.sys_status.configure(text="All requirements OK", text_color=OK_GREEN)
                    else:
                        failed = [r["name"] for r in results if r["ok"] is False]
                        self.sys_status.configure(text=f"Problems: {', '.join(failed)}", text_color=WARN_AMBER)
                    self.check_btn.configure(state="normal", text="Run system check")
                self.after(0, apply)
            except Exception as exc:
                self.after(0, lambda: (
                    self.sys_status.configure(text=f"Check failed: {exc}", text_color=ERR_RED),
                    self.check_btn.configure(state="normal", text="Run system check")))

        threading.Thread(target=worker, daemon=True).start()

    def log(self, msg: str):
        self.log_queue.put(msg)

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_box.insert("end", msg + "\n")
                self.log_box.see("end")
        except queue.Empty:
            pass
        self.after(120, self._poll_log_queue)

    def on_generate(self):
        if self.busy:
            return
        cfg = self._collect_settings()
        product = self.product_entry.get().strip()
        description = self.desc_box.get("1.0", "end").strip()
        tagline = self.tagline_entry.get().strip()
        language = self.lang_menu.get()
        gender = self.voice_menu.get()
        style = self.style_menu.get()
        mood = self.mood_menu.get()
        duration = int(self.dur_slider.get())
        aspect = "9:16" if self.aspect_menu.get().startswith("9:16") else "16:9"
        model_key = cfg.get("default_model", "auto")
        source_mode = "fallback" if self.source_menu.get().startswith("Fallback") else "auto"
        captions = self.captions_var.get()
        music = self.music_var.get()
        api_key = cfg.get("api_key", "")
        output_dir = cfg.get("output_dir", "")
        reference = self.ref_image_path
        if not product or not description:
            self.log("Please fill product name and description.")
            return
        params = dict(product=product, description=description, tagline=tagline,
                      language=language, gender=gender, style=style, duration=duration,
                      aspect=aspect, model_key=model_key, source_mode=source_mode,
                      captions=captions, music=music, reference_image=reference, mood=mood)
        try:
            if self._resume_project:
                pdir = Path(self._resume_project)
                self._resume_project = ""
                pdata = projects.load(pdir)
                merged = dict(pdata.get("params", {}))
                merged.update(params)
                if (merged.get("reference_image")
                        and Path(merged["reference_image"]).exists()
                        and not str(Path(merged["reference_image"]).resolve())
                        .startswith(str(pdir.resolve()))):
                    src = Path(merged["reference_image"])
                    dest = pdir / f"reference{src.suffix.lower() or '.jpg'}"
                    try:
                        shutil.copy2(src, dest)
                        merged["reference_image"] = str(dest)
                    except OSError:
                        pass
                pdata["params"] = merged
                pdata["status"] = "running"
                pdata["error"] = ""
                projects.save(pdir, pdata)
            else:
                pdata = projects.create(Path(output_dir), params, reference)
                pdir = Path(pdata["dir"])
            self.log(f"Project: {pdir.name}")
        except Exception as exc:
            self.log(f"Project setup failed: {exc}")
            return
        self._active_project_dir = str(pdir)
        self.busy = True
        self.generate_btn.configure(state="disabled", text="Generating...")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.status_label.configure(text="Working...", text_color=MUTED)

        def worker():
            started = time.time()
            try:
                out = pipeline.generate_ad(
                    product=product, description=description, tagline=tagline,
                    language=language, gender=gender, style=style, duration=duration,
                    aspect=aspect, model_key=model_key, source_mode=source_mode,
                    captions=captions, music=music, api_key=api_key,
                    output_dir=output_dir, log=self.log, reference_image=reference,
                    mood=mood, project_dir=str(pdir),
                )
                self.last_video = out
                secs = int(time.time() - started)
                self.after(0, lambda: (
                    self.status_label.configure(text=f"Done in {secs}s", text_color=OK_GREEN),
                    self._show_video_thumb(out),
                    self.refresh_recent()))
            except Exception as exc:
                self.log(f"ERROR: {exc}")
                self.after(0, lambda: (
                    self.status_label.configure(text=f"Failed: {exc}", text_color=ERR_RED),
                    self.refresh_recent()))
            finally:
                self._active_project_dir = ""
                self.after(0, self._done)

        threading.Thread(target=worker, daemon=True).start()

    def _build_recent_tab(self, tab):
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(top, text="RECENT PROJECTS", text_color=ACCENT,
                     font=_font(11, "bold")).pack(side="left")
        self.recent_count = ctk.CTkLabel(top, text="", text_color=MUTED, font=_font(11))
        self.recent_count.pack(side="left", padx=(12, 0))
        self._btn(top, "Refresh", width=90, height=28, font=_font(11),
                  command=lambda: self.refresh_recent(force=True)).pack(side="right")
        self.recent_scroll = ctk.CTkScrollableFrame(tab, fg_color="transparent", corner_radius=10)
        self.recent_scroll.pack(fill="both", expand=True, padx=10, pady=(2, 10))
        self.refresh_recent(force=True)

    def _poll_recent(self):
        try:
            if not self.busy and self.tabview.get() == "Recent":
                self.refresh_recent()
        except Exception:
            pass
        self.after(900, self._poll_recent)

    def refresh_recent(self, force=False):
        try:
            out = Path(self.cfg.get("output_dir", ""))
            plist = projects.list_projects(out)
            for p in plist:
                if (p.get("status") == "running"
                        and p.get("dir") != self._active_project_dir):
                    p["status"] = "interrupted"
                    projects.save(Path(p["dir"]), p)
            sig = tuple((p.get("id"), p.get("status"), p.get("updated", 0)) for p in plist)
            if not force and sig == self._recent_sig:
                return
            self._recent_sig = sig
            for w in self.recent_scroll.winfo_children():
                w.destroy()
            n = len(plist)
            self.recent_count.configure(text=f"{n} project" + ("s" if n != 1 else ""))
            if not plist:
                ctk.CTkLabel(self.recent_scroll,
                             text="No projects yet - generate an ad and it will show up here.",
                             text_color=MUTED, font=_font(13)).pack(pady=40)
                return
            for p in plist:
                self._recent_row(self.recent_scroll, p)
        except Exception as exc:
            self.log(f"Recent list error: {exc}")

    def _recent_row(self, parent, p):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12, border_width=1,
                            border_color=BORDER)
        card.pack(fill="x", pady=(0, 6), padx=2)
        params = p.get("params", {})
        product = params.get("product", "Ad")
        status = p.get("status", "?")
        thumb = self._project_thumb(p)
        if thumb:
            lab = ctk.CTkLabel(card, image=thumb, text="", width=76, height=102)
            lab._adv_img = thumb
        else:
            lab = ctk.CTkLabel(card, text="▶" if status == "completed" else "...",
                               width=76, height=102, fg_color=CARD_ALT, corner_radius=10,
                               text_color=MUTED, font=_font(22))
        lab.pack(side="left", padx=(12, 12), pady=10)
        mid = ctk.CTkFrame(card, fg_color="transparent")
        mid.pack(side="left", fill="both", expand=True, pady=10)
        ctk.CTkLabel(mid, text=product, text_color=TEXT, font=_font(13, "bold"),
                     anchor="w", wraplength=420).pack(anchor="w")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(p.get("updated", 0)))
        meta = f"{when}  ·  {params.get('duration', '?')}s  ·  {params.get('aspect', '?')}"
        ctk.CTkLabel(mid, text=meta, text_color=MUTED, font=_font(11),
                     anchor="w").pack(anchor="w", pady=(2, 0))
        stage = projects.next_stage(p)
        if status == "completed":
            video = p.get("video", "")
            size = ""
            try:
                if video and Path(video).exists():
                    size = f"  ·  {Path(video).stat().st_size // 1024} KB"
            except OSError:
                pass
            status_txt, status_color = f"completed{size}", OK_GREEN
        elif status == "failed":
            err = (p.get("error") or "")[:70]
            status_txt = f"failed{' at ' + stage if stage else ''} - resumable" + (
                f"  ({err})" if err else "")
            status_color = ERR_RED
        elif status == "interrupted":
            status_txt = (f"interrupted{' at ' + stage if stage else ''}"
                          " - press Resume to continue")
            status_color = WARN_AMBER
        else:
            status_txt, status_color = "generating...", ACCENT
        ctk.CTkLabel(mid, text=status_txt, text_color=status_color, font=_font(11, "bold"),
                     anchor="w", wraplength=430).pack(anchor="w", pady=(3, 0))
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(side="right", padx=(8, 12), pady=10)
        if status == "completed":
            self._btn(btns, "Open", width=64, height=28, font=_font(10),
                      command=lambda pr=p: self.on_recent_open(pr)).pack(
                side="left", padx=(0, 6))
        if status in ("interrupted", "failed", "running"):
            self._btn(btns, "Resume", width=76, height=28, font=_font(10),
                      command=lambda pr=p: self.on_recent_resume(pr)).pack(
                side="left", padx=(0, 6))
        self._btn(btns, "Create similar", width=100, height=28, font=_font(10),
                  command=lambda pr=p: self.on_recent_similar(pr)).pack(
            side="left", padx=(0, 6))
        self._btn(btns, "Delete", width=64, height=28, font=_font(10), text_color=ERR_RED,
                  command=lambda pr=p: self.on_recent_delete(pr)).pack(side="left")

    def _project_thumb(self, p):
        try:
            params = p.get("params", {})
            ref = params.get("reference_image", "")
            video = p.get("video", "")
            src = None
            cache_dir = Path(tempfile.gettempdir()) / "adv_thumbs"
            cache_dir.mkdir(exist_ok=True)
            pid = "".join(c if c.isalnum() else "_" for c in str(p.get("id", "x")))
            if p.get("status") == "completed" and video and Path(video).exists():
                cache = cache_dir / f"{pid}.jpg"
                vmt = int(Path(video).stat().st_mtime)
                if not cache.exists() or int(cache.stat().st_mtime) < vmt:
                    raw = cache_dir / f"{pid}_raw.jpg"
                    subprocess.run(
                        [ffmpeg_exe(), "-hide_banner", "-ss", "1.2", "-i", str(video),
                         "-frames:v", "1", "-q:v", "5", str(raw)],
                        capture_output=True, timeout=45, creationflags=CREATE_NO_WINDOW)
                    if raw.exists():
                        raw.replace(cache)
                if cache.exists():
                    src = cache
            if src is None and ref and Path(ref).exists():
                src = Path(ref)
            if src is None:
                return None
            img = Image.open(src)
            img.thumbnail((76, 102))
            return ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        except Exception:
            return None

    def on_recent_open(self, p):
        video = p.get("video", "")
        if video and Path(video).exists():
            os.startfile(str(video))
            return
        d = Path(p.get("dir", ""))
        if d.exists():
            os.startfile(str(d))
        else:
            self.log("Project folder no longer exists")
            self.refresh_recent(force=True)

    def on_recent_resume(self, p):
        if self.busy:
            self.log("Wait for the current generation to finish first")
            return
        if not Path(p.get("dir", "")).exists():
            self.log("Project folder no longer exists")
            self.refresh_recent(force=True)
            return
        self._apply_params_to_form(p.get("params", {}))
        self._resume_project = p["dir"]
        self.tabview.set("Create Ad")
        stage = projects.next_stage(p) or "compose"
        self.status_label.configure(
            text=f"Resumable from '{stage}' - press Generate", text_color=WARN_AMBER)
        self.log(f'Ready to resume "{p.get("params", {}).get("product", "ad")}" '
                 f"from stage {stage}")

    def on_recent_similar(self, p):
        if self.busy:
            self.log("Wait for the current generation to finish first")
            return
        self._apply_params_to_form(p.get("params", {}))
        self._resume_project = ""
        self.tabview.set("Create Ad")
        self.status_label.configure(text="Template loaded - edit or press Generate",
                                    text_color=MUTED)
        self.log(f'Loaded "{p.get("params", {}).get("product", "ad")}" as a template '
                 "- edit anything, then Generate (saves as a new project)")

    def on_recent_delete(self, p):
        if (p.get("status") == "running"
                and p.get("dir") == self._active_project_dir):
            self.log("Cannot delete the project currently generating")
            return
        name = p.get("params", {}).get("product", "ad")
        projects.delete(p.get("dir", ""))
        self.log(f'Deleted project "{name}"')
        self.refresh_recent(force=True)

    def _apply_params_to_form(self, params: dict):
        def set_entry(entry, value):
            entry.delete(0, "end")
            if value:
                entry.insert(0, str(value))
        set_entry(self.product_entry, params.get("product", ""))
        set_entry(self.tagline_entry, params.get("tagline", ""))
        self.desc_box.delete("1.0", "end")
        self.desc_box.insert("1.0", params.get("description", ""))
        for menu, key in ((self.lang_menu, "language"), (self.voice_menu, "gender"),
                          (self.style_menu, "style"), (self.mood_menu, "mood")):
            v = params.get(key)
            if v:
                try:
                    menu.set(str(v))
                except Exception:
                    pass
        aspect = str(params.get("aspect", "9:16"))
        self.aspect_menu.set("9:16 (TikTok / Reels / Shorts)" if aspect.startswith("9:16")
                             else "16:9 (YouTube / Web)")
        source = str(params.get("source_mode", "auto"))
        self.source_menu.set("Auto (AI video if key set)" if source != "fallback"
                             else "Fallback (animated AI image)")
        try:
            d = int(params.get("duration", 8))
        except (TypeError, ValueError):
            d = 8
        d = max(7, min(10, d))
        self.dur_slider.set(d)
        self.dur_label.configure(text=f"{d}s")
        self.captions_var.set(bool(params.get("captions", True)))
        self.music_var.set(bool(params.get("music", True)))
        ref = params.get("reference_image", "")
        if ref and Path(ref).exists():
            self._set_ref_image(ref, note="from project")
        else:
            self.clear_reference()

    def _done(self):
        self.busy = False
        self.generate_btn.configure(state="normal", text="  Generate Ad")
        self.progress.stop()
        self.progress.set(0)


class CropEditor(ctk.CTkToplevel):
    """Drag-to-crop overlay for the reference image, with aspect presets."""

    def __init__(self, master, image_path, on_apply):
        super().__init__(master)
        from tkinter import Canvas
        from PIL import ImageTk

        self.on_apply = on_apply
        self.title("Crop reference image")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.full = Image.open(image_path)
        if self.full.mode != "RGB":
            self.full = self.full.convert("RGB")
        self.disp_scale = min(1.0, 640 / self.full.width, 460 / self.full.height)
        dw = max(1, int(self.full.width * self.disp_scale))
        dh = max(1, int(self.full.height * self.disp_scale))
        self.disp_size = (dw, dh)
        self.disp = self.full.resize((dw, dh), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(self.disp)
        self.ratio_val = None
        self.sel = None
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(bar, text="Crop ratio:", text_color=MUTED, font=_font(12)).pack(
            side="left", padx=(0, 8))
        self._ratio_btns = []
        for label, val in (("free", None), ("1:1", 1.0), ("9:16", 9 / 16), ("16:9", 16 / 9)):
            b = ctk.CTkButton(bar, text=label, width=64, height=26, font=_font(11),
                              corner_radius=8, fg_color=CARD, hover_color=ACCENT_SOFT,
                              border_width=1, border_color=BORDER, text_color=TEXT,
                              command=lambda v=val, btn=None: self._set_ratio(v))
            b.pack(side="left", padx=(0, 6))
            self._ratio_btns.append((b, val))
        self.canvas = Canvas(self, width=dw, height=dh, bg="#1B2030", highlightthickness=0)
        self.canvas.pack(padx=12, pady=4)
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.info = ctk.CTkLabel(self, text="drag on the image to select a crop area",
                                 text_color=MUTED, font=_font(12))
        self.info.pack(pady=(2, 0))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=(6, 12))
        ctk.CTkButton(row, text="Apply crop", width=110, height=30, font=_font(12),
                      corner_radius=8, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color="#FFFFFF", border_width=0,
                      command=self._apply).pack(side="left", padx=(0, 8))
        ctk.CTkButton(row, text="Cancel", width=90, height=30, font=_font(12),
                      corner_radius=8, fg_color=CARD, hover_color=ACCENT_SOFT,
                      border_width=1, border_color=BORDER, text_color=TEXT,
                      command=self.destroy).pack(side="left")
        self.rect_id = None
        self.start_xy = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        try:
            mx, my = master.winfo_rootx(), master.winfo_rooty()
            mw, mh = master.winfo_width(), master.winfo_height()
            w, h = self.winfo_reqwidth(), self.winfo_reqheight()
            self.geometry(f"+{max(0, mx + (mw - w) // 2)}+{max(0, my + (mh - h) // 2)}")
        except Exception:
            pass

    def _set_ratio(self, val):
        self.ratio_val = val
        for b, v in self._ratio_btns:
            active = v == val
            b.configure(fg_color=ACCENT if active else CARD,
                        text_color="#FFFFFF" if active else TEXT,
                        border_color=ACCENT if active else BORDER)
        if self.sel:
            x0, y0, x1, y1 = self.sel
            x0, y0, x1, y1 = self._fit_ratio(x0, y0, x1, y1)
            self.sel = (x0, y0, x1, y1)
            self._draw_rect()
            self._update_info()

    def _clamp(self, x, y):
        dw, dh = self.disp_size
        return (max(0, min(dw, x)), max(0, min(dh, y)))

    def _fit_ratio(self, x0, y0, x1, y1):
        if self.ratio_val is None:
            return x0, y0, x1, y1
        dw, dh = self.disp_size
        sx = 1 if x1 >= x0 else -1
        sy = 1 if y1 >= y0 else -1
        w = max(abs(x1 - x0), 2)
        h = max(abs(y1 - y0), 2)
        if w / h > self.ratio_val:
            h = w / self.ratio_val
        else:
            w = h * self.ratio_val
        max_w = (dw - x0) if sx > 0 else x0
        max_h = (dh - y0) if sy > 0 else y0
        if w > max_w:
            w = max_w
            h = w / self.ratio_val
        if h > max_h:
            h = max_h
            w = h * self.ratio_val
        return x0, y0, x0 + sx * w, y0 + sy * h

    def _press(self, event):
        self.start_xy = self._clamp(event.x, event.y)
        if self.rect_id:
            self.canvas.delete(self.rect_id)
            self.rect_id = None
        self.sel = None

    def _drag(self, event):
        if not self.start_xy:
            return
        x1, y1 = self._clamp(event.x, event.y)
        self.sel = self._fit_ratio(self.start_xy[0], self.start_xy[1], x1, y1)
        self._draw_rect()
        self._update_info()

    def _release(self, event):
        if self.sel:
            x0, y0, x1, y1 = self._fit_ratio(*self.sel)
            self.sel = (x0, y0, x1, y1)
            self._draw_rect()
            self._update_info()

    def _draw_rect(self):
        if self.rect_id:
            self.canvas.delete(self.rect_id)
            self.rect_id = None
        if not self.sel:
            return
        x0, y0, x1, y1 = self.sel
        self.rect_id = self.canvas.create_rectangle(
            x0, y0, x1, y1, outline="#7C5CFF", width=2)

    def _update_info(self):
        if not self.sel:
            return
        s = self.disp_scale
        xa = int(min(self.sel[0], self.sel[2]) / s)
        ya = int(min(self.sel[1], self.sel[3]) / s)
        xb = int(max(self.sel[0], self.sel[2]) / s)
        yb = int(max(self.sel[1], self.sel[3]) / s)
        self.info.configure(
            text=f"selected {max(1, xb - xa)}x{max(1, yb - ya)} px (of "
                 f"{self.full.width}x{self.full.height})")

    def _apply(self):
        if not self.sel:
            self.info.configure(text="drag on the image to select a crop area first")
            return
        s = self.disp_scale
        xa = int(min(self.sel[0], self.sel[2]) / s)
        ya = int(min(self.sel[1], self.sel[3]) / s)
        xb = int(max(self.sel[0], self.sel[2]) / s)
        yb = int(max(self.sel[1], self.sel[3]) / s)
        xa = max(0, min(xa, self.full.width - 2))
        ya = max(0, min(ya, self.full.height - 2))
        xb = max(xa + 2, min(xb, self.full.width))
        yb = max(ya + 2, min(yb, self.full.height))
        cropped = self.full.crop((xa, ya, xb, yb))
        self.on_apply(cropped)
        self.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
