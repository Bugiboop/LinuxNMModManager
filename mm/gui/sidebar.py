import tkinter as tk

import customtkinter as ctk

from .constants import (
    _CARD_H, _SEP_H, _V_PAD, _V_BUF,
    _CARD_NORMAL, _CARD_FOCUSED, _CARD_CHECKED,
)
from .tooltip import attach_tooltip


class SidebarMixin:
    """Mixin providing sidebar + virtual mod list for ModManagerApp."""

    # ── Sidebar ───────────────────────────────────────────────────────

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=330, corner_radius=0,
                          fg_color=("gray90", "gray15"))
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.grid_rowconfigure(2, weight=1)
        sb.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(sb, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 4))
        hdr.grid_columnconfigure(0, weight=1)

        self._game_menu = ctk.CTkOptionMenu(
            hdr, variable=self._game_var,
            values=["Stellar Blade"],
            width=150, height=24,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._on_game_select,
        )
        self._game_menu.grid(row=0, column=0, sticky="w")

        _add_btn = ctk.CTkButton(
            hdr, text="+", width=24, height=24,
            fg_color="transparent", hover_color=("gray70", "gray30"),
            font=ctk.CTkFont(size=14),
            command=self._add_game_dialog,
        )
        _add_btn.grid(row=0, column=1, padx=(4, 0))
        attach_tooltip(_add_btn, "Add a new game")

        self._count_label = ctk.CTkLabel(hdr, text="",
                                         font=ctk.CTkFont(size=11),
                                         text_color=("gray45", "gray55"))
        self._count_label.grid(row=0, column=2, sticky="e", padx=(8, 0))

        _settings_btn = ctk.CTkButton(
            hdr, text="⚙", width=28, height=22,
            fg_color="transparent", hover_color=("gray70", "gray30"),
            font=ctk.CTkFont(size=14),
            command=self._open_settings,
        )
        _settings_btn.grid(row=0, column=3, sticky="e", padx=(4, 0))
        attach_tooltip(_settings_btn, "Open settings")

        _check_btn = ctk.CTkButton(
            hdr, text="Check", width=52, height=22,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=11),
            command=lambda: self._run_bg(["--check"], on_done=self.refresh_mods),
        )
        _check_btn.grid(row=0, column=4, sticky="e", padx=(4, 0))
        attach_tooltip(_check_btn, "Verify all symlinks are intact and check for conflicts")

        # Sort control
        sort_bar = ctk.CTkFrame(sb, fg_color="transparent")
        sort_bar.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 4))
        sort_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(sort_bar, text="Sort",
                     font=ctk.CTkFont(size=11),
                     text_color=("gray50", "gray55"),
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkOptionMenu(
            sort_bar,
            values=["Name A→Z", "Name Z→A", "Enabled first", "Disabled first"],
            variable=self._sort_var,
            width=160, height=24,
            font=ctk.CTkFont(size=11),
            command=lambda _: self.refresh_mods(),
        ).grid(row=0, column=1, sticky="e")

        list_outer = ctk.CTkFrame(sb, fg_color="transparent")
        list_outer.grid(row=2, column=0, sticky="nsew", padx=6)
        list_outer.grid_rowconfigure(0, weight=1)
        list_outer.grid_columnconfigure(0, weight=1)

        self._vlist_canvas = tk.Canvas(
            list_outer, highlightthickness=0, bd=0, bg="#242424",
        )
        self._vlist_canvas.grid(row=0, column=0, sticky="nsew")

        _vscroll = ctk.CTkScrollbar(list_outer, command=self._vlist_yview)
        _vscroll.grid(row=0, column=1, sticky="ns")
        self._vlist_canvas.configure(yscrollcommand=_vscroll.set)

        self._vlist_canvas.bind("<Configure>",  self._vlist_on_configure)
        self._vlist_canvas.bind("<Button-4>",   lambda _: self._vlist_scroll(-1))
        self._vlist_canvas.bind("<Button-5>",   lambda _: self._vlist_scroll(1))

        btns = ctk.CTkFrame(sb, fg_color="transparent")
        btns.grid(row=3, column=0, sticky="ew", padx=12, pady=12)
        btns.grid_columnconfigure((0, 1), weight=1)

        _ena_all = ctk.CTkButton(
            btns, text="Enable All", height=34,
            command=lambda: self._run_interactive(["--enable"], on_done=self.refresh_mods),
        )
        _ena_all.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        attach_tooltip(_ena_all, "Enable every mod in the list")

        _dis_all = ctk.CTkButton(
            btns, text="Disable All", height=34,
            fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
            command=lambda: self._run_bg(["--disable"], on_done=self.refresh_mods),
        )
        _dis_all.grid(row=0, column=1, padx=(4, 0), sticky="ew")
        attach_tooltip(_dis_all, "Disable every currently enabled mod")

        self._btn_enable_sel = ctk.CTkButton(
            btns, text="Enable Selected", height=34, state="disabled",
            fg_color=("gray72", "gray30"), hover_color=("#1a6aaa", "#1a5a8a"),
            command=self._enable_selected,
        )
        self._btn_enable_sel.grid(row=1, column=0, padx=(0, 4), pady=(6, 0), sticky="ew")
        attach_tooltip(self._btn_enable_sel, "Enable all checked mods")

        self._btn_disable_sel = ctk.CTkButton(
            btns, text="Disable Selected", height=34, state="disabled",
            fg_color=("gray72", "gray30"), hover_color=("gray52", "gray42"),
            command=self._disable_selected,
        )
        self._btn_disable_sel.grid(row=1, column=1, padx=(4, 0), pady=(6, 0), sticky="ew")
        attach_tooltip(self._btn_disable_sel, "Disable all checked mods")

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
        attach_tooltip(self._btn_clear_sel, "Clear all checkbox selections")

        _refresh_btn = ctk.CTkButton(
            util, text="↺  Refresh", height=28,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=12),
            command=self.refresh_mods,
        )
        _refresh_btn.grid(row=0, column=1, padx=(3, 0), sticky="ew")
        attach_tooltip(_refresh_btn, "Reload mod list and state from disk")

    # ── Virtual list helpers ───────────────────────────────────────────

    def _vlist_yview(self, *args):
        """Scrollbar command: move canvas immediately, debounce card creation."""
        self._vlist_canvas.yview(*args)
        # Debounce: rapid scrollbar drags fire hundreds of events; don't create
        # widgets on every pixel — wait until the user stops dragging.
        if self._vlist_render_after is not None:
            self.after_cancel(self._vlist_render_after)
        self._vlist_render_after = self.after(120, self._vlist_render_deferred)

    def _vlist_render_deferred(self):
        self._vlist_render_after = None
        self._vlist_render()

    def _vlist_scroll(self, direction: int):
        """Mouse-wheel scroll: one step at a time, render immediately."""
        self._vlist_canvas.yview_scroll(direction, "units")
        self._vlist_render()

    def _vlist_on_configure(self, event=None):
        """Canvas resized: update card widths and re-render."""
        if event and event.width > 10:
            new_w = event.width - 4
            for w_data in self._vlist_widgets.values():
                self._vlist_canvas.itemconfigure(w_data["cid"], width=new_w)
        self._vlist_render()

    _SHELL_BATCH = 20  # outer shells to create per event-loop tick

    def _vlist_batch_shells(self, start: int):
        """Phase 1: create outer card shells in batches, keeping UI responsive."""
        canvas   = self._vlist_canvas
        canvas_w = max(canvas.winfo_width() - 4, 10)
        items    = self._vlist_items
        end      = min(start + self._SHELL_BATCH, len(items))

        for i in range(start, end):
            if i in self._vlist_widgets:
                continue
            item  = items[i]
            yo    = self._vlist_yoffs[i]
            shell = self._vlist_create_shell(item)
            cid   = canvas.create_window(2, yo, window=shell,
                                         anchor="nw", width=canvas_w)
            self._vlist_widgets[i] = {"frame": shell, "cid": cid}
            if item["type"] in ("mod", "archive"):
                self._cards[item["name"]] = shell
            if item["type"] == "sep":
                self._vlist_populated.add(i)   # sep is complete at shell creation

        # After the first batch, populate visible shells immediately so the
        # user sees content while the rest of the shells are still being created.
        if start == 0:
            self._vlist_render()

        if end < len(items):
            self.after(0, lambda: self._vlist_batch_shells(end))

    def _vlist_create_shell(self, item: dict) -> ctk.CTkFrame:
        """Phase 1: create the outer card frame (border only, no internal widgets)."""
        itype = item["type"]
        name  = item["name"]

        if itype == "sep":
            f = ctk.CTkFrame(self._vlist_canvas, fg_color="transparent",
                             height=_SEP_H)
            ctk.CTkLabel(
                f, text=name,
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=("gray50", "gray50"), anchor="w",
            ).place(x=10, y=8)
            self._bind_canvas_scroll(f)
            return f

        checked = (name in self._selected) if itype == "mod" else False
        focused = name == self._focused
        if checked:
            bg, bw = _CARD_CHECKED, 2
        elif focused:
            bg, bw = _CARD_FOCUSED, 1
        else:
            bg, bw = _CARD_NORMAL, 0

        shell = ctk.CTkFrame(
            self._vlist_canvas, fg_color=bg, corner_radius=7,
            border_width=bw, border_color="#2980b9",
            height=_CARD_H,
        )
        shell.grid_propagate(False)
        self._bind_canvas_scroll(shell)
        return shell

    def _vlist_render(self):
        """Phase 2: populate shells that are visible but not yet filled."""
        canvas   = self._vlist_canvas
        canvas_h = canvas.winfo_height()
        if canvas_h < 10 or not self._vlist_items:
            return

        y_top   = canvas.canvasy(0)
        y_bot   = canvas.canvasy(canvas_h)
        buf_px  = _V_BUF * (_CARD_H + _V_PAD)
        vis_top = y_top - buf_px
        vis_bot = y_bot + buf_px

        for i, yo in enumerate(self._vlist_yoffs):
            if i in self._vlist_populated:
                continue
            h = _SEP_H if self._vlist_items[i]["type"] == "sep" else _CARD_H
            if yo + h > vis_top and yo < vis_bot:
                if i in self._vlist_widgets:
                    self._vlist_populate_card(i)

    def _vlist_populate_card(self, idx: int):
        """Phase 2: fill a shell frame with its internal widgets."""
        item  = self._vlist_items[idx]
        shell = self._vlist_widgets[idx]["frame"]
        itype = item["type"]
        name  = item["name"]

        if itype == "mod":
            is_on    = item["is_on"]
            symlinks = item["symlinks"]
            exists   = item["exists"]
            disp     = item["disp"]
            checked  = name in self._selected
        else:  # archive
            is_on    = False
            symlinks = 0
            exists   = False
            disp     = item["disp"]
            checked  = False

        shell.grid_propagate(True)
        shell.grid_columnconfigure(2, weight=1)

        # col 0 – checkbox (mod) or spacer (archive)
        if itype == "mod":
            cb_var = ctk.BooleanVar(value=checked)
            self._checkboxvars[name] = cb_var
            cb = ctk.CTkCheckBox(
                shell, text="", variable=cb_var,
                width=20, checkbox_width=15, checkbox_height=15,
                command=lambda n=name, v=cb_var: self._on_checkbox_change(n, v),
            )
            cb.grid(row=0, column=0, padx=(8, 2), pady=8)
            if not exists:
                cb.configure(state="disabled")
        else:
            ctk.CTkLabel(shell, text="", width=20).grid(
                row=0, column=0, padx=(8, 2))

        # col 1 – status dot
        if itype == "archive":
            dot_col = "#e67e22"
        elif is_on:
            dot_col = "#27ae60"
        else:
            dot_col = ("gray52", "gray40")
        dot_lbl = ctk.CTkLabel(shell, text="●", font=ctk.CTkFont(size=13),
                               text_color=dot_col, width=22)
        dot_lbl.grid(row=0, column=1, padx=(2, 4), pady=8)

        # col 2 – mod name
        name_col = ("gray52", "gray46") if (itype == "mod" and not exists) \
                   else ("gray10", "gray90")
        name_lbl = ctk.CTkLabel(shell, text=disp, font=ctk.CTkFont(size=12),
                                text_color=name_col, anchor="w")
        name_lbl.grid(row=0, column=2, sticky="w", padx=4)
        self._name_labels[name] = name_lbl

        # col 3 – badge
        badge = None
        if itype == "mod" and is_on and symlinks:
            badge = ctk.CTkLabel(
                shell, text=str(symlinks),
                font=ctk.CTkFont(size=10),
                text_color=("gray50", "gray48"),
                fg_color=("gray70", "gray30"),
                corner_radius=4, width=28, height=18,
            )
            badge.grid(row=0, column=3, padx=6)
        elif itype == "archive":
            badge = ctk.CTkLabel(
                shell, text="archive",
                font=ctk.CTkFont(size=10),
                text_color=("#7a4a10", "#e09050"),
                fg_color=("#f5dfc0", "#3a2a10"),
                corner_radius=4, width=50, height=18,
            )
            badge.grid(row=0, column=3, padx=6)
        else:
            ctk.CTkLabel(shell, text="", width=28).grid(row=0, column=3)

        # col 4 – switch (mod) or spacer (archive)
        if itype == "mod":
            var = ctk.BooleanVar(value=is_on)
            sw  = ctk.CTkSwitch(
                shell, text="", variable=var, width=46,
                onvalue=True, offvalue=False,
                command=lambda n=name, v=var: self._toggle(n, v),
            )
            sw.grid(row=0, column=4, padx=(4, 10), pady=8)
            if not exists:
                sw.configure(state="disabled")
            self._switches[name] = (var, sw)
        else:
            ctk.CTkLabel(shell, text="", width=56).grid(row=0, column=4)

        # Click-to-focus bindings
        if (itype == "mod" and exists) or itype == "archive":
            click_widgets = [shell, dot_lbl, name_lbl] + ([badge] if badge else [])
            for w in click_widgets:
                w.bind("<Button-1>", lambda _, n=name: self._set_focus(n))
            shell.configure(cursor="hand2")

        # Bind scroll on new children (shell itself was already bound at shell creation)
        for child in shell.winfo_children():
            self._bind_canvas_scroll(child)

        self._vlist_populated.add(idx)

    def _bind_canvas_scroll(self, widget):
        """Recursively bind Linux scroll events on widget to scroll the virtual list."""
        widget.bind("<Button-4>", lambda _: self._vlist_scroll(-1), add="+")
        widget.bind("<Button-5>", lambda _: self._vlist_scroll(1),  add="+")
        for child in widget.winfo_children():
            self._bind_canvas_scroll(child)

    def _scroll_into_view(self, name: str):
        """Scroll the virtual list so the item for 'name' is fully visible."""
        try:
            idx = next(i for i, it in enumerate(self._vlist_items)
                       if it.get("name") == name)
        except StopIteration:
            return
        yo       = self._vlist_yoffs[idx]
        h        = _SEP_H if self._vlist_items[idx]["type"] == "sep" else _CARD_H
        canvas   = self._vlist_canvas
        canvas_h = canvas.winfo_height()
        total_h  = self._vlist_total_h
        if canvas_h <= 0 or total_h <= canvas_h:
            return
        y_top = canvas.canvasy(0)
        y_bot = canvas.canvasy(canvas_h)
        if yo < y_top:
            canvas.yview_moveto(yo / total_h)
            self._vlist_render()
        elif yo + h > y_bot:
            canvas.yview_moveto(max(0.0, (yo + h - canvas_h) / total_h))
            self._vlist_render()

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
        target = self._all_mods[idx]
        self._set_focus(target)
        self._scroll_into_view(target)
