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
from .nexus import (_nexus_id, _display_name, _strip_html,
                    _nexus_fetch_requirements, _installed_nexus_ids)
from .tooltip import attach_tooltip


def _gather_recommendations(mod_name: str, mod_dir, cfg: dict) -> list:
    """Return unmet recommendations for a mod: mm_metadata.json + Nexus requirements.

    Filters out mods the user already has installed (by Nexus ID match).
    Each entry is a dict with 'name' and 'reason'.
    """
    from pathlib import Path
    import json as _json

    recs: list[dict] = []
    seen_names: set[str] = set()

    # 1. mm_metadata.json (hand-authored, non-Nexus-aware)
    meta_path = Path(mod_dir) / "mm_metadata.json"
    if meta_path.exists():
        try:
            for r in _json.loads(meta_path.read_text(encoding="utf-8")).get("recommended_mods", []):
                name = r.get("name", "")
                if name and name not in seen_names:
                    recs.append({"name": name, "reason": r.get("reason", ""), "source": "local"})
                    seen_names.add(name)
        except Exception:
            pass

    # 2. Nexus requirements (only when API key is configured)
    api_key = cfg.get("nexus_api_key", "")
    mods_dir = cfg.get("mods_dir")
    if api_key and mods_dir:
        mod_id = _nexus_id(mod_name)
        if mod_id:
            installed_ids = _installed_nexus_ids(Path(mods_dir))
            for req in _nexus_fetch_requirements(mod_id, api_key):
                req_id  = str(req.get("mod_id", ""))
                req_name = req.get("name") or req.get("mod_name") or ""
                if not req_name or req_id in installed_ids:
                    continue
                if req_name not in seen_names:
                    recs.append({"name": req_name, "reason": "Listed as a requirement on the Nexus mod page.",
                                 "nexus_id": req_id, "source": "nexus"})
                    seen_names.add(req_name)

    return recs


class PanelsMixin:
    """Mixin providing main panel (info + log), settings window, and image zoom overlay."""

    # ── Main panel (info 70% / log 30%) ──────────────────────────────

    def _build_main(self):
        main = ctk.CTkFrame(self, corner_radius=0,
                            fg_color=("gray95", "gray12"))
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=0)   # nav bar (fixed height)
        main.grid_rowconfigure(1, weight=1)   # page content (expands)

        # ── Nav bar ───────────────────────────────────────────────────
        nav = ctk.CTkFrame(main, height=38, fg_color=("gray88", "gray13"),
                           corner_radius=0)
        nav.grid(row=0, column=0, sticky="ew")
        nav.grid_propagate(False)
        nav.grid_columnconfigure(2, weight=1)   # spacer pushes right-side buttons to the edge

        self._page_nav = ctk.CTkSegmentedButton(
            nav, values=["Mods", "Downloads"],
            font=ctk.CTkFont(size=12),
            command=self._on_page_select,
        )
        self._page_nav.set("Mods")
        self._page_nav.grid(row=0, column=0, padx=8, pady=5)
        self._page_nav_has_plugins = False   # track whether Plugins tab is present

        # Badge shows active download count
        self._dl_nav_badge = ctk.CTkLabel(
            nav, text="",
            font=ctk.CTkFont(size=11),
            text_color=("#c07010", "#c07010"),
        )
        self._dl_nav_badge.grid(row=0, column=1, padx=(0, 8), pady=5)

        # Asset Search button
        _search_btn = ctk.CTkButton(
            nav, text="🔍  Asset Search", height=28,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=12),
            command=self._open_asset_search,
        )
        _search_btn.grid(row=0, column=3, sticky="e", padx=(0, 8), pady=5)
        attach_tooltip(_search_btn,
                       "Search inside mod files for an internal game asset path or string")

        # Launch Game (Steam) — right side of nav bar
        self._btn_launch_steam = ctk.CTkButton(
            nav, text="▶  Launch Game (Steam)", height=28,
            fg_color=("#1b5e9e", "#1a4f7a"), hover_color=("#1565c0", "#1a6aaa"),
            font=ctk.CTkFont(size=12),
            command=self._launch_steam,
        )
        self._btn_launch_steam.grid(row=0, column=4, sticky="e", padx=(0, 10), pady=5)
        attach_tooltip(self._btn_launch_steam, "Launch the game via Steam")

        # ── Page frames ───────────────────────────────────────────────
        self._page_mods = ctk.CTkFrame(main, fg_color="transparent",
                                       corner_radius=0)
        self._page_mods.grid(row=1, column=0, sticky="nsew")
        self._page_mods.grid_columnconfigure(0, weight=1)
        self._page_mods.grid_rowconfigure(0, weight=1)

        self._page_downloads = ctk.CTkFrame(main, fg_color="transparent",
                                            corner_radius=0)
        self._page_downloads.grid(row=1, column=0, sticky="nsew")
        self._page_downloads.grid_columnconfigure(0, weight=1)
        self._page_downloads.grid_rowconfigure(0, weight=1)
        self._page_downloads.grid_remove()   # hidden initially

        # Plugins page (shown only for Bethesda-engine games)
        self._page_plugins = ctk.CTkFrame(main, fg_color="transparent",
                                          corner_radius=0)
        self._page_plugins.grid(row=1, column=0, sticky="nsew")
        self._page_plugins.grid_columnconfigure(0, weight=1)
        self._page_plugins.grid_rowconfigure(0, weight=1)
        self._page_plugins.grid_remove()     # hidden initially
        self._build_plugins_panel(self._page_plugins)

        # Conflicts page (shown when any conflicts exist)
        self._page_conflicts = ctk.CTkFrame(main, fg_color="transparent",
                                            corner_radius=0)
        self._page_conflicts.grid(row=1, column=0, sticky="nsew")
        self._page_conflicts.grid_columnconfigure(0, weight=1)
        self._page_conflicts.grid_rowconfigure(0, weight=1)
        self._page_conflicts.grid_remove()   # hidden initially
        self._build_conflicts_panel(self._page_conflicts)
        self._page_nav_has_conflicts = False

        # Game Settings (INI) page — shown when profile has ini_files
        self._page_ini = ctk.CTkFrame(main, fg_color="transparent", corner_radius=0)
        self._page_ini.grid(row=1, column=0, sticky="nsew")
        self._page_ini.grid_columnconfigure(0, weight=1)
        self._page_ini.grid_rowconfigure(0, weight=1)
        self._page_ini.grid_remove()   # hidden initially
        self._build_ini_panel(self._page_ini)
        self._page_nav_has_ini = False

        # ── Mods page: info + log panels (unchanged layout) ───────────
        self._build_info_panel(self._page_mods)
        log_outer = self._build_log_panel(self._page_mods)
        log_outer.grid_propagate(False)

        _last_h = [0]
        def _resize(_=None):
            h = self._page_mods.winfo_height()
            if h < 2 or h == _last_h[0]:
                return
            _last_h[0] = h
            log_outer.configure(height=int(h * 0.30))

        self._page_mods.bind("<Configure>", _resize)

        # ── Downloads page ────────────────────────────────────────────
        self._build_downloads_panel(self._page_downloads)

    def _on_page_select(self, value: str):
        self._page_mods.grid_remove()
        self._page_downloads.grid_remove()
        self._page_plugins.grid_remove()
        self._page_conflicts.grid_remove()
        self._page_ini.grid_remove()
        if value == "Downloads":
            self._page_downloads.grid()
        elif value == "Plugins":
            self._page_plugins.grid()
            self._refresh_plugins_panel()
        elif value == "Conflicts":
            self._page_conflicts.grid()
            self._refresh_conflicts_panel()
        elif value == "Game Settings":
            self._page_ini.grid()
            self._refresh_ini_panel()
        else:
            self._page_mods.grid()

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
        tab_bar.grid_columnconfigure((0, 1, 2), weight=1)
        tab_bar.grid_propagate(False)

        self._tab_info_btn = ctk.CTkButton(
            tab_bar, text="Info", corner_radius=0, height=30,
            fg_color=_BG, hover_color=_HOVER,
            font=ctk.CTkFont(size=12),
            command=lambda: self._switch_info_tab("info"),
        )
        self._tab_info_btn.grid(row=0, column=0, sticky="nsew")

        self._tab_files_btn = ctk.CTkButton(
            tab_bar, text="Files", corner_radius=0, height=30,
            fg_color=_INACTIVE, hover_color=_HOVER,
            font=ctk.CTkFont(size=12),
            command=lambda: self._switch_info_tab("files"),
        )
        self._tab_files_btn.grid(row=0, column=1, sticky="nsew")

        self._tab_assets_btn = ctk.CTkButton(
            tab_bar, text="Assets", corner_radius=0, height=30,
            fg_color=_INACTIVE, hover_color=_HOVER,
            font=ctk.CTkFont(size=12),
            command=lambda: self._switch_info_tab("assets"),
        )
        self._tab_assets_btn.grid(row=0, column=2, sticky="nsew")

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

        self._files_scroll = ctk.CTkScrollableFrame(
            content, fg_color="transparent",
            scrollbar_button_color=("gray70", "gray30"),
            scrollbar_button_hover_color=("gray60", "gray40"),
        )
        self._files_scroll.grid(row=0, column=0, sticky="nsew")
        self._files_scroll.grid_columnconfigure(0, weight=1)
        self._bind_scroll(self._files_scroll, self._files_scroll)
        self._files_scroll.grid_remove()

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
        self._files_mod_name  = None

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
        # Hide all content panes
        self._info_scroll.grid_remove()
        self._files_scroll.grid_remove()
        self._assets_scroll.grid_remove()
        # Reset all tab button colours
        for btn in (self._tab_info_btn, self._tab_files_btn, self._tab_assets_btn):
            btn.configure(fg_color=_INACTIVE)

        if tab == "info":
            self._tab_info_btn.configure(fg_color=_BG)
            self._info_scroll.grid()
        elif tab == "files":
            self._tab_files_btn.configure(fg_color=_BG)
            self._files_scroll.grid()
            if self._focused:
                self._files_mod_name = self._focused
                self._load_files_tab(self._focused)
        else:
            self._tab_assets_btn.configure(fg_color=_BG)
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

    # ── Files tab ─────────────────────────────────────────────────────

    def _load_files_tab(self, mod_name: str):
        for w in self._files_scroll.winfo_children():
            w.destroy()

        ms = self._state["mods"].get(mod_name, {})
        active  = ms.get("symlinks", [])
        parked  = ms.get("disabled_symlinks", [])
        is_enabled = ms.get("enabled", False)
        all_entries = active + parked
        if not all_entries:
            lbl = ctk.CTkLabel(
                self._files_scroll,
                text="No placed files recorded for this mod.\n"
                     "Enable the mod first to see its files here.",
                font=ctk.CTkFont(size=12),
                text_color=("gray55", "gray50"),
            )
            lbl.grid(row=0, column=0, pady=30, padx=12)
            self._bind_scroll(lbl, self._files_scroll)
            return

        from pathlib import Path as _P
        from collections import defaultdict
        game_root      = self._cfg.get("game_root")
        profile        = self._cfg.get("profile", {})
        plugin_exts    = {e.lower() for e in profile.get("plugin_extensions", [])}
        disabled_stems = set(ms.get("disabled_files", []))

        def _rel(link_str: str) -> str:
            p = _P(link_str)
            if game_root:
                try:
                    return str(p.relative_to(game_root))
                except ValueError:
                    pass
            return str(p)

        row = 0

        if plugin_exts:
            # ── Bethesda mode: plugins with toggles + directory summary ──────
            plugins = [e for e in all_entries
                       if _P(e["link"]).suffix.lower() in plugin_exts]
            others  = [e for e in all_entries
                       if _P(e["link"]).suffix.lower() not in plugin_exts]

            if plugins:
                ctk.CTkLabel(
                    self._files_scroll,
                    text="PLUGINS",
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color=("gray45", "gray55"), anchor="w",
                ).grid(row=row, column=0, sticky="w", padx=10, pady=(8, 2))
                row += 1

                for entry in sorted(plugins, key=lambda e: _P(e["link"]).name.lower()):
                    stem     = _P(entry["target"]).stem
                    is_act   = stem not in disabled_stems
                    fname    = _P(entry["link"]).name
                    rf = ctk.CTkFrame(self._files_scroll, fg_color="transparent")
                    rf.grid(row=row, column=0, sticky="ew", padx=8, pady=1)
                    rf.grid_columnconfigure(1, weight=1)
                    sw_var = ctk.BooleanVar(value=is_act)
                    ctk.CTkSwitch(
                        rf, text="", variable=sw_var, width=40,
                        onvalue=True, offvalue=False,
                        command=lambda s=stem, v=sw_var:
                            self._toggle_mod_file(mod_name, s, v),
                    ).grid(row=0, column=0, padx=(0, 8))
                    ctk.CTkLabel(
                        rf, text=fname,
                        font=ctk.CTkFont(size=12),
                        text_color=("gray15", "gray85") if is_act else ("gray55", "gray45"),
                        anchor="w",
                    ).grid(row=0, column=1, sticky="w")
                    self._bind_scroll(rf, self._files_scroll)
                    row += 1

            if others:
                ctk.CTkLabel(
                    self._files_scroll,
                    text="OTHER FILES",
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color=("gray45", "gray55"), anchor="w",
                ).grid(row=row, column=0, sticky="w", padx=10, pady=(12, 2))
                row += 1

                dir_counts: dict = defaultdict(int)
                for entry in others:
                    dir_counts[_P(_rel(entry["link"])).parent].files = \
                        dir_counts.get(_P(_rel(entry["link"])).parent, 0) + 1
                # Simpler: just count per parent dir string
                dir_counts2: dict = defaultdict(int)
                for entry in others:
                    dir_counts2[str(_P(_rel(entry["link"])).parent)] += 1

                for d in sorted(dir_counts2):
                    n = dir_counts2[d]
                    lbl = ctk.CTkLabel(
                        self._files_scroll,
                        text=f"  {d}  ({n} file{'s' if n != 1 else ''})",
                        font=ctk.CTkFont(family="monospace", size=11),
                        text_color=("gray40", "gray65"), anchor="w",
                    )
                    lbl.grid(row=row, column=0, sticky="w", padx=8, pady=1)
                    self._bind_scroll(lbl, self._files_scroll)
                    row += 1

        else:
            # ── UE4/UE5 mode: group by stem so .pak/.utoc/.ucas share a row ──
            groups: dict = defaultdict(list)
            for entry in all_entries:
                stem = _P(entry["target"]).stem
                groups[stem].append(entry)

            for stem in sorted(groups):
                entries  = groups[stem]
                is_act   = stem not in disabled_stems
                exts     = sorted({_P(e["target"]).suffix for e in entries})
                dest_dir = str(_P(entries[0]["link"]).parent)
                if game_root:
                    try:
                        dest_dir = str(_P(dest_dir).relative_to(game_root))
                    except ValueError:
                        pass

                rf = ctk.CTkFrame(self._files_scroll, fg_color="transparent")
                rf.grid(row=row, column=0, sticky="ew", padx=8, pady=2)
                rf.grid_columnconfigure(1, weight=1)

                sw_var = ctk.BooleanVar(value=is_act)
                ctk.CTkSwitch(
                    rf, text="", variable=sw_var, width=40,
                    onvalue=True, offvalue=False,
                    command=lambda s=stem, v=sw_var:
                        self._toggle_mod_file(mod_name, s, v),
                ).grid(row=0, column=0, padx=(0, 8))
                ctk.CTkLabel(
                    rf, text=stem,
                    font=ctk.CTkFont(size=12),
                    text_color=("gray15", "gray85") if is_act else ("gray55", "gray45"),
                    anchor="w",
                ).grid(row=0, column=1, sticky="w")
                ctk.CTkLabel(
                    rf, text="  ".join(exts),
                    font=ctk.CTkFont(size=10),
                    text_color=("gray55", "gray50"), anchor="e",
                ).grid(row=0, column=2, padx=(4, 0))

                dest_lbl = ctk.CTkLabel(
                    self._files_scroll, text=dest_dir,
                    font=ctk.CTkFont(size=10),
                    text_color=("gray55", "gray45"), anchor="w",
                )
                dest_lbl.grid(row=row + 1, column=0, sticky="w",
                               padx=(60, 8), pady=(0, 4))

                self._bind_scroll(rf,       self._files_scroll)
                self._bind_scroll(dest_lbl, self._files_scroll)
                row += 2

    def _toggle_mod_file(self, mod_name: str, stem: str, var: "ctk.BooleanVar"):
        from mm.mods import toggle_mod_file_stem
        enable = var.get()
        action = "enabled" if enable else "disabled"
        try:
            toggle_mod_file_stem(mod_name, stem, enable, self._cfg, self._state)
            _gc._save_state(self._state)   # save via GUI path — mm.config.STATE_FILE is wrong here
            self._log_write(f"[file-{action}]  {stem}  ({mod_name})\n")
        except Exception as e:
            self._log_write(f"[error] Could not toggle {stem}: {e}\n")
        # Refresh the tab to reflect the new state
        self._files_mod_name = None
        self._load_files_tab(mod_name)

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
        import time as _time
        _now = _time.monotonic()
        # Skip rebuild if the same panel was built for the same mod within 150 ms.
        # Background callbacks (Nexus sort flush, _maybe_refresh_nexus) fire many
        # times per click; coalescing them here prevents the flash-on-every-completion
        # effect regardless of which code path triggered the call.
        if (name is not None
                and name == getattr(self, "_info_panel_last_name", None)
                and _now - getattr(self, "_info_panel_last_ts", 0.0) < 0.15):
            return
        self._info_panel_last_name = name
        self._info_panel_last_ts   = _now
        self._update_action_buttons()
        self._cancel_img_overlay()
        for w in self._info_scroll.winfo_children():
            w.destroy()
        self._folder_btn.grid_remove()
        self._nexus_btn.grid_remove()
        self._info_img_ref = None
        # Reset tab caches so they reload for the new mod
        self._assets_mod_dir = None
        self._files_mod_name = None
        for w in self._assets_scroll.winfo_children():
            w.destroy()
        for w in self._files_scroll.winfo_children():
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

        # For mods that share a Nexus page (same mod ID, different files), the
        # API returns the same mod-page name for both.  Use the folder-derived
        # name instead — it captures file-specific descriptions like "CBBE Patch v2".
        dup_nids = getattr(self, "_dup_nids", set())
        if nid and nid in dup_nids:
            folder_title = _display_name(name)
            title = folder_title if folder_title and folder_title != name else (info.get("name") or disp)
        else:
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

        # ── Conflict note ─────────────────────────────────────────────
        from mm.conflicts import get_conflicts_for_mod, get_rule as _get_rule
        _rules = self._state.get("conflict_rules", [])
        all_conflict_pairs = get_conflicts_for_mod(name, self._state)
        if all_conflict_pairs:
            n_unresolved = sum(
                1 for o, _ in all_conflict_pairs
                if _get_rule(name, o, _rules) is None
            )
            if n_unresolved:
                note = f"⚠  {n_unresolved} unresolved conflict{'s' if n_unresolved > 1 else ''}"
                col  = ("#c07010", "#c08030")
            else:
                note = f"✓  {len(all_conflict_pairs)} conflict{'s' if len(all_conflict_pairs) > 1 else ''} — rules set"
                col  = ("gray50", "gray55")
            ctk.CTkButton(
                meta, text=note + "  →  Conflicts tab",
                height=22, fg_color="transparent", hover=False,
                font=ctk.CTkFont(size=11, underline=True),
                text_color=col, anchor="w",
                command=lambda: (self._page_nav.set("Conflicts"),
                                 self._on_page_select("Conflicts")),
            ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 0)); r += 1

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

        # If assets/files tab is already visible, reload it for the new mod now
        if self._active_info_tab == "assets" and self._folder_path:
            self._assets_mod_dir = self._folder_path
            self._load_assets_tab(self._folder_path)
        elif self._active_info_tab == "files" and name:
            self._files_mod_name = name
            self._load_files_tab(name)

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

        # External tools row (shown only when profile defines external_tools)
        self._tools_frame = ctk.CTkFrame(outer, fg_color="transparent")
        self._tools_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        self._tools_frame.grid_remove()   # hidden until tools are detected

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
            exists = name in self._on_disk
            if ms.get("enabled"):
                self._btn_mod_action.configure(
                    state="normal", text="Disable",
                    fg_color=("gray52", "gray38"), hover_color=("gray42", "gray46"),
                )
                self._tt_mod_action.update("Disable this mod (removes its symlinks)")
            elif exists:
                self._btn_mod_action.configure(
                    state="normal", text="Enable",
                    fg_color=("#1a5a9a", "#1a5a9a"), hover_color=("#1a6aaa", "#1a6aaa"),
                )
                self._tt_mod_action.update("Enable this mod (creates symlinks into the game folder)")
            else:
                # Ghost entry: tracked in state but folder deleted from disk
                self._btn_mod_action.configure(
                    state="disabled", text="Enable",
                    fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
                )
                self._tt_mod_action.update("Mod folder not found — use Uninstall to remove this entry")
            self._btn_uninstall.configure(
                state="normal",
                text="Remove" if not exists else "Uninstall",
            )

    def _mod_action(self):
        """Enable, disable, or extract the focused mod depending on its current state."""
        name = self._focused
        if not name:
            return
        if name in self._archived:
            self._run_interactive(["--extract", name], on_done=self.refresh_mods)
        else:
            ms = self._state["mods"].get(name, {})
            self._stage_mod(name, not ms.get("enabled", False))
            self.refresh_mods()

    def _fomod_or_enable(self, mod_name: str, on_done=None):
        """
        Central enable entry point used by the Enable button, toggle switch, and
        batch-enable. Checks for a FOMOD installer and routes accordingly.
        """
        if on_done is None:
            on_done = self.refresh_mods

        mod_dir = self._cfg.get("mods_dir")
        if mod_dir is None:
            self._run_interactive(["--enable", mod_name], on_done=on_done)
            return

        mod_path = mod_dir / mod_name
        if not mod_path.is_dir():
            self._run_interactive(["--enable", mod_name], on_done=on_done)
            return

        from mm.fomod import find_fomod_xml
        fomod_xml = find_fomod_xml(mod_path)
        if fomod_xml:
            self._enable_with_fomod(mod_name, on_done=on_done)
        else:
            def _on_done_with_recs():
                if on_done:
                    on_done()
                self._check_and_show_recommendations(mod_name, mod_path)
            self._run_interactive(["--enable", mod_name], on_done=_on_done_with_recs)

    def _enable_with_fomod(self, mod_name: str, on_done=None):
        """Show the FOMOD wizard then install the selected files in a background thread."""
        from mm.fomod import find_fomod_xml, parse_fomod
        from mm.gui.fomod_dialog import FomodWizard
        from mm.mods import enable_mod

        if on_done is None:
            on_done = self.refresh_mods

        mod_dir = self._cfg["mods_dir"] / mod_name
        fomod_xml = find_fomod_xml(mod_dir)
        if not fomod_xml:
            self._run_interactive(["--enable", mod_name], on_done=on_done)
            return

        try:
            fomod_config = parse_fomod(fomod_xml)
        except Exception as e:
            self._log_write(f"[fomod] Failed to parse installer: {e}\n")
            self._run_interactive(["--enable", mod_name], on_done=on_done)
            return

        # No install steps → only requiredInstallFiles / conditional patterns.
        # Skip the wizard entirely and install silently with empty selections.
        if not fomod_config.steps:
            selections = {}
        else:
            stored = self._state["mods"].get(mod_name, {}).get("fomod_selections")
            initial = {k: v for k, v in stored.items()} if stored else None

            wizard = FomodWizard(self, fomod_config, mod_dir, initial_selections=initial)
            self.wait_window(wizard)

            if wizard.result is None:
                self._log_write(f"[cancelled] FOMOD wizard cancelled for {mod_name}\n")
                return

            selections = wizard.result

        def fomod_callback(config, stored_sel=None):
            return selections

        def _worker():
            try:
                # For Bethesda-engine games with Data/ anchor rules, game_tree is not
                # needed — anchor rules route every file without a full directory scan.
                game_tree = set() if self._profile and self._profile.get("install_rules") else None
                enable_mod(mod_name, self._cfg, self._state,
                           game_tree=game_tree, fomod_callback=fomod_callback)
                _gc._save_state(self._state)
            except Exception as e:
                self.after(0, self._log_write, f"[error] {e}\n")
            finally:
                self.after(200, on_done)
                recs = _gather_recommendations(mod_name, mod_dir, self._cfg)
                if recs:
                    self.after(400, self._show_recommendations, mod_name, recs)

        self._log_write(f"\n[fomod] Installing {mod_name} with wizard selections…\n")
        threading.Thread(target=_worker, daemon=True).start()

    def _check_and_show_recommendations(self, mod_name: str, mod_path):
        """Merge mm_metadata.json + Nexus requirements in a background thread."""
        cfg_snapshot = dict(self._cfg)

        def _bg():
            recs = _gather_recommendations(mod_name, mod_path, cfg_snapshot)
            if recs:
                self.after(200, self._show_recommendations, mod_name, recs)

        threading.Thread(target=_bg, daemon=True).start()

    def _show_recommendations(self, mod_name: str, recs: list):
        """Show a non-blocking notice listing recommended companion mods."""
        import customtkinter as ctk

        win = ctk.CTkToplevel(self)
        win.title("Recommended Mods")
        win.geometry("520x300")
        win.resizable(True, True)
        win.transient(self)
        win.grab_set()
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            win,
            text=f'Mods recommended alongside  “{mod_name}”:',
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 6))

        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=0)
        scroll.grid_columnconfigure(0, weight=1)

        for i, rec in enumerate(recs):
            card = ctk.CTkFrame(scroll, fg_color=("gray88", "gray16"), corner_radius=6)
            card.grid(row=i, column=0, sticky="ew", padx=10, pady=(0, 8))
            card.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                card,
                text=rec.get("name", ""),
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))

            reason = rec.get("reason", "")
            if reason:
                ctk.CTkLabel(
                    card,
                    text=reason,
                    font=ctk.CTkFont(size=11),
                    text_color=("gray45", "gray60"),
                    anchor="w",
                    wraplength=460,
                    justify="left",
                ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 10))

        ctk.CTkButton(
            win, text="Dismiss", width=100, command=win.destroy,
        ).grid(row=2, column=0, pady=10)

        win.after(50, lambda: (
            win.deiconify(),
            win.lift(),
            win.focus_force(),
        ))

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

    # ── Steam launch ──────────────────────────────────────────────────

    def _open_asset_search(self):
        from .asset_search import AssetSearchWindow
        AssetSearchWindow(self, self._state, mods_dir=self._cfg.get("mods_dir"))

    def _launch_steam(self):
        app_id = self._profile.get("steam_app_id") if self._profile else None
        if not app_id:
            tkmsgbox.showwarning(
                "No Steam App ID",
                "This game profile does not have a steam_app_id set.\n"
                "Add  \"steam_app_id\": \"<id>\"  to its profile JSON.",
            )
            return
        try:
            subprocess.Popen(["xdg-open", f"steam://run/{app_id}"])
        except FileNotFoundError:
            try:
                subprocess.Popen(["steam", f"steam://run/{app_id}"])
            except FileNotFoundError:
                tkmsgbox.showerror("Steam Not Found",
                                   "Could not find 'steam' or 'xdg-open'.\n"
                                   "Make sure Steam is installed.")

    def _launch_via_script_extender(self):
        from mm.external_tools import launch_script_extender
        ok, msg = launch_script_extender(
            self._profile, self._cfg["game_root"], self._cfg)
        if ok:
            self._log_write(f"[launch] {msg}\n")
        else:
            tkmsgbox.showerror("Launch Error", msg, parent=self)

    def _has_script_extender(self) -> bool:
        se_exe = (self._profile or {}).get("script_extender_exe", "")
        game_root = self._cfg.get("game_root")
        if not se_exe or not game_root:
            return False
        return (game_root / se_exe).exists()

    def _update_launch_button(self):
        """Show or hide the Launch button; switch to F4SE/SKSE if installed."""
        has_id = bool((self._profile or {}).get("steam_app_id"))
        has_se = self._has_script_extender()
        se_name = ((self._profile or {}).get("script_extender_exe", "").split(".")[0].upper()
                   or "Script Extender")

        if has_se:
            self._btn_launch_steam.configure(
                text=f"▶  Launch via {se_name}",
                command=self._launch_via_script_extender,
            )
        else:
            self._btn_launch_steam.configure(
                text="▶  Launch Game (Steam)",
                command=self._launch_steam,
            )

        if has_id or has_se:
            self._btn_launch_steam.grid()
        else:
            self._btn_launch_steam.grid_remove()

    def _refresh_tools_buttons(self):
        """Rebuild the external tools button bar below the mod action buttons."""
        if not hasattr(self, "_tools_frame"):
            return
        for w in self._tools_frame.winfo_children():
            w.destroy()

        profile = getattr(self, "_profile", {}) or {}
        tools = profile.get("external_tools", [])
        if not tools:
            self._tools_frame.grid_remove()
            return

        from mm.external_tools import get_tool_buttons, launch_tool
        mods_dir = self._cfg.get("mods_dir")
        game_root = self._cfg.get("game_root")
        tool_list = get_tool_buttons(profile, mods_dir, game_root, self._cfg)

        if not tool_list:
            self._tools_frame.grid_remove()
            return

        self._tools_frame.grid()
        for col, tinfo in enumerate(tool_list):
            btn = ctk.CTkButton(
                self._tools_frame,
                text=tinfo["name"],
                height=28,
                font=ctk.CTkFont(size=11),
                fg_color=("gray72", "gray30") if tinfo["available"] else ("gray80", "gray22"),
                hover_color=("gray62", "gray38") if tinfo["available"] else ("gray75", "gray25"),
                text_color=("gray10", "gray90") if tinfo["available"] else ("gray55", "gray55"),
                command=(lambda tc=tinfo["tool_cfg"]:
                         self._launch_external_tool(tc)) if tinfo["available"] else None,
            )
            btn.grid(row=0, column=col, padx=(0, 4), sticky="w")

    def _launch_external_tool(self, tool_cfg: dict):
        from mm.external_tools import launch_tool
        mods_dir = self._cfg.get("mods_dir")
        game_root = self._cfg.get("game_root")
        ok, msg = launch_tool(tool_cfg, mods_dir, game_root, self._cfg,
                              profile=self._profile)
        if ok:
            self._log_write(f"[tool] {msg}\n")
        else:
            tkmsgbox.showerror(f"{tool_cfg.get('name', 'Tool')} Error", msg, parent=self)

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

        # Spinner state (for profile-switching animation)
        self._spinner_after_id: str | None = None
        self._spinner_frame_idx: int = 0

    _SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    _SPINNER_MSG    = "Loading…"

    def _spinner_tick(self):
        """Advance spinner one frame; reschedule itself until stopped."""
        if self._spinner_after_id is None:
            return   # stopped
        f = self._SPINNER_FRAMES[self._spinner_frame_idx % len(self._SPINNER_FRAMES)]
        self._spinner_frame_idx += 1
        self._busy.configure(text=f"  {f}  {self._SPINNER_MSG}")
        self._spinner_after_id = self.after(80, self._spinner_tick)

    def _spinner_start(self, msg: str = "Loading…"):
        self._SPINNER_MSG = msg
        if self._spinner_after_id is not None:
            return   # already running
        self._spinner_frame_idx = 0
        self._spinner_after_id = self.after(0, self._spinner_tick)

    def _spinner_stop(self):
        if self._spinner_after_id is not None:
            self.after_cancel(self._spinner_after_id)
            self._spinner_after_id = None
        # Only clear _busy if it's showing the spinner (not a subprocess message)
        current = self._busy.cget("text")
        if self._SPINNER_MSG in current:
            self._busy.configure(text="")

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

        # ── Plugins (Bethesda games only) ─────────────────────────────
        if self._profile and self._profile.get("plugin_extensions"):
            row = _section_header("PLUGINS", row)

            # Plugins.txt file picker
            f = ctk.CTkFrame(scroll, fg_color="transparent")
            f.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 2))
            f.grid_columnconfigure(1, weight=1)
            row += 1

            ctk.CTkLabel(f, text="Plugins.txt", width=120, anchor="w",
                         font=ctk.CTkFont(size=12),
                         ).grid(row=0, column=0, sticky="w", pady=4)

            profile_default = self._profile.get("plugins_txt_path", "")
            plugins_txt_var = ctk.StringVar(
                value=game_section.get("plugins_txt_path", "") or profile_default)
            path_vars["plugins_txt_path"] = plugins_txt_var
            ctk.CTkEntry(f, textvariable=plugins_txt_var,
                         placeholder_text="Path to Plugins.txt",
                         ).grid(row=0, column=1, sticky="ew", padx=(0, 6))

            def _browse_plugins_txt(v=plugins_txt_var):
                p = tkinter.filedialog.askopenfilename(
                    title="Select Plugins.txt",
                    filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
                    parent=win,
                )
                if p:
                    v.set(p)

            ctk.CTkButton(f, text="Browse…", width=80, height=28,
                          fg_color=("gray72", "gray30"),
                          hover_color=("gray62", "gray38"),
                          font=ctk.CTkFont(size=11),
                          command=_browse_plugins_txt,
                          ).grid(row=0, column=2)

            ctk.CTkLabel(
                scroll, text="Usually inside your Proton/Wine prefix at "
                             "AppData/Local/<Game>/Plugins.txt",
                font=ctk.CTkFont(size=10),
                text_color=("gray55", "gray50"), anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=32, pady=(0, 4))
            row += 1

        # ── External Tools (Wine/Proton) ───────────────────────────────
        if self._profile and self._profile.get("external_tools"):
            wine_tools = [t for t in self._profile["external_tools"]
                          if t.get("launcher") in ("wine", "proton")]
            if wine_tools:
                row = _section_header("EXTERNAL TOOLS", row)

                f = ctk.CTkFrame(scroll, fg_color="transparent")
                f.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 2))
                f.grid_columnconfigure(1, weight=1)
                row += 1

                ctk.CTkLabel(f, text="Wine/Proton\nCommand", width=120, anchor="w",
                             font=ctk.CTkFont(size=12),
                             ).grid(row=0, column=0, sticky="w", pady=4)

                wine_var = ctk.StringVar(value=game_section.get("wine_cmd", "wine"))
                path_vars["wine_cmd"] = wine_var
                ctk.CTkEntry(f, textvariable=wine_var,
                             placeholder_text="wine",
                             ).grid(row=0, column=1, sticky="ew", padx=(0, 6))

                ctk.CTkLabel(
                    scroll,
                    text="Command used to run Windows .exe tools (BodySlide, Nemesis…).\n"
                         "Use 'wine', or a full path to a Proton binary.",
                    font=ctk.CTkFont(size=10),
                    text_color=("gray55", "gray50"), anchor="w", justify="left",
                ).grid(row=row, column=0, sticky="w", padx=32, pady=(0, 4))
                row += 1

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
            game_data = {
                "game_root":      path_vars["game_root"].get().strip(),
                "mods_dir":       path_vars["mods_dir"].get().strip(),
                "compressed_dir": path_vars["compressed_dir"].get().strip(),
                "nexus_api_key":  api_var.get().strip(),
            }
            # Optional Bethesda fields (only present if profile has them)
            if "plugins_txt_path" in path_vars:
                v = path_vars["plugins_txt_path"].get().strip()
                if v:
                    game_data["plugins_txt_path"] = v
            if "wine_cmd" in path_vars:
                v = path_vars["wine_cmd"].get().strip()
                if v:
                    game_data["wine_cmd"] = v
            new_raw.setdefault("games", {})[current_game] = game_data
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
