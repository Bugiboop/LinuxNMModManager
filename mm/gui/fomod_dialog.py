"""
Multi-page FOMOD installer wizard dialog.

Opens as a modal CTkToplevel. After the user steps through all install pages
and clicks Install, self.result contains the selections dict
{(step_idx, group_idx): [plugin_idx, ...]} or None if cancelled.
"""
from __future__ import annotations

import gc
import tkinter
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from mm.fomod import FomodConfig, FomodStep, FomodGroup, FomodPlugin

try:
    from PIL import Image as _PILImage
    _PIL = True
except ImportError:
    _PIL = False

# Small cache: we only ever display ONE image at a time (the hovered/selected
# option), so caching ~10 recent images is plenty and keeps X pixmap count ≤ 10.
_PREVIEW_CACHE_MAX = 10


class FomodWizard(ctk.CTkToplevel):
    """
    Multi-page FOMOD installer wizard.

    result:
      None  → user cancelled
      dict  → {(step_idx, group_idx): [plugin_idx, ...]}
    """

    def __init__(self, parent, config: FomodConfig, mod_dir: Path,
                 initial_selections: Optional[dict] = None):
        super().__init__(parent)
        self.result = None
        self._config = config
        self._mod_dir = mod_dir
        self._current_page = 0
        # selections[(step_idx, group_idx)] = [plugin_idx, ...]
        self._selections: dict[tuple, list] = {}
        # Small LRU cache for the single preview panel — avoids re-decoding the
        # same image when the user moves the mouse back and forth between options.
        self._image_cache: OrderedDict[str, ctk.CTkImage] = OrderedDict()
        # The CTkLabel used as the preview panel (right column), or None.
        self._preview_label: Optional[ctk.CTkLabel] = None
        # Strong ref to the currently displayed CTkImage (prevents GC while shown).
        self._preview_img_ref = None
        self._init_defaults(initial_selections)

        self.title(f"Install: {config.module_name or 'Mod Installer'}")
        self.geometry("860x640")
        self.minsize(640, 480)
        self.resizable(True, True)
        self.transient(parent)
        self.withdraw()   # hide until fully built — prevents blank flash on Linux
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Header ────────────────────────────────────────────────────
        self._header = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray20", "gray90"), anchor="w",
        )
        self._header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 0))

        # ── Page container ────────────────────────────────────────────
        self._page_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._page_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        self._page_frame.grid_columnconfigure(0, weight=1)
        self._page_frame.grid_rowconfigure(0, weight=1)

        # ── Navigation bar ────────────────────────────────────────────
        nav = ctk.CTkFrame(self, fg_color=("gray86", "gray17"), corner_radius=0)
        nav.grid(row=2, column=0, sticky="ew")
        nav.grid_columnconfigure(1, weight=1)

        self._btn_back = ctk.CTkButton(
            nav, text="← Back", width=90, height=34,
            fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
            command=self._go_back,
        )
        self._btn_back.grid(row=0, column=0, padx=(10, 4), pady=8, sticky="w")

        self._page_indicator = ctk.CTkLabel(
            nav, text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray45", "gray55"),
        )
        self._page_indicator.grid(row=0, column=1)

        self._btn_next = ctk.CTkButton(
            nav, text="Next →", width=110, height=34,
            command=self._go_next,
        )
        self._btn_next.grid(row=0, column=2, padx=4, pady=8)

        self._btn_cancel = ctk.CTkButton(
            nav, text="Cancel", width=90, height=34,
            fg_color=("#b03030", "#7a2020"),
            hover_color=("#c04040", "#962020"),
            command=self._cancel,
        )
        self._btn_cancel.grid(row=0, column=3, padx=(4, 10), pady=8, sticky="e")

        self._render_page(0)
        self.bind("<Return>", lambda _: self._go_next())

        # Deferred show: deiconify after the event loop has processed all pending
        # widget draws. Without this the window appears blank on Linux.
        self.after(50, self._show_window)

    def _show_window(self):
        """Deiconify, center over parent, and grab focus once widgets are drawn."""
        parent = self.master
        try:
            parent.update_idletasks()
            px = parent.winfo_x() + (parent.winfo_width()  - 860) // 2
            py = parent.winfo_y() + (parent.winfo_height() - 640) // 2
            self.geometry(f"860x640+{max(0, px)}+{max(0, py)}")
        except Exception:
            pass
        self.deiconify()
        self.grab_set()
        self.lift()
        self.focus_force()

    # ── Default pre-selection ─────────────────────────────────────────

    def _init_defaults(self, initial: Optional[dict]):
        if initial:
            # Restore previous selections (normalise string keys from state.json)
            for k, v in initial.items():
                if isinstance(k, str):
                    parts = k.split(",")
                    self._selections[(int(parts[0]), int(parts[1]))] = list(v)
                else:
                    self._selections[tuple(k)] = list(v)
            return

        for step_idx, step in enumerate(self._config.steps):
            for grp_idx, group in enumerate(step.groups):
                key = (step_idx, grp_idx)
                if group.type == "SelectAll":
                    self._selections[key] = list(range(len(group.plugins)))
                elif group.type in ("SelectExactlyOne", "SelectAtLeastOne"):
                    # Pre-select first Recommended, then first Required, else first plugin
                    chosen = None
                    for i, p in enumerate(group.plugins):
                        if p.type_descriptor in ("Required", "Recommended") and chosen is None:
                            chosen = i
                    self._selections[key] = [chosen if chosen is not None
                                              else (0 if group.plugins else -1)]
                    if self._selections[key] == [-1]:
                        self._selections[key] = []
                else:
                    # SelectAny / SelectAtMostOne: pre-select Recommended ones
                    self._selections[key] = [
                        i for i, p in enumerate(group.plugins)
                        if p.type_descriptor == "Recommended"
                    ]

    # ── Page rendering ────────────────────────────────────────────────

    def _render_page(self, step_idx: int):
        # Release the preview image ref before destroying the label widget.
        self._preview_img_ref = None
        self._preview_label = None

        for w in self._page_frame.winfo_children():
            w.destroy()

        # GC pass frees any lingering PhotoImage objects from the destroyed
        # widgets and their associated X server pixmaps.
        gc.collect()

        visible_steps = [i for i in range(len(self._config.steps))
                         if self._step_visible(i)]
        n_visible = len(visible_steps)
        vis_pos = (visible_steps.index(step_idx) + 1) if step_idx in visible_steps else 1

        step = self._config.steps[step_idx]
        self._header.configure(
            text=f"Step {vis_pos} of {n_visible}:  {step.name}")
        self._page_indicator.configure(text=f"{vis_pos} / {n_visible}")
        has_prev = self._prev_visible_step(step_idx) is not None
        self._btn_back.configure(state="normal" if has_prev else "disabled")
        is_last = self._next_visible_step(step_idx) is None
        self._btn_next.configure(text="Install" if is_last else "Next →")

        # Detect whether any plugin on this page has a preview image.
        has_images = _PIL and any(
            plugin.image_path
            for grp in step.groups
            for plugin in grp.plugins
        )

        # ── Preview panel (right column) ──────────────────────────────
        # By keeping at most ONE CTkImage live at any time we avoid exhausting
        # the X server's pixmap allocation — the root cause of the BadAlloc
        # crash on image-heavy pages like Eyes of Beauty's Pale Blue Eyes step.
        if has_images:
            self._page_frame.grid_columnconfigure(1, weight=0, minsize=230)
            pv_frame = ctk.CTkFrame(
                self._page_frame, width=220,
                fg_color=("gray84", "gray18"), corner_radius=8,
            )
            pv_frame.grid(row=0, column=1, sticky="ns", padx=(6, 2), pady=2)
            pv_frame.grid_propagate(False)

            self._preview_label = ctk.CTkLabel(
                pv_frame,
                text="Hover or select\nan option to\nsee a preview",
                font=ctk.CTkFont(size=11),
                text_color=("gray55", "gray50"),
                wraplength=210,
            )
            self._preview_label.pack(expand=True, fill="both", padx=6, pady=8)
        else:
            # No images on this page — use full width.
            self._page_frame.grid_columnconfigure(1, weight=0, minsize=0)

        # ── Scrollable options area (left / full column) ───────────────
        scroll = ctk.CTkScrollableFrame(self._page_frame, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        canvas = scroll._parent_canvas
        self.bind_all("<Button-4>", lambda e, c=canvas: c.yview_scroll(-1, "units"))
        self.bind_all("<Button-5>", lambda e, c=canvas: c.yview_scroll(1, "units"))

        # Track the initial preview to show when the page loads.
        initial_preview_path: Optional[Path] = None

        row = 0
        for grp_idx, group in enumerate(step.groups):
            ctk.CTkLabel(
                scroll, text=group.name,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=("gray25", "gray85"), anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=16, pady=(14, 2))
            row += 1

            grp_frame = ctk.CTkFrame(scroll, fg_color=("gray88", "gray16"),
                                     corner_radius=6)
            grp_frame.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 4))
            grp_frame.grid_columnconfigure(0, weight=1)
            row += 1

            ip = self._render_group(grp_frame, step_idx, grp_idx, group)
            if ip and initial_preview_path is None:
                initial_preview_path = ip

        # Show the selected option's image when the page first loads.
        if initial_preview_path:
            self._show_preview(initial_preview_path)

    def _render_group(self, parent, step_idx: int, grp_idx: int,
                      group: FomodGroup) -> Optional[Path]:
        """Render all plugin rows for the group. Returns the pre-selected image path (if any)."""
        key = (step_idx, grp_idx)
        is_radio = group.type in ("SelectExactlyOne", "SelectAtMostOne")

        initial_img: Optional[Path] = None

        if is_radio:
            current = self._selections.get(key, [])
            sel_var = ctk.IntVar(value=current[0] if current else -1)
            if group.type == "SelectAtMostOne":
                ctk.CTkRadioButton(
                    parent, text="(None — skip this group)", value=-1, variable=sel_var,
                    font=ctk.CTkFont(size=12),
                    command=lambda k=key, v=sel_var: (
                        self._on_radio(k, v), self._show_preview(None)),
                ).grid(row=0, column=0, sticky="w", padx=14, pady=(8, 2))

            pre_selected_idx = current[0] if current else -1

            for i, plugin in enumerate(group.plugins):
                img_path = (
                    self._mod_dir / plugin.image_path.replace("\\", "/")
                    if plugin.image_path else None
                )
                self._render_plugin_row_radio(
                    parent, plugin, i,
                    i + (1 if group.type == "SelectAtMostOne" else 0),
                    sel_var, key, img_path,
                )
                if i == pre_selected_idx and img_path:
                    initial_img = img_path
        else:
            pre_checked = self._selections.get(key, [])
            for i, plugin in enumerate(group.plugins):
                is_all = group.type == "SelectAll"
                img_path = (
                    self._mod_dir / plugin.image_path.replace("\\", "/")
                    if plugin.image_path else None
                )
                self._render_plugin_row_checkbox(
                    parent, plugin, i, i,
                    step_idx, grp_idx, is_all, img_path,
                )
                if i in pre_checked and img_path and initial_img is None:
                    initial_img = img_path

        return initial_img

    def _render_plugin_row_radio(self, parent, plugin: FomodPlugin, idx: int,
                                  grid_row: int, sel_var: ctk.IntVar, key: tuple,
                                  img_path: Optional[Path]):
        disabled = plugin.type_descriptor in ("NotUsable",)
        is_required = plugin.type_descriptor == "Required"

        pf = ctk.CTkFrame(parent, fg_color="transparent")
        pf.grid(row=grid_row, column=0, sticky="ew", padx=8, pady=2)
        pf.grid_columnconfigure(0, weight=1)

        label = plugin.name + (" (required)" if is_required else "")
        rb = ctk.CTkRadioButton(
            pf, text=label, value=idx, variable=sel_var,
            font=ctk.CTkFont(size=12),
            state="disabled" if (disabled or is_required) else "normal",
            command=lambda k=key, v=sel_var, ip=img_path: (
                self._on_radio(k, v), self._show_preview(ip)),
        )
        rb.grid(row=0, column=0, sticky="w", padx=(6, 4))

        if plugin.description:
            ctk.CTkLabel(
                pf, text=plugin.description,
                font=ctk.CTkFont(size=11),
                text_color=("gray50", "gray55"), anchor="w",
                wraplength=480, justify="left",
            ).grid(row=1, column=0, sticky="w", padx=(28, 8), pady=(0, 2))

        # Hover → show this option's preview image without changing selection.
        if img_path:
            pf.bind("<Enter>", lambda e, ip=img_path: self._show_preview(ip))
            rb.bind("<Enter>",  lambda e, ip=img_path: self._show_preview(ip))

    def _render_plugin_row_checkbox(self, parent, plugin: FomodPlugin, idx: int,
                                     grid_row: int, step_idx: int, grp_idx: int,
                                     is_all: bool, img_path: Optional[Path]):
        key = (step_idx, grp_idx)
        is_required = plugin.type_descriptor == "Required"
        is_not_usable = plugin.type_descriptor == "NotUsable"
        currently = idx in self._selections.get(key, [])
        var = ctk.BooleanVar(value=currently or is_required or is_all)

        pf = ctk.CTkFrame(parent, fg_color="transparent")
        pf.grid(row=grid_row, column=0, sticky="ew", padx=8, pady=2)
        pf.grid_columnconfigure(0, weight=1)

        label = plugin.name + (" (required)" if is_required else "")
        state = "disabled" if (is_all or is_required or is_not_usable) else "normal"
        cb = ctk.CTkCheckBox(
            pf, text=label, variable=var,
            font=ctk.CTkFont(size=12),
            state=state,
            command=lambda v=var, i=idx, k=key, ip=img_path: (
                self._on_checkbox(k, i, v), self._show_preview(ip)),
        )
        cb.grid(row=0, column=0, sticky="w", padx=(6, 4))

        if plugin.description:
            ctk.CTkLabel(
                pf, text=plugin.description,
                font=ctk.CTkFont(size=11),
                text_color=("gray50", "gray55"), anchor="w",
                wraplength=480, justify="left",
            ).grid(row=1, column=0, sticky="w", padx=(28, 8), pady=(0, 2))

        if img_path:
            pf.bind("<Enter>", lambda e, ip=img_path: self._show_preview(ip))
            cb.bind("<Enter>",  lambda e, ip=img_path: self._show_preview(ip))

    # ── Preview panel ─────────────────────────────────────────────────

    def _show_preview(self, img_path: Optional[Path]):
        """Load img_path into the right-column preview label (one pixmap at a time)."""
        if self._preview_label is None:
            return

        # Release the previous X pixmap before allocating the new one.
        self._preview_img_ref = None

        if img_path is None or not _PIL or not img_path.exists():
            try:
                self._preview_label.configure(image=None,
                                              text="No preview\navailable")
            except Exception:
                pass
            return

        try:
            key = str(img_path)
            if key in self._image_cache:
                self._image_cache.move_to_end(key)
                ctk_img = self._image_cache[key]
            else:
                pil = _PILImage.open(img_path).convert("RGB")
                pil.thumbnail((210, 160), _PILImage.LANCZOS)
                w, h = pil.size
                ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(w, h))
                self._image_cache[key] = ctk_img
                while len(self._image_cache) > _PREVIEW_CACHE_MAX:
                    self._image_cache.popitem(last=False)

            self._preview_img_ref = ctk_img
            self._preview_label.configure(image=ctk_img, text="")
        except Exception:
            try:
                self._preview_label.configure(image=None,
                                              text="Preview\nnot available")
            except Exception:
                pass

    # ── Event handlers ────────────────────────────────────────────────

    def _on_radio(self, key: tuple, var: ctk.IntVar):
        val = var.get()
        self._selections[key] = [val] if val >= 0 else []
        self._update_nav_buttons()

    def _on_checkbox(self, key: tuple, idx: int, var: ctk.BooleanVar):
        sel = set(self._selections.get(key, []))
        if var.get():
            sel.add(idx)
        else:
            sel.discard(idx)
        self._selections[key] = sorted(sel)
        self._update_nav_buttons()

    def _update_nav_buttons(self):
        """Refresh Next/Install label and page count after a selection changes."""
        is_last = self._next_visible_step(self._current_page) is None
        self._btn_next.configure(text="Install" if is_last else "Next →")
        visible_steps = [i for i in range(len(self._config.steps))
                         if self._step_visible(i)]
        n_visible = len(visible_steps)
        vis_pos = (visible_steps.index(self._current_page) + 1
                   if self._current_page in visible_steps else 1)
        self._page_indicator.configure(text=f"{vis_pos} / {n_visible}")

    # ── Navigation ────────────────────────────────────────────────────

    def _compute_active_flags(self, up_to_step: int) -> dict:
        """Return all condition flags accumulated by selections on steps 0..up_to_step-1."""
        flags: dict = {}
        for step_idx in range(up_to_step):
            step = self._config.steps[step_idx]
            for grp_idx, group in enumerate(step.groups):
                for plugin_idx in self._selections.get((step_idx, grp_idx), []):
                    if 0 <= plugin_idx < len(group.plugins):
                        flags.update(group.plugins[plugin_idx].flags)
        return flags

    def _step_visible(self, step_idx: int) -> bool:
        from mm.fomod import _step_is_visible
        return _step_is_visible(
            self._config.steps[step_idx],
            self._compute_active_flags(step_idx),
        )

    def _next_visible_step(self, from_step: int) -> Optional[int]:
        for i in range(from_step + 1, len(self._config.steps)):
            if self._step_visible(i):
                return i
        return None

    def _prev_visible_step(self, from_step: int) -> Optional[int]:
        for i in range(from_step - 1, -1, -1):
            if self._step_visible(i):
                return i
        return None

    def _last_visible_step(self) -> int:
        for i in range(len(self._config.steps) - 1, -1, -1):
            if self._step_visible(i):
                return i
        return len(self._config.steps) - 1

    def _go_next(self):
        if not self._validate_current():
            return
        next_page = self._next_visible_step(self._current_page)
        if next_page is None:
            self._finish()
        else:
            self._current_page = next_page
            self._render_page(next_page)

    def _go_back(self):
        prev = self._prev_visible_step(self._current_page)
        if prev is not None:
            self._current_page = prev
            self._render_page(prev)

    def _validate_current(self) -> bool:
        step = self._config.steps[self._current_page]
        for grp_idx, group in enumerate(step.groups):
            key = (self._current_page, grp_idx)
            n = len(self._selections.get(key, []))
            if group.type == "SelectExactlyOne" and n != 1:
                self._show_error(
                    f'"{group.name}" requires exactly one selection.')
                return False
            if group.type == "SelectAtLeastOne" and n < 1:
                self._show_error(
                    f'"{group.name}" requires at least one selection.')
                return False
        return True

    def _show_error(self, msg: str):
        tkinter.messagebox.showwarning("Selection Required", msg, parent=self)

    def _finish(self):
        self.result = dict(self._selections)
        self._cleanup()
        self.destroy()

    def _cancel(self):
        self.result = None
        self._cleanup()
        self.destroy()

    def _cleanup(self):
        try:
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")
        except Exception:
            pass
        self._preview_img_ref = None
        self._preview_label = None
        self._image_cache.clear()
        gc.collect()
