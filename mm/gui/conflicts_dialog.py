"""
Conflict priority dialog.

Shows every mod that shares files with the selected mod, lets the user
set which mod wins each pair, checks for circular rules, and applies
symlink changes immediately when the user clicks Apply.
"""
from __future__ import annotations

import customtkinter as ctk

from mm.conflicts import (
    get_conflicts_for_mod, get_rule, set_rule, remove_rule,
    detect_cycles, apply_rule, revert_rule,
)
from mm.config import save_state


class ConflictsDialog(ctk.CTkToplevel):
    """
    Modal dialog for managing conflict priorities for one mod.

    Usage:
        dlg = ConflictsDialog(parent, mod_name, state, cfg)
        # non-blocking: caller can wait with dlg.wait_window() if needed
    """

    def __init__(self, parent, mod_name: str, state: dict, cfg: dict,
                 on_close=None):
        super().__init__(parent)
        self._mod_name = mod_name
        self._state    = state
        self._cfg      = cfg
        self._on_close = on_close
        # pending changes: {(mod_a, mod_b): "a" | "b" | None}
        self._pending: dict[tuple[str, str], str | None] = {}

        self.title(f"Conflicts — {mod_name}")
        self.resizable(True, True)
        self.minsize(520, 280)
        self.transient(parent)
        self.withdraw()   # defer show to avoid blank window on Linux

        self._build()
        self.after(50, self._show)

    # ── Build UI ──────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            hdr, text="CONFLICT RULES",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray45", "gray55"),
        ).grid(row=0, column=0, sticky="w")

        # Scrollable conflict list
        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        self._scroll.grid_columnconfigure(0, weight=1)
        self._populate_rows()

        # Cycle indicator
        self._cycle_lbl = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(size=11),
            text_color=("#c0392b", "#e74c3c"),
            justify="left", anchor="w",
        )
        self._cycle_lbl.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 0))

        # Buttons
        btn_bar = ctk.CTkFrame(self, fg_color=("gray86", "gray17"), corner_radius=0)
        btn_bar.grid(row=3, column=0, sticky="ew")
        btn_bar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            btn_bar,
            text="Changes take effect immediately for currently-enabled mods.",
            font=ctk.CTkFont(size=10),
            text_color=("gray55", "gray50"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=6)

        self._apply_btn = ctk.CTkButton(
            btn_bar, text="Apply", width=90, height=32,
            command=self._apply,
        )
        self._apply_btn.grid(row=0, column=1, padx=6, pady=6, sticky="e")

        ctk.CTkButton(
            btn_bar, text="Close", width=90, height=32,
            fg_color=("gray72", "gray30"),
            hover_color=("gray62", "gray38"),
            command=self._close,
        ).grid(row=0, column=2, padx=(0, 12), pady=6, sticky="e")

        # Now safe to call since _apply_btn exists
        self._update_cycle_indicator()

    def _populate_rows(self):
        for w in self._scroll.winfo_children():
            w.destroy()
        self._row_vars: dict[tuple[str, str], ctk.StringVar] = {}

        conflicts = get_conflicts_for_mod(self._mod_name, self._state)
        if not conflicts:
            ctk.CTkLabel(
                self._scroll,
                text="No file conflicts detected with any other installed mod.\n\n"
                     "Conflicts are recorded when two enabled mods both install\n"
                     "a file to the same game path.",
                font=ctk.CTkFont(size=12),
                text_color=("gray55", "gray50"),
                justify="center",
            ).grid(row=0, column=0, pady=30)
            return

        rules = self._state.get("conflict_rules", [])

        for i, (other, count) in enumerate(sorted(conflicts, key=lambda x: x[0].lower())):
            # Determine current rule from perspective of self._mod_name
            current = get_rule(self._mod_name, other, rules)
            # current="a" → self wins, "b" → other wins, None → no rule

            bg = ("gray90", "gray16") if i % 2 == 0 else ("gray88", "gray14")
            row_f = ctk.CTkFrame(self._scroll, fg_color=bg, corner_radius=6)
            row_f.grid(row=i, column=0, sticky="ew", padx=4, pady=2)
            row_f.grid_columnconfigure(1, weight=1)

            # Mod A label (self)
            ctk.CTkLabel(
                row_f, text=self._mod_name,
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="e",
            ).grid(row=0, column=0, sticky="e", padx=(10, 4), pady=6)

            # File count badge
            ctk.CTkLabel(
                row_f, text=f"←  {count} file{'s' if count != 1 else ''}  →",
                font=ctk.CTkFont(size=10),
                text_color=("gray55", "gray50"),
                width=110,
            ).grid(row=0, column=1, padx=4, pady=6)

            # Mod B label (other)
            ctk.CTkLabel(
                row_f, text=other,
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="w",
            ).grid(row=0, column=2, sticky="w", padx=(4, 0), pady=6)

            # Radio buttons: "A wins" / "No rule" / "B wins"
            # current="a"→self wins, "b"→other wins, None→no rule
            if current == "a":
                initial = "self"
            elif current == "b":
                initial = "other"
            else:
                initial = "none"

            var = ctk.StringVar(value=initial)
            key = (self._mod_name, other)
            self._row_vars[key] = var

            btn_f = ctk.CTkFrame(row_f, fg_color="transparent")
            btn_f.grid(row=1, column=0, columnspan=3, sticky="ew",
                       padx=10, pady=(0, 8))

            ctk.CTkRadioButton(
                btn_f,
                text=f"{self._mod_name} wins",
                variable=var, value="self",
                font=ctk.CTkFont(size=11),
                command=lambda k=key: self._on_rule_change(k),
            ).grid(row=0, column=0, padx=(0, 12))

            ctk.CTkRadioButton(
                btn_f,
                text="No rule (last-enabled wins)",
                variable=var, value="none",
                font=ctk.CTkFont(size=11),
                command=lambda k=key: self._on_rule_change(k),
            ).grid(row=0, column=1, padx=(0, 12))

            ctk.CTkRadioButton(
                btn_f,
                text=f"{other} wins",
                variable=var, value="other",
                font=ctk.CTkFont(size=11),
                command=lambda k=key: self._on_rule_change(k),
            ).grid(row=0, column=2)

    # ── Interaction ───────────────────────────────────────────────────────

    def _on_rule_change(self, key: tuple[str, str]):
        mod_a, mod_b = key
        val = self._row_vars[key].get()
        if val == "self":
            self._pending[key] = "a"
        elif val == "other":
            self._pending[key] = "b"
        else:
            self._pending[key] = None
        self._update_cycle_indicator()

    def _update_cycle_indicator(self):
        # Build a preview of rules including pending changes
        rules = list(self._state.get("conflict_rules", []))
        # Apply pending changes temporarily
        for (a, b), winner in self._pending.items():
            rules = [r for r in rules
                     if not ({r.get("loser"), r.get("winner")} == {a, b})]
            if winner == "a":
                rules.append({"loser": b, "winner": a})
            elif winner == "b":
                rules.append({"loser": a, "winner": b})

        cycles = detect_cycles(rules)
        if cycles:
            chains = "\n".join(" → ".join(c) for c in cycles[:3])
            self._cycle_lbl.configure(
                text=f"⚠  Circular rules detected — resolve before applying:\n{chains}"
            )
            self._apply_btn.configure(state="disabled")
        else:
            self._cycle_lbl.configure(text="")
            self._apply_btn.configure(state="normal")

    def _apply(self):
        if not self._pending:
            self._close()
            return

        for (mod_a, mod_b), winner in self._pending.items():
            if winner == "a":
                set_rule(loser=mod_b, winner=mod_a, state=self._state)
                apply_rule(loser=mod_b, winner=mod_a,
                           state=self._state, _cfg=self._cfg)
            elif winner == "b":
                set_rule(loser=mod_a, winner=mod_b, state=self._state)
                apply_rule(loser=mod_a, winner=mod_b,
                           state=self._state, _cfg=self._cfg)
            else:
                remove_rule(mod_a, mod_b, state=self._state)

        save_state(self._state)
        self._pending.clear()
        self._close()

    def _close(self):
        if self._on_close:
            self._on_close()
        self.destroy()

    def _show(self):
        self.deiconify()
        # Size to content, cap at 80% of screen
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        rw = min(self.winfo_reqwidth()  + 40, int(sw * 0.8))
        rh = min(self.winfo_reqheight() + 20, int(sh * 0.8))
        rw = max(rw, 520)
        rh = max(rh, 280)
        px = self.master.winfo_rootx() + (self.master.winfo_width()  - rw) // 2
        py = self.master.winfo_rooty() + (self.master.winfo_height() - rh) // 2
        self.geometry(f"{rw}x{rh}+{px}+{py}")
        self.grab_set()
        self.lift()
        self.focus_force()
