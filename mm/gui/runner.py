import re
import subprocess
import sys
import threading
import webbrowser

import customtkinter as ctk

import mm.gui.config as _gc
from .constants import _CARD_NORMAL, _CARD_FOCUSED, _CARD_CHECKED
from .dialogs import _detect_prompt, _InteractiveDialog


def _build_cmd(script_dir, args: list) -> list:
    """
    Build the subprocess command list for running CLI operations.

    - Frozen (PyInstaller): re-invoke the same executable with the CLI args;
      sbmm_gui.py detects CLI flags and routes to mm.commands instead of
      opening a second GUI window.
    - Development: use .venv python + sbmm.py, falling back to sys.executable.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable] + args

    sbmm   = script_dir / "sbmm.py"
    python = script_dir / ".venv" / "bin" / "python"
    if not python.exists():
        python = sys.executable
    return [str(python), "-u", str(sbmm)] + args


class RunnerMixin:
    """Mixin providing subprocess runner and mod selection for ModManagerApp."""

    # ── Scroll helper (info panel) ─────────────────────────────────────

    def _bind_scroll(self, widget, scroll_frame):
        """Recursively bind Linux scroll events on widget to scroll scroll_frame."""
        canvas = scroll_frame._parent_canvas
        widget.bind("<Button-4>",
                    lambda _: canvas.yview_scroll(-1, "units"), add="+")
        widget.bind("<Button-5>",
                    lambda _: canvas.yview_scroll(1, "units"), add="+")
        for child in widget.winfo_children():
            self._bind_scroll(child, scroll_frame)

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
        if not card or not card.winfo_exists():
            self._cards.pop(name, None)
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
        SCRIPT_DIR = _gc.SCRIPT_DIR

        self._log_write(f"\n$ sbmm {flag} [{len(names)} mod(s)]\n", bold=True)
        self._busy.configure(text="⏳ Running…")

        def worker():
            for name in names:
                cmd = _build_cmd(SCRIPT_DIR, [flag, name])
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
        SCRIPT_DIR = _gc.SCRIPT_DIR
        cmd = _build_cmd(SCRIPT_DIR, args)

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
                    # Section markers (new mod being processed) → fresh context
                    if re.match(r"^\[(?:enabling|extract)\b", line_buf):
                        context_buf = line_buf
                    else:
                        context_buf += line_buf
                        # Keep only the last 15 lines as context for dialogs
                        context_buf = "".join(
                            context_buf.splitlines(keepends=True)[-15:])
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
                        context_buf = ""   # reset so next prompt only shows fresh output
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
        SCRIPT_DIR = _gc.SCRIPT_DIR
        cmd = _build_cmd(SCRIPT_DIR, args)

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
        SCRIPT_DIR = _gc.SCRIPT_DIR
        cmd_str = (
            f"{' '.join(str(p) for p in _build_cmd(SCRIPT_DIR, args))}; "
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

    # ── Mod folder / Nexus links ──────────────────────────────────────

    def _open_mod_folder(self):
        if self._folder_path and self._folder_path.is_dir():
            subprocess.Popen(["xdg-open", str(self._folder_path)])

    def _open_nexus(self):
        if self._nexus_id:
            webbrowser.open(self._nexus_id)
