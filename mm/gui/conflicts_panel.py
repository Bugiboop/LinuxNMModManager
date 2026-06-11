"""
Global conflict rules panel mixin.

Shows every pair of mods that share files, lets the user set which
one wins for each pair, checks for circular rules, and applies
symlink changes when the user clicks Apply.
"""
from __future__ import annotations

import customtkinter as ctk

from mm.conflicts import (
    get_conflicts_for_mod, get_rule, set_rule, remove_rule,
    detect_cycles, apply_rule,
)
from mm.config import save_state


class ConflictsPanelMixin:
    """
    Mixin that adds a Conflicts page to the main app.
    Requires self._state, self._cfg to be available.
    """

    # ── Build ──────────────────────────────────────────────────────────

    def _build_conflicts_panel(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            hdr, text="CONFLICT RULES",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray45", "gray55"),
        ).grid(row=0, column=0, sticky="w")

        self._conflicts_apply_btn = ctk.CTkButton(
            hdr, text="Apply Changes", width=130, height=28,
            fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
            font=ctk.CTkFont(size=11),
            state="disabled",
            command=self._apply_conflict_rules,
        )
        self._conflicts_apply_btn.grid(row=0, column=1, sticky="e")

        # Scrollable list
        self._conflicts_scroll = ctk.CTkScrollableFrame(
            parent, fg_color="transparent")
        self._conflicts_scroll.grid(row=1, column=0, sticky="nsew")
        self._conflicts_scroll.grid_columnconfigure(0, weight=1)

        canvas = self._conflicts_scroll._parent_canvas

        def _bind_scroll(w):
            w.bind("<Button-4>", lambda _: canvas.yview_scroll(-1, "units"), add="+")
            w.bind("<Button-5>", lambda _: canvas.yview_scroll(1,  "units"), add="+")
            for child in w.winfo_children():
                _bind_scroll(child)

        _bind_scroll(self._conflicts_scroll)

        # Cycle / status bar
        self._conflicts_status = ctk.CTkLabel(
            parent, text="",
            font=ctk.CTkFont(size=11),
            text_color=("#c0392b", "#e74c3c"),
            justify="left", anchor="w",
        )
        self._conflicts_status.grid(row=2, column=0, sticky="ew",
                                     padx=16, pady=(4, 8))

        # Pending rule changes: {(mod_a, mod_b): "a"|"b"|None}
        self._conflict_pending: dict = {}
        # StringVar per pair for radio buttons
        self._conflict_vars: dict = {}

    # ── Refresh ────────────────────────────────────────────────────────

    def _refresh_conflicts_panel(self):
        if not hasattr(self, "_conflicts_scroll"):
            return

        self._conflict_pending.clear()
        self._conflict_vars.clear()

        for w in self._conflicts_scroll.winfo_children():
            w.destroy()

        # Collect every unique pair that has conflicts
        seen: set = set()
        pairs: list[tuple[str, str, int]] = []  # (mod_a, mod_b, file_count)
        for mod_name, ms in self._state.get("mods", {}).items():
            for other, count in ms.get("conflicts", {}).items():
                key = tuple(sorted([mod_name, other]))
                if key not in seen:
                    seen.add(key)
                    pairs.append((key[0], key[1], count))

        if not pairs:
            ctk.CTkLabel(
                self._conflicts_scroll,
                text=(
                    "No file conflicts detected.\n\n"
                    "Conflicts are recorded when two enabled mods both install\n"
                    "a file to the same game path.\n\n"
                    "Enable mods that share files to see them here."
                ),
                font=ctk.CTkFont(size=12),
                text_color=("gray55", "gray50"),
                justify="center",
            ).grid(row=0, column=0, pady=50)
            self._conflicts_apply_btn.configure(state="disabled")
            self._conflicts_status.configure(text="")
            return

        rules = self._state.get("conflict_rules", [])
        pairs.sort(key=lambda t: (t[0].lower(), t[1].lower()))

        for i, (mod_a, mod_b, count) in enumerate(pairs):
            current = get_rule(mod_a, mod_b, rules)
            # "a" → mod_a wins, "b" → mod_b wins, None → no rule
            if current == "a":
                initial = "a"
            elif current == "b":
                initial = "b"
            else:
                initial = "none"

            bg = ("gray90", "gray16") if i % 2 == 0 else ("gray88", "gray14")
            row_f = ctk.CTkFrame(self._conflicts_scroll,
                                 fg_color=bg, corner_radius=6)
            row_f.grid(row=i, column=0, sticky="ew", padx=6, pady=2)
            row_f.grid_columnconfigure(0, weight=1)
            row_f.grid_columnconfigure(2, weight=1)

            # Mod A name
            ctk.CTkLabel(
                row_f, text=mod_a,
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="e",
            ).grid(row=0, column=0, sticky="e", padx=(10, 6), pady=(6, 2))

            # File count
            ctk.CTkLabel(
                row_f,
                text=f"← {count} file{'s' if count != 1 else ''} →",
                font=ctk.CTkFont(size=10),
                text_color=("gray55", "gray50"),
                width=110,
            ).grid(row=0, column=1, padx=4, pady=(6, 2))

            # Mod B name
            ctk.CTkLabel(
                row_f, text=mod_b,
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="w",
            ).grid(row=0, column=2, sticky="w", padx=(6, 10), pady=(6, 2))

            # Radio buttons row
            radio_f = ctk.CTkFrame(row_f, fg_color="transparent")
            radio_f.grid(row=1, column=0, columnspan=3,
                         sticky="ew", padx=10, pady=(0, 8))

            var = ctk.StringVar(value=initial)
            key = (mod_a, mod_b)
            self._conflict_vars[key] = var

            ctk.CTkRadioButton(
                radio_f,
                text=f"{mod_a} wins",
                variable=var, value="a",
                font=ctk.CTkFont(size=11),
                command=lambda k=key: self._on_conflict_radio(k),
            ).grid(row=0, column=0, padx=(0, 16))

            ctk.CTkRadioButton(
                radio_f,
                text="No rule",
                variable=var, value="none",
                font=ctk.CTkFont(size=11),
                command=lambda k=key: self._on_conflict_radio(k),
            ).grid(row=0, column=1, padx=(0, 16))

            ctk.CTkRadioButton(
                radio_f,
                text=f"{mod_b} wins",
                variable=var, value="b",
                font=ctk.CTkFont(size=11),
                command=lambda k=key: self._on_conflict_radio(k),
            ).grid(row=0, column=2)

        self._update_conflicts_status()

    # ── Interaction ────────────────────────────────────────────────────

    def _on_conflict_radio(self, key: tuple[str, str]):
        val = self._conflict_vars[key].get()
        self._conflict_pending[key] = val
        self._update_conflicts_status()

    def _update_conflicts_status(self):
        if not hasattr(self, "_conflict_pending"):
            return

        # Build preview of rules including pending changes
        rules = list(self._state.get("conflict_rules", []))
        for (a, b), val in self._conflict_pending.items():
            rules = [r for r in rules
                     if not ({r.get("loser"), r.get("winner")} == {a, b})]
            if val == "a":
                rules.append({"loser": b, "winner": a})
            elif val == "b":
                rules.append({"loser": a, "winner": b})

        cycles = detect_cycles(rules)
        has_pending = bool(self._conflict_pending)

        if cycles:
            chains = "  |  ".join(" → ".join(c) for c in cycles[:2])
            self._conflicts_status.configure(
                text=f"⚠  Circular rules detected — resolve before applying:  {chains}",
                text_color=("#c0392b", "#e74c3c"),
            )
            self._conflicts_apply_btn.configure(state="disabled")
        elif has_pending:
            self._conflicts_status.configure(
                text="Unsaved changes — click Apply to update symlinks.",
                text_color=("gray45", "gray55"),
            )
            self._conflicts_apply_btn.configure(state="normal")
        else:
            self._conflicts_status.configure(text="")
            self._conflicts_apply_btn.configure(state="disabled")

    def _apply_conflict_rules(self):
        if not self._conflict_pending:
            return

        for (mod_a, mod_b), val in self._conflict_pending.items():
            if val == "a":
                set_rule(loser=mod_b, winner=mod_a, state=self._state)
                apply_rule(loser=mod_b, winner=mod_a,
                           state=self._state, _cfg=self._cfg)
            elif val == "b":
                set_rule(loser=mod_a, winner=mod_b, state=self._state)
                apply_rule(loser=mod_a, winner=mod_b,
                           state=self._state, _cfg=self._cfg)
            else:
                remove_rule(mod_a, mod_b, state=self._state)

        import mm.gui.config as _gc
        _gc._save_state(self._state)
        self._conflict_pending.clear()

        # Refresh the panel and the mod list so badges update
        self._refresh_conflicts_panel()
        self.refresh_mods()
