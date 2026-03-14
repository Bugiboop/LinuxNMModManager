#!/usr/bin/env python3
"""Universal Mod Manager – GUI entry point.
Requires: pip install customtkinter Pillow   (already in .venv)
Run with: .venv/bin/python sbmm_gui.py

When packaged as a single frozen executable the GUI spawns subprocesses of
itself with CLI flags (--enable, --disable, etc.).  argv is inspected here so
those invocations are routed to the CLI backend instead of opening a second
GUI window.
"""
import sys

_CLI_FLAGS = {
    "--enable", "--disable", "--list", "--conflicts",
    "--install", "--uninstall", "--purge", "--clean",
    "--assetcheck", "--extract",
}

if __name__ == "__main__":
    if any(a in _CLI_FLAGS for a in sys.argv[1:]):
        from mm.commands import main as _cli_main
        _cli_main()
    else:
        from mm.gui import main as _gui_main
        _gui_main()
