"""Asset search popup — searches raw bytes of installed mod files for a string."""

import mmap
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tkinter import ttk

import customtkinter as ctk

# .ucas is pure compressed bulk data — no readable strings, skip it
_SEARCH_EXTS = {".pak", ".utoc"}

# Compiled once: runs of ≥6 printable ASCII bytes, and UTF-16LE equivalents
_ASCII_RE = re.compile(rb"[\x20-\x7e\t]{6,}")
_UTF16_RE = re.compile(rb"(?:[\x20-\x7e]\x00){6,}")


def _search_data(buf, query_lower: str) -> list[str]:
    """Extract readable strings from *buf* and return those containing the query."""
    seen: set[str] = set()
    results: list[str] = []

    for m in _ASCII_RE.finditer(buf):
        s = m.group().decode("ascii", errors="replace").strip()
        if query_lower in s.lower() and s not in seen:
            seen.add(s)
            results.append(s[:140])

    for m in _UTF16_RE.finditer(buf):
        s = m.group()[::2].decode("ascii", errors="replace").strip()
        if query_lower in s.lower() and s not in seen:
            seen.add(s)
            results.append(s[:140])

    return results


def _search_file(path: Path, query_lower: str) -> list[str]:
    """Search *path* for strings containing *query_lower*."""
    try:
        size = path.stat().st_size
        if size == 0:
            return []
        with open(path, "rb") as f:
            if size > 32 * 1024 * 1024:          # >32 MB: mmap avoids a full read
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    return _search_data(mm, query_lower)
            else:
                return _search_data(f.read(), query_lower)
    except Exception:
        return []


_WORKERS = min(8, (os.cpu_count() or 4))


def _do_search(state: dict, query: str, enabled_only: bool, search_names: bool,
               mods_dir: Path | None,
               progress_cb, result_cb, done_cb):
    """Search mod files in parallel using a thread pool."""
    query_lower = query.lower()

    # Build candidate list: (mod_name, Path)
    candidates: list[tuple[str, Path]] = []
    seen_paths: set = set()

    for mod_name, info in state.get("mods", {}).items():
        is_enabled = info.get("enabled", False)
        if enabled_only and not is_enabled:
            continue

        if is_enabled:
            # Enabled mod: source paths are recorded in symlinks + disabled_symlinks
            for sl in info.get("symlinks", []) + info.get("disabled_symlinks", []):
                p = Path(sl["target"])
                if p.suffix.lower() in _SEARCH_EXTS and p not in seen_paths and p.exists():
                    candidates.append((mod_name, p))
                    seen_paths.add(p)
        elif mods_dir:
            # Disabled mod: symlinks were cleared; scan the folder on disk
            mod_folder = mods_dir / mod_name
            if mod_folder.is_dir():
                for root, _dirs, files in os.walk(mod_folder):
                    for fname in files:
                        p = Path(root) / fname
                        if p.suffix.lower() in _SEARCH_EXTS and p not in seen_paths:
                            candidates.append((mod_name, p))
                            seen_paths.add(p)

    # When not filtering by enabled, also pick up mod folders never tracked in state
    if not enabled_only and mods_dir and mods_dir.is_dir():
        tracked = set(state.get("mods", {}).keys())
        for mod_folder in mods_dir.iterdir():
            if mod_folder.is_dir() and mod_folder.name not in tracked:
                for root, _dirs, files in os.walk(mod_folder):
                    for fname in files:
                        p = Path(root) / fname
                        if p.suffix.lower() in _SEARCH_EXTS and p not in seen_paths:
                            candidates.append((mod_folder.name, p))
                            seen_paths.add(p)

    total = len(candidates)
    done_count = 0

    # Emit name-match rows before the content scan
    if search_names:
        seen_mod_names: set[str] = set()
        for mod_name, path in candidates:
            if mod_name not in seen_mod_names and query_lower in mod_name.lower():
                seen_mod_names.add(mod_name)
                result_cb(mod_name, "", "[mod name match]")
            if query_lower in path.name.lower():
                result_cb(mod_name, path.name, "[filename match]")

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {
            pool.submit(_search_file, path, query_lower): (mod_name, path)
            for mod_name, path in candidates
        }
        for future in as_completed(futures):
            mod_name, path = futures[future]
            done_count += 1
            progress_cb(done_count, total, mod_name)
            try:
                for ctx in future.result():
                    result_cb(mod_name, path.name, ctx)
            except Exception:
                pass

    done_cb(total)


class AssetSearchWindow(ctk.CTkToplevel):
    """Popup that searches raw file contents of installed mods for a string."""

    def __init__(self, master, state: dict, mods_dir: Path | None = None, **kw):
        super().__init__(master, **kw)
        self._state    = state
        self._mods_dir = mods_dir
        self._thread: threading.Thread | None = None

        self.title("Asset Search")
        self.geometry("960x560")
        self.resizable(True, True)

        self._build()
        # grab_set must be deferred until the window is actually mapped
        self.after(100, self._safe_grab)

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    # ── Layout ────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ── Search bar ────────────────────────────────────────────────
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        bar.grid_columnconfigure(0, weight=1)

        self._query_var = ctk.StringVar()
        entry = ctk.CTkEntry(
            bar, textvariable=self._query_var,
            placeholder_text="Internal asset path or string to find…",
            height=34, font=ctk.CTkFont(size=13),
        )
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        entry.bind("<Return>", lambda _: self._start_search())
        entry.focus_set()

        self._btn_search = ctk.CTkButton(
            bar, text="Search", width=90, height=34,
            command=self._start_search,
        )
        self._btn_search.grid(row=0, column=1)

        self._enabled_only = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            bar, text="Enabled mods only",
            variable=self._enabled_only,
            font=ctk.CTkFont(size=11),
        ).grid(row=0, column=2, padx=(12, 0))

        self._search_names = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            bar, text="Include mod & file names",
            variable=self._search_names,
            font=ctk.CTkFont(size=11),
        ).grid(row=0, column=3, padx=(12, 0))

        # ── Status line ───────────────────────────────────────────────
        self._status_var = ctk.StringVar(
            value="Search mod files for an internal game asset path or string.")
        ctk.CTkLabel(
            self, textvariable=self._status_var,
            font=ctk.CTkFont(size=11),
            text_color=("gray45", "gray55"),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

        # ── Results table ─────────────────────────────────────────────
        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "AS.Treeview",
            background="#1e1e1e", foreground="#d4d4d4",
            fieldbackground="#1e1e1e", rowheight=22,
            font=("monospace", 10),
        )
        style.configure(
            "AS.Treeview.Heading",
            background="#2d2d2d", foreground="#aaaaaa",
            font=("sans-serif", 10, "bold"),
        )
        style.map("AS.Treeview", background=[("selected", "#264f78")])

        self._tree = ttk.Treeview(
            tree_frame, style="AS.Treeview",
            columns=("mod", "file", "context"),
            show="headings", selectmode="browse",
        )
        self._tree.heading("mod",     text="Mod Name")
        self._tree.heading("file",    text="File")
        self._tree.heading("context", text="Matched String")
        self._tree.column("mod",     width=280, minwidth=160, stretch=False)
        self._tree.column("file",    width=160, minwidth=100, stretch=False)
        self._tree.column("context", width=460, minwidth=200, stretch=True)
        self._tree.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self._tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=vsb.set)

        hsb = ttk.Scrollbar(tree_frame, orient="horizontal",
                            command=self._tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self._tree.configure(xscrollcommand=hsb.set)

    # ── Search logic ──────────────────────────────────────────────────

    def _start_search(self):
        query = self._query_var.get().strip()
        if not query:
            return
        if self._thread and self._thread.is_alive():
            return

        self._tree.delete(*self._tree.get_children())
        self._btn_search.configure(state="disabled", text="Searching…")
        self._status_var.set("Scanning mod files…")

        self._thread = threading.Thread(
            target=_do_search,
            args=(
                self._state,
                query,
                self._enabled_only.get(),
                self._search_names.get(),
                self._mods_dir,
                self._on_progress,
                self._on_result,
                self._on_done,
            ),
            daemon=True,
        )
        self._thread.start()

    def _on_progress(self, done: int, total: int, current: str):
        self.after(0, lambda: self._status_var.set(
            f"[{done}/{total}]  {Path(current).name[:70]}…"))

    def _on_result(self, mod_name: str, filename: str, context: str):
        self.after(0, lambda: self._tree.insert(
            "", "end", values=(mod_name, filename, context)))

    def _on_done(self, total: int):
        count = len(self._tree.get_children())
        msg   = (f"Found {count} match(es) across {total} file(s) searched."
                 if count else
                 f"No matches found in {total} file(s) searched.")
        self.after(0, lambda: (
            self._status_var.set(msg),
            self._btn_search.configure(state="normal", text="Search"),
        ))
