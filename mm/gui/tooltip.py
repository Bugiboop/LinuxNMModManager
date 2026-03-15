"""
mm/gui/tooltip.py — lightweight hover tooltip for tkinter/customtkinter widgets.

Usage:
    from .tooltip import attach_tooltip
    attach_tooltip(my_button, "Does something useful")
"""
import tkinter as tk


class _Tooltip:
    def __init__(self, widget, text: str, delay: int = 600):
        self._widget  = widget
        self._text    = text
        self._delay   = delay
        self._after_id = None
        self._win      = None
        widget.bind("<Enter>",       self._on_enter,  add="+")
        widget.bind("<Leave>",       self._on_leave,  add="+")
        widget.bind("<ButtonPress>", self._on_leave,  add="+")

    def update(self, text: str):
        self._text = text
        if self._win:
            for child in self._win.winfo_children():
                child.configure(text=text)

    def _on_enter(self, _=None):
        self._cancel()
        self._after_id = self._widget.after(self._delay, self._show)

    def _on_leave(self, _=None):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._after_id is not None:
            self._widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        self._after_id = None
        if not self._text:
            return
        try:
            x = self._widget.winfo_rootx() + self._widget.winfo_width() // 2
            y = self._widget.winfo_rooty() + self._widget.winfo_height() + 6
        except Exception:
            return

        self._win = tk.Toplevel(self._widget)
        self._win.wm_overrideredirect(True)
        self._win.wm_attributes("-topmost", True)
        self._win.wm_geometry(f"+{x}+{y}")

        lbl = tk.Label(
            self._win,
            text=self._text,
            background="#1e1e1e",
            foreground="#d4d4d4",
            relief="solid",
            borderwidth=1,
            font=("", 11),
            padx=7,
            pady=4,
            wraplength=320,
            justify="left",
        )
        lbl.pack()

    def _hide(self):
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None


def attach_tooltip(widget, text: str, delay: int = 600) -> _Tooltip:
    """Attach a hover tooltip to *widget* showing *text* after *delay* ms."""
    return _Tooltip(widget, text, delay)
