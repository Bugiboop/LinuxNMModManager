import re
import tkinter as tk

import customtkinter as ctk


def _detect_prompt(buf: str):
    """
    Return (kind, n) if buf matches a known sbmm.py interactive prompt,
    else None.  n is the number of numbered options (int) or None.
    """
    b = buf.rstrip()
    m = re.search(r"Keep which\? \[1-(\d+)/a=keep all/s=skip\]:\s*$", b)
    if m:
        return ("variant", int(m.group(1)))
    if re.search(r"Resolve conflict \[1/2/s\]:\s*$", b):
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

        # Bind Linux scroll wheel to the context scrollable frame
        _cv = ctx_scroll._parent_canvas
        def _bind_scroll(w):
            w.bind("<Button-4>", lambda _: _cv.yview_scroll(-1, "units"), add="+")
            w.bind("<Button-5>", lambda _: _cv.yview_scroll( 1, "units"), add="+")
            for child in w.winfo_children():
                _bind_scroll(child)
        _bind_scroll(ctx_outer)
        _bind_scroll(self)

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
            elif kind == "conflict":
                choices.append(("s", "(s)  Skip conflicting files — install the rest normally"))
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
