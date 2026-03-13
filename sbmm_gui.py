#!/usr/bin/env python3
"""
Stellar Blade Mod Manager – GUI
Requires: pip install customtkinter Pillow   (already in .venv)
Run with: .venv/bin/python sbmm_gui.py
"""

import sys
import json
import re
import threading
import subprocess
import configparser
import webbrowser
import tkinter as tk
import tkinter.filedialog
from tkinter import messagebox as tkmsgbox
import urllib.request
import urllib.error
from pathlib import Path

try:
    import customtkinter as ctk
except ImportError:
    print("customtkinter is not installed.  Run:  pip install customtkinter")
    sys.exit(1)

try:
    from PIL import Image as PILImage, ImageTk as PILImageTk
    _PIL = True
except ImportError:
    _PIL = False

SCRIPT_DIR  = Path(__file__).parent.resolve()
SBMM        = SCRIPT_DIR / "sbmm.py"
CONFIG_FILE = SCRIPT_DIR / "config.json"
STATE_FILE  = SCRIPT_DIR / "state.json"

NEXUS_BASE  = "https://www.nexusmods.com/stellarblade/mods/"

_CARD_NORMAL   = ("gray80", "gray22")
_CARD_FOCUSED  = ("gray74", "gray28")      # info panel active, single-click
_CARD_CHECKED  = ("#1a4a72", "#1a4a72")    # checked for batch ops

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _display_name(folder: str) -> str:
    return re.sub(r"[-_]\d+(?:[-_]\d+)*$", "", folder).strip("-_ ") or folder


def _nexus_id(folder: str) -> str | None:
    """Extract the Nexus mod ID from a folder name like 'ModName-1234-1-0-...'"""
    m = re.search(r"-(\d{3,6})(?:-\d+)+-\d{9,}$", folder)
    return m.group(1) if m else None


_ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}


def _load_config() -> dict:
    with open(CONFIG_FILE) as f:
        raw = json.load(f)
    base = SCRIPT_DIR
    return {
        "mods_dir":       base / raw.get("mods_dir", "mods"),
        "compressed_dir": base / raw.get("compressed_dir", "compressed"),
        "nexus_api_key":  raw.get("nexus_api_key", "").strip(),
    }


_NEXUS_CACHE_DIR = SCRIPT_DIR / ".nexus_cache"
_NEXUS_API_BASE  = "https://api.nexusmods.com/v1/games/stellarblade/mods"
_UA              = "StellarBladeModManager/1.0"


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


def _nexus_api_fetch(nid: str, api_key: str) -> dict | None:
    url = f"{_NEXUS_API_BASE}/{nid}.json"
    req = urllib.request.Request(
        url,
        headers={"apikey": api_key, "Accept": "application/json",
                 "User-Agent": _UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": str(e.code)}
    except Exception as e:
        return {"_error": str(e)}


def _nexus_download_image(url: str, dest: Path) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception:
        return False


def _load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"mods": {}}


def _read_mod_info(mod_dir: Path) -> dict:
    """
    Collect display metadata for a mod folder.
    Returns a dict with keys: name, author, version, description,
    bundle, image_path, pak_stems, readme_text.
    """
    info: dict = {}

    # ── Find modinfo.ini (may be in a subfolder) ───────────────────────
    ini_candidates = list(mod_dir.rglob("modinfo.ini")) + list(mod_dir.rglob("Modinfo.ini"))
    if ini_candidates:
        ini_path = ini_candidates[0]
        ini_dir  = ini_path.parent
        # modinfo.ini is often section-less; wrap it before parsing
        raw_text = ini_path.read_text(errors="replace")
        if not any(line.strip().startswith("[") for line in raw_text.splitlines()):
            raw_text = "[mod]\n" + raw_text
        cp2 = configparser.RawConfigParser()
        try:
            cp2.read_string(raw_text)
        except Exception:
            cp2 = configparser.RawConfigParser()
        section = cp2[cp2.sections()[0]] if cp2.sections() else {}

        def _get(*keys):
            for k in keys:
                v = section.get(k, "").strip()
                if v:
                    return v
            return ""

        info["name"]        = _get("name")
        info["author"]      = _get("author")
        info["version"]     = _get("version")
        info["description"] = _get("description")
        info["bundle"]      = _get("nameasbundle")

        # Image referenced by modinfo
        screenshot = _get("screenshot", "preview", "image")
        if screenshot:
            candidate = ini_dir / screenshot
            if candidate.exists():
                info["image_path"] = candidate

    # ── Find preview image if not yet set ─────────────────────────────
    if "image_path" not in info:
        priority_names = ["1.png", "preview.png", "thumbnail.png",
                          "cover.png", "screenshot.png"]
        for name in priority_names:
            p = mod_dir / name
            if p.exists():
                info["image_path"] = p
                break
        if "image_path" not in info:
            # Any image at root level
            for ext in ("*.png", "*.jpg", "*.jpeg"):
                hits = sorted(mod_dir.glob(ext))
                if hits:
                    info["image_path"] = hits[0]
                    break
        if "image_path" not in info:
            # One level deeper
            for ext in ("*.png", "*.jpg", "*.jpeg"):
                hits = sorted(mod_dir.rglob(ext))
                if hits:
                    info["image_path"] = hits[0]
                    break

    # ── README ─────────────────────────────────────────────────────────
    readme_names = ["README.md", "README.txt", "readme.txt",
                    "readme.md", "Readme.txt", "README - Installation.txt"]
    for rn in readme_names:
        p = mod_dir / rn
        if not p.exists():
            hits = list(mod_dir.rglob(rn))
            p = hits[0] if hits else None
        if p and p.exists():
            try:
                text = p.read_text(errors="replace").strip()
                if text:
                    info["readme_text"] = text[:800]
                    break
            except Exception:
                pass

    # ── Pak stems ──────────────────────────────────────────────────────
    paks  = sorted(mod_dir.rglob("*.pak"))
    stems = sorted({p.stem for p in paks})
    info["pak_stems"] = stems

    return info


# ---------------------------------------------------------------------------
# Interactive-prompt helpers
# ---------------------------------------------------------------------------

def _detect_prompt(buf: str):
    """
    Return (kind, n) if buf matches a known sbmm.py interactive prompt,
    else None.  n is the number of numbered options (int) or None.
    """
    b = buf.rstrip()
    m = re.search(r"Keep which\? \[1-(\d+)/a=keep all/s=skip\]:\s*$", b)
    if m:
        return ("variant", int(m.group(1)))
    if re.search(r"Which should take priority\? \[1/2\]:\s*$", b):
        return ("conflict", 2)
    if re.search(r"Keep which\? \[1/2/s=skip once/a=always keep both\]:\s*$", b):
        return ("asset", 2)
    if re.search(r"Remove these records from state\? \[y/N\]:\s*$", b):
        return ("purge", None)
    return None


class _InteractiveDialog(ctk.CTkToplevel):
    """
    Modal dialog shown when sbmm.py emits an interactive prompt.
    Displays accumulated output as context and presents radio-button choices.
    """

    _TITLES = {
        "variant":  "Choose Variant",
        "conflict": "Resolve Mod Conflict",
        "asset":    "Resolve Asset Conflict",
        "purge":    "Confirm Purge",
    }

    def __init__(self, parent, context: str, kind: str, n_choices: int = 2):
        super().__init__(parent)
        self.result = None
        self.title(self._TITLES.get(kind, "Input Required"))
        self.geometry("580x480")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── Context (scrollable monospace) ────────────────────────────
        ctx_outer = ctk.CTkFrame(self, fg_color=("gray85", "gray18"),
                                 corner_radius=6)
        ctx_outer.grid(row=0, column=0, sticky="nsew", padx=14, pady=(14, 6))
        ctx_outer.grid_columnconfigure(0, weight=1)
        ctx_outer.grid_rowconfigure(0, weight=1)

        ctx_scroll = ctk.CTkScrollableFrame(ctx_outer, fg_color="transparent")
        ctx_scroll.grid(row=0, column=0, sticky="nsew")
        ctx_scroll.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            ctx_scroll, text=context.strip(),
            font=ctk.CTkFont(family="monospace", size=11),
            text_color=("gray20", "gray82"),
            justify="left", anchor="nw", wraplength=520,
        ).grid(row=0, column=0, sticky="w", padx=6, pady=4)

        # ── Radio buttons ─────────────────────────────────────────────
        radio_frame = ctk.CTkFrame(self, fg_color="transparent")
        radio_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))

        self._var = ctk.StringVar()
        choices = self._build_choices(context, kind, n_choices)
        if choices:
            self._var.set(choices[0][0])
        for val, label in choices:
            ctk.CTkRadioButton(
                radio_frame, text=label, value=val,
                variable=self._var,
                font=ctk.CTkFont(size=12),
            ).pack(anchor="w", padx=4, pady=2)

        # ── Confirm button ────────────────────────────────────────────
        ctk.CTkButton(
            self, text="Confirm", height=34,
            command=self._submit,
        ).grid(row=2, column=0, padx=14, pady=(8, 14), sticky="e")

        self.bind("<Return>", lambda _: self._submit())

    def _build_choices(self, context: str, kind: str, n: int) -> list:
        choices = []
        if kind in ("variant", "conflict", "asset"):
            for line in context.splitlines():
                m = re.match(r"^\s*\((\d+)\)\s+(.+)$", line.rstrip())
                if m:
                    val, label = m.group(1), m.group(2).strip()
                    choices.append((val, f"({val})  {label}"))
            if kind == "variant":
                choices.append(("a", "(a)  Keep all variants"))
                choices.append(("s", "(s)  Skip (keep all, no removal)"))
            elif kind == "asset":
                choices.append(("s", "(s)  Skip once  (keep both this time)"))
                choices.append(("a", "(a)  Always keep both  (never ask again)"))
        elif kind == "purge":
            choices = [
                ("y", "Yes — remove orphaned state records"),
                ("n", "No — leave records as-is"),
            ]
        # Fallback if nothing was parsed
        if not choices and n:
            choices = [(str(i), f"Option {i}") for i in range(1, n + 1)]
        return choices

    def _submit(self):
        self.result = self._var.get()
        self.destroy()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class App(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("Stellar Blade Mod Manager")
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

        self._build_sidebar()
        self._build_main()
        self._build_statusbar()

        self.bind_all("<Up>",   self._on_arrow_key)
        self.bind_all("<Down>", self._on_arrow_key)

        self.refresh_mods()

    # ── Sidebar ───────────────────────────────────────────────────────

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=330, corner_radius=0,
                          fg_color=("gray90", "gray15"))
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.grid_rowconfigure(1, weight=1)
        sb.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(sb, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(hdr, text="MODS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=("gray45", "gray55"),
                     ).grid(row=0, column=0, sticky="w")

        self._count_label = ctk.CTkLabel(hdr, text="",
                                         font=ctk.CTkFont(size=11),
                                         text_color=("gray45", "gray55"))
        self._count_label.grid(row=0, column=2, sticky="e")

        ctk.CTkButton(
            hdr, text="⚙", width=28, height=22,
            fg_color="transparent", hover_color=("gray70", "gray30"),
            font=ctk.CTkFont(size=14),
            command=self._open_settings,
        ).grid(row=0, column=3, sticky="e", padx=(6, 0))

        self._mod_list = ctk.CTkScrollableFrame(
            sb, fg_color="transparent",
            scrollbar_button_color=("gray70", "gray30"),
            scrollbar_button_hover_color=("gray60", "gray40"),
        )
        self._mod_list.grid(row=1, column=0, sticky="nsew", padx=6)
        self._mod_list.grid_columnconfigure(0, weight=1)
        # Bind scroll wheel to the frame itself (catches events on empty area)
        self._bind_scroll(self._mod_list, self._mod_list)

        btns = ctk.CTkFrame(sb, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="ew", padx=12, pady=12)
        btns.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            btns, text="Enable All", height=34,
            command=lambda: self._run_bg(["--enable"], on_done=self.refresh_mods),
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            btns, text="Disable All", height=34,
            fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
            command=lambda: self._run_bg(["--disable"], on_done=self.refresh_mods),
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self._btn_enable_sel = ctk.CTkButton(
            btns, text="Enable Selected", height=34, state="disabled",
            fg_color=("gray72", "gray30"), hover_color=("#1a6aaa", "#1a5a8a"),
            command=self._enable_selected,
        )
        self._btn_enable_sel.grid(row=1, column=0, padx=(0, 4), pady=(6, 0), sticky="ew")

        self._btn_disable_sel = ctk.CTkButton(
            btns, text="Disable Selected", height=34, state="disabled",
            fg_color=("gray72", "gray30"), hover_color=("gray52", "gray42"),
            command=self._disable_selected,
        )
        self._btn_disable_sel.grid(row=1, column=1, padx=(4, 0), pady=(6, 0), sticky="ew")

        util = ctk.CTkFrame(btns, fg_color="transparent")
        util.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        util.grid_columnconfigure((0, 1), weight=1)

        self._btn_clear_sel = ctk.CTkButton(
            util, text="✕  Clear Selection", height=28,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=11), state="disabled",
            command=self._clear_selection,
        )
        self._btn_clear_sel.grid(row=0, column=0, padx=(0, 3), sticky="ew")

        ctk.CTkButton(
            util, text="↺  Refresh", height=28,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=12),
            command=self.refresh_mods,
        ).grid(row=0, column=1, padx=(3, 0), sticky="ew")

    # ── Main panel (info 70% / log 30%) ──────────────────────────────

    def _build_main(self):
        main = ctk.CTkFrame(self, corner_radius=0,
                            fg_color=("gray95", "gray12"))
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=7)   # info
        main.grid_rowconfigure(1, weight=3)   # log

        self._build_info_panel(main)
        self._build_log_panel(main)

    # ── Info panel ────────────────────────────────────────────────────

    def _build_info_panel(self, parent):
        outer = ctk.CTkFrame(parent, fg_color=("gray91", "gray14"),
                             corner_radius=0)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(1, weight=1)

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
        # Shown only when the mod folder exists on disk
        self._folder_path: "Path | None" = None

        self._nexus_btn = ctk.CTkButton(
            hdr, text="View on Nexus Mods", height=24, width=150,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=11),
            command=self._open_nexus,
        )
        # Shown only when a Nexus ID is known
        self._nexus_id: str | None = None

        # Scrollable content area
        self._info_scroll = ctk.CTkScrollableFrame(
            outer, fg_color="transparent",
            scrollbar_button_color=("gray70", "gray30"),
            scrollbar_button_hover_color=("gray60", "gray40"),
        )
        self._info_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(6, 4))
        self._info_scroll.grid_columnconfigure(0, weight=1)

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

    def _show_archive_info(self, name: str, arch_path: Path):
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
            nexus_url = NEXUS_BASE + nid
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
                     text="Click  Extract  below to unpack this archive into mods/,\n"
                          "or  Install  to extract and enable all mods at once.",
                     font=ctk.CTkFont(size=12),
                     text_color=("gray40", "gray60"),
                     justify="left",
                     ).grid(row=2, column=0, sticky="w", padx=16, pady=(0, 8))

        # Augment with Nexus data if available
        api_key = ""
        try:
            api_key = _load_config().get("nexus_api_key", "")
        except Exception:
            pass
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

    def _update_info_panel(self, name: str | None):
        """Populate the info panel for the given mod folder name (or None)."""
        self._cancel_img_overlay()
        for w in self._info_scroll.winfo_children():
            w.destroy()
        self._folder_btn.grid_remove()
        self._nexus_btn.grid_remove()
        self._info_img_ref = None

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
            cfg = _load_config()
            mod_dir = cfg["mods_dir"] / name
        except Exception:
            pass

        state   = _load_state()
        ms      = state["mods"].get(name, {})
        is_on   = ms.get("enabled", False)
        links   = len(ms.get("symlinks", []))
        exists  = mod_dir and mod_dir.is_dir()
        disp    = _display_name(name)
        nid     = _nexus_id(name)

        if exists:
            self._folder_path = mod_dir
            self._folder_btn.grid(row=0, column=1, sticky="e", padx=(0, 6))

        try:
            info = _read_mod_info(mod_dir) if exists else {}
        except Exception:
            info = {}

        # ── Augment with cached Nexus data ────────────────────────────
        api_key = ""
        try:
            api_key = _load_config().get("nexus_api_key", "")
        except Exception:
            pass

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
            nexus_url = NEXUS_BASE + nid
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

        # ── Pak files ─────────────────────────────────────────────────
        stems = info.get("pak_stems", [])
        if stems:
            sep2 = ctk.CTkFrame(self._info_scroll, height=1,
                                fg_color=("gray75", "gray28"))
            sep2.grid(row=4, column=0, sticky="ew", padx=8, pady=(10, 6))

            ctk.CTkLabel(self._info_scroll,
                         text=f"Contents  ({len(stems)} pak file(s))",
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=("gray45", "gray55"), anchor="w",
                         ).grid(row=5, column=0, sticky="w", padx=12)

            files_frame = ctk.CTkFrame(self._info_scroll, fg_color="transparent")
            files_frame.grid(row=6, column=0, sticky="ew", padx=12, pady=(2, 8))

            limit = 12
            for i, stem in enumerate(stems[:limit]):
                ctk.CTkLabel(files_frame, text=f"  {stem}",
                             font=ctk.CTkFont(family="monospace", size=11),
                             text_color=("gray35", "gray70"), anchor="w",
                             ).grid(row=i, column=0, sticky="w")
            if len(stems) > limit:
                ctk.CTkLabel(files_frame,
                             text=f"  … and {len(stems) - limit} more",
                             font=ctk.CTkFont(size=11),
                             text_color=("gray55", "gray50"), anchor="w",
                             ).grid(row=limit, column=0, sticky="w")

    # ── Nexus API background fetch ────────────────────────────────────

    def _bg_fetch_nexus(self, nid: str, api_key: str):
        """Fetch mod data from Nexus API in a background thread, then refresh."""
        _NEXUS_CACHE_DIR.mkdir(exist_ok=True)
        json_path = _NEXUS_CACHE_DIR / f"{nid}.json"

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
                img_path = _NEXUS_CACHE_DIR / f"{nid}.{ext}"
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
                        lbl.configure(text=nexus_name)
                    except Exception:
                        pass
        # Refresh info panel if this mod is focused
        if self._focused and _nexus_id(self._focused) == nid:
            self._update_info_panel(self._focused)

    # ── Settings window ───────────────────────────────────────────────

    def _open_settings(self):
        # If already open, just focus it
        if hasattr(self, "_settings_win") and self._settings_win and \
                self._settings_win.winfo_exists():
            self._settings_win.focus()
            return

        # Load full raw config so we can round-trip all fields
        try:
            with open(CONFIG_FILE) as f:
                raw = json.load(f)
        except Exception:
            raw = {}

        win = ctk.CTkToplevel(self)
        win.title("Settings")
        win.geometry("580x530")
        win.minsize(480, 440)
        win.resizable(True, True)
        win.transient(self)
        win.grab_set()
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

        api_var   = ctk.StringVar(value=raw.get("nexus_api_key", ""))
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
            if _NEXUS_CACHE_DIR.exists():
                for fp in _NEXUS_CACHE_DIR.iterdir():
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

            var = ctk.StringVar(value=raw.get(key, ""))
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
            new_raw["nexus_api_key"]  = api_var.get().strip()
            new_raw["game_root"]      = path_vars["game_root"].get().strip()
            new_raw["mods_dir"]       = path_vars["mods_dir"].get().strip()
            new_raw["compressed_dir"] = path_vars["compressed_dir"].get().strip()
            new_raw["theme"]          = theme_var.get()
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

    def _open_mod_folder(self):
        if self._folder_path and self._folder_path.is_dir():
            subprocess.Popen(["xdg-open", str(self._folder_path)])

    def _open_nexus(self):
        if self._nexus_id:
            webbrowser.open(self._nexus_id)

    # ── Image zoom overlay ────────────────────────────────────────────

    def _schedule_img_overlay(self, img_path: Path, img_frame):
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

    def _show_img_overlay(self, img_path: Path, img_frame):
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

        ctk.CTkButton(
            log_hdr, text="Clear", width=64, height=24,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=11),
            command=self._clear_log,
        ).grid(row=0, column=1, sticky="e")

        self._log = ctk.CTkTextbox(
            outer,
            font=ctk.CTkFont(family="monospace", size=12),
            wrap="word", state="disabled",
            fg_color=("gray85", "gray18"),
        )
        self._log.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 0))

        # Action buttons
        act = ctk.CTkFrame(outer, fg_color="transparent")
        act.grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 12))

        actions = [
            ("Install",    ["--install"],    "interactive"),
            ("Extract",    ["--extract"],    "interactive"),
            ("Check",      ["--check"],      "bg"),
            ("AssetCheck", ["--assetcheck"], "interactive"),
            ("Conflicts",  ["--conflicts"],  "bg"),
            ("Clean",      ["--clean"],      "terminal"),
            ("Purge",      ["--purge"],      "purge"),
            ("Uninstall",  ["--uninstall"],  "uninstall"),
        ]

        for i, (label, cmd_args, kind) in enumerate(actions):
            act.grid_columnconfigure(i, weight=1)

            if kind == "uninstall":
                fg, hv = "#c0392b", "#922b21"
            elif kind == "terminal":
                fg = ("gray68", "gray32")
                hv = ("gray58", "gray42")
            else:
                fg, hv = None, None

            kw = dict(
                text=label, height=32,
                font=ctk.CTkFont(size=12),
                command=lambda a=cmd_args, k=kind: self._dispatch(a, k),
            )
            if fg:
                kw["fg_color"] = fg
            if hv:
                kw["hover_color"] = hv

            ctk.CTkButton(act, **kw).grid(row=0, column=i, padx=3, sticky="ew")

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

    # ── Mod list ──────────────────────────────────────────────────────

    def refresh_mods(self):
        try:
            cfg   = _load_config()
            state = _load_state()
        except Exception as e:
            self._log_write(f"[error] Could not load config/state: {e}\n")
            return

        mods_dir = cfg["mods_dir"]
        self._on_disk = (
            {d.name for d in mods_dir.iterdir() if d.is_dir()}
            if mods_dir.exists() else set()
        )
        tracked  = set(state["mods"].keys())
        all_mods = sorted(self._on_disk | tracked)

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

        for w in self._mod_list.winfo_children():
            w.destroy()
        self._switches.clear()
        self._cards.clear()
        self._checkboxvars.clear()
        self._name_labels.clear()

        enabled = 0
        for i, name in enumerate(all_mods):
            ms       = state["mods"].get(name, {})
            is_on    = ms.get("enabled", False)
            symlinks = len(ms.get("symlinks", []))
            exists   = name in self._on_disk
            disp     = self._get_disp_name(name)
            checked  = name in self._selected
            focused  = name == self._focused
            if is_on:
                enabled += 1

            if checked:
                bg, bw = _CARD_CHECKED, 2
            elif focused:
                bg, bw = _CARD_FOCUSED, 1
            else:
                bg, bw = _CARD_NORMAL, 0

            card = ctk.CTkFrame(
                self._mod_list,
                fg_color=bg, corner_radius=7,
                border_width=bw, border_color="#2980b9",
            )
            card.grid(row=i, column=0, sticky="ew", pady=2, padx=2)
            card.grid_columnconfigure(2, weight=1)
            self._cards[name] = card

            # Checkbox for batch selection (col 0)
            cb_var = ctk.BooleanVar(value=checked)
            self._checkboxvars[name] = cb_var
            cb = ctk.CTkCheckBox(
                card, text="", variable=cb_var,
                width=20, checkbox_width=15, checkbox_height=15,
                command=lambda n=name, v=cb_var: self._on_checkbox_change(n, v),
            )
            cb.grid(row=0, column=0, padx=(8, 2), pady=8)
            if not exists:
                cb.configure(state="disabled")

            dot_col = "#27ae60" if is_on else ("gray52", "gray40")
            dot_lbl = ctk.CTkLabel(card, text="●", font=ctk.CTkFont(size=13),
                                   text_color=dot_col, width=22)
            dot_lbl.grid(row=0, column=1, padx=(2, 4), pady=8)

            name_col = ("gray10", "gray90") if exists else ("gray52", "gray46")
            name_lbl = ctk.CTkLabel(card, text=disp,
                                    font=ctk.CTkFont(size=12),
                                    text_color=name_col, anchor="w")
            name_lbl.grid(row=0, column=2, sticky="w", padx=4)
            self._name_labels[name] = name_lbl

            badge = None
            if is_on and symlinks:
                badge = ctk.CTkLabel(
                    card, text=str(symlinks),
                    font=ctk.CTkFont(size=10),
                    text_color=("gray50", "gray48"),
                    fg_color=("gray70", "gray30"),
                    corner_radius=4, width=28, height=18,
                )
                badge.grid(row=0, column=3, padx=6)

            var = ctk.BooleanVar(value=is_on)
            sw  = ctk.CTkSwitch(
                card, text="", variable=var, width=46,
                onvalue=True, offvalue=False,
                command=lambda n=name, v=var: self._toggle(n, v),
            )
            sw.grid(row=0, column=4, padx=(4, 10), pady=8)
            if not exists:
                sw.configure(state="disabled")

            self._switches[name] = (var, sw)

            # Card body click → show info (focus), no batch-select change
            if exists:
                for widget in (card, dot_lbl, name_lbl) + ((badge,) if badge else ()):
                    widget.bind("<Button-1>",
                                lambda _, n=name: self._set_focus(n))
                card.configure(cursor="hand2")

            self._bind_scroll(card, self._mod_list)

        # ── Archive-only entries ───────────────────────────────────────
        base_row = len(all_mods)
        if self._archived:
            sep_lbl = ctk.CTkLabel(
                self._mod_list,
                text="AVAILABLE ARCHIVES",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=("gray50", "gray50"),
                anchor="w",
            )
            sep_lbl.grid(row=base_row, column=0, sticky="ew",
                         padx=10, pady=(10, 2))
            self._bind_scroll(sep_lbl, self._mod_list)
            base_row += 1

        for j, (stem, _) in enumerate(sorted(self._archived.items())):
            disp    = _display_name(stem)
            focused = stem == self._focused
            bg, bw  = (_CARD_FOCUSED, 1) if focused else (_CARD_NORMAL, 0)

            card = ctk.CTkFrame(
                self._mod_list,
                fg_color=bg, corner_radius=7,
                border_width=bw, border_color="#2980b9",
            )
            card.grid(row=base_row + j, column=0, sticky="ew", pady=2, padx=2)
            card.grid_columnconfigure(2, weight=1)
            self._cards[stem] = card

            # Placeholder checkbox space (no checkbox for archives)
            ctk.CTkLabel(card, text="", width=20).grid(
                row=0, column=0, padx=(8, 2))

            dot_lbl = ctk.CTkLabel(card, text="●", font=ctk.CTkFont(size=13),
                                   text_color="#e67e22", width=22)
            dot_lbl.grid(row=0, column=1, padx=(2, 4), pady=8)

            name_lbl = ctk.CTkLabel(card, text=disp,
                                    font=ctk.CTkFont(size=12),
                                    text_color=("gray10", "gray90"), anchor="w")
            name_lbl.grid(row=0, column=2, sticky="w", padx=4)

            badge = ctk.CTkLabel(
                card, text="archive",
                font=ctk.CTkFont(size=10),
                text_color=("#7a4a10", "#e09050"),
                fg_color=("#f5dfc0", "#3a2a10"),
                corner_radius=4, width=50, height=18,
            )
            badge.grid(row=0, column=3, padx=6)

            # No switch — just a spacer to keep layout consistent
            ctk.CTkLabel(card, text="", width=56).grid(row=0, column=4)

            for widget in (card, dot_lbl, name_lbl, badge):
                widget.bind("<Button-1>", lambda _, n=stem: self._set_focus(n))
            card.configure(cursor="hand2")

            self._bind_scroll(card, self._mod_list)

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

        self._update_info_panel(self._focused)

    # ── Scroll helper ─────────────────────────────────────────────────

    def _bind_scroll(self, widget, scroll_frame):
        """Recursively bind Linux scroll events on widget to scroll scroll_frame."""
        canvas = scroll_frame._parent_canvas
        widget.bind("<Button-4>",
                    lambda _: canvas.yview_scroll(-1, "units"), add="+")
        widget.bind("<Button-5>",
                    lambda _: canvas.yview_scroll(1, "units"), add="+")
        for child in widget.winfo_children():
            self._bind_scroll(child, scroll_frame)

    # ── Keyboard navigation ───────────────────────────────────────────

    def _on_arrow_key(self, event):
        if not self._all_mods:
            return
        if self._focused not in self._all_mods:
            idx = 0
        else:
            idx = self._all_mods.index(self._focused)
            if event.keysym == "Up":
                idx = max(0, idx - 1)
            else:
                idx = min(len(self._all_mods) - 1, idx + 1)
        self._set_focus(self._all_mods[idx])

    # ── Selection ─────────────────────────────────────────────────────

    def _set_focus(self, name: str):
        """Single-click a card: show its info. Does not affect batch selection."""
        prev = self._focused
        self._focused = name
        if prev and prev != name:
            self._repaint_card(prev)
        self._repaint_card(name)
        self._update_info_panel(name)

    def _on_checkbox_change(self, name: str, var: ctk.BooleanVar):
        """Checkbox toggled: update batch-select set."""
        if var.get():
            self._selected.add(name)
        else:
            self._selected.discard(name)
        self._repaint_card(name)
        self._update_selection_ui()

    def _clear_selection(self):
        prev = set(self._selected)
        self._selected.clear()
        for name in prev:
            cbv = self._checkboxvars.get(name)
            if cbv:
                cbv.set(False)
            self._repaint_card(name)
        self._update_selection_ui()

    def _repaint_card(self, name: str):
        card = self._cards.get(name)
        if not card:
            return
        checked = name in self._selected
        focused = self._focused == name
        if checked:
            bg, bw = _CARD_CHECKED, 2
        elif focused:
            bg, bw = _CARD_FOCUSED, 1
        else:
            bg, bw = _CARD_NORMAL, 0
        card.configure(fg_color=bg, border_width=bw)

    def _update_selection_ui(self):
        n   = len(self._selected)
        has = n > 0
        if has:
            self._btn_enable_sel.configure(
                state="normal", text=f"Enable Selected ({n})",
                fg_color=("#1a5a9a", "#1a5a9a"),
                hover_color=("#1a6aaa", "#1a6aaa"),
            )
            self._btn_disable_sel.configure(
                state="normal", text=f"Disable Selected ({n})",
                fg_color=("gray50", "gray38"),
                hover_color=("gray42", "gray46"),
            )
            self._btn_clear_sel.configure(state="normal")
        else:
            self._btn_enable_sel.configure(
                state="disabled", text="Enable Selected",
                fg_color=("gray72", "gray30"),
            )
            self._btn_disable_sel.configure(
                state="disabled", text="Disable Selected",
                fg_color=("gray72", "gray30"),
            )
            self._btn_clear_sel.configure(state="disabled")

    def _enable_selected(self):
        # Enable uses interactive runner so conflict/variant prompts show as dialogs
        names = sorted(self._selected & self._on_disk)
        if not names:
            return
        self._log_write(f"\n$ sbmm --enable [{len(names)} mod(s)]\n", bold=True)
        remaining = list(names)

        def run_next():
            if not remaining:
                self.refresh_mods()
                return
            self._run_interactive(["--enable", remaining.pop(0)],
                                  on_done=run_next)

        run_next()

    def _disable_selected(self):
        self._run_selected("--disable")

    def _run_selected(self, flag: str):
        names = sorted(self._selected & self._on_disk)
        if not names:
            return
        python = SCRIPT_DIR / ".venv" / "bin" / "python"
        if not python.exists():
            python = sys.executable

        self._log_write(f"\n$ sbmm {flag} [{len(names)} mod(s)]\n", bold=True)
        self._busy.configure(text="⏳ Running…")

        def worker():
            for name in names:
                cmd = [str(python), str(SBMM), flag, name]
                try:
                    proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, cwd=str(SCRIPT_DIR),
                    )
                    for line in proc.stdout:
                        self.after(0, self._log_write, line)
                    proc.wait()
                except Exception as e:
                    self.after(0, self._log_write, f"[error] {name}: {e}\n")
            self.after(0, lambda: self._busy.configure(text=""))
            self.after(200, self.refresh_mods)

        threading.Thread(target=worker, daemon=True).start()

    # ── Interactive subprocess (prompt dialogs) ───────────────────────

    def _run_interactive(self, args: list, on_done=None):
        """
        Run sbmm.py with stdin/stdout piped.  Output streams to the log panel.
        When a known interactive prompt is detected (no trailing newline, matches
        a sbmm.py prompt pattern), a modal dialog is shown with radio buttons.
        The user's answer is written back to the process's stdin.
        """
        python = SCRIPT_DIR / ".venv" / "bin" / "python"
        if not python.exists():
            python = sys.executable
        cmd = [str(python), str(SBMM)] + args

        self._log_write(f"\n$ sbmm {' '.join(args)}\n", bold=True)
        self._busy.configure(text="⏳ Running…")
        self.update_idletasks()

        def worker():
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=0,
                    cwd=str(SCRIPT_DIR),
                )
            except Exception as e:
                self.after(0, self._log_write, f"[error] {e}\n")
                self.after(0, lambda: self._busy.configure(text=""))
                return

            line_buf    = ""
            context_buf = ""

            while True:
                ch = proc.stdout.read(1)
                if not ch:
                    break
                line_buf += ch

                if ch == "\n":
                    self.after(0, self._log_write, line_buf)
                    context_buf += line_buf
                    # Keep only the last 30 lines as context for dialogs
                    context_buf = "".join(
                        context_buf.splitlines(keepends=True)[-30:])
                    line_buf = ""
                else:
                    result = _detect_prompt(line_buf)
                    if result is not None:
                        kind, n = result
                        answer_event  = threading.Event()
                        answer_holder = [None]

                        def _show(ctx=context_buf, k=kind, nc=n,
                                  ev=answer_event, holder=answer_holder):
                            dlg = _InteractiveDialog(self, ctx, k, nc)
                            self.update_idletasks()
                            dx = self.winfo_x() + \
                                 (self.winfo_width()  - 580) // 2
                            dy = self.winfo_y() + \
                                 (self.winfo_height() - 480) // 2
                            dlg.geometry(f"580x480+{dx}+{dy}")
                            dlg.wait_window()
                            holder[0] = dlg.result or ""
                            ev.set()

                        self.after(0, _show)
                        answer_event.wait(timeout=300)   # 5-min timeout
                        answer = answer_holder[0] or ""

                        self.after(0, self._log_write,
                                   f"{line_buf}{answer}\n")
                        context_buf += line_buf + answer + "\n"
                        line_buf = ""

                        try:
                            proc.stdin.write(answer + "\n")
                            proc.stdin.flush()
                        except Exception:
                            pass

            if line_buf:
                self.after(0, self._log_write, line_buf + "\n")
            proc.wait()
            self.after(0, lambda: self._busy.configure(text=""))
            if on_done:
                self.after(200, on_done)

        threading.Thread(target=worker, daemon=True).start()

    # ── Per-toggle and global dispatch ───────────────────────────────

    def _toggle(self, name: str, var: ctk.BooleanVar):
        if var.get():
            self._run_interactive(["--enable", name], on_done=self.refresh_mods)
        else:
            self._run_bg(["--disable", name], on_done=self.refresh_mods)

    def _dispatch(self, args: list, kind: str):
        if kind == "uninstall":
            dlg = ctk.CTkInputDialog(
                text="Type  yes  to remove all symlinks and restore backups:",
                title="Confirm Uninstall",
            )
            if dlg.get_input() != "yes":
                self._log_write("[cancelled]\n")
                return
            self._run_bg(args, on_done=self.refresh_mods)
        elif kind == "purge":
            dlg = ctk.CTkInputDialog(
                text="Type  yes  to remove state records for deleted mod folders:",
                title="Confirm Purge",
            )
            if dlg.get_input() != "yes":
                self._log_write("[cancelled]\n")
                return
            self._run_bg(args, stdin_data="y\n", on_done=self.refresh_mods)
        elif kind == "terminal":
            self._open_terminal(args)
        elif kind == "interactive":
            self._run_interactive(args, on_done=self.refresh_mods)
        else:
            self._run_bg(args, on_done=self.refresh_mods)

    # ── Background subprocess ─────────────────────────────────────────

    def _run_bg(self, args: list, stdin_data: str = None, on_done=None):
        python = SCRIPT_DIR / ".venv" / "bin" / "python"
        if not python.exists():
            python = sys.executable
        cmd = [str(python), str(SBMM)] + args

        self._log_write(f"\n$ sbmm {' '.join(args)}\n", bold=True)
        self._busy.configure(text="⏳ Running…")
        self.update_idletasks()

        def worker():
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE if stdin_data else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True, cwd=str(SCRIPT_DIR),
                )
                if stdin_data:
                    out, _ = proc.communicate(input=stdin_data)
                    self.after(0, self._log_write, out)
                else:
                    for line in proc.stdout:
                        self.after(0, self._log_write, line)
                    proc.wait()
            except Exception as e:
                self.after(0, self._log_write, f"[error] {e}\n")
            finally:
                self.after(0, lambda: self._busy.configure(text=""))
                if on_done:
                    self.after(200, on_done)

        threading.Thread(target=worker, daemon=True).start()

    # ── Interactive terminal ──────────────────────────────────────────

    def _open_terminal(self, args: list):
        py = SCRIPT_DIR / ".venv" / "bin" / "python"
        if not py.exists():
            py = sys.executable
        cmd_str = (
            f"{py} {SBMM} {' '.join(args)}; "
            "echo; echo '--- Press Enter to close ---'; read"
        )
        for term in [
            ["x-terminal-emulator", "-e",  "bash", "-c", cmd_str],
            ["gnome-terminal",      "--",  "bash", "-c", cmd_str],
            ["xterm",               "-e",              cmd_str   ],
            ["konsole",             "-e",  "bash", "-c", cmd_str],
            ["xfce4-terminal",      "-x",  "bash", "-c", cmd_str],
            ["mate-terminal",       "-e",  "bash", "-c", cmd_str],
        ]:
            try:
                subprocess.Popen(term, cwd=str(SCRIPT_DIR))
                self._log_write(
                    f"[terminal] Launched {term[0]}: sbmm {' '.join(args)}\n"
                )
                self.after(4000, self.refresh_mods)
                return
            except FileNotFoundError:
                continue
        self._log_write(
            "[error] No terminal emulator found.\n"
            f"Run manually:  python sbmm.py {' '.join(args)}\n"
        )

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


# ---------------------------------------------------------------------------

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
