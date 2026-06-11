from pathlib import Path
import os

from . import config


def _normalize_path_case(path: Path) -> Path:
    """
    Walk each component of *path* and, if the parent directory already exists
    on disk, replace the component with the actual on-disk casing of a matching
    entry (case-insensitive match).  Components that don't exist yet are left
    as-is.  This prevents mods that use different cases for the same directory
    (e.g. 'Bodyslide' vs 'BodySlide') from creating duplicate directories on
    Linux's case-sensitive filesystem.
    """
    parts = path.parts
    result = Path(parts[0])
    for part in parts[1:]:
        if result.is_dir():
            try:
                for child in result.iterdir():
                    if child.name.lower() == part.lower():
                        result = child
                        break
                else:
                    result = result / part
            except OSError:
                result = result / part
        else:
            result = result / part
    return result


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
            # dest_root: place file directly in game_root (strips all wrapper folders)
            if rule.get("dest_root") == "game_root":
                return _normalize_path_case(game_root / tail.name)
            raw = (game_root / prefix / tail) if prefix else (game_root / tail)
            return _normalize_path_case(raw)

    # Game-tree match – strip wrapper folders and compare suffix against real paths
    if game_tree:
        for i in range(1, len(parts)):
            if len(parts) - i < 2:
                break
            candidate = str(Path(*parts[i:]))
            if candidate in game_tree:
                return _normalize_path_case(game_root / Path(*parts[i:]))

    # Data-subdir anchor pass: if a well-known Data subdirectory name appears
    # anywhere in the path, route from that component onward into the install base.
    # This handles mods packed with a version-named wrapper (e.g. "TWB v1.2/Tools/…")
    # where no explicit anchor rule matches the wrapper folder name.
    install_base_for_subdir = (profile or {}).get("default_install_path", "")
    for subdir in (profile or {}).get("data_subdir_anchors", []):
        subdir_lower = subdir.lower()
        if subdir_lower in lower_parts:
            idx  = lower_parts.index(subdir_lower)
            tail = Path(*parts[idx:])
            if install_base_for_subdir:
                return _normalize_path_case(game_root / install_base_for_subdir / tail)
            return _normalize_path_case(game_root / tail)

    # Catch-all: profile-defined default path (or safe fallback).
    # Preserve the full relative path within the mod (not just the filename) so
    # subdirectory structure like Tools/BodySlide/SliderSets/ is kept intact.
    ext          = file_path.suffix.lower()
    special      = (profile or {}).get("special_extension_paths", {})
    install_base = (profile or {}).get("default_install_path", "")
    if ext in special:
        return _normalize_path_case(game_root / special[ext] / rel)
    if install_base:
        return _normalize_path_case(game_root / install_base / rel)
    return _normalize_path_case(game_root / rel)


def iter_mod_files(mod_dir: Path, profile: dict = None):
    """Yield all regular files inside a mod directory, skipping known metadata files."""
    ignored_dirs_lower = {d.lower() for d in (profile or {}).get("ignored_directories", [])}
    for root, dirs, files in os.walk(mod_dir):
        if ignored_dirs_lower:
            dirs[:] = [d for d in dirs if d.lower() not in ignored_dirs_lower]
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
