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

    def _on_archive_checkbox_change(self, name: str, var: ctk.BooleanVar):
        """Archive checkbox toggled: update archive-select set."""
        if var.get():
            self._selected_archives.add(name)
        else:
            self._selected_archives.discard(name)
        self._repaint_card(name)
        self._update_selection_ui()

    def _clear_selection(self):
        prev_mods = set(self._selected)
        prev_arcs = set(self._selected_archives)
        self._selected.clear()
        self._selected_archives.clear()
        for name in prev_mods | prev_arcs:
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
        checked = name in self._selected or name in self._selected_archives
        focused = self._focused == name
        if checked:
            bg, bw = _CARD_CHECKED, 2
        elif focused:
            bg, bw = _CARD_FOCUSED, 1
        else:
            bg, bw = _CARD_NORMAL, 0
        card.configure(fg_color=bg, border_width=bw)

    def _update_selection_ui(self):
        n_mods = len(self._selected)
        n_arcs = len(self._selected_archives)
        has_mods = n_mods > 0
        has_arcs = n_arcs > 0

        if has_mods:
            self._btn_enable_sel.configure(
                state="normal", text=f"Enable Selected ({n_mods})",
                fg_color=("#1a5a9a", "#1a5a9a"),
                hover_color=("#1a6aaa", "#1a6aaa"),
            )
            self._btn_disable_sel.configure(
                state="normal", text=f"Disable Selected ({n_mods})",
                fg_color=("gray50", "gray38"),
                hover_color=("gray42", "gray46"),
            )
        else:
            self._btn_enable_sel.configure(
                state="disabled", text="Enable Selected",
                fg_color=("gray72", "gray30"),
            )
            self._btn_disable_sel.configure(
                state="disabled", text="Disable Selected",
                fg_color=("gray72", "gray30"),
            )

        if has_arcs:
            self._btn_extract_sel.configure(
                state="normal", text=f"Extract Selected ({n_arcs})",
                fg_color=("#8a5000", "#7a3a00"),
                hover_color=("#a06008", "#6a3a04"),
            )
        else:
            self._btn_extract_sel.configure(
                state="disabled", text="Extract Selected",
                fg_color=("gray72", "gray30"),
            )

        self._btn_clear_sel.configure(
            state="normal" if (has_mods or has_arcs) else "disabled"
        )

    def _stage_enable_all(self):
        """Stage all on-disk mods for enabling without touching disk."""
        names = [n for n in self._on_disk
                 if not self._state["mods"].get(n, {}).get("enabled")]
        if not names:
            self._log_write("[info] All on-disk mods are already staged/enabled.\n")
            return
        self._log_write(f"\n[stage] Marking {len(names)} mod(s) for enable…\n", bold=True)
        for name in names:
            ms = self._state["mods"].setdefault(name, {})
            if "deployed" not in ms:
                ms["deployed"] = ms.get("enabled", False)
            ms["enabled"] = True
        self._save_state_async()
        self._update_deploy_button()
        self.refresh_mods()

    def _extract_selected(self):
        names = sorted(self._selected_archives)
        if not names:
            return
        self._log_write(f"\n$ sbmm --extract [{len(names)} archive(s)]\n", bold=True)
        remaining = list(names)

        def run_next():
            if not remaining:
                self._selected_archives.clear()
                self._update_selection_ui()
                self.refresh_mods()
                return
            self._run_interactive(["--extract", remaining.pop(0)], on_done=run_next)

        run_next()

    def _enable_selected(self):
        names = sorted(self._selected & self._on_disk)
        if not names:
            return
        self._log_write(f"\n[stage] Marking {len(names)} mod(s) for enable…\n", bold=True)
        for name in names:
            self._stage_mod(name, True)
        self.refresh_mods()

    def _disable_selected(self):
        names = sorted(self._selected & self._on_disk)
        if not names:
            return
        self._log_write(f"\n[stage] Marking {len(names)} mod(s) for disable…\n", bold=True)
        for name in names:
            self._stage_mod(name, False)
        self.refresh_mods()

    def _disable_all_mods(self):
        """Remove all deployed mod symlinks immediately (Purge All)."""
        # Use `deployed` as the authority — catches mods staged-for-disable that
        # still have symlinks on disk.
        enabled = [
            name for name, ms in self._state["mods"].items()
            if ms.get("deployed", ms.get("enabled", False))
        ]
        if not enabled:
            self._log_write("[info] No deployed mods to purge.\n")
            return

        from mm.mods import disable_mod
        import mm.gui.config as _gc

        self._log_write(f"\n[purge] Removing {len(enabled)} deployed mod(s)…\n", bold=True)
        self._busy.configure(text="⏳ Running…")
        self.update_idletasks()

        # Use the full cfg that includes game_root; fall back to building it
        cfg = dict(self._cfg)
        if not cfg.get("game_root") or not cfg.get("profile"):
            # Build a minimal cfg compatible with disable_mod
            cfg["profile"] = self._profile or {}

        def worker():
            for name in enabled:
                try:
                    disable_mod(name, cfg, self._state, _no_save=True)
                    self.after(0, self._log_write, f"[disabled] {name}\n")
                except Exception as e:
                    self.after(0, self._log_write, f"[error] {name}: {e}\n")
            _gc._save_state(self._state)   # single save after all changes
            self.after(0, lambda: self._busy.configure(text=""))
            self.after(0, self._update_deploy_button)
            self.after(200, self.refresh_mods)

        threading.Thread(target=worker, daemon=True).start()

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
                            dlg.lift()
                            dlg.focus_force()
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

    # ── Stage helpers ─────────────────────────────────────────────────

    def _save_state_async(self):
        """
        Serialize state JSON on the main thread (fast), then write to disk in a
        background thread so file I/O never blocks the UI.
        """
        import json as _json
        try:
            data = _json.dumps(self._state, indent=2)
        except Exception:
            return
        import mm.gui.config as _gc2
        path = _gc2._STATE_FILE
        threading.Thread(
            target=lambda d=data, p=path: p.write_text(d, encoding="utf-8"),
            daemon=True,
        ).start()

    def _stage_mod(self, name: str, enable: bool):
        """
        Stage a mod's desired state without touching disk.

        FOMOD wizard is always shown when enabling (pre-populated with prior
        selections). Variant detection for UE4/UE5 games is handled here so
        it isn't repeated at deploy time.
        """
        ms = self._state["mods"].setdefault(name, {})

        # Capture current deployed state before changing intent so the diff stays
        # accurate for old state files that don't have the `deployed` key yet.
        if "deployed" not in ms:
            ms["deployed"] = ms.get("enabled", False)

        if enable:
            mod_path = self._cfg.get("mods_dir")
            if mod_path:
                mod_path = mod_path / name

            if mod_path and mod_path.is_dir():
                # FOMOD: always show wizard so the user can review/change options.
                # Pre-populate with stored selections from last install.
                from mm.fomod import find_fomod_xml
                fomod_xml = find_fomod_xml(mod_path)
                if fomod_xml:
                    from mm.fomod import parse_fomod
                    from mm.gui.fomod_dialog import FomodWizard
                    try:
                        fomod_config = parse_fomod(fomod_xml)
                    except Exception as e:
                        self._log_write(f"[fomod] Failed to parse: {e}\n")
                        fomod_xml = None   # fall through without wizard
                    else:
                        if fomod_config.steps:
                            initial = ms.get("fomod_selections")
                            if initial:
                                initial = {
                                    tuple(int(x) for x in k.split(",")): v
                                    for k, v in initial.items()
                                }
                            wizard = FomodWizard(
                                self, fomod_config, mod_path,
                                initial_selections=initial,
                            )
                            self.update_idletasks()
                            wizard.lift(); wizard.focus_force()
                            self.wait_window(wizard)
                            if wizard.result is None:
                                # Wizard cancelled → revert switch
                                sw_info = self._switches.get(name)
                                if sw_info:
                                    sw_info[0].set(False)
                                return
                            ms["fomod_selections"] = {
                                f"{k[0]},{k[1]}": v for k, v in wizard.result.items()
                            }
                        else:
                            fomod_xml = None   # no steps → treat as plain mod

                # Variant detection for UE4/UE5 (Stellar Blade etc.)
                if fomod_xml is None and not self._cfg.get("profile", {}).get("plugin_extensions"):
                    from mm.archive import detect_variant_groups
                    profile  = self._cfg.get("profile", {})
                    anchors  = {r["anchor"].lower() for r in profile.get("install_rules", [])}
                    groups   = detect_variant_groups(mod_path, anchor_names=anchors)
                    for _parent_dir, variants in groups:
                        chosen = variants[0]
                        import shutil as _shutil
                        for v in variants:
                            if v != chosen:
                                _shutil.rmtree(v)
                        self._log_write(
                            f"  [variants] Auto-selected '{chosen.name}' for {name}\n"
                        )

        ms["enabled"] = enable
        self._save_state_async()   # serialize on main thread, write in background
        self._repaint_card(name)
        self._update_deploy_button()

    def _count_pending_changes(self) -> int:
        """Number of mods whose desired state differs from their deployed state."""
        count = 0
        for ms in self._state.get("mods", {}).values():
            enabled  = ms.get("enabled", False)
            deployed = ms.get("deployed", enabled)  # old state: assume in sync
            if enabled != deployed:
                count += 1
        return count

    def _update_deploy_button(self):
        """Refresh the Deploy button label and state."""
        n = self._count_pending_changes()
        if n > 0:
            self._btn_deploy.configure(
                state="normal",
                text=f"▶ Deploy ({n})",
                fg_color=("#1a6a1a", "#1a4a1a"),
                hover_color=("#1a8a1a", "#1a6a1a"),
            )
        else:
            self._btn_deploy.configure(
                state="disabled",
                text="▶ Deploy",
                fg_color=("gray72", "gray30"),
                hover_color=("gray62", "gray38"),
            )

    def _deploy_staged(self):
        """
        Apply all staged changes to disk in a background thread.

        Mods with enabled=True but deployed=False get symlinked.
        Mods with enabled=False but deployed=True get unlinked.
        """
        from mm.mods import enable_mod, disable_mod
        from mm.resolver import build_target_map

        cfg   = dict(self._cfg)
        state = self._state
        mods  = state.get("mods", {})

        to_enable = [
            n for n, ms in mods.items()
            if ms.get("enabled") and not ms.get("deployed", ms.get("enabled", False))
            and n in self._on_disk
        ]
        to_disable = [
            n for n, ms in mods.items()
            if not ms.get("enabled") and ms.get("deployed", ms.get("enabled", False))
        ]

        if not to_enable and not to_disable:
            self._log_write("[deploy] Nothing to deploy.\n")
            return

        self._log_write(
            f"\n[deploy] {len(to_enable)} to enable, {len(to_disable)} to disable\n",
            bold=True,
        )
        self._busy.configure(text="⏳ Deploying…")
        self.update_idletasks()

        profile = cfg.get("profile", {})

        def worker():
            from mm.config import scan_game_tree
            needs_tree = not profile.get("install_rules")
            game_tree  = scan_game_tree(cfg["game_root"]) if needs_tree else set()
            target_map = build_target_map(state) if to_enable else {}

            # Disable first so freed targets become available.
            # _no_save=True skips the per-mod disk write; one save at the end.
            for name in to_disable:
                try:
                    disable_mod(name, cfg, state, _no_save=True)
                    self.after(0, self._log_write, f"[disabled] {name}\n")
                except Exception as e:
                    self.after(0, self._log_write, f"[error] disable {name}: {e}\n")

            for name in to_enable:
                ms = mods.get(name, {})
                stored = ms.get("fomod_selections")

                def _make_cb(sel=stored):
                    if not sel:
                        return None
                    def cb(config, _stored=None):
                        return {
                            tuple(int(x) for x in k.split(",")): v
                            for k, v in sel.items()
                        }
                    return cb

                try:
                    enable_mod(
                        name, cfg, state,
                        target_map=target_map,
                        game_tree=game_tree,
                        fomod_callback=_make_cb(),
                        skip_variants=True,
                        _no_save=True,
                    )
                    self.after(0, self._log_write, f"[enabled] {name}\n")
                except Exception as e:
                    self.after(0, self._log_write, f"[error] enable {name}: {e}\n")

            import mm.gui.config as _gc2
            _gc2._save_state(state)   # single save after all changes
            self.after(0, lambda: self._busy.configure(text=""))
            self.after(0, self._update_deploy_button)
            self.after(200, self.refresh_mods)

        threading.Thread(target=worker, daemon=True).start()

    # ── Per-toggle and global dispatch ───────────────────────────────

    def _toggle(self, name: str, var: ctk.BooleanVar):
        self._stage_mod(name, var.get())

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
