from pathlib import Path
import os
import shutil

from .config import save_state
from .archive import detect_variant_groups, prompt_variant_choice
from .resolver import resolve_target, iter_mod_files, build_target_map
from .ue4ss import _find_ue4ss_mod_names, _register_ue4ss_mods, _unregister_ue4ss_mods


def _prompt_conflict(mod_name: str, other_mod: str, claimed: list) -> str:
    """
    Ask the user how to handle a conflict between two mods.
    Returns: 's' = skip conflicting files, '1' = disable other mod, '2' = skip this mod.
    """
    sample    = [Path(t).name for t in claimed[:3]]
    extra     = len(claimed) - 3
    file_list = ", ".join(sample) + (f"  (+{extra} more)" if extra > 0 else "")
    print()
    print(f"  [conflict] '{other_mod}' owns {len(claimed)} file(s) also claimed by '{mod_name}':")
    print(f"    {file_list}")
    print(f"    (1) Disable '{other_mod}' — '{mod_name}' takes over all its files")
    print(f"    (2) Skip '{mod_name}' entirely — keep '{other_mod}' as-is")
    print(f"    (s) Skip conflicting files — install the rest of '{mod_name}' normally")
    while True:
        try:
            choice = input("  Resolve conflict [1/2/s]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "s"
        if choice in ("1", "2"):
            return choice
        if choice in ("s", ""):
            return "s"
        print("  Please enter 1, 2, or s.")


def enable_mod(mod_name: str, cfg: dict, state: dict, target_map: dict = None, game_tree: set = None):
    from .config import scan_game_tree
    mod_dir = cfg["mods_dir"] / mod_name
    print(f"[enabling] {mod_name}")
    if not mod_dir.is_dir():
        print(f"[error] Mod directory not found: {mod_dir}")
        return

    mod_state = state["mods"].setdefault(mod_name, {"enabled": False, "symlinks": [], "backups": []})

    if mod_state["enabled"]:
        print(f"[skip] {mod_name} is already enabled.")
        return

    # --- Variant detection: prune unselected version folders before enabling ---
    groups = detect_variant_groups(mod_dir)
    for parent_dir, variants in groups:
        chosen = prompt_variant_choice(parent_dir, variants)
        if chosen is not None:
            removed = [v for v in variants if v != chosen]
            for v in removed:
                shutil.rmtree(v)
            print(f"  [variants] Kept '{chosen.name}', removed {len(removed)} other variant(s).")

    if target_map is None:
        target_map = build_target_map(state)
    if game_tree is None:
        game_tree = scan_game_tree(cfg["game_root"])

    game_root: Path = cfg["game_root"]
    profile = cfg.get("profile", {})

    # --- Pre-scan: group conflicts by owning mod, then prompt once per owner ---
    conflicts_with: dict = {}  # {other_mod: [target_str, ...]}
    for src_file in iter_mod_files(mod_dir):
        target = resolve_target(mod_dir, src_file, game_root, profile, game_tree)
        if target is None:
            continue
        owner = target_map.get(str(target))
        if owner and owner != mod_name:
            conflicts_with.setdefault(owner, []).append(str(target))

    skip_targets: set = set()  # targets to skip during placement

    for other_mod, claimed in conflicts_with.items():
        choice = _prompt_conflict(mod_name, other_mod, claimed)
        if choice == "2":
            print(f"[skip] '{mod_name}' — keeping '{other_mod}' as-is")
            return
        if choice == "1":
            print(f"  [conflict] Disabling '{other_mod}' — '{mod_name}' takes over")
            disable_mod(other_mod, cfg, state)
            target_map.clear()
            target_map.update(build_target_map(state))
        else:  # "s"
            skip_targets.update(claimed)

    symlinks = []
    backups = []
    skipped = 0

    for src_file in iter_mod_files(mod_dir):
        target = resolve_target(mod_dir, src_file, game_root, profile, game_tree)
        if target is None:
            continue

        if str(target) in skip_targets:
            print(f"  [conflict] {src_file.name}  ←  skipping (conflict)")
            skipped += 1
            continue

        target.parent.mkdir(parents=True, exist_ok=True)

        # Backup real game files that would be overwritten
        if target.exists() and not target.is_symlink():
            bak = Path(str(target) + ".bak")
            print(f"  [backup]  {target.relative_to(game_root)}  →  {bak.name}")
            target.rename(bak)
            backups.append({"original": str(target), "backup": str(bak)})

        if target.is_symlink():
            target.unlink()

        os.symlink(src_file, target)
        target_map[str(target)] = mod_name
        symlinks.append({"link": str(target), "target": str(src_file)})

    # Register any UE4SS mod subfolders in the game's mods.txt
    ue4ss_names = _find_ue4ss_mod_names(mod_dir)
    added = _register_ue4ss_mods(ue4ss_names, game_root, profile)
    if added:
        mod_state["ue4ss_mods"] = added
        print(f"  [ue4ss] Registered in mods.txt: {', '.join(added)}")

    mod_state["enabled"] = True
    mod_state["symlinks"] = symlinks
    mod_state["backups"] = backups
    save_state(state)

    parts = [f"{len(symlinks)} linked"]
    if skipped:
        parts.append(f"{skipped} file(s) skipped (conflict)")
    if added:
        parts.append(f"{len(added)} ue4ss registered")
    if backups:
        parts.append(f"{len(backups)} backed up")
    print(f"[enabled]  {mod_name}  —  {', '.join(parts)}")


def disable_mod(mod_name: str, cfg: dict, state: dict):
    mod_state = state["mods"].get(mod_name)
    if not mod_state:
        print(f"[skip] {mod_name} has no recorded state.")
        return
    if not mod_state["enabled"]:
        print(f"[skip] {mod_name} is already disabled.")
        return

    unlinked = 0
    restored = 0

    for entry in mod_state.get("symlinks", []):
        link = Path(entry["link"])
        if link.is_symlink():
            link.unlink()
            unlinked += 1
        elif link.exists():
            print(f"  [warn] {link.name} exists but is not a symlink — skipping removal")

    for entry in mod_state.get("backups", []):
        bak = Path(entry["backup"])
        original = Path(entry["original"])
        if bak.exists():
            bak.rename(original)
            restored += 1
        else:
            print(f"  [warn] backup not found: {bak.name}")

    # Unregister any UE4SS mods we previously added to mods.txt
    ue4ss_names = mod_state.pop("ue4ss_mods", [])
    if ue4ss_names:
        _unregister_ue4ss_mods(ue4ss_names, cfg["game_root"], cfg.get("profile", {}))
        print(f"  [ue4ss] Removed from mods.txt: {', '.join(ue4ss_names)}")

    mod_state["enabled"] = False
    mod_state["symlinks"] = []
    mod_state["backups"] = []
    save_state(state)

    parts = [f"{unlinked} unlinked"]
    if ue4ss_names:
        parts.append(f"{len(ue4ss_names)} ue4ss unregistered")
    if restored:
        parts.append(f"{restored} restored")
    print(f"[disabled] {mod_name}  —  {', '.join(parts)}")
