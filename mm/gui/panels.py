import json
import subprocess
import threading
import tkinter as tk
import tkinter.filedialog
import webbrowser
from tkinter import messagebox as tkmsgbox

import customtkinter as ctk

try:
    from PIL import Image as PILImage, ImageTk as PILImageTk
    _PIL = True
except ImportError:
    _PIL = False

import mm.gui.config as _gc
from mm.gui.config import CONFIG_FILE
from .constants import _BG, _INACTIVE, _HOVER
from .info import _read_mod_info, _utoc_assets
from .nexus import _nexus_id, _display_name, _strip_html
from .tooltip import attach_tooltip


class PanelsMixin:
    """Mixin providing main panel (info + log), settings window, and image zoom overlay."""

    # ── Main panel (info 70% / log 30%) ──────────────────────────────

    def _build_main(self):
        main = ctk.CTkFrame(self, corner_radius=0,
                            fg_color=("gray95", "gray12"))
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)   # info expands to fill remaining space

        self._build_info_panel(main)
        log_outer = self._build_log_panel(main)
        log_outer.grid_propagate(False)       # fix log panel at explicit height

        _last_h = [0]
        def _resize(_=None):
            h = main.winfo_height()
            if h < 2 or h == _last_h[0]:
                return
            _last_h[0] = h
            log_outer.configure(height=int(h * 0.30))

        main.bind("<Configure>", _resize)

    # ── Info panel ────────────────────────────────────────────────────

    def _build_info_panel(self, parent):
        outer = ctk.CTkFrame(parent, fg_color=("gray91", "gray14"),
                             corner_radius=0)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(hdr, text="MOD INFO",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=("gray45", "gray55"),
                     ).grid(row=0, column=0, sticky="w")

        self._folder_btn = ctk.CTkButton(
            hdr, text="📂  Open Folder", height=24, width=120,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=11),
            command=self._open_mod_folder,
        )
        attach_tooltip(self._folder_btn, "Open this mod's folder in the file manager")
        # Shown only when the mod folder exists on disk
        self._folder_path = None

        self._nexus_btn = ctk.CTkButton(
            hdr, text="View on Nexus Mods", height=24, width=150,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=11),
            command=self._open_nexus,
        )
        attach_tooltip(self._nexus_btn, "Open this mod's Nexus Mods page in the browser")
        # Shown only when a Nexus ID is known
        self._nexus_id = None

        tab_bar = ctk.CTkFrame(outer, fg_color=_INACTIVE, corner_radius=0, height=30)
        tab_bar.grid(row=1, column=0, sticky="ew")
        tab_bar.grid_columnconfigure((0, 1), weight=1)
        tab_bar.grid_propagate(False)

        self._tab_info_btn = ctk.CTkButton(
            tab_bar, text="Info", corner_radius=0, height=30,
            fg_color=_BG, hover_color=_HOVER,
            font=ctk.CTkFont(size=12),
            command=lambda: self._switch_info_tab("info"),
        )
        self._tab_info_btn.grid(row=0, column=0, sticky="nsew")

        self._tab_assets_btn = ctk.CTkButton(
            tab_bar, text="Assets", corner_radius=0, height=30,
            fg_color=_INACTIVE, hover_color=_HOVER,
            font=ctk.CTkFont(size=12),
            command=lambda: self._switch_info_tab("assets"),
        )
        self._tab_assets_btn.grid(row=0, column=1, sticky="nsew")

        # Content container — both scrollable frames stacked here; only one shown
        content = ctk.CTkFrame(outer, fg_color=_BG, corner_radius=0)
        content.grid(row=2, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)
        outer.grid_rowconfigure(2, weight=1)
        outer.grid_rowconfigure(1, weight=0)

        self._info_scroll = ctk.CTkScrollableFrame(
            content, fg_color="transparent",
            scrollbar_button_color=("gray70", "gray30"),
            scrollbar_button_hover_color=("gray60", "gray40"),
        )
        self._info_scroll.grid(row=0, column=0, sticky="nsew")
        self._info_scroll.grid_columnconfigure(0, weight=1)
        self._bind_scroll(self._info_scroll, self._info_scroll)

        self._assets_scroll = ctk.CTkScrollableFrame(
            content, fg_color="transparent",
            scrollbar_button_color=("gray70", "gray30"),
            scrollbar_button_hover_color=("gray60", "gray40"),
        )
        self._assets_scroll.grid(row=0, column=0, sticky="nsew")
        self._assets_scroll.grid_columnconfigure(0, weight=1)
        self._bind_scroll(self._assets_scroll, self._assets_scroll)
        self._assets_scroll.grid_remove()  # Info tab is default

        self._active_info_tab = "info"
        self._assets_mod_dir  = None

        # Initial placeholder
        self._show_info_placeholder("← Select a mod to view details")

    def _show_info_placeholder(self, text: str):
        for w in self._info_scroll.winfo_children():
            w.destroy()
        self._folder_btn.grid_remove()
        self._nexus_btn.grid_remove()
        self._info_img_ref = None
        ctk.CTkLabel(
            self._info_scroll, text=text,
            font=ctk.CTkFont(size=13),
            text_color=("gray55", "gray50"),
        ).grid(row=0, column=0, pady=40)

    def _switch_info_tab(self, tab: str):
        self._active_info_tab = tab
        if tab == "info":
            self._tab_info_btn.configure(fg_color=_BG)
            self._tab_assets_btn.configure(fg_color=_INACTIVE)
            self._assets_scroll.grid_remove()
            self._info_scroll.grid()
        else:
            self._tab_assets_btn.configure(fg_color=_BG)
            self._tab_info_btn.configure(fg_color=_INACTIVE)
            self._info_scroll.grid_remove()
            self._assets_scroll.grid()
            mod_dir = self._folder_path
            if mod_dir and mod_dir != self._assets_mod_dir:
                self._assets_mod_dir = mod_dir
                self._load_assets_tab(mod_dir)

    def _load_assets_tab(self, mod_dir):
        for w in self._assets_scroll.winfo_children():
            w.destroy()

        # Serve from cache immediately if available
        if mod_dir in self._assets_cache:
            self._show_assets(self._assets_cache[mod_dir])
            return

        ctk.CTkLabel(self._assets_scroll, text="Scanning…",
                     font=ctk.CTkFont(size=12),
                     text_color=("gray55", "gray50"),
                     ).grid(row=0, column=0, pady=30)

        def worker():
            assets = []
            try:
                for utoc in sorted(mod_dir.rglob("*.utoc")):
                    assets.extend(_utoc_assets(utoc))
            except Exception:
                pass
            self._assets_cache[mod_dir] = assets
            self.after(0, lambda: self._show_assets(assets))

        threading.Thread(target=worker, daemon=True).start()

    def _show_assets(self, assets: list):
        for w in self._assets_scroll.winfo_children():
            w.destroy()
        if not assets:
            ctk.CTkLabel(self._assets_scroll,
                         text="No .utoc asset data found for this mod.",
                         font=ctk.CTkFont(size=12),
                         text_color=("gray55", "gray50"),
                         ).grid(row=0, column=0, pady=30)
            return

        # Strip game-specific path prefixes for cleaner display
        strip_prefixes = self._profile.get("utoc_strip_prefixes", []) if self._profile else []

        def _clean_path(p: str) -> str:
            for prefix in strip_prefixes:
                p = p.removeprefix(prefix)
            return p

        # Group by directory prefix
        grouped: dict = {}
        for a in assets:
            a = _clean_path(a)
            parts = a.rsplit("/", 1)
            directory = parts[0] if len(parts) == 2 else ""
            filename  = parts[-1]
            grouped.setdefault(directory, []).append(filename)

        row = 0
        for directory in sorted(grouped):
            files = sorted(grouped[directory])
            ctk.CTkLabel(self._assets_scroll,
                         text=directory or "/",
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=("gray45", "gray55"), anchor="w",
                         ).grid(row=row, column=0, sticky="w", padx=8, pady=(8, 1))
            row += 1
            for fname in files:
                ctk.CTkLabel(self._assets_scroll,
                             text=f"  {fname}",
                             font=ctk.CTkFont(family="monospace", size=11),
                             text_color=("gray30", "gray72"), anchor="w",
                             ).grid(row=row, column=0, sticky="w", padx=8)
                row += 1

    def _show_archive_info(self, name: str, arch_path):
        """Info panel content for an archive that hasn't been extracted yet."""
        for w in self._info_scroll.winfo_children():
            w.destroy()
        self._folder_btn.grid_remove()
        self._nexus_btn.grid_remove()
        self._info_img_ref = None

        disp = _display_name(name)
        nid  = _nexus_id(name)

        top = ctk.CTkFrame(self._info_scroll, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))
        top.grid_columnconfigure(1, weight=1)

        # Archive icon placeholder
        icon_frame = ctk.CTkFrame(top, fg_color=("gray78", "gray20"),
                                  corner_radius=8, width=190, height=190)
        icon_frame.grid(row=0, column=0, padx=(4, 12), pady=4, sticky="n")
        icon_frame.grid_propagate(False)
        ctk.CTkLabel(icon_frame, text="📦",
                     font=ctk.CTkFont(size=48),
                     ).place(relx=0.5, rely=0.5, anchor="center")

        meta = ctk.CTkFrame(top, fg_color="transparent")
        meta.grid(row=0, column=1, sticky="nw", pady=4)

        ctk.CTkLabel(meta, text=disp,
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=("gray10", "gray92"), anchor="w",
                     wraplength=400,
                     ).grid(row=0, column=0, columnspan=2, sticky="w",
                            pady=(0, 6))

        def _field(label, value, row):
            ctk.CTkLabel(meta, text=label,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=("gray45", "gray55"), anchor="w",
                         ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=1)
            ctk.CTkLabel(meta, text=value,
                         font=ctk.CTkFont(size=12),
                         text_color=("gray15", "gray88"), anchor="w",
                         wraplength=380,
                         ).grid(row=row, column=1, sticky="w", pady=1)

        r = 1
        _field("File", arch_path.name, r); r += 1
        try:
            size_mb = arch_path.stat().st_size / (1024 * 1024)
            _field("Size", f"{size_mb:.1f} MB", r); r += 1
        except Exception:
            pass

        if nid:
            nexus_url = _gc.NEXUS_BASE + nid
            _field("Nexus ID", f"#{nid}", r); r += 1
            self._nexus_id = nexus_url
            self._nexus_btn.grid(row=0, column=2, sticky="e")

        # Status note
        ctk.CTkLabel(meta, text="Status",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=("gray45", "gray55"), anchor="w",
                     ).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=1)
        ctk.CTkLabel(meta, text="📦  archived — not yet extracted",
                     font=ctk.CTkFont(size=12),
                     text_color="#e67e22", anchor="w",
                     ).grid(row=r, column=1, sticky="w", pady=1); r += 1

        # Hint
        sep = ctk.CTkFrame(self._info_scroll, height=1,
                           fg_color=("gray75", "gray28"))
        sep.grid(row=1, column=0, sticky="ew", padx=8, pady=(10, 6))
        ctk.CTkLabel(self._info_scroll,
                     text="Click  Extract  in the action bar below to unpack this archive.",
                     font=ctk.CTkFont(size=12),
                     text_color=("gray40", "gray60"),
                     justify="left",
                     ).grid(row=2, column=0, sticky="w", padx=16, pady=(0, 8))

        # Augment with Nexus data if available
        api_key = self._cfg.get("nexus_api_key", "")
        if nid:
            nd = self._nexus_cache.get(nid)
            if nd and not nd.get("_error"):
                desc = _strip_html(nd.get("summary") or nd.get("description") or "")
                if desc:
                    ctk.CTkLabel(self._info_scroll,
                                 text=desc[:800],
                                 font=ctk.CTkFont(size=12),
                                 text_color=("gray20", "gray80"),
                                 justify="left", wraplength=560,
                                 ).grid(row=3, column=0, sticky="w",
                                        padx=12, pady=(2, 8))
            elif api_key and nid not in self._nexus_fetching:
                self._nexus_fetching.add(nid)
                threading.Thread(
                    target=self._bg_fetch_nexus,
                    args=(nid, api_key),
                    daemon=True,
                ).start()

    def _update_info_panel(self, name):
        """Populate the info panel for the given mod folder name (or None)."""
        self._update_action_buttons()
        self._cancel_img_overlay()
        for w in self._info_scroll.winfo_children():
            w.destroy()
        self._folder_btn.grid_remove()
        self._nexus_btn.grid_remove()
        self._info_img_ref = None
        # Reset assets tab so it reloads for the new mod
        self._assets_mod_dir = None
        for w in self._assets_scroll.winfo_children():
            w.destroy()

        if name is None:
            self._show_info_placeholder("← Click a mod to view details")
            return

        # ── Archive-only entry (no extracted folder yet) ───────────────
        arch_path = self._archived.get(name)
        if arch_path is not None:
            self._show_archive_info(name, arch_path)
            return

        mod_dir = None
        try:
            mod_dir = self._cfg["mods_dir"] / name
        except Exception:
            pass

        ms      = self._state["mods"].get(name, {})
        is_on   = ms.get("enabled", False)
        links   = len(ms.get("symlinks", []))
        exists  = mod_dir and mod_dir.is_dir()
        disp    = _display_name(name)
        nid     = _nexus_id(name)

        if exists:
            self._folder_path = mod_dir
            self._folder_btn.grid(row=0, column=1, sticky="e", padx=(0, 6))

        if exists:
            if mod_dir not in self._mod_info_cache:
                try:
                    self._mod_info_cache[mod_dir] = _read_mod_info(mod_dir)
                except Exception:
                    self._mod_info_cache[mod_dir] = {}
            info = self._mod_info_cache[mod_dir].copy()
        else:
            info = {}

        # ── Augment with cached Nexus data ────────────────────────────
        api_key = self._cfg.get("nexus_api_key", "")

        if nid:
            nd = self._nexus_cache.get(nid)
            if nd and not nd.get("_error"):
                if not info.get("name"):
                    info["name"] = nd.get("name", "")
                if not info.get("author"):
                    info["author"] = nd.get("author", "") or nd.get("uploaded_by", "")
                if not info.get("version"):
                    info["version"] = nd.get("version", "")
                if not info.get("description"):
                    raw_desc = nd.get("summary") or nd.get("description") or ""
                    info["description"] = _strip_html(raw_desc)[:800]
                if not info.get("image_path") and nd.get("_cached_image"):
                    from pathlib import Path
                    info["image_path"] = Path(nd["_cached_image"])
            elif api_key and nid not in self._nexus_fetching:
                self._nexus_fetching.add(nid)
                threading.Thread(
                    target=self._bg_fetch_nexus,
                    args=(nid, api_key),
                    daemon=True,
                ).start()

        # ── Layout: image left, metadata right ────────────────────────
        top = ctk.CTkFrame(self._info_scroll, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))
        top.grid_columnconfigure(1, weight=1)

        # Image
        img_frame = ctk.CTkFrame(top, fg_color=("gray78", "gray20"),
                                 corner_radius=8, width=190, height=190)
        img_frame.grid(row=0, column=0, padx=(4, 12), pady=4, sticky="n")
        img_frame.grid_propagate(False)

        img_path = info.get("image_path")
        if img_path and _PIL:
            try:
                pil_img = PILImage.open(img_path).convert("RGBA")
                pil_img.thumbnail((186, 186), PILImage.LANCZOS)
                w, h = pil_img.size
                ctk_img = ctk.CTkImage(light_image=pil_img,
                                       dark_image=pil_img, size=(w, h))
                self._info_img_ref = ctk_img
                ctk.CTkLabel(img_frame, image=ctk_img, text="",
                             ).place(relx=0.5, rely=0.5, anchor="center")
            except Exception:
                img_path = None
                ctk.CTkLabel(img_frame, text="No preview",
                             text_color=("gray55", "gray48"),
                             font=ctk.CTkFont(size=11),
                             ).place(relx=0.5, rely=0.5, anchor="center")
        else:
            img_path = None
            ctk.CTkLabel(img_frame, text="No preview",
                         text_color=("gray55", "gray48"),
                         font=ctk.CTkFont(size=11),
                         ).place(relx=0.5, rely=0.5, anchor="center")

        # Hover → zoom overlay; dismissal is handled by cursor-position polling
        if img_path:
            for w in (img_frame,) + tuple(img_frame.winfo_children()):
                w.bind("<Enter>",
                       lambda _, p=img_path, f=img_frame:
                           self._schedule_img_overlay(p, f),
                       add="+")

        # Metadata
        meta = ctk.CTkFrame(top, fg_color="transparent")
        meta.grid(row=0, column=1, sticky="nw", pady=4)

        def _field(label: str, value: str, row: int, link=False):
            if not value:
                return
            ctk.CTkLabel(meta, text=label,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=("gray45", "gray55"), anchor="w",
                         ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=1)
            if link:
                btn = ctk.CTkButton(
                    meta, text=value, height=20,
                    fg_color="transparent",
                    font=ctk.CTkFont(size=12, underline=True),
                    text_color=("#4a9edd", "#5aaeee"),
                    hover=False,
                    anchor="w",
                    command=lambda v=value: webbrowser.open(v),
                )
                btn.grid(row=row, column=1, sticky="w", pady=1)
            else:
                ctk.CTkLabel(meta, text=value,
                             font=ctk.CTkFont(size=12),
                             text_color=("gray15", "gray88"), anchor="w",
                             wraplength=380,
                             ).grid(row=row, column=1, sticky="w", pady=1)

        title = info.get("name") or disp
        ctk.CTkLabel(meta, text=title,
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=("gray10", "gray92"), anchor="w",
                     wraplength=400,
                     ).grid(row=0, column=0, columnspan=2, sticky="w",
                            pady=(0, 6))

        r = 1
        if info.get("bundle") and info["bundle"] != title:
            _field("Bundle name", info["bundle"], r); r += 1
        if info.get("author"):
            _field("Author", info["author"], r); r += 1
        if info.get("version"):
            _field("Version", info["version"], r); r += 1
        if nid:
            nexus_url = _gc.NEXUS_BASE + nid
            _field("Nexus ID", f"#{nid}", r); r += 1
            self._nexus_id = nexus_url
            self._nexus_btn.grid(row=0, column=2, sticky="e")

        status_text = ("✔  enabled" if is_on else "✘  disabled") + \
                      (f"  ({links} symlinks)" if is_on and links else "")
        status_col  = "#27ae60" if is_on else ("gray52", "gray45")
        ctk.CTkLabel(meta, text="Status",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=("gray45", "gray55"), anchor="w",
                     ).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=1)
        ctk.CTkLabel(meta, text=status_text,
                     font=ctk.CTkFont(size=12),
                     text_color=status_col, anchor="w",
                     ).grid(row=r, column=1, sticky="w", pady=1); r += 1

        if not exists:
            _field("Note", "Folder not on disk (state record only)", r); r += 1

        # ── Description ───────────────────────────────────────────────
        desc = info.get("description") or info.get("readme_text", "")
        if desc:
            sep = ctk.CTkFrame(self._info_scroll, height=1,
                               fg_color=("gray75", "gray28"))
            sep.grid(row=1, column=0, sticky="ew", padx=8, pady=(10, 6))

            ctk.CTkLabel(self._info_scroll, text="Description",
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=("gray45", "gray55"), anchor="w",
                         ).grid(row=2, column=0, sticky="w", padx=12)

            ctk.CTkLabel(self._info_scroll, text=desc,
                         font=ctk.CTkFont(size=12),
                         text_color=("gray20", "gray80"), anchor="w",
                         justify="left", wraplength=560,
                         ).grid(row=3, column=0, sticky="w", padx=12, pady=(2, 0))

        # ── Contents ──────────────────────────────────────────────────
        stems      = info.get("pak_stems", [])
        ue4ss_mods = info.get("ue4ss_mods", [])
        scripts    = info.get("script_files", [])

        items: list = []
        heading = ""
        if stems:
            items = stems
            heading = f"Contents  ({len(stems)} pak file(s))"
        elif ue4ss_mods:
            items = ue4ss_mods + scripts
            heading = f"Contents  (UE4SS — {', '.join(ue4ss_mods)})"

        if items:
            sep2 = ctk.CTkFrame(self._info_scroll, height=1,
                                fg_color=("gray75", "gray28"))
            sep2.grid(row=4, column=0, sticky="ew", padx=8, pady=(10, 6))

            ctk.CTkLabel(self._info_scroll, text=heading,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=("gray45", "gray55"), anchor="w",
                         ).grid(row=5, column=0, sticky="w", padx=12)

            files_frame = ctk.CTkFrame(self._info_scroll, fg_color="transparent")
            files_frame.grid(row=6, column=0, sticky="ew", padx=12, pady=(2, 8))

            limit = 12
            display = items if stems else scripts  # for UE4SS, list scripts under the mod name
            for i, fname in enumerate(display[:limit]):
                ctk.CTkLabel(files_frame, text=f"  {fname}",
                             font=ctk.CTkFont(family="monospace", size=11),
                             text_color=("gray35", "gray70"), anchor="w",
                             ).grid(row=i, column=0, sticky="w")
            if len(display) > limit:
                ctk.CTkLabel(files_frame,
                             text=f"  … and {len(display) - limit} more",
                             font=ctk.CTkFont(size=11),
                             text_color=("gray55", "gray50"), anchor="w",
                             ).grid(row=limit, column=0, sticky="w")

        # If assets tab is already visible, load assets for the new mod now
        if self._active_info_tab == "assets" and self._folder_path:
            self._assets_mod_dir = self._folder_path
            self._load_assets_tab(self._folder_path)

    # ── Image zoom overlay ────────────────────────────────────────────

    def _schedule_img_overlay(self, img_path, img_frame):
        """Schedule show after a brief hover delay and start cursor polling."""
        # Cancel any previous sequence, but don't restart if already showing
        if self._img_overlay:
            return
        if self._overlay_after:
            self.after_cancel(self._overlay_after)
        self._overlay_after = self.after(
            280, lambda: self._show_img_overlay(img_path, img_frame))
        self._start_hover_poll(img_frame)

    def _start_hover_poll(self, img_frame):
        """Poll cursor position every 80 ms; dismiss when cursor leaves img_frame."""
        if self._poll_after:
            self.after_cancel(self._poll_after)

        def poll():
            try:
                px = self.winfo_pointerx()
                py = self.winfo_pointery()
                fx = img_frame.winfo_rootx()
                fy = img_frame.winfo_rooty()
                fw = img_frame.winfo_width()
                fh = img_frame.winfo_height()
                over = fx <= px <= fx + fw and fy <= py <= fy + fh
            except Exception:
                over = False

            if over:
                self._poll_after = self.after(80, poll)
            else:
                self._poll_after = None
                self._cancel_img_overlay()

        self._poll_after = self.after(80, poll)

    def _cancel_img_overlay(self):
        if self._overlay_after:
            self.after_cancel(self._overlay_after)
            self._overlay_after = None
        if self._poll_after:
            self.after_cancel(self._poll_after)
            self._poll_after = None
        self._hide_img_overlay()

    def _show_img_overlay(self, img_path, img_frame):
        self._overlay_after = None
        if not _PIL:
            return
        try:
            pil_img = PILImage.open(img_path).convert("RGBA")
        except Exception:
            return

        # Max 70 % of screen, never upscale beyond original pixel count
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        max_w = int(sw * 0.70)
        max_h = int(sh * 0.70)
        img_w, img_h = pil_img.size
        scale = min(max_w / img_w, max_h / img_h, 1.0)
        new_w = max(1, int(img_w * scale))
        new_h = max(1, int(img_h * scale))
        if scale < 1.0:
            pil_img = pil_img.resize((new_w, new_h), PILImage.LANCZOS)

        # Center over the main window
        ax = self.winfo_x()
        ay = self.winfo_y()
        aw = self.winfo_width()
        ah = self.winfo_height()
        ox = ax + (aw - new_w) // 2
        oy = ay + (ah - new_h) // 2
        # Clamp to screen bounds
        ox = max(0, min(ox, sw - new_w))
        oy = max(0, min(oy, sh - new_h))

        ov = tk.Toplevel(self)
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        ov.geometry(f"{new_w}x{new_h}+{ox}+{oy}")
        ov.configure(bg="#0a0a0a")

        photo = PILImageTk.PhotoImage(pil_img)
        self._overlay_imgref = photo   # prevent GC

        lbl = tk.Label(ov, image=photo, bd=0, bg="#0a0a0a")
        lbl.pack(fill="both", expand=True)

        # Click anywhere on overlay to dismiss
        for widget in (ov, lbl):
            widget.bind("<Button-1>", lambda _: self._cancel_img_overlay())

        self._img_overlay = ov
        # Keep the poll running so cursor leaving img_frame dismisses the overlay
        self._start_hover_poll(img_frame)

    def _hide_img_overlay(self):
        if self._img_overlay:
            try:
                self._img_overlay.destroy()
            except Exception:
                pass
            self._img_overlay    = None
            self._overlay_imgref = None

    # ── Log panel ─────────────────────────────────────────────────────

    def _build_log_panel(self, parent):
        outer = ctk.CTkFrame(parent, corner_radius=0,
                             fg_color=("gray95", "gray12"))
        outer.grid(row=1, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(1, weight=1)

        log_hdr = ctk.CTkFrame(outer, fg_color="transparent")
        log_hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(8, 4))
        log_hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(log_hdr, text="OUTPUT",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=("gray45", "gray55"),
                     ).grid(row=0, column=0, sticky="w")

        _clear_btn = ctk.CTkButton(
            log_hdr, text="Clear", width=64, height=24,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=11),
            command=self._clear_log,
        )
        _clear_btn.grid(row=0, column=1, sticky="e")
        attach_tooltip(_clear_btn, "Clear the output log")

        self._log = ctk.CTkTextbox(
            outer,
            font=ctk.CTkFont(family="monospace", size=12),
            wrap="word", state="disabled",
            fg_color=("gray85", "gray18"),
        )
        self._log.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 0))

        # Action buttons — per focused mod
        act = ctk.CTkFrame(outer, fg_color="transparent")
        act.grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 12))
        act.grid_columnconfigure(0, weight=1)
        act.grid_columnconfigure(1, weight=1)

        self._btn_mod_action = ctk.CTkButton(
            act, text="Enable", height=32,
            font=ctk.CTkFont(size=12),
            state="disabled",
            fg_color=("gray72", "gray30"),
            command=self._mod_action,
        )
        self._btn_mod_action.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self._tt_mod_action = attach_tooltip(
            self._btn_mod_action,
            "Enable or disable the focused mod, or extract it if it is an archive",
        )

        self._btn_uninstall = ctk.CTkButton(
            act, text="Uninstall", height=32,
            font=ctk.CTkFont(size=12),
            state="disabled",
            fg_color="#c0392b", hover_color="#922b21",
            command=self._uninstall_focused,
        )
        self._btn_uninstall.grid(row=0, column=1, padx=(4, 0), sticky="ew")
        attach_tooltip(
            self._btn_uninstall,
            "Disable this mod, delete its folder, and clear its saved state",
        )

        return outer

    def _update_action_buttons(self):
        """Update the Enable/Extract/Disable and Uninstall buttons for the focused mod."""
        name = self._focused
        if not name:
            self._btn_mod_action.configure(
                state="disabled", text="Enable",
                fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
            )
            self._btn_uninstall.configure(state="disabled")
            return

        if name in self._archived:
            self._btn_mod_action.configure(
                state="normal", text="Extract",
                fg_color=("#c07010", "#8a4e08"), hover_color=("#a06008", "#6a3a04"),
            )
            self._tt_mod_action.update("Extract this archive into the mods folder")
            self._btn_uninstall.configure(state="disabled")
        else:
            ms = self._state["mods"].get(name, {})
            if ms.get("enabled"):
                self._btn_mod_action.configure(
                    state="normal", text="Disable",
                    fg_color=("gray52", "gray38"), hover_color=("gray42", "gray46"),
                )
                self._tt_mod_action.update("Disable this mod (removes its symlinks)")
            else:
                self._btn_mod_action.configure(
                    state="normal", text="Enable",
                    fg_color=("#1a5a9a", "#1a5a9a"), hover_color=("#1a6aaa", "#1a6aaa"),
                )
                self._tt_mod_action.update("Enable this mod (creates symlinks into the game folder)")
            self._btn_uninstall.configure(state="normal")

    def _mod_action(self):
        """Enable, disable, or extract the focused mod depending on its current state."""
        name = self._focused
        if not name:
            return
        if name in self._archived:
            self._run_interactive(["--extract", name], on_done=self.refresh_mods)
        else:
            ms = self._state["mods"].get(name, {})
            if ms.get("enabled"):
                self._run_bg(["--disable", name], on_done=self.refresh_mods)
            else:
                self._run_interactive(["--enable", name], on_done=self.refresh_mods)

    def _uninstall_focused(self):
        """Disable, delete folder, and purge state for the focused mod."""
        name = self._focused
        if not name or name in self._archived:
            return
        disp = self._get_disp_name(name)
        dlg = ctk.CTkInputDialog(
            text=f"Type  yes  to fully uninstall '{disp}':\n"
                 "This disables it, deletes its folder, and clears its state.\n"
                 "You will need to re-extract it from the archive to use it again.",
            title="Confirm Uninstall",
        )
        if dlg.get_input() != "yes":
            self._log_write("[cancelled]\n")
            return
        self._run_bg(["--uninstall", name], on_done=self.refresh_mods)

    # ── Status bar ────────────────────────────────────────────────────

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, height=26, corner_radius=0,
                           fg_color=("gray83", "gray16"))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        self._status = ctk.CTkLabel(bar, text="Ready",
                                    font=ctk.CTkFont(size=11),
                                    text_color=("gray38", "gray60"))
        self._status.grid(row=0, column=0, sticky="w", padx=12)

        self._busy = ctk.CTkLabel(bar, text="",
                                  font=ctk.CTkFont(size=11),
                                  text_color=("gray38", "gray60"))
        self._busy.grid(row=0, column=1, sticky="e", padx=12)

    # ── Settings window ───────────────────────────────────────────────

    def _open_settings(self):
        # If already open, just focus it
        if hasattr(self, "_settings_win") and self._settings_win and \
                self._settings_win.winfo_exists():
            self._settings_win.focus()
            return

        # Load full raw config; normalise to multi-game format for round-tripping
        try:
            with open(CONFIG_FILE) as f:
                raw = json.load(f)
        except Exception:
            raw = {}
        if "game_root" in raw:
            raw = {
                "current_game": "stellar_blade",
                "games": {"stellar_blade": {k: v for k, v in raw.items() if k != "theme"}},
                "theme": raw.get("theme", "dark"),
            }
        current_game = raw.get("current_game", "stellar_blade")
        game_section = raw.setdefault("games", {}).setdefault(current_game, {})
        game_name_label = self._profile.get("name", current_game) if self._profile else current_game

        win = ctk.CTkToplevel(self)
        win.title(f"Settings — {game_name_label}")
        win.geometry("580x530")
        win.minsize(480, 440)
        win.resizable(True, True)
        win.transient(self)
        win.withdraw()          # hide until fully built (prevents blank flash on Linux)
        self._settings_win = win
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(0, weight=1)

        # Center over main window
        self.update_idletasks()
        wx = self.winfo_x() + (self.winfo_width()  - 580) // 2
        wy = self.winfo_y() + (self.winfo_height() - 530) // 2
        win.geometry(f"580x530+{wx}+{wy}")

        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        def _section_header(text, r):
            ctk.CTkLabel(
                scroll, text=text,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=("gray45", "gray55"), anchor="w",
            ).grid(row=r, column=0, sticky="ew", padx=16, pady=(16, 2))
            ctk.CTkFrame(scroll, height=1, fg_color=("gray75", "gray30"),
                         ).grid(row=r + 1, column=0, sticky="ew", padx=16, pady=(0, 6))
            return r + 2

        row = 0

        # ── Nexus Mods ────────────────────────────────────────────────
        row = _section_header("NEXUS MODS", row)

        # API key row
        api_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        api_frame.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 2))
        api_frame.grid_columnconfigure(1, weight=1)
        row += 1

        ctk.CTkLabel(api_frame, text="API Key", width=120, anchor="w",
                     font=ctk.CTkFont(size=12),
                     ).grid(row=0, column=0, sticky="w", pady=4)

        api_var   = ctk.StringVar(value=game_section.get("nexus_api_key", ""))
        api_entry = ctk.CTkEntry(api_frame, textvariable=api_var, show="•",
                                 placeholder_text="Paste your Nexus Mods API key here")
        api_entry.grid(row=0, column=1, sticky="ew", padx=(0, 6))

        show_var = ctk.BooleanVar(value=False)

        def _toggle_show():
            api_entry.configure(show="" if show_var.get() else "•")

        ctk.CTkCheckBox(
            api_frame, text="Show", variable=show_var,
            width=70, checkbox_width=15, checkbox_height=15,
            command=_toggle_show,
        ).grid(row=0, column=2)

        # Link to Nexus API-key page
        link_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        link_frame.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 2))
        row += 1

        ctk.CTkLabel(link_frame, text="Get your API key at:",
                     font=ctk.CTkFont(size=11),
                     text_color=("gray50", "gray55"),
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            link_frame, text="nexusmods.com/settings/api-keys",
            fg_color="transparent", hover=False,
            font=ctk.CTkFont(size=11, underline=True),
            text_color=("#4a9edd", "#5aaeee"),
            command=lambda: webbrowser.open(
                "https://www.nexusmods.com/settings/api-keys"),
        ).grid(row=0, column=1, sticky="w", padx=(4, 0))

        # Clear Nexus cache
        cache_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        cache_frame.grid(row=row, column=0, sticky="ew", padx=16, pady=(4, 0))
        row += 1

        cache_info = ctk.CTkLabel(cache_frame, text="",
                                  font=ctk.CTkFont(size=11),
                                  text_color=("gray50", "gray55"))

        def _clear_cache():
            count = 0
            cache_dir = _gc._NEXUS_CACHE_DIR
            if cache_dir.exists():
                for fp in cache_dir.iterdir():
                    try:
                        fp.unlink()
                        count += 1
                    except Exception:
                        pass
            self._nexus_cache.clear()
            self._nexus_fetching.clear()
            cache_info.configure(text=f"Cleared {count} cached file(s).")

        ctk.CTkButton(
            cache_frame, text="Clear Nexus Cache", width=160, height=28,
            fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
            font=ctk.CTkFont(size=11),
            command=_clear_cache,
        ).grid(row=0, column=0, sticky="w")
        cache_info.grid(row=0, column=1, sticky="w", padx=(12, 0))

        # ── Paths ─────────────────────────────────────────────────────
        row = _section_header("PATHS", row)

        path_vars: dict = {}

        def _path_row(label, key, r):
            f = ctk.CTkFrame(scroll, fg_color="transparent")
            f.grid(row=r, column=0, sticky="ew", padx=16, pady=(0, 2))
            f.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(f, text=label, width=120, anchor="w",
                         font=ctk.CTkFont(size=12),
                         ).grid(row=0, column=0, sticky="w", pady=4)

            var = ctk.StringVar(value=game_section.get(key, ""))
            path_vars[key] = var
            ent = ctk.CTkEntry(f, textvariable=var,
                               placeholder_text=f"{label} path")
            ent.grid(row=0, column=1, sticky="ew", padx=(0, 6))

            def _browse(v=var, lbl=label):
                d = tkinter.filedialog.askdirectory(title=f"Select {lbl}",
                                                    parent=win)
                if d:
                    v.set(d)

            ctk.CTkButton(f, text="Browse…", width=80, height=28,
                          fg_color=("gray72", "gray30"),
                          hover_color=("gray62", "gray38"),
                          font=ctk.CTkFont(size=11),
                          command=_browse,
                          ).grid(row=0, column=2)
            return r + 1

        row = _path_row("Game Root",       "game_root",      row)
        row = _path_row("Mods Folder",     "mods_dir",       row)
        row = _path_row("Archives Folder", "compressed_dir", row)

        # ── Appearance ────────────────────────────────────────────────
        row = _section_header("APPEARANCE", row)

        ap_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        ap_frame.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 4))
        ap_frame.grid_columnconfigure(1, weight=1)
        row += 1

        ctk.CTkLabel(ap_frame, text="Theme", width=120, anchor="w",
                     font=ctk.CTkFont(size=12),
                     ).grid(row=0, column=0, sticky="w", pady=4)
        theme_var = ctk.StringVar(value=raw.get("theme", ctk.get_appearance_mode().lower()))
        ctk.CTkOptionMenu(ap_frame, values=["dark", "light", "system"],
                          variable=theme_var, width=140,
                          ).grid(row=0, column=1, sticky="w")

        # ── Save / Cancel ─────────────────────────────────────────────
        btn_bar = ctk.CTkFrame(win, fg_color=("gray86", "gray17"), corner_radius=0)
        btn_bar.grid(row=1, column=0, sticky="ew")
        btn_bar.grid_columnconfigure(0, weight=1)

        def _save():
            new_raw = dict(raw)
            new_raw["theme"] = theme_var.get()
            new_raw.setdefault("games", {})[current_game] = {
                "game_root":      path_vars["game_root"].get().strip(),
                "mods_dir":       path_vars["mods_dir"].get().strip(),
                "compressed_dir": path_vars["compressed_dir"].get().strip(),
                "nexus_api_key":  api_var.get().strip(),
            }
            try:
                CONFIG_FILE.write_text(json.dumps(new_raw, indent=2))
            except Exception as e:
                tkmsgbox.showerror("Save Error", str(e), parent=win)
                return
            ctk.set_appearance_mode(theme_var.get())
            self.refresh_mods()
            win.destroy()

        ctk.CTkButton(btn_bar, text="Save", width=100, height=34,
                      command=_save,
                      ).grid(row=0, column=1, padx=8, pady=8, sticky="e")
        ctk.CTkButton(btn_bar, text="Cancel", width=100, height=34,
                      fg_color=("gray72", "gray30"),
                      hover_color=("gray62", "gray38"),
                      command=win.destroy,
                      ).grid(row=0, column=2, padx=(0, 12), pady=8, sticky="e")

        # Show the window now that all widgets are built (prevents blank CTkToplevel on Linux)
        def _show_win():
            win.deiconify()
            win.grab_set()
            win.lift()
            win.focus_force()
        win.after(50, _show_win)

    # ── Log helpers ───────────────────────────────────────────────────

    def _log_write(self, text: str, bold: bool = False):
        self._log.configure(state="normal")
        self._log.insert("end", text)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
