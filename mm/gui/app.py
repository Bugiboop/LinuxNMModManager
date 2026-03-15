import json
import sys
import threading
import tkinter
import tkinter.filedialog
import webbrowser
from tkinter import messagebox as tkmsgbox
from pathlib import Path

import customtkinter as ctk

try:
    from PIL import Image as PILImage, ImageTk as PILImageTk
    _PIL = True
except ImportError:
    _PIL = False

import mm.gui.config as _gc
from mm.gui.config import CONFIG_FILE, _PROFILES_DIR
from .sidebar import SidebarMixin
from .panels import PanelsMixin
from .runner import RunnerMixin
from .nexus import _nexus_id, _nexus_id_cache, _display_name
from .dialogs import _InteractiveDialog, _detect_prompt
from .info import _read_mod_info
from .constants import _ARCHIVE_EXTENSIONS, _CARD_NORMAL, _CARD_FOCUSED, _CARD_CHECKED, _CARD_H, _SEP_H, _V_PAD
from .nexus import _nexus_api_fetch, _nexus_download_image

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class ModManagerApp(SidebarMixin, PanelsMixin, RunnerMixin, ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("Mod Manager")
        self.geometry("1260x780")
        self.minsize(960, 560)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._switches:      dict     = {}
        self._cards:         dict     = {}
        self._checkboxvars:  dict     = {}   # name → BooleanVar for batch-select
        self._selected:      set      = set()  # batch-checked mods
        self._focused:       str|None = None   # mod whose info is shown
        self._all_mods:      list     = []     # ordered mod names for arrow-key nav
        self._on_disk:       set      = set()
        self._info_img_ref   = None   # prevent GC of CTkImage
        self._img_overlay    = None   # floating zoom window
        self._overlay_imgref = None   # prevent GC of overlay image
        self._overlay_after  = None   # pending show-delay callback
        self._poll_after     = None   # pending cursor-poll callback
        self._nexus_cache:    dict = {}  # nid → fetched API data
        self._nexus_fetching: set  = set()  # nids currently in-flight
        self._archived:       dict = {}  # stem → Path for archives with no extracted folder
        self._name_labels:    dict = {}  # mod name → CTkLabel (for live Nexus name update)
        self._sort_var      = ctk.StringVar(value="Name A→Z")
        self._current_game: str  = "stellar_blade"
        self._profile:      dict = {}
        self._game_var      = ctk.StringVar(value="Stellar Blade")
        self._cfg:            dict = {}  # cached config, updated by refresh_mods
        self._state:          dict = {"mods": {}}  # cached state, updated by refresh_mods
        self._mod_info_cache: dict = {}   # mod_dir path → _read_mod_info result
        self._assets_cache:   dict = {}   # mod_dir path → list of asset strings
        # Virtual mod list state
        self._vlist_items:        list     = []   # item dicts in display order
        self._vlist_yoffs:        list     = []   # y pixel offset for each item
        self._vlist_total_h:      int      = 0    # total canvas scroll height
        self._vlist_widgets:      dict     = {}   # idx → {"frame": CTkFrame, "cid": int}
        self._vlist_populated:    set      = set()# indices whose shells have been filled
        self._vlist_render_after: str|None = None # pending debounce id

        self._build_sidebar()
        self._build_main()
        self._build_statusbar()

        self.bind_all("<Up>",   self._on_arrow_key)
        self.bind_all("<Down>", self._on_arrow_key)

        # Load nexus disk cache in a background thread so the window appears
        # immediately, then populate the mod list once the cache is ready.
        threading.Thread(target=self._preload_nexus_cache, daemon=True).start()

    def _preload_nexus_cache(self):
        """Sync game profiles from GitHub, then read cached Nexus JSON files (background thread)."""
        # Silently download any missing game profiles
        try:
            from mm.profiles import sync_profiles
            downloaded, _ = sync_profiles(_gc._PROFILES_DIR)
            if downloaded:
                self.after(0, lambda d=downloaded: self._log_write(
                    f"[profiles] Downloaded: {', '.join(d)}\n"))
        except Exception:
            pass

        cache = {}
        cache_dir = _gc._NEXUS_CACHE_DIR   # capture at thread-start time
        if cache_dir.exists():
            for _p in cache_dir.glob("*.json"):
                try:
                    _nid  = _p.stem
                    _data = json.loads(_p.read_text())
                    if not _data.get("_cached_image"):
                        _pic_url = _data.get("picture_url", "")
                        if _pic_url:
                            _ext = _pic_url.rsplit(".", 1)[-1].split("?")[0].lower()
                            if _ext not in ("jpg", "jpeg", "png", "webp"):
                                _ext = "jpg"
                            _img = cache_dir / f"{_nid}.{_ext}"
                            if _img.exists():
                                _data["_cached_image"] = str(_img)
                    cache[_nid] = _data
                except Exception:
                    pass
        self.after(0, lambda: self._finish_startup(cache))

    def _finish_startup(self, cache: dict):
        self._nexus_cache.update(cache)
        self.refresh_mods()

    # ── Game switching ─────────────────────────────────────────────────

    def _configured_games(self) -> list:
        """Return [(game_id, display_name), ...] for all games in config."""
        try:
            with open(CONFIG_FILE) as f:
                raw = json.load(f)
        except Exception:
            return [("stellar_blade", "Stellar Blade")]
        if "game_root" in raw:
            return [("stellar_blade", "Stellar Blade")]
        result = []
        for gid in raw.get("games", {}).keys():
            try:
                name = _gc._load_profile(gid).get("name", gid)
            except Exception:
                name = gid
            result.append((gid, name))
        return result or [("stellar_blade", "Stellar Blade")]

    def _on_game_select(self, display_name: str):
        """Called when the user picks a game from the dropdown."""
        for gid, name in self._configured_games():
            if name == display_name:
                self._switch_game(gid)
                return

    def _switch_game(self, game_id: str):
        """Save current_game to config and reload the mod list."""
        if game_id == self._current_game:
            return
        try:
            with open(CONFIG_FILE) as f:
                raw = json.load(f)
            if "game_root" in raw:
                raw = {
                    "current_game": "stellar_blade",
                    "games": {"stellar_blade": {k: v for k, v in raw.items() if k != "theme"}},
                    "theme": raw.get("theme", "dark"),
                }
            raw["current_game"] = game_id
            CONFIG_FILE.write_text(json.dumps(raw, indent=2))
        except Exception as e:
            tkmsgbox.showerror("Switch Game", f"Could not update config:\n{e}")
            return
        self._nexus_cache.clear()
        self._nexus_fetching.clear()
        self.refresh_mods()

    def _add_game_dialog(self):
        """Open a dialog to add a new game profile to config."""
        all_profile_ids  = _gc._available_profile_ids()
        configured_ids   = {gid for gid, _ in self._configured_games()}
        available_ids    = [p for p in all_profile_ids if p not in configured_ids]

        if not available_ids:
            tkmsgbox.showinfo("Add Game",
                              "All available game profiles are already configured.\n"
                              "Add a new profile JSON to game_profiles/ to support more games.")
            return

        win = ctk.CTkToplevel(self)
        win.title("Add Game")
        win.geometry("480x240")
        win.resizable(False, False)
        win.transient(self)
        win.withdraw()
        win.grid_columnconfigure(0, weight=1)

        inner = ctk.CTkFrame(win, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="nsew", padx=20, pady=16)
        inner.grid_columnconfigure(1, weight=1)

        # Profile selector
        ctk.CTkLabel(inner, text="Game Profile", width=110, anchor="w",
                     font=ctk.CTkFont(size=12)).grid(row=0, column=0, sticky="w", pady=6)
        profile_names = [_gc._load_profile(p).get("name", p) for p in available_ids]
        profile_var = ctk.StringVar(value=profile_names[0] if profile_names else "")
        ctk.CTkOptionMenu(inner, values=profile_names, variable=profile_var,
                          width=200, height=28,
                          font=ctk.CTkFont(size=12)).grid(row=0, column=1, sticky="w")

        # Game root path
        ctk.CTkLabel(inner, text="Game Root", width=110, anchor="w",
                     font=ctk.CTkFont(size=12)).grid(row=1, column=0, sticky="w", pady=6)
        root_var = ctk.StringVar()
        root_row = ctk.CTkFrame(inner, fg_color="transparent")
        root_row.grid(row=1, column=1, sticky="ew")
        root_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(root_row, textvariable=root_var,
                     placeholder_text="Path to game installation"
                     ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(root_row, text="Browse…", width=80, height=28,
                      fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
                      font=ctk.CTkFont(size=11),
                      command=lambda: root_var.set(
                          tkinter.filedialog.askdirectory(title="Game Root", parent=win) or root_var.get()
                      )).grid(row=0, column=1)

        def _save():
            chosen_name = profile_var.get()
            game_root   = root_var.get().strip()
            if not game_root:
                tkmsgbox.showerror("Add Game", "Game Root cannot be empty.", parent=win)
                return
            # Map display name back to profile id
            game_id = available_ids[profile_names.index(chosen_name)]
            try:
                with open(CONFIG_FILE) as f:
                    raw = json.load(f)
                if "game_root" in raw:
                    raw = {
                        "current_game": "stellar_blade",
                        "games": {"stellar_blade": {k: v for k, v in raw.items() if k != "theme"}},
                        "theme": raw.get("theme", "dark"),
                    }
                raw.setdefault("games", {})[game_id] = {"game_root": game_root}
                raw["current_game"] = game_id
                CONFIG_FILE.write_text(json.dumps(raw, indent=2))
            except Exception as e:
                tkmsgbox.showerror("Add Game", f"Could not save config:\n{e}", parent=win)
                return
            win.destroy()
            self._nexus_cache.clear()
            self._nexus_fetching.clear()
            self.refresh_mods()

        btn_bar = ctk.CTkFrame(win, fg_color=("gray86", "gray17"), corner_radius=0)
        btn_bar.grid(row=1, column=0, sticky="ew")
        btn_bar.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(btn_bar, text="Add", width=100, height=34,
                      command=_save).grid(row=0, column=1, padx=8, pady=8, sticky="e")
        ctk.CTkButton(btn_bar, text="Cancel", width=100, height=34,
                      fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
                      command=win.destroy).grid(row=0, column=2, padx=(0, 12), pady=8)

        def _show():
            win.deiconify(); win.grab_set(); win.lift(); win.focus_force()
        win.after(50, _show)

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
        win.geometry("580x620")
        win.minsize(480, 500)
        win.resizable(True, True)
        win.transient(self)
        win.withdraw()          # hide until fully built (prevents blank flash on Linux)
        self._settings_win = win
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(0, weight=1)

        # Center over main window
        self.update_idletasks()
        wx = self.winfo_x() + (self.winfo_width()  - 580) // 2
        wy = self.winfo_y() + (self.winfo_height() - 620) // 2
        win.geometry(f"580x620+{wx}+{wy}")

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

        api_var   = ctk.StringVar(value=raw.get("nexus_api_key", game_section.get("nexus_api_key", "")))
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

        # ── Game Profiles ─────────────────────────────────────────────
        row = _section_header("GAME PROFILES", row)

        prof_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        prof_frame.grid(row=row, column=0, sticky="ew", padx=16, pady=(4, 0))
        row += 1

        prof_status = ctk.CTkLabel(prof_frame, text="",
                                   font=ctk.CTkFont(size=11),
                                   text_color=("gray50", "gray55"))

        def _update_profiles():
            prof_status.configure(text="Checking GitHub…")
            prof_frame.update_idletasks()

            def _do():
                try:
                    from mm.profiles import sync_profiles
                    downloaded, failed = sync_profiles(_gc._PROFILES_DIR, force=True)
                    if downloaded:
                        msg = f"Updated: {', '.join(downloaded)}"
                    elif failed:
                        msg = f"Failed: {', '.join(failed)}"
                    else:
                        msg = "All profiles are up to date."
                except Exception as exc:
                    msg = f"Error: {exc}"
                self.after(0, lambda: prof_status.configure(text=msg))

            threading.Thread(target=_do, daemon=True).start()

        ctk.CTkButton(
            prof_frame, text="Update Game Profiles", width=160, height=28,
            fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
            font=ctk.CTkFont(size=11),
            command=_update_profiles,
        ).grid(row=0, column=0, sticky="w")
        prof_status.grid(row=0, column=1, sticky="w", padx=(12, 0))

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
            new_raw["nexus_api_key"] = api_var.get().strip()
            new_raw.setdefault("games", {})[current_game] = {
                "game_root":      path_vars["game_root"].get().strip(),
                "mods_dir":       path_vars["mods_dir"].get().strip(),
                "compressed_dir": path_vars["compressed_dir"].get().strip(),
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

    # ── Mod list ──────────────────────────────────────────────────────

    def refresh_mods(self):
        try:
            cfg   = _gc._load_config()
            state = _gc._load_state()
        except Exception as e:
            self._log_write(f"[error] Could not load config/state: {e}\n")
            return
        self._cfg          = cfg
        self._state        = state
        self._current_game = cfg.get("game_id", "stellar_blade")
        self._profile      = _gc._load_profile(self._current_game)

        # Sync game selector dropdown
        games = self._configured_games()
        names = [name for _, name in games]
        current_name = self._profile.get("name", self._current_game)
        self._game_menu.configure(values=names)
        self._game_var.set(current_name)
        self.title(f"{current_name} — Mod Manager")
        self._mod_info_cache.clear()
        self._assets_cache.clear()

        mods_dir = cfg["mods_dir"]
        self._on_disk = (
            {d.name for d in mods_dir.iterdir() if d.is_dir()}
            if mods_dir.exists() else set()
        )
        tracked  = set(state["mods"].keys())
        sort_mode = self._sort_var.get()
        def _disp_key(n):
            return self._get_disp_name(n).lower()
        if sort_mode == "Name Z→A":
            all_mods = sorted(self._on_disk | tracked, key=_disp_key, reverse=True)
        elif sort_mode == "Enabled first":
            all_mods = sorted(self._on_disk | tracked,
                              key=lambda n: (not state["mods"].get(n, {}).get("enabled", False),
                                             _disp_key(n)))
        elif sort_mode == "Disabled first":
            all_mods = sorted(self._on_disk | tracked,
                              key=lambda n: (state["mods"].get(n, {}).get("enabled", False),
                                             _disp_key(n)))
        else:  # "Name A→Z"
            all_mods = sorted(self._on_disk | tracked, key=_disp_key)

        # Archives in compressed/ that haven't been extracted yet
        compressed_dir = cfg.get("compressed_dir")
        self._archived = {}
        if compressed_dir and compressed_dir.exists():
            for p in sorted(compressed_dir.iterdir()):
                if p.is_file() and p.suffix.lower() in _ARCHIVE_EXTENSIONS:
                    if p.stem not in self._on_disk and p.stem not in tracked:
                        self._archived[p.stem] = p

        self._all_mods = all_mods + sorted(self._archived.keys())

        self._selected &= self._on_disk
        if self._focused not in self._on_disk and \
                self._focused not in self._archived:
            self._focused = None

        # Destroy all currently-rendered virtual cards
        for w_data in self._vlist_widgets.values():
            try:
                w_data["frame"].destroy()
            except Exception:
                pass
        self._vlist_widgets.clear()
        self._vlist_populated.clear()
        self._switches.clear()
        self._cards.clear()
        self._checkboxvars.clear()
        self._name_labels.clear()

        # Build the virtual items list
        items: list  = []
        yoffs: list  = []
        y = 0
        enabled = 0
        for name in all_mods:
            ms       = state["mods"].get(name, {})
            is_on    = ms.get("enabled", False)
            if is_on:
                enabled += 1
            items.append({
                "type":     "mod",
                "name":     name,
                "disp":     self._get_disp_name(name),
                "is_on":    is_on,
                "symlinks": len(ms.get("symlinks", [])),
                "exists":   name in self._on_disk,
            })
            yoffs.append(y)
            y += _CARD_H + _V_PAD

        if self._archived:
            items.append({"type": "sep", "name": "AVAILABLE ARCHIVES"})
            yoffs.append(y)
            y += _SEP_H + _V_PAD
            for stem in sorted(self._archived.keys()):
                items.append({
                    "type": "archive",
                    "name": stem,
                    "disp": _display_name(stem),
                })
                yoffs.append(y)
                y += _CARD_H + _V_PAD

        self._vlist_items   = items
        self._vlist_yoffs   = yoffs
        self._vlist_total_h = y
        self._vlist_canvas.configure(scrollregion=(0, 0, 0, max(y, 1)))

        self._count_label.configure(text=f"{enabled}/{len(all_mods)}")
        self._status.configure(
            text=f"{enabled} of {len(all_mods)} mod(s) enabled  ·  "
                 f"{len(self._on_disk)} on disk"
                 + (f"  ·  {len(self._archived)} archive(s) ready"
                    if self._archived else "")
        )
        self._update_selection_ui()

        # Kick off background Nexus fetches for all mods with a known ID
        api_key = cfg.get("nexus_api_key", "")
        if api_key:
            for mod_name in list(all_mods) + list(self._archived.keys()):
                nid = _nexus_id(mod_name)
                if nid and nid not in self._nexus_cache \
                        and nid not in self._nexus_fetching:
                    self._nexus_fetching.add(nid)
                    threading.Thread(
                        target=self._bg_fetch_nexus,
                        args=(nid, api_key),
                        daemon=True,
                    ).start()

        # Phase 1: batch-create all outer card shells (deferred so window stays
        # responsive). Visible shells are populated immediately in _vlist_render.
        self.after(0, lambda: self._vlist_batch_shells(0))

        self._update_info_panel(self._focused)

    # ── Nexus API background fetch ────────────────────────────────────

    def _bg_fetch_nexus(self, nid: str, api_key: str):
        """Fetch mod data from Nexus API in a background thread, then refresh."""
        cache_dir = _gc._NEXUS_CACHE_DIR   # capture at thread-start time
        cache_dir.mkdir(exist_ok=True)
        json_path = cache_dir / f"{nid}.json"

        # Try disk cache first (avoids re-fetching across sessions)
        data = None
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text())
            except Exception:
                pass

        if data is None:
            data = _nexus_api_fetch(nid, api_key)
            if data and not data.get("_error"):
                try:
                    json_path.write_text(json.dumps(data))
                except Exception:
                    pass

        if data and not data.get("_error"):
            # Download cover image if not already cached
            pic_url = data.get("picture_url", "")
            if pic_url:
                ext = pic_url.rsplit(".", 1)[-1].split("?")[0].lower()
                if ext not in ("jpg", "jpeg", "png", "webp"):
                    ext = "jpg"
                img_path = cache_dir / f"{nid}.{ext}"
                if not img_path.exists():
                    _nexus_download_image(pic_url, img_path)
                if img_path.exists():
                    data["_cached_image"] = str(img_path)

        self._nexus_cache[nid] = data or {"_error": "no data"}
        self._nexus_fetching.discard(nid)
        # Refresh the panel if this mod is still focused
        self.after(0, lambda: self._maybe_refresh_nexus(nid))

    def _get_disp_name(self, name: str) -> str:
        """Return Nexus mod name if cached, otherwise cleaned folder name."""
        nid = _nexus_id(name)
        if nid:
            nd = self._nexus_cache.get(nid)
            if nd and not nd.get("_error") and nd.get("name"):
                return nd["name"]
        return _display_name(name)

    def _maybe_refresh_nexus(self, nid: str):
        # Update any visible card labels for this nid
        nd = self._nexus_cache.get(nid)
        if nd and not nd.get("_error") and nd.get("name"):
            nexus_name = nd["name"]
            for mod_name, lbl in list(self._name_labels.items()):
                if _nexus_id(mod_name) == nid:
                    try:
                        if lbl.winfo_exists():
                            lbl.configure(text=nexus_name)
                    except Exception:
                        pass
            # Re-sort if a new mod name just arrived (only uncached mods reach here)
            if self._sort_var.get() in ("Name A→Z", "Name Z→A"):
                self.refresh_mods()
        # Refresh info panel if this mod is focused
        if self._focused and _nexus_id(self._focused) == nid:
            self._update_info_panel(self._focused)


def main():
    app = ModManagerApp()
    app.mainloop()
