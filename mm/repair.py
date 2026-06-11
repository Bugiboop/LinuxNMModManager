"""
Filesystem repair utilities for the mod manager.
"""
from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path


def _find_case_conflict_groups(root: Path) -> list[list[Path]]:
    """
    Return groups of directories under *root* that have the same name when
    compared case-insensitively but differ in actual casing on disk.
    Only non-symlink directories are considered.
    Each group has 2+ members and is sorted longest-path-first so callers can
    process leaf duplicates before their parents.
    """
    by_lower: dict[str, list[Path]] = defaultdict(list)
    for d in root.rglob("*"):
        if d.is_dir() and not d.is_symlink():
            key = str(d).lower()
            by_lower[key].append(d)

    groups = [sorted(v, key=lambda p: -len(p.parts))
              for v in by_lower.values() if len(v) > 1]
    # Process deepest paths first so nested duplicates are resolved before parents
    groups.sort(key=lambda g: -len(g[0].parts))
    return groups


def _pick_canonical(group: list[Path], state: dict) -> Path:
    """
    Choose the canonical directory from a case-conflict group.

    Priority (descending):
      1. Most referenced in state.json symlink records
      2. Most entries (files + subdirs) on disk
      3. Lexicographically first (deterministic tiebreak)
    """
    # Count state.json references per candidate path prefix
    all_links: list[str] = []
    for ms in state.get("mods", {}).values():
        for entry in ms.get("symlinks", []):
            all_links.append(entry.get("link", ""))

    def _score(d: Path) -> tuple:
        prefix = str(d)
        refs = sum(1 for lnk in all_links if lnk.startswith(prefix + os.sep) or lnk == prefix)
        try:
            entries = len(list(d.iterdir()))
        except OSError:
            entries = 0
        return (refs, entries)

    return max(group, key=_score)


def repair_case_conflicts(game_root: Path, state: dict) -> dict:
    """
    Find all case-conflicting directories under game_root/Data, merge each
    group into a single canonical directory, update state.json symlink records
    in place, and remove empty leftovers.

    Returns a summary dict:
      {"fixed": int, "merged": [(canonical_path, [removed_paths])], "errors": [str]}
    """
    data_dir = game_root / "Data"
    if not data_dir.is_dir():
        return {"fixed": 0, "merged": [], "errors": ["Data directory not found"]}

    groups = _find_case_conflict_groups(data_dir)
    if not groups:
        return {"fixed": 0, "merged": [], "errors": []}

    merged: list[tuple[str, list[str]]] = []
    errors: list[str] = []
    total_fixed = 0

    for group in groups:
        # Re-check — earlier iterations may have already removed some members
        group = [d for d in group if d.exists() and d.is_dir() and not d.is_symlink()]
        if len(group) < 2:
            continue

        canonical = _pick_canonical(group, state)
        losers = [d for d in group if d != canonical]

        removed_names: list[str] = []
        for loser in losers:
            fixed_in_group, errs = _merge_dir(loser, canonical, state)
            total_fixed += fixed_in_group
            errors.extend(errs)
            removed_names.append(str(loser))

        if removed_names:
            merged.append((str(canonical), removed_names))

    return {"fixed": total_fixed, "merged": merged, "errors": errors}


def _merge_dir(src: Path, dst: Path, state: dict) -> tuple[int, list[str]]:
    """
    Move all symlinks from *src* into *dst* (maintaining sub-structure),
    update state.json records, then remove *src* if it is empty.
    Returns (count_fixed, errors).
    """
    fixed = 0
    errors: list[str] = []

    for item in list(src.rglob("*")):
        if not item.is_symlink():
            continue

        rel = item.relative_to(src)
        new_path = dst / rel

        try:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            if new_path.exists() or new_path.is_symlink():
                # Canonical already has a symlink here — remove the duplicate
                item.unlink()
            else:
                link_target = os.readlink(item)
                item.unlink()
                os.symlink(link_target, new_path)
                _update_state_link(state, str(item), str(new_path))
                fixed += 1
        except OSError as e:
            errors.append(f"{item}: {e}")

    # Remove empty directories left in src (bottom-up)
    _remove_empty_dirs(src, errors)
    return fixed, errors


def _update_state_link(state: dict, old_link: str, new_link: str) -> None:
    """Replace old_link with new_link in every mod's symlink list in state."""
    for ms in state.get("mods", {}).values():
        for entry in ms.get("symlinks", []):
            if entry.get("link") == old_link:
                entry["link"] = new_link
        for entry in ms.get("disabled_symlinks", []):
            if entry.get("link") == old_link:
                entry["link"] = new_link
        cs = ms.get("conflict_sources", {})
        if old_link in cs:
            cs[new_link] = cs.pop(old_link)


def _remove_empty_dirs(path: Path, errors: list[str]) -> None:
    """Remove *path* and any empty ancestor directories bottom-up."""
    if not path.exists():
        return
    for dirpath, dirnames, filenames in os.walk(path, topdown=False):
        dp = Path(dirpath)
        try:
            if not any(dp.iterdir()):
                dp.rmdir()
        except OSError as e:
            errors.append(f"rmdir {dp}: {e}")
    try:
        if path.exists() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass
