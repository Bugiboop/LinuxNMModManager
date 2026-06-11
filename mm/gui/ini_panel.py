"""
mm/gui/ini_panel.py — "Game Settings" panel mixin driven by `ini_files`
definitions in the game profile JSON.

Adds IniPanelMixin which provides:
  _build_ini_panel(parent)     — call once when creating the Game Settings page
  _refresh_ini_panel()         — call when the tab becomes visible
  _save_ini_settings()         — called by the Save button
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import customtkinter as ctk

from .tooltip import attach_tooltip


# ---------------------------------------------------------------------------
# Low-level INI helpers (module-level, no self)
# ---------------------------------------------------------------------------

def _ini_read(path: Path) -> list[str]:
    """Read an INI file, auto-detecting UTF-8 BOM vs latin-1 encoding."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    return text.splitlines(keepends=True)


def _ini_get(lines: list[str], section: str, key: str) -> Optional[str]:
    """Return the value for key in section (case-insensitive), or None."""
    in_section = False
    sec_lower = section.lower()
    key_lower = key.lower()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(";"):
            continue
        if stripped.startswith("["):
            end = stripped.find("]")
            cur = stripped[1:end].strip().lower() if end != -1 else stripped[1:].strip().lower()
            in_section = (cur == sec_lower)
            continue
        if in_section and "=" in stripped:
            k, _, v = stripped.partition("=")
            if k.strip().lower() == key_lower:
                return v.strip()
    return None


def _ini_set(lines: list[str], section: str, key: str, value: str) -> list[str]:
    """Return a new lines list with key=value set in section.

    - If the key already exists in the section it is replaced in-place,
      preserving the original line ending.
    - If the section exists but the key is missing the line is appended
      just before the next section header (or at end of section).
    - If the section itself is missing it is appended at the very end.
    """
    result: list[str] = list(lines)
    sec_lower = section.lower()
    key_lower = key.lower()

    # Detect line ending to use for new lines
    def _eol(l: str) -> str:
        if l.endswith("\r\n"):
            return "\r\n"
        if l.endswith("\r"):
            return "\r"
        return "\n"

    in_section = False
    section_start: Optional[int] = None  # index of the [section] line
    section_end: Optional[int] = None    # index after last line in section

    for i, line in enumerate(result):
        stripped = line.strip()
        if stripped.startswith("["):
            end = stripped.find("]")
            cur = stripped[1:end].strip().lower() if end != -1 else stripped[1:].strip().lower()
            if in_section:
                # We were inside the target section — key was not found
                section_end = i
                break
            if cur == sec_lower:
                in_section = True
                section_start = i
            continue

        if in_section and not stripped.startswith(";") and "=" in stripped:
            k, _, _ = stripped.partition("=")
            if k.strip().lower() == key_lower:
                # Replace in-place, preserving line ending
                eol = _eol(line)
                result[i] = f"{key}={value}{eol}"
                return result

    if in_section:
        # Reached end of file while still in section — key not found
        section_end = len(result)

    if section_start is not None:
        # Section exists, key was not found — insert before next section / end
        insert_at = section_end if section_end is not None else len(result)
        # Pick eol from the last line of the section, or "\n"
        if insert_at > 0:
            eol = _eol(result[insert_at - 1])
        else:
            eol = "\n"
        result.insert(insert_at, f"{key}={value}{eol}")
        return result

    # Section does not exist — append it at the end
    eol = "\n"
    if result:
        eol = _eol(result[-1])
    if result and result[-1].strip():
        result.append(eol)
    result.append(f"[{section}]{eol}")
    result.append(f"{key}={value}{eol}")
    return result


def _ini_write(path: Path, lines: list[str]) -> None:
    """Write lines back to path, using the same encoding as the original file."""
    raw = path.read_bytes() if path.exists() else b""
    if raw.startswith(b"\xef\xbb\xbf"):
        path.write_bytes(b"\xef\xbb\xbf" + "".join(lines).encode("utf-8"))
    else:
        try:
            "".join(lines).encode("utf-8")
            path.write_text("".join(lines), encoding="utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            path.write_text("".join(lines), encoding="latin-1")


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------

class IniPanelMixin:
    """
    Mixin that adds a "Game Settings" INI editor panel.

    Requires:
      self._cfg        — config dict (keys: games, game_root, …)
      self._profile    — active game profile dict (may contain ini_files,
                         ini_docs_path, ini_docs_relative, steam_app_id)
      self._current_game — string key for self._cfg["games"]

    The host class must call _build_ini_panel(parent) once during UI
    construction and _refresh_ini_panel() when the tab becomes visible.
    """

    # ── Path resolution ────────────────────────────────────────────────

    def _resolve_ini_docs_path(self) -> Optional[Path]:
        """Return the directory that holds the game's INI files, or None."""
        game_id = getattr(self, "_current_game", None)
        cfg     = getattr(self, "_cfg", {})
        profile = getattr(self, "_profile", {}) or {}

        # 1. Per-game explicit override in config
        if game_id:
            override = cfg.get("games", {}).get(game_id, {}).get("ini_docs_path", "")
            if override:
                p = Path(override)
                if p.is_dir():
                    return p

        # 2. Profile default
        profile_default = profile.get("ini_docs_path", "")
        if profile_default:
            p = Path(profile_default)
            if p.is_dir():
                return p

        # 3. Auto-derive from game_root + steam_app_id + ini_docs_relative
        game_root_raw = cfg.get("game_root") or (
            cfg.get("games", {}).get(game_id or "", {}).get("game_root", ""))
        ini_rel = profile.get("ini_docs_relative", "")
        steam_id = str(profile.get("steam_app_id", ""))

        if game_root_raw and ini_rel and steam_id:
            game_root = Path(game_root_raw)
            # Walk up to find steamapps/
            candidate = game_root
            for _ in range(6):
                if (candidate / "compatdata").is_dir() or candidate.name == "steamapps":
                    break
                candidate = candidate.parent

            # Try both: candidate itself and candidate / "steamapps"
            for steam_root in (candidate, candidate / "steamapps"):
                prefix_base = (
                    steam_root
                    / "compatdata"
                    / steam_id
                    / "pfx"
                    / "drive_c"
                    / "users"
                    / "steamuser"
                )
                p = prefix_base / ini_rel
                if p.is_dir():
                    return p

        return None

    # ── Build ──────────────────────────────────────────────────────────

    def _build_ini_panel(self, parent):
        """Call once to build the Game Settings page inside *parent*."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=0)

        # Scrollable content area
        self._ini_scroll = ctk.CTkScrollableFrame(
            parent,
            fg_color=("gray91", "gray14"),
            corner_radius=0,
            scrollbar_button_color=("gray70", "gray30"),
            scrollbar_button_hover_color=("gray60", "gray40"),
        )
        self._ini_scroll.grid(row=0, column=0, sticky="nsew")
        self._ini_scroll.grid_columnconfigure(0, weight=1)

        # Bind mousewheel scroll
        _canvas = self._ini_scroll._parent_canvas

        def _bind_scroll(w):
            w.bind("<Button-4>", lambda _: _canvas.yview_scroll(-1, "units"), add="+")
            w.bind("<Button-5>", lambda _: _canvas.yview_scroll(1, "units"), add="+")

        _bind_scroll(self._ini_scroll)

        # Footer: status label + Save button
        footer = ctk.CTkFrame(parent, height=52, fg_color=("gray86", "gray17"),
                              corner_radius=0)
        footer.grid(row=1, column=0, sticky="ew")
        footer.grid_propagate(False)
        footer.grid_columnconfigure(0, weight=1)

        self._ini_status_lbl = ctk.CTkLabel(
            footer, text="",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray65"),
            anchor="w",
        )
        self._ini_status_lbl.grid(row=0, column=0, sticky="w", padx=16, pady=10)

        _save_btn = ctk.CTkButton(
            footer, text="Save", width=100, height=32,
            command=self._save_ini_settings,
        )
        _save_btn.grid(row=0, column=1, sticky="e", padx=12, pady=10)
        attach_tooltip(_save_btn, "Write all changed settings back to the INI files")

        # Runtime state
        self._ini_getters: dict[str, list] = {}   # path_str → [(section, sdef, getter_fn)]

    # ── Refresh ────────────────────────────────────────────────────────

    def _refresh_ini_panel(self):
        """Rebuild the panel from profile ini_files; call when tab is shown."""
        scroll = self._ini_scroll
        for w in scroll.winfo_children():
            w.destroy()
        self._ini_getters.clear()

        profile = getattr(self, "_profile", {}) or {}
        ini_files = profile.get("ini_files", [])

        if not ini_files:
            ctk.CTkLabel(
                scroll,
                text="No INI settings defined for this game profile.",
                font=ctk.CTkFont(size=13),
                text_color=("gray55", "gray50"),
            ).grid(row=0, column=0, pady=40)
            return

        docs_path = self._resolve_ini_docs_path()

        for entry in ini_files:
            file_label = entry.get("label", entry.get("file", ""))
            filename   = entry.get("file", "")
            settings   = entry.get("settings", [])

            if not filename or not settings:
                continue

            if docs_path:
                path = docs_path / filename
            else:
                path = Path(filename)   # relative — may not exist; show grayed hint

            # Read lines once per file
            lines: list[str] = []
            if path.exists():
                try:
                    lines = _ini_read(path)
                except Exception:
                    lines = []

            self._build_ini_file_section(scroll, file_label, str(path), lines, settings)

    # ── Section builder ────────────────────────────────────────────────

    def _build_ini_file_section(
        self,
        parent,
        file_label: str,
        path_str: str,
        lines: list[str],
        settings: list[dict],
    ) -> None:
        """Render one file block (header + settings rows) into *parent*."""
        # File label (small bold uppercase header)
        ctk.CTkLabel(
            parent,
            text=file_label.upper(),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray45", "gray55"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 2))

        # 1px separator under the file label
        ctk.CTkFrame(parent, height=1, fg_color=("gray75", "gray28"),
                     corner_radius=0).pack(fill="x", padx=16, pady=(0, 4))

        if not Path(path_str).exists():
            ctk.CTkLabel(
                parent,
                text=f"File not found: {path_str}",
                font=ctk.CTkFont(size=11),
                text_color=("gray55", "gray50"),
                anchor="w",
            ).pack(fill="x", padx=24, pady=(2, 8))
            return

        last_group: Optional[str] = None

        for sdef in settings:
            group = sdef.get("group", "")

            if group != last_group:
                # Thin separator between groups (except before the first)
                if last_group is not None:
                    ctk.CTkFrame(
                        parent, height=1,
                        fg_color=("gray82", "gray22"),
                        corner_radius=0,
                    ).pack(fill="x", padx=24, pady=(6, 2))

                # Group header
                ctk.CTkLabel(
                    parent,
                    text=group,
                    font=ctk.CTkFont(size=12, weight="bold"),
                    text_color=("gray30", "gray70"),
                    anchor="w",
                ).pack(fill="x", padx=24, pady=(8, 2))

                last_group = group

            getter = self._build_ini_row(parent, path_str, lines, sdef)
            if getter is not None:
                self._ini_getters.setdefault(path_str, []).append(
                    (sdef.get("section", ""), sdef, getter)
                )

    # ── Row builder ────────────────────────────────────────────────────

    def _build_ini_row(
        self,
        parent,
        path_str: str,
        lines: list[str],
        sdef: dict,
    ):
        """Build a single label+control row; return getter callable or None."""
        row_frame = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        row_frame.pack(fill="x", padx=24, pady=3)
        row_frame.grid_columnconfigure(0, weight=1)

        label_text = sdef.get("label", sdef.get("key", ""))
        hint       = sdef.get("hint", "")
        stype      = sdef.get("type", "str")

        lbl = ctk.CTkLabel(
            row_frame,
            text=label_text,
            font=ctk.CTkFont(size=12),
            text_color=("gray15", "gray88"),
            anchor="w",
        )
        lbl.grid(row=0, column=0, sticky="w")

        if hint:
            attach_tooltip(lbl, hint)

        if stype == "resolution":
            getter = self._build_resolution_row(row_frame, lines, sdef)
        elif stype == "bool":
            getter = self._build_bool_row(row_frame, lines, sdef)
        elif stype == "choice":
            getter = self._build_choice_row(row_frame, lines, sdef)
        elif stype in ("int", "float"):
            getter = self._build_int_row(row_frame, lines, sdef)
        else:
            # Generic string entry fallback
            getter = self._build_int_row(row_frame, lines, sdef)

        return getter

    # ── Control builders ───────────────────────────────────────────────

    _RESOLUTION_PRESETS = [
        "1280 × 720  (720p)",
        "1920 × 1080 (1080p)",
        "2560 × 1440 (1440p)",
        "3440 × 1440 (UW 1440p)",
        "3840 × 2160 (4K)",
        "Custom…",
    ]

    # Maps preset label → (width, height) strings
    _PRESET_WH: dict[str, tuple[str, str]] = {
        "1280 × 720  (720p)":     ("1280", "720"),
        "1920 × 1080 (1080p)":    ("1920", "1080"),
        "2560 × 1440 (1440p)":    ("2560", "1440"),
        "3440 × 1440 (UW 1440p)": ("3440", "1440"),
        "3840 × 2160 (4K)":       ("3840", "2160"),
    }

    def _build_resolution_row(self, row_frame, lines: list[str], sdef: dict):
        key_w   = sdef.get("key_w", "iSize W")
        key_h   = sdef.get("key_h", "iSize H")
        section = sdef.get("section", "Display")

        cur_w = _ini_get(lines, section, key_w) or ""
        cur_h = _ini_get(lines, section, key_h) or ""

        # Detect if current values match a preset
        def _wh_to_preset(w: str, h: str) -> str:
            for label, (pw, ph) in _IniPanelMixin_PRESET_WH.items():
                if w == pw and h == ph:
                    return label
            return "Custom…"

        initial_preset = _wh_to_preset(cur_w, cur_h)

        ctrl_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        ctrl_frame.grid(row=0, column=1, sticky="e")

        preset_var = ctk.StringVar(value=initial_preset)

        # Custom W/H entry frame (hidden unless Custom… is selected)
        custom_frame = ctk.CTkFrame(ctrl_frame, fg_color="transparent")
        w_var = ctk.StringVar(value=cur_w)
        h_var = ctk.StringVar(value=cur_h)

        ctk.CTkLabel(custom_frame, text="W", font=ctk.CTkFont(size=11),
                     text_color=("gray45", "gray60")).pack(side="left", padx=(0, 2))
        ctk.CTkEntry(custom_frame, textvariable=w_var, width=60,
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(custom_frame, text="H", font=ctk.CTkFont(size=11),
                     text_color=("gray45", "gray60")).pack(side="left", padx=(0, 2))
        ctk.CTkEntry(custom_frame, textvariable=h_var, width=60,
                     font=ctk.CTkFont(size=12)).pack(side="left")

        def _on_preset_change(choice: str):
            if choice == "Custom…":
                custom_frame.pack(side="left", padx=(6, 0))
            else:
                custom_frame.pack_forget()
                pw, ph = IniPanelMixin._PRESET_WH[choice]
                w_var.set(pw)
                h_var.set(ph)

        menu = ctk.CTkOptionMenu(
            ctrl_frame,
            values=IniPanelMixin._RESOLUTION_PRESETS,
            variable=preset_var,
            width=220,
            font=ctk.CTkFont(size=12),
            command=_on_preset_change,
        )
        menu.pack(side="left")

        if initial_preset == "Custom…":
            custom_frame.pack(side="left", padx=(6, 0))

        def getter() -> dict:
            return {key_w: w_var.get().strip(), key_h: h_var.get().strip()}

        return getter

    def _build_bool_row(self, row_frame, lines: list[str], sdef: dict):
        key     = sdef.get("key", "")
        section = sdef.get("section", "")

        raw = _ini_get(lines, section, key)
        # For iPresentInterval, 0 means VSync off; 1 means on.
        # General convention: any nonzero int → True; "1" → True.
        try:
            initial = bool(int(raw)) if raw is not None else False
        except (ValueError, TypeError):
            initial = False

        var = ctk.BooleanVar(value=initial)
        sw = ctk.CTkSwitch(
            row_frame,
            text="",
            variable=var,
            onvalue=True,
            offvalue=False,
            width=46,
        )
        sw.grid(row=0, column=1, sticky="e")

        def getter() -> dict:
            return {key: "1" if var.get() else "0"}

        return getter

    def _build_choice_row(self, row_frame, lines: list[str], sdef: dict):
        key     = sdef.get("key", "")
        section = sdef.get("section", "")
        choices = sdef.get("choices", [])   # list of {"label": ..., "value": ...}

        labels = [c["label"] for c in choices]
        values = [c["value"] for c in choices]

        raw = _ini_get(lines, section, key) or ""
        # Find matching label for current value
        try:
            idx = values.index(raw)
            initial_label = labels[idx]
        except ValueError:
            initial_label = labels[0] if labels else ""

        var = ctk.StringVar(value=initial_label)
        ctk.CTkOptionMenu(
            row_frame,
            values=labels,
            variable=var,
            width=160,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=1, sticky="e")

        def getter() -> dict:
            sel = var.get()
            try:
                idx2 = labels.index(sel)
                return {key: values[idx2]}
            except ValueError:
                return {key: sel}

        return getter

    def _build_int_row(self, row_frame, lines: list[str], sdef: dict):
        key     = sdef.get("key", "")
        section = sdef.get("section", "")

        raw = _ini_get(lines, section, key) or ""

        var = ctk.StringVar(value=raw)
        ctk.CTkEntry(
            row_frame,
            textvariable=var,
            width=80,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=1, sticky="e")

        def getter() -> dict:
            return {key: var.get().strip()}

        return getter

    # ── Save ───────────────────────────────────────────────────────────

    def _save_ini_settings(self):
        """Write all changed INI values back to disk."""
        if not hasattr(self, "_ini_getters"):
            return

        files_saved = 0

        for path_str, getter_list in self._ini_getters.items():
            path = Path(path_str)
            if not path.exists():
                continue

            try:
                lines = _ini_read(path)
            except Exception:
                continue

            original_text = "".join(lines)
            new_lines = list(lines)

            for section, sdef, getter_fn in getter_list:
                try:
                    kv = getter_fn()
                except Exception:
                    continue
                for k, v in kv.items():
                    new_lines = _ini_set(new_lines, section, k, v)

            if "".join(new_lines) != original_text:
                try:
                    _ini_write(path, new_lines)
                    files_saved += 1
                except Exception as exc:
                    self._ini_status_lbl.configure(
                        text=f"Error saving {path.name}: {exc}",
                        text_color=("#c0392b", "#e74c3c"),
                    )
                    return

        if files_saved:
            msg = f"Saved {files_saved} file(s)."
            clear_ms = 3000
        else:
            msg = "No changes to save."
            clear_ms = 2000

        self._ini_status_lbl.configure(
            text=msg,
            text_color=("gray40", "gray65"),
        )
        self.after(clear_ms, lambda: self._ini_status_lbl.configure(text=""))


# ---------------------------------------------------------------------------
# Module-level alias so _build_resolution_row can reference the class dict
# without forward-reference issues in the lambda closure.
# ---------------------------------------------------------------------------
_IniPanelMixin_PRESET_WH = IniPanelMixin._PRESET_WH
