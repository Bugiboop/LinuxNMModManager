from pathlib import Path
import os

from . import config


def resolve_target(mod_root: Path, file_path: Path, game_root: Path,
                   profile: dict = None, game_tree: set = None) -> Path:
    """
    Given a file inside a mod folder, return its absolute target path in the game tree.

    Rules are loaded from the game profile's 'install_rules' list (anchor-based routing),
    followed by a game-tree scan match, then a catch-all using 'default_install_path'.
    """
    rel        = file_path.relative_to(mod_root)
    parts      = rel.parts
    lower_parts = [p.lower() for p in parts]

    # Anchor rules from the game profile
    for rule in (profile or {}).get("install_rules", []):
        anchor      = rule["anchor"]
        ci          = rule.get("case_insensitive", False)
        search      = lower_parts if ci else list(parts)
        find_anchor = anchor.lower() if ci else anchor

        if find_anchor in search:
            idx = search.index(find_anchor)
            # bare_returns_none: skip if nothing follows the anchor (e.g. ~mods dir itself)
            if rule.get("bare_returns_none") and idx == len(parts) - 1:
                return None
            # anchor_offset: start the tail N steps before the anchor (preserves parent folders)
            start  = max(0, idx - rule.get("anchor_offset", 0))
            tail   = Path(*parts[start:])
            prefix = rule.get("prefix", "")
            return (game_root / prefix / tail) if prefix else (game_root / tail)

    # Game-tree match – strip wrapper folders and compare suffix against real paths
    if game_tree:
        for i in range(1, len(parts)):
            if len(parts) - i < 2:
                break
            candidate = str(Path(*parts[i:]))
            if candidate in game_tree:
                return game_root / Path(*parts[i:])

    # Catch-all: profile-defined default path (or safe fallback)
    ext          = file_path.suffix.lower()
    special      = (profile or {}).get("special_extension_paths", {})
    install_base = (profile or {}).get("default_install_path", "")
    if ext in special:
        return game_root / special[ext] / file_path.name
    if install_base:
        return game_root / install_base / file_path.name
    return game_root / file_path.name


def iter_mod_files(mod_dir: Path):
    """Yield all regular files inside a mod directory, skipping known metadata files."""
    for root, _dirs, files in os.walk(mod_dir):
        for fname in files:
            if fname.lower() in config.IGNORED_FILENAMES:
                continue
            yield Path(root) / fname


def build_target_map(state: dict) -> dict:
    """Return {str(target_path): mod_name} for every symlink owned by an enabled mod."""
    result = {}
    for mod_name, mod_state in state.get("mods", {}).items():
        if mod_state.get("enabled"):
            for entry in mod_state.get("symlinks", []):
                result[entry["link"]] = mod_name
    return result
