"""
Plugin load order panel mixin for Bethesda-engine games.

Shows active plugins from Plugins.txt in load order, with
per-plugin owner mod label and a "Sort with LOOT" button.
"""
from __future__ import annotations

import customtkinter as ctk

from mm.plugins import get_plugin_load_order
from mm.external_tools import launch_tool


class PluginsPanelMixin:
    """
    Mixin that adds a Plugins page to the main app.
    Requires self._profile, self._cfg, self._state to be available.
    """

    # ── Build ──────────────────────────────────────────────────────────

    def _build_plugins_panel(self, parent):
        """Call once when creating the Plugins page frame."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # Header row
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            hdr, text="PLUGIN LOAD ORDER",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray45", "gray55"),
        ).grid(row=0, column=0, sticky="w")

        self._repair_plugins_btn = ctk.CTkButton(
            hdr, text="Repair", width=70, height=28,
            fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
            font=ctk.CTkFont(size=11),
            command=self._repair_plugins,
        )
        self._repair_plugins_btn.grid(row=0, column=1, sticky="e", padx=(0, 4))

        self._repair_casing_btn = ctk.CTkButton(
            hdr, text="Fix Path Casing", width=120, height=28,
            fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
            font=ctk.CTkFont(size=11),
            command=self._repair_path_casing,
        )
        self._repair_casing_btn.grid(row=0, column=2, sticky="e", padx=(0, 4))

        self._loot_btn = ctk.CTkButton(
            hdr, text="Sort with LOOT", width=140, height=28,
            fg_color=("gray72", "gray30"), hover_color=("gray62", "gray38"),
            font=ctk.CTkFont(size=11),
            command=self._sort_with_loot,
        )
        self._loot_btn.grid(row=0, column=3, sticky="e")

        # Plugin list
        self._plugins_scroll = ctk.CTkScrollableFrame(
            parent, fg_color="transparent")
        self._plugins_scroll.grid(row=1, column=0, sticky="nsew")
        self._plugins_scroll.grid_columnconfigure(0, weight=1)

        canvas = self._plugins_scroll._parent_canvas

        def _bind_scroll(w):
            w.bind("<Button-4>", lambda _: canvas.yview_scroll(-1, "units"), add="+")
            w.bind("<Button-5>", lambda _: canvas.yview_scroll(1, "units"), add="+")
            for child in w.winfo_children():
                _bind_scroll(child)

        _bind_scroll(self._plugins_scroll)

    # ── Refresh ────────────────────────────────────────────────────────

    def _refresh_plugins_panel(self):
        """Reload and redisplay the plugin list from Plugins.txt."""
        if not hasattr(self, "_plugins_scroll"):
            return
        profile = getattr(self, "_profile", {}) or {}
        if not profile.get("plugin_extensions"):
            return

        for w in self._plugins_scroll.winfo_children():
            w.destroy()

        plugins = get_plugin_load_order(profile, self._cfg)

        if not plugins:
            ctk.CTkLabel(
                self._plugins_scroll,
                text=(
                    "No active plugins found in Plugins.txt.\n\n"
                    "Enable mods with .esp / .esm / .esl files and\n"
                    "they will appear here in load order.\n\n"
                    "Make sure Plugins.txt path is set correctly in Settings."
                ),
                font=ctk.CTkFont(size=12),
                text_color=("gray55", "gray50"),
                justify="center",
            ).grid(row=0, column=0, pady=50, padx=16)
            return

        # Build owner map from state
        plugin_owners: dict[str, str] = {}
        for mod_name, ms in self._state.get("mods", {}).items():
            for p in ms.get("plugins", []):
                plugin_owners[p.lower()] = mod_name

        for i, plugin_name in enumerate(plugins):
            owner = plugin_owners.get(plugin_name.lower(), "")
            bg = ("gray90", "gray15") if i % 2 == 0 else ("gray88", "gray13")
            row_frame = ctk.CTkFrame(self._plugins_scroll,
                                     fg_color=bg, corner_radius=4)
            row_frame.grid(row=i, column=0, sticky="ew", padx=8, pady=1)
            row_frame.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                row_frame,
                text=f"{i + 1:3d}",
                font=ctk.CTkFont(family="monospace", size=11),
                text_color=("gray50", "gray50"),
                width=36,
            ).grid(row=0, column=0, sticky="w", padx=(8, 4), pady=3)

            ctk.CTkLabel(
                row_frame, text=plugin_name,
                font=ctk.CTkFont(size=12),
                text_color=("gray10", "gray92"), anchor="w",
            ).grid(row=0, column=1, sticky="w", pady=3)

            if owner:
                ctk.CTkLabel(
                    row_frame, text=owner,
                    font=ctk.CTkFont(size=10),
                    text_color=("gray55", "gray50"), anchor="e",
                ).grid(row=0, column=2, sticky="e", padx=(0, 10), pady=3)

    # ── Repair ─────────────────────────────────────────────────────────

    def _repair_plugins(self):
        """
        Scan symlinks of all enabled mods and register any .esp/.esm/.esl
        that are installed to Data/ but not yet in Plugins.txt.
        Useful after upgrading the mod manager or fixing a missed registration.
        """
        profile = getattr(self, "_profile", {}) or {}
        if not profile.get("plugin_extensions"):
            return

        from mm.plugins import register_plugins, _plugins_txt_path
        from pathlib import Path

        pt = _plugins_txt_path(profile, self._cfg)
        if not pt:
            import tkinter.messagebox as mb
            mb.showerror(
                "Plugins.txt Not Found",
                "Could not locate Plugins.txt.\n"
                "Set the path in Settings → Plugins.",
                parent=self,
            )
            return

        plugin_exts  = {e.lower() for e in profile.get("plugin_extensions", [])}
        install_base = profile.get("default_install_path", "") or "Data"
        game_root    = self._cfg.get("game_root")
        data_dir     = game_root / install_base if game_root else None

        found = []
        for mod_name, ms in self._state.get("mods", {}).items():
            if not ms.get("enabled"):
                continue
            if ms.get("plugins"):
                continue   # already registered
            for entry in ms.get("symlinks", []):
                link = Path(entry["link"])
                if (link.suffix.lower() in plugin_exts
                        and data_dir and link.parent == data_dir):
                    found.append((mod_name, link.name))

        if not found:
            import tkinter.messagebox as mb
            mb.showinfo("Repair Plugins",
                        "All installed plugins are already registered in Plugins.txt.",
                        parent=self)
            return

        added_total = 0
        for mod_name, plugin_name in found:
            added = register_plugins([plugin_name], profile, self._cfg)
            if added:
                self._state["mods"][mod_name].setdefault("plugins", [])
                self._state["mods"][mod_name]["plugins"].append(plugin_name)
                added_total += 1

        if added_total:
            import mm.gui.config as _gc
            _gc._save_state(self._state)
            self._refresh_plugins_panel()
            import tkinter.messagebox as mb
            mb.showinfo("Repair Plugins",
                        f"Registered {added_total} plugin(s) in Plugins.txt.",
                        parent=self)

    # ── Path casing repair ─────────────────────────────────────────────

    def _repair_path_casing(self):
        """
        Merge case-conflicting directories in the game's Data folder, update
        state.json symlink records to point to the canonical paths, and remove
        the now-empty duplicate directories.
        """
        import tkinter.messagebox as mb
        from mm.repair import repair_case_conflicts
        import mm.gui.config as _gc

        game_root = self._cfg.get("game_root")
        if not game_root:
            mb.showerror("Fix Path Casing", "No game root configured.", parent=self)
            return

        result = repair_case_conflicts(game_root, self._state)

        if result["errors"]:
            mb.showwarning(
                "Fix Path Casing",
                f"Completed with {len(result['errors'])} error(s):\n" +
                "\n".join(result["errors"][:5]),
                parent=self,
            )

        if result["fixed"] == 0 and not result["merged"]:
            mb.showinfo("Fix Path Casing",
                        "No case-conflicting directories found.",
                        parent=self)
            return

        _gc._save_state(self._state)

        lines = [f"Fixed {result['fixed']} symlink(s) across {len(result['merged'])} group(s).\n"]
        for canonical, removed in result["merged"][:8]:
            from pathlib import Path as _P
            canon_rel = _P(canonical).name
            removed_names = ", ".join(_P(r).name for r in removed)
            lines.append(f"  {canon_rel}  ←  merged from: {removed_names}")
        if len(result["merged"]) > 8:
            lines.append(f"  … and {len(result['merged']) - 8} more")

        mb.showinfo("Fix Path Casing", "\n".join(lines), parent=self)

    # ── LOOT ───────────────────────────────────────────────────────────

    def _sort_with_loot(self):
        profile = getattr(self, "_profile", {}) or {}
        tools = profile.get("external_tools", [])
        loot_cfg = next((t for t in tools if t.get("id") == "loot"), None)

        if not loot_cfg:
            import tkinter.messagebox as mb
            mb.showinfo(
                "LOOT Not Configured",
                "LOOT is not listed in this game's external_tools profile.\n"
                "Add LOOT to fallout4.json to enable this button.",
                parent=self,
            )
            return

        mods_dir = self._cfg.get("mods_dir")
        game_root = self._cfg.get("game_root")
        ok, msg = launch_tool(loot_cfg, mods_dir, game_root, self._cfg,
                              profile=profile)

        if ok:
            self._log_write(f"[loot] {msg}\n")
        else:
            import tkinter.messagebox as mb
            mb.showerror("LOOT Error", msg, parent=self)
