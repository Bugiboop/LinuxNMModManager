from __future__ import annotations

from pathlib import Path


def _plugins_txt_path(profile: dict, cfg: dict) -> Path | None:
    """
    Return Plugins.txt path.

    Priority:
    1. Per-game override in config.json (cfg["plugins_txt_path"]) — user-configured
    2. Profile default (profile["plugins_txt_path"]) — may contain ~/.local/... default
    3. Auto-detect by scanning all Steam library compatdata folders
    """
    raw = cfg.get("plugins_txt_path") or profile.get("plugins_txt_path", "")
    if raw:
        p = Path(raw).expanduser()
        if p.exists():
            return p

    # Auto-detect: scan all Steam library folders
    app_id = profile.get("steam_app_id", "")
    relative_suffix = profile.get("plugins_txt_relative", "")
    if not app_id or not relative_suffix:
        # Build suffix from game name heuristic if not explicit
        if raw:
            return Path(raw).expanduser()  # Return even if missing; create on write
        return None

    from mm.external_tools import _steam_library_paths
    for library in _steam_library_paths():
        candidate = (library / "steamapps" / "compatdata" / str(app_id)
                     / "pfx" / "drive_c" / relative_suffix)
        if candidate.exists():
            return candidate

    # Fallback: return the expanded profile default (may not exist yet; will be created)
    if raw:
        return Path(raw).expanduser()
    return None


def _read_lines(path: Path) -> list[str]:
    if not path or not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _bare(line: str) -> str:
    """Strip leading * from a plugin line."""
    return line.lstrip("*")


def register_plugins(plugin_names: list[str], profile: dict, cfg: dict) -> list[str]:
    """
    Add plugin_names to Plugins.txt as active entries (prefixed with *).
    If a plugin is present but inactive (no *), activates it in place.
    Skips names already active. Returns names actually added/activated.
    """
    path = _plugins_txt_path(profile, cfg)
    if not path:
        return []

    lines = _read_lines(path)
    active_lower = {_bare(l).lower() for l in lines if l.startswith("*")}

    added = []
    for name in plugin_names:
        name_lower = name.lower()
        if name_lower in active_lower:
            continue  # already active
        # Present but inactive? Activate in place.
        activated = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("*"):
                if _bare(stripped).lower() == name_lower:
                    lines[i] = f"*{stripped}"
                    activated = True
                    active_lower.add(name_lower)
                    added.append(name)
                    break
        if not activated:
            lines.append(f"*{name}")
            active_lower.add(name_lower)
            added.append(name)

    if added:
        _write_lines(path, lines)
    return added


def unregister_plugins(plugin_names: list[str], profile: dict, cfg: dict) -> None:
    """
    Deactivate plugins by removing the * prefix. Preserves order and file existence.
    The game ignores lines without the * prefix.
    """
    path = _plugins_txt_path(profile, cfg)
    if not path:
        return

    lower_names = {n.lower() for n in plugin_names}
    lines = _read_lines(path)
    new_lines = []
    for line in lines:
        if line.startswith("*") and _bare(line).lower() in lower_names:
            new_lines.append(_bare(line))
        else:
            new_lines.append(line)
    _write_lines(path, new_lines)


def get_plugin_load_order(profile: dict, cfg: dict) -> list[str]:
    """Return ordered list of active plugin names (lines prefixed with *)."""
    path = _plugins_txt_path(profile, cfg)
    if not path:
        return []
    return [_bare(l) for l in _read_lines(path) if l.startswith("*")]


def find_plugins_in_mod(mod_dir: Path, profile: dict) -> list[str]:
    """
    Return plugin filenames (.esp/.esm/.esl) found in the mod's Data/ folder (or root).
    Only checks one level deep — plugins live directly in Data/, not in subdirectories.
    """
    exts = {e.lower() for e in profile.get("plugin_extensions", [])}
    if not exts:
        return []

    search_dir = mod_dir / "Data"
    if not search_dir.is_dir():
        search_dir = mod_dir

    results = []
    for f in search_dir.iterdir():
        if f.is_file() and f.suffix.lower() in exts:
            results.append(f.name)
    return results
