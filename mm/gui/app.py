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
from .downloads import DownloadsMixin
from .plugins_panel import PluginsPanelMixin
from .conflicts_panel import ConflictsPanelMixin
from .ini_panel import IniPanelMixin
from .nexus import _nexus_id, _nexus_id_cache, _display_name, _nexus_file_version
from .dialogs import _InteractiveDialog, _detect_prompt
from .info import _read_mod_info
from .constants import _ARCHIVE_EXTENSIONS, _CARD_NORMAL, _CARD_FOCUSED, _CARD_CHECKED, _CARD_H, _SEP_H, _V_PAD
from .nexus import _nexus_api_fetch, _nexus_download_image

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class ModManagerApp(SidebarMixin, PanelsMixin, RunnerMixin, DownloadsMixin, PluginsPanelMixin, ConflictsPanelMixin, IniPanelMixin, ctk.CTk):

    def __init__(self, nxm_url: str | None = None):
        super().__init__()
        self._pending_nxm = nxm_url
        self.title("Mod Manager")
        self.geometry("1260x780")
        self.minsize(960, 560)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._switches:           dict     = {}
        self._cards:              dict     = {}
        self._checkboxvars:       dict     = {}   # name → BooleanVar for batch-select
        self._selected:           set      = set()  # batch-checked mods
        self._selected_archives:  set      = set()  # batch-checked archives
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
        self._filter_var    = ctk.StringVar()
        self._filter_after_id: str|None = None
        # Cached unfiltered mod data (populated by refresh_mods, read by _apply_filter)
        self._mods_item_cache:    dict = {}   # name → item dict
        self._archive_item_cache: dict = {}   # stem → item dict
        self._all_mods_sorted:    list = []   # full sorted mod list (unfiltered)
        self._archived_sorted:    list = []   # full sorted archive stems (unfiltered)
        self._dup_nids:           set  = set() # Nexus IDs with >1 installed package
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
        self._vlist_batch_gen:    int      = 0    # incremented to cancel stale batch chains

        self._build_sidebar()
        self._build_main()
        self._build_statusbar()

        self.bind_all("<Up>",   self._on_arrow_key)
        self.bind_all("<Down>", self._on_arrow_key)

        # Start IPC server so nxm:// links from a second invocation reach us
        from mm.gui.ipc import start_server as _ipc_start
        _ipc_start(lambda url: self.after(0, lambda u=url: self._on_nxm_received(u)))

        # Prime _NEXUS_CACHE_DIR for the active game before the background thread
        # captures it — otherwise it defaults to the legacy root-level path.
        try:
            _gc._load_config()
        except Exception:
            pass

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
        # Process any NXM URL that was passed on the command line
        if self._pending_nxm:
            url = self._pending_nxm
            self._pending_nxm = None
            self.after(200, lambda u=url: self._on_nxm_received(u))

    def _on_nxm_received(self, url: str):
        """Switch to the Downloads page and queue the NXM URL."""
        self._page_nav.set("Downloads")
        self._on_page_select("Downloads")
        self.queue_nxm_url(url)

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
        self._spinner_start("Switching…")
        self.update_idletasks()   # ensure spinner renders before any blocking work
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
            self._spinner_stop()
            tkmsgbox.showerror("Switch Game", f"Could not update config:\n{e}")
            return
        self._nexus_cache.clear()
        self._nexus_fetching.clear()
        from mm.gui.nexus import _nexus_id_cache
        _nexus_id_cache.clear()
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
            self._spinner_start("Loading…")
            self.update_idletasks()
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
        win.geometry("580x720")
        win.minsize(480, 560)
        win.resizable(True, True)
        win.transient(self)
        win.withdraw()          # hide until fully built (prevents blank flash on Linux)
        self._settings_win = win
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(0, weight=1)

        # Center over main window
        self.update_idletasks()
        wx = self.winfo_x() + (self.winfo_width()  - 580) // 2
        wy = self.winfo_y() + (self.winfo_height() - 720) // 2
        win.geometry(f"580x720+{wx}+{wy}")

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

        # ── NXM Downloads ─────────────────────────────────────────────
        row = _section_header("NXM DOWNLOADS", row)

        nxm_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        nxm_frame.grid(row=row, column=0, sticky="ew", padx=16, pady=(4, 0))
        row += 1

        nxm_status = ctk.CTkLabel(
            nxm_frame, text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
            wraplength=340, justify="left",
        )

        def _register_nxm():
            from mm.gui.ipc import register_nxm_handler
            from mm.gui.config import SCRIPT_DIR
            msg = register_nxm_handler(SCRIPT_DIR)
            nxm_status.configure(text=msg)

        ctk.CTkButton(
            nxm_frame, text="Register as NXM Handler", width=190, height=28,
            fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
            font=ctk.CTkFont(size=11),
            command=_register_nxm,
        ).grid(row=0, column=0, sticky="w")
        nxm_status.grid(row=1, column=0, sticky="w", pady=(4, 0))

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

        # ── Plugins (Bethesda games only) ─────────────────────────────
        if self._profile and self._profile.get("plugin_extensions"):
            row = _section_header("PLUGINS", row)

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
                scroll,
                text="Usually inside your Proton/Wine prefix at "
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
            new_raw["nexus_api_key"] = api_var.get().strip()
            game_data = {
                "game_root":      path_vars["game_root"].get().strip(),
                "mods_dir":       path_vars["mods_dir"].get().strip(),
                "compressed_dir": path_vars["compressed_dir"].get().strip(),
            }
            # Optional Bethesda fields (only present when profile has them)
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

    # ── Mod list ──────────────────────────────────────────────────────

    def refresh_mods(self, _skip_info_panel: bool = False):
        # Cancel any pending debounced refresh so we don't double-fire
        if hasattr(self, "_nexus_sort_after") and self._nexus_sort_after:
            self.after_cancel(self._nexus_sort_after)
            self._nexus_sort_after = None

        try:
            cfg   = _gc._load_config()
            state = _gc._load_state()
        except Exception as e:
            self._log_write(f"[error] Could not load config/state: {e}\n")
            self._spinner_stop()
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
            {d.name for d in mods_dir.iterdir()
             if d.is_dir() and not d.name.startswith(".")}
            if mods_dir.exists() else set()
        )
        # Also exclude dot-named entries that might have ended up in state
        tracked = {k for k in state["mods"].keys() if not k.startswith(".")}
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

        self._selected &= self._on_disk
        self._selected_archives &= set(self._archived.keys())
        if self._focused not in self._on_disk and \
                self._focused not in self._archived:
            self._focused = None

        # Detect Nexus IDs that appear more than once (multiple packages of same mod)
        from collections import Counter
        nid_counts = Counter(_nexus_id(n) for n in all_mods if _nexus_id(n))
        self._dup_nids = {nid for nid, cnt in nid_counts.items() if cnt >= 2}

        # Cache item dicts so _apply_filter can rebuild without touching disk
        enabled = 0
        self._mods_item_cache = {}
        for name in all_mods:
            ms    = state["mods"].get(name, {})
            is_on = ms.get("enabled", False)
            if is_on:
                enabled += 1
            from mm.conflicts import get_conflicts_for_mod, get_rule as _get_rule
            _rules = state.get("conflict_rules", [])
            total_conflicts = sum(
                1 for other, _ in get_conflicts_for_mod(name, state)
                if _get_rule(name, other, _rules) is None
            )
            self._mods_item_cache[name] = {
                "type":      "mod",
                "name":      name,
                "disp":      self._get_disp_name(name),
                "is_on":     is_on,
                "symlinks":  len(ms.get("symlinks", [])),
                "exists":    name in self._on_disk,
                "conflicts": total_conflicts,
            }
        self._archive_item_cache = {
            stem: {"type": "archive", "name": stem, "disp": _display_name(stem)}
            for stem in self._archived
        }
        self._all_mods_sorted  = list(all_mods)
        self._archived_sorted  = sorted(self._archived.keys())

        self._count_label.configure(text=f"{enabled}/{len(all_mods)}")
        self._status.configure(
            text=f"{enabled} of {len(all_mods)} mod(s) enabled  ·  "
                 f"{len(self._on_disk)} on disk"
                 + (f"  ·  {len(self._archived)} archive(s) ready"
                    if self._archived else "")
        )
        self._update_selection_ui()
        self._apply_filter()

        # Nexus data is fetched on-demand when the user selects a mod,
        # not for all mods upfront.  Batch-fetching 30+ mods at once caused
        # simultaneous API requests that hit rate-limits, cached error
        # responses, and then looped endlessly retrying.

        if not _skip_info_panel:
            self._update_info_panel(self._focused)
        self._update_launch_button()
        self._update_nav_tabs()
        self._refresh_tools_buttons()
        self._update_deploy_button()
        self._spinner_stop()

    def _update_nav_tabs(self):
        """
        Sync the nav segmented button to reflect current plugin/conflict state.
        Rebuilds the button values at most once per call and only when the tab
        list has actually changed — avoids the CTkSegmentedButton full-redraw
        that causes a window-wide flash.
        """
        has_plugins   = bool((self._profile or {}).get("plugin_extensions"))
        has_conflicts = any(
            ms.get("conflicts")
            for ms in self._state.get("mods", {}).values()
        )
        has_ini = bool((self._profile or {}).get("ini_files"))

        self._page_nav_has_plugins   = has_plugins
        self._page_nav_has_conflicts = has_conflicts
        self._page_nav_has_ini       = has_ini

        tabs = ["Mods"]
        if has_plugins:
            tabs.append("Plugins")
        if has_conflicts:
            tabs.append("Conflicts")
        if has_ini:
            tabs.append("Game Settings")
        tabs.append("Downloads")

        # Skip the heavy configure() call if values haven't changed
        if list(self._page_nav.cget("values")) == tabs:
            return

        current = self._page_nav.get()
        self._page_nav.configure(values=tabs)
        if current not in tabs:
            self._page_nav.set("Mods")
            self._on_page_select("Mods")

    # ── Filter ────────────────────────────────────────────────────────

    def _on_filter_change(self, *_):
        if self._filter_after_id:
            self.after_cancel(self._filter_after_id)
        self._filter_after_id = self.after(150, self._apply_filter)

    def _apply_filter(self):
        self._filter_after_id = None
        query = self._filter_var.get().strip().lower()

        # Bump generation so any in-flight _vlist_batch_shells chain self-cancels
        self._vlist_batch_gen += 1
        gen = self._vlist_batch_gen

        # Collect old frames and clear tracking dicts immediately (don't block here)
        old_frames = [w["frame"] for w in self._vlist_widgets.values()]
        self._vlist_widgets.clear()
        self._vlist_populated.clear()
        self._switches.clear()
        self._cards.clear()
        self._checkboxvars.clear()
        self._name_labels.clear()

        # Destroy old frames in small batches so keypresses aren't starved
        def _destroy_batch(frames: list, idx: int):
            for f in frames[idx:idx + 15]:
                try:
                    f.destroy()
                except Exception:
                    pass
            if idx + 15 < len(frames):
                self.after(0, lambda: _destroy_batch(frames, idx + 15))

        if old_frames:
            self.after(0, lambda: _destroy_batch(old_frames, 0))

        items: list = []
        yoffs: list = []
        y = 0
        nav_mods: list = []

        for name in self._all_mods_sorted:
            item = self._mods_item_cache.get(name)
            if not item:
                continue
            if query and query not in item["disp"].lower() \
                     and query not in name.lower():
                continue
            items.append(item)
            yoffs.append(y)
            y += _CARD_H + _V_PAD
            nav_mods.append(name)

        visible_archives = [
            stem for stem in self._archived_sorted
            if not query
               or query in self._archive_item_cache[stem]["disp"].lower()
               or query in stem.lower()
        ]
        if visible_archives:
            items.append({"type": "sep", "name": "AVAILABLE ARCHIVES"})
            yoffs.append(y)
            y += _SEP_H + _V_PAD
            for stem in visible_archives:
                items.append(self._archive_item_cache[stem])
                yoffs.append(y)
                y += _CARD_H + _V_PAD
                nav_mods.append(stem)

        self._all_mods      = nav_mods
        self._vlist_items   = items
        self._vlist_yoffs   = yoffs
        self._vlist_total_h = y

        # Preserve scroll position when refreshing; reset only when filter changes
        saved_frac = self._vlist_canvas.yview()[0]
        self._vlist_canvas.configure(scrollregion=(0, 0, 0, max(y, 1)))
        if query != getattr(self, "_last_filter_query", None):
            saved_frac = 0.0
        self._last_filter_query = query
        self._vlist_canvas.yview_moveto(saved_frac)

        # Immediately create shells for the visible viewport so cards appear
        # without needing a scroll nudge, then batch-create the rest in the bg.
        canvas   = self._vlist_canvas
        canvas_w = max(canvas.winfo_width() - 4, 10)
        canvas_h = canvas.winfo_height()
        y_top    = canvas.canvasy(0)
        y_bot    = canvas.canvasy(max(canvas_h, 1))
        buf      = _CARD_H * 3
        for i, (item, yo) in enumerate(zip(items, yoffs)):
            h = _SEP_H if item["type"] == "sep" else _CARD_H
            if yo + h >= y_top - buf and yo <= y_bot + buf:
                shell = self._vlist_create_shell(item)
                cid   = canvas.create_window(2, yo, window=shell,
                                             anchor="nw", width=canvas_w)
                self._vlist_widgets[i] = {"frame": shell, "cid": cid}
                if item["type"] in ("mod", "archive"):
                    self._cards[item["name"]] = shell
                if item["type"] == "sep":
                    self._vlist_populated.add(i)
        self._vlist_render()

        # Phase 1: batch-create remaining shells in the background
        self.after(0, lambda g=gen: self._vlist_batch_shells(0, g))

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
        """Return Nexus mod name if cached, otherwise cleaned folder name.

        When multiple installed mods share the same Nexus ID (different packages
        of the same mod), the version from the folder name is appended so the
        user can tell them apart.
        """
        nid = _nexus_id(name)
        if nid:
            nd = self._nexus_cache.get(nid)
            if nd and not nd.get("_error") and nd.get("name"):
                nexus_name = nd["name"]
                if nid in self._dup_nids:
                    v = _nexus_file_version(name)
                    if v:
                        # Put version FIRST so it's visible even when the label
                        # is truncated in the narrow sidebar card.
                        return f"[{v}]  {nexus_name}"
                return nexus_name
        return _display_name(name)

    def _maybe_refresh_nexus(self, nid: str):
        # Update any visible card labels for this nid immediately (fast, in-place)
        nd = self._nexus_cache.get(nid)
        if nd and not nd.get("_error") and nd.get("name"):
            for mod_name, lbl in list(self._name_labels.items()):
                if _nexus_id(mod_name) == nid:
                    try:
                        if lbl.winfo_exists():
                            lbl.configure(text=self._get_disp_name(mod_name))
                    except Exception:
                        pass

            # The card label is already updated in-place above; a full list
            # rebuild to re-sort by Nexus name is not needed for on-demand fetches.

        # Refresh info panel if this mod is currently focused AND the data is valid.
        # Skip error responses — there's nothing to show, and triggering a rebuild
        # would restart the fetch loop (the info panel retried failed fetches).
        _nd = self._nexus_cache.get(nid, {})
        if (self._focused and _nexus_id(self._focused) == nid
                and _nd and not _nd.get("_error")):
            if not hasattr(self, "_nexus_info_after"):
                self._nexus_info_after = None
            if self._nexus_info_after:
                self.after_cancel(self._nexus_info_after)
            self._nexus_info_after = self.after(
                150,
                lambda: setattr(self, "_nexus_info_after", None) or
                        self._update_info_panel(self._focused),
            )

    def _nexus_sort_flush(self):
        """Debounced: rebuild the mod list once after a burst of Nexus completions."""
        self._nexus_sort_after = None
        self.refresh_mods(_skip_info_panel=True)


def main(nxm_url: str | None = None):
    app = ModManagerApp(nxm_url=nxm_url)
    app.mainloop()
