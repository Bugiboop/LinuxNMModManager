from pathlib import Path
import os
import shutil
import argparse

from .config import load_config, load_state, save_state, scan_game_tree
from .archive import extract_archives
from .mods import enable_mod, disable_mod
from .assets import _build_asset_conflicts, _remove_loser, _mod_pak_count
from .resolver import resolve_target, iter_mod_files, build_target_map


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_extract(args, cfg, state):
    archive_name = None if args.extract is True else args.extract
    extract_archives(cfg, force=args.force, archive_name=archive_name)


def cmd_install(args, cfg, state):
    extract_archives(cfg, force=args.force)
    mods_dir: Path = cfg["mods_dir"]
    mod_names = [d.name for d in sorted(mods_dir.iterdir()) if d.is_dir()]
    if not mod_names:
        print("[info] No mod folders found in mods/ after extraction.")
        return
    target_map = build_target_map(state)
    game_tree = scan_game_tree(cfg["game_root"])
    for name in mod_names:
        enable_mod(name, cfg, state, target_map, game_tree)


def cmd_enable(args, cfg, state):
    # args.enable is a mod name string, or True when flag used with no value
    mod_name = None if args.enable is True else args.enable
    mods_dir: Path = cfg["mods_dir"]
    if mod_name:
        game_tree = scan_game_tree(cfg["game_root"])
        enable_mod(mod_name, cfg, state, game_tree=game_tree)
    else:
        target_map = build_target_map(state)
        game_tree = scan_game_tree(cfg["game_root"])
        mod_names = [d.name for d in sorted(mods_dir.iterdir()) if d.is_dir()]
        for name in mod_names:
            enable_mod(name, cfg, state, target_map, game_tree)


def cmd_disable(args, cfg, state):
    mod_name = None if args.disable is True else args.disable
    if mod_name:
        disable_mod(mod_name, cfg, state)
    else:
        for name in list(state["mods"].keys()):
            disable_mod(name, cfg, state)


def _uninstall_mod(mod_name: str, cfg: dict, state: dict):
    """Disable, delete extracted folder, and purge all state for a single mod."""
    mods_dir: Path = cfg["mods_dir"]
    print(f"[uninstall] {mod_name}")

    if state["mods"].get(mod_name, {}).get("enabled"):
        disable_mod(mod_name, cfg, state)

    mod_dir = mods_dir / mod_name
    if mod_dir.exists():
        shutil.rmtree(mod_dir)
        print(f"  [deleted]  {mod_dir.name}/")

    if mod_name in state["mods"]:
        del state["mods"][mod_name]
        print(f"  [purged]   State record cleared")

    ignored = state.get("clean_ignored_pairs", [])
    before = len(ignored)
    state["clean_ignored_pairs"] = [p for p in ignored if mod_name not in p]
    if before - len(state["clean_ignored_pairs"]):
        print(f"  [cleared]  Conflict resolution choices forgotten")

    save_state(state)
    print(f"[done] {mod_name} uninstalled — re-extract from compressed/ to reinstall")


def cmd_uninstall(args, cfg, state):
    mod_name = None if args.uninstall is True else args.uninstall
    if mod_name:
        _uninstall_mod(mod_name, cfg, state)
    else:
        print("[uninstall] Removing all symlinks and restoring backups...")
        for name in list(state["mods"].keys()):
            disable_mod(name, cfg, state)
        print("[done] Uninstall complete.")


def cmd_conflicts(args, cfg, state):
    game_root: Path = cfg["game_root"]
    mods_dir: Path = cfg["mods_dir"]
    profile = cfg.get("profile", {})

    mod_dirs = sorted([d for d in mods_dir.iterdir() if d.is_dir()])
    if not mod_dirs:
        print("[info] No mod folders found in mods/.")
        return

    game_tree = scan_game_tree(game_root)

    # Dry-run: walk every mod on disk and compute where each file would land.
    # Track (mod_name, src_file) pairs per target so we can distinguish
    # intra-mod collisions (variant folders) from inter-mod conflicts.
    target_to_sources: dict = {}  # {target_str: [(mod_name, src_file), ...]}
    for mod_dir in mod_dirs:
        for src_file in iter_mod_files(mod_dir):
            target = resolve_target(mod_dir, src_file, game_root, profile, game_tree)
            if target is None:
                continue
            target_to_sources.setdefault(str(target), []).append((mod_dir.name, src_file))

    # Split into intra-mod (same mod, multiple variants → same dest) and inter-mod
    intra: dict = {}  # {target_str: [(mod_name, src_file), ...]}
    inter: dict = {}  # {target_str: {mod_name: [src_file, ...]}}
    for target_str, sources in target_to_sources.items():
        if len(sources) <= 1:
            continue
        mod_names = {mod for mod, _ in sources}
        if len(mod_names) == 1:
            intra[target_str] = sources
        else:
            grouped: dict = {}
            for mod_name, src_file in sources:
                grouped.setdefault(mod_name, []).append(src_file)
            inter[target_str] = grouped

    if not intra and not inter:
        print(f"No conflicts detected across {len(mod_dirs)} mod(s).")
        return

    def active_mod_for(target_str):
        target = Path(target_str)
        if not target.is_symlink():
            return None
        real = Path(os.readlink(target))
        for mod_name, mod_state in state["mods"].items():
            for entry in mod_state.get("symlinks", []):
                if entry["link"] == target_str and Path(entry["target"]) == real:
                    return mod_name
        return None

    if inter:
        print(f"INTER-MOD CONFLICTS ({len(inter)} file(s) claimed by multiple mods)")
        print("-" * 70)
        for target_str, grouped in sorted(inter.items()):
            target = Path(target_str)
            try:
                rel = target.relative_to(game_root)
            except ValueError:
                rel = target
            active = active_mod_for(target_str)
            active_label = f"  (active: {active})" if active else ""
            print(f"  {rel}{active_label}")
            for mod_name, srcs in sorted(grouped.items()):
                for s in srcs:
                    print(f"      {mod_name}  ←  {s.relative_to(mods_dir)}")
            print()

    if intra:
        print(f"INTRA-MOD COLLISIONS ({len(intra)} file(s) where one mod has multiple variants resolving to the same name)")
        print("-" * 70)
        for target_str, sources in sorted(intra.items()):
            target = Path(target_str)
            try:
                rel = target.relative_to(game_root)
            except ValueError:
                rel = target
            mod_name = sources[0][0]
            print(f"  {rel}  [{mod_name}]")
            for _, src_file in sources:
                print(f"      {src_file.relative_to(mods_dir / mod_name)}")
            print()


def cmd_check(args, cfg, state):
    game_root: Path = cfg["game_root"]
    issues = 0

    # Build a set of all link paths state knows about (for orphan detection)
    known_links: set = set()
    for mod_state in state.get("mods", {}).values():
        for entry in mod_state.get("symlinks", []):
            known_links.add(entry["link"])

    # --- Per-mod checks ---
    for mod_name, mod_state in sorted(state.get("mods", {}).items()):
        if not mod_state.get("enabled"):
            continue

        mod_issues = []

        for entry in mod_state.get("symlinks", []):
            link = Path(entry["link"])
            expected_src = Path(entry["target"])

            if not link.exists() and not link.is_symlink():
                mod_issues.append(f"  [missing]  symlink gone from disk: {link}")
            elif not link.is_symlink():
                mod_issues.append(f"  [not-symlink]  expected symlink but found real file: {link}")
            elif Path(os.readlink(link)) != expected_src:
                mod_issues.append(f"  [stale]  points to wrong target: {link}")
                mod_issues.append(f"             expected: {expected_src}")
                mod_issues.append(f"             actual:   {os.readlink(link)}")
            elif not expected_src.exists():
                mod_issues.append(f"  [broken]  source file missing: {expected_src.relative_to(cfg['mods_dir'])}")

        for entry in mod_state.get("backups", []):
            bak = Path(entry["backup"])
            if not bak.exists():
                mod_issues.append(f"  [backup-missing]  {bak.name}")

        if mod_issues:
            print(f"\n{mod_name}")
            for line in mod_issues:
                print(line)
            issues += len(mod_issues)

    # --- Orphan scan: symlinks in the game tree not tracked by state ---
    orphans = []
    if game_root.exists():
        for root, _dirs, files in os.walk(game_root):
            for fname in files:
                full = Path(root) / fname
                if full.is_symlink() and str(full) not in known_links:
                    try:
                        rel = full.relative_to(game_root)
                    except ValueError:
                        rel = full
                    orphans.append(str(rel))

    if orphans:
        print(f"\n[orphans]  {len(orphans)} symlink(s) in game tree not tracked by state:")
        for o in sorted(orphans):
            print(f"  {o}")
        issues += len(orphans)

    # --- Conflict check ---
    print()
    print("── CONFLICT CHECK " + "─" * 52)
    target_map = build_target_map(state)
    conflicts: dict = {}  # {target_str: [mod_name, ...]}
    for target_str, owner in target_map.items():
        conflicts.setdefault(target_str, [])
    # Rebuild from scratch to catch all owners
    conflict_targets: dict = {}
    for mod_name, mod_state in state.get("mods", {}).items():
        if not mod_state.get("enabled"):
            continue
        for entry in mod_state.get("symlinks", []):
            conflict_targets.setdefault(entry["link"], []).append(mod_name)
    conflict_found = False
    for target_str, owners in sorted(conflict_targets.items()):
        if len(owners) > 1:
            try:
                rel = Path(target_str).relative_to(cfg["game_root"])
            except ValueError:
                rel = target_str
            print(f"  [conflict]  {rel}")
            for o in owners:
                print(f"      claimed by: {o}")
            conflict_found = True
    if not conflict_found:
        print("  No symlink conflicts.")

    # --- Summary ---
    print()
    if issues == 0:
        print(f"[ok]  No issues found.")
    else:
        print(f"[warn]  {issues} issue(s) found.")


def cmd_clean(args, cfg, state):
    mods_dir: Path = cfg["mods_dir"]
    compressed_dir: Path = cfg["compressed_dir"]
    strip_prefixes = cfg.get("profile", {}).get("utoc_strip_prefixes", ["../../../"])

    if not mods_dir.exists() or not any(mods_dir.iterdir()):
        print("[info] No mod folders found.")
        return

    print("[clean] Scanning .utoc files for internal asset conflicts...")
    _, pairs = _build_asset_conflicts(mods_dir)

    if not pairs:
        print("No internal asset conflicts detected.")
        return

    # Pairs the user has permanently marked as "keep both"
    ignored_pairs: list = state.setdefault("clean_ignored_pairs", [])
    ignored_set = {frozenset(p) for p in ignored_pairs}

    def _is_ignored(ma: str, mb: str) -> bool:
        return frozenset([ma, mb]) in ignored_set

    def _ignore_pair(ma: str, mb: str):
        key = sorted([ma, mb])
        if key not in ignored_pairs:
            ignored_pairs.append(key)
            ignored_set.add(frozenset(key))
        save_state(state)

    def _prompt_winner(ma: str, mb: str, assets: list, utocs_a: set, utocs_b: set) -> str | None:
        """Returns winning mod name, '__always__' to permanently ignore, or None to skip."""
        pak_a = _mod_pak_count(mods_dir / ma)
        pak_b = _mod_pak_count(mods_dir / mb)
        # Show a sample of shared assets grouped by directory
        by_dir: dict = {}
        for a in sorted(assets):
            parts = a.rsplit("/", 1)
            d, f = (parts[0], parts[1]) if len(parts) == 2 else ("", a)
            by_dir.setdefault(d, []).append(f)
        print(f"  {len(assets)} shared internal asset(s):")
        shown = 0
        for d, files in sorted(by_dir.items()):
            if shown >= 8:
                print(f"    ... (+{len(assets) - shown} more)")
                break
            short_d = d
            for prefix in strip_prefixes:
                short_d = short_d.replace(prefix, "")
            print(f"    {short_d}/")
            for fname in files:
                if shown >= 8:
                    break
                print(f"      {fname}")
                shown += 1
        paks_a = sorted({u.stem for u in utocs_a})
        paks_b = sorted({u.stem for u in utocs_b})
        print(f"  (1) {ma}  ({pak_a} pak(s))")
        for p in paks_a:
            print(f"        - {p}")
        print(f"  (2) {mb}  ({pak_b} pak(s))")
        for p in paks_b:
            print(f"        - {p}")
        while True:
            try:
                raw = input("  Keep which? [1/2/s=skip once/a=always keep both]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return None
            if raw == "1":
                return ma
            if raw == "2":
                return mb
            if raw in ("s", "skip", ""):
                return None
            if raw == "a":
                return "__always__"
            print("  Enter 1, 2, s (skip once), or a (always keep both).")

    print(f"Found {len(pairs)} conflicting mod pair(s).\n")
    print("── ASSET-LEVEL CONFLICTS " + "─" * 46)

    for ma, mb, assets, utocs_a, utocs_b in pairs:
        if _is_ignored(ma, mb):
            print(f"\n  [always-keep-both] {ma}  vs  {mb}  (skipping)")
            continue
        print(f"\n  {ma}")
        print(f"  {mb}")
        winner = _prompt_winner(ma, mb, assets, utocs_a, utocs_b)
        if winner == "__always__":
            _ignore_pair(ma, mb)
            print("  [saved] will always keep both — won't ask again")
            continue
        if winner is None:
            print("  [skip] keeping both")
            continue
        loser = mb if winner == ma else ma
        loser_utocs = utocs_b if loser == mb else utocs_a
        loser_dir = mods_dir / loser
        print(f"  → keeping {winner}, removing {loser}")
        _remove_loser(loser, loser_dir, loser_utocs, compressed_dir, cfg, state)

    print("\n[done] Clean complete.")


def cmd_purge(args, cfg, state):
    """Remove state records for mods whose folder no longer exists in mods/."""
    mods_dir: Path = cfg["mods_dir"]
    on_disk = {d.name for d in mods_dir.iterdir() if d.is_dir()} if mods_dir.exists() else set()

    stale = [name for name in state["mods"] if name not in on_disk]

    if not stale:
        print("[purge] Nothing to purge — all tracked mods still have folders on disk.")
        return

    print(f"[purge] {len(stale)} stale record(s) found (folder deleted but state remains):\n")
    for name in sorted(stale):
        mod_state = state["mods"][name]
        status = "enabled" if mod_state.get("enabled") else "disabled"
        symlink_count = len(mod_state.get("symlinks", []))
        print(f"  {name}  [{status}]  ({symlink_count} recorded symlink(s))")

    print()
    try:
        confirm = input("Remove these records from state? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if confirm != "y":
        print("[purge] Aborted.")
        return

    stale_set = set(stale)
    for name in stale:
        del state["mods"][name]

    # Clean clean_ignored_pairs entries that reference removed mods
    ignored = state.get("clean_ignored_pairs", [])
    state["clean_ignored_pairs"] = [p for p in ignored if not any(m in p for m in stale_set)]

    save_state(state)
    print(f"[purge] Removed {len(stale)} record(s).")


def cmd_assetcheck(args, cfg, state):
    """Scan .utoc files in all mod folders and report internal asset-level conflicts."""
    mods_dir: Path = cfg["mods_dir"]
    strip_prefixes = cfg.get("profile", {}).get("utoc_strip_prefixes", ["../../../"])

    mod_dirs = sorted([d for d in mods_dir.iterdir() if d.is_dir()])
    if not mod_dirs:
        print("[info] No mod folders found.")
        return

    no_utoc = [d.name for d in mod_dirs if not any(d.rglob("*.utoc"))]
    if no_utoc:
        print(f"[info] {len(no_utoc)} mod(s) have no .utoc files (pak-only or not yet extracted):")
        for name in no_utoc:
            print(f"  {name}")
        print()

    asset_index, pairs = _build_asset_conflicts(mods_dir)

    if not pairs:
        print("No internal asset conflicts found across mod .utoc files.")
        return

    total_assets = sum(len(assets) for _, _, assets, _, _ in pairs)
    print(f"ASSET-LEVEL CONFLICTS ({len(pairs)} mod pair(s), {total_assets} shared asset(s))")
    print("─" * 70)
    for ma, mb, assets, _, _ in pairs:
        print(f"\n  {ma}")
        print(f"  {mb}")
        print(f"  {len(assets)} shared asset(s):")
        by_dir: dict = {}
        for a in sorted(assets):
            parts = a.rsplit("/", 1)
            d, f = (parts[0], parts[1]) if len(parts) == 2 else ("", a)
            by_dir.setdefault(d, []).append(f)
        for d, files in sorted(by_dir.items()):
            short_d = d
            for prefix in strip_prefixes:
                short_d = short_d.replace(prefix, "")
            print(f"    {short_d}/")
            for fname in files:
                print(f"      {fname}")


def cmd_list(args, cfg, state):
    mods_dir: Path = cfg["mods_dir"]
    on_disk = {d.name for d in mods_dir.iterdir() if d.is_dir()} if mods_dir.exists() else set()
    tracked = set(state["mods"].keys())
    all_mods = sorted(on_disk | tracked)

    if not all_mods:
        print("No mods found.")
        return

    print(f"{'Mod':<40} {'Status':<12} {'Symlinks':>8}")
    print("-" * 62)
    for name in all_mods:
        mod_state = state["mods"].get(name, {})
        enabled = mod_state.get("enabled", False)
        n_links = len(mod_state.get("symlinks", []))
        status = "enabled" if enabled else "disabled"
        marker = "+" if enabled else " "
        print(f"  [{marker}] {name:<36} {status:<12} {n_links:>8}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="sbmm",
        description="Stellar Blade Mod Manager – symlink-based mod management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python sbmm.py --install              # extract archives + enable all mods\n"
            "  python sbmm.py --extract              # extract archives only, don't enable\n"
            "  python sbmm.py --install --force      # re-extract even if folder exists\n"
            "  python sbmm.py --enable               # enable all mods in mods/\n"
            "  python sbmm.py --enable MyMod         # enable a specific mod\n"
            "  python sbmm.py --disable MyMod        # disable a specific mod\n"
            "  python sbmm.py --disable              # disable all mods\n"
            "  python sbmm.py --uninstall            # remove all symlinks, restore backups\n"
            "  python sbmm.py --list                 # show mod status\n"
            "  python sbmm.py --conflicts            # show mods that claim the same file\n"
        ),
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--install", action="store_true",
        help="Extract archives from compressed/ and enable all mods",
    )
    group.add_argument(
        "--extract", nargs="?", const=True, metavar="ARCHIVE",
        help="Extract archives from compressed/ (omit name to extract all)",
    )
    group.add_argument(
        "--enable", nargs="?", const=True, metavar="MOD",
        help="Enable a mod by folder name (omit name to enable all)",
    )
    group.add_argument(
        "--disable", nargs="?", const=True, metavar="MOD",
        help="Disable a mod by folder name (omit name to disable all)",
    )
    group.add_argument(
        "--uninstall", nargs="?", const=True, metavar="MOD",
        help="Uninstall a mod (disable + delete folder + purge state), or all mods if no name given",
    )
    group.add_argument(
        "--list", action="store_true",
        help="List all mods and their current status",
    )
    group.add_argument(
        "--conflicts", action="store_true",
        help="Show target-path collisions between currently-enabled mods",
    )
    group.add_argument(
        "--check", action="store_true",
        help="Verify state matches disk: missing/stale symlinks, missing backups, orphaned symlinks",
    )
    group.add_argument(
        "--clean", action="store_true",
        help="Interactively resolve pak-level conflicts: exact filename matches and inferred outfit overlaps",
    )
    group.add_argument(
        "--purge", action="store_true",
        help="Remove state records for mods whose folder no longer exists in mods/",
    )
    group.add_argument(
        "--assetcheck", action="store_true",
        help="Scan .utoc files to find mods overwriting the same internal game assets",
    )

    parser.add_argument(
        "--force", action="store_true",
        help="With --install: re-extract archives even if already extracted",
    )

    args = parser.parse_args()

    cfg = load_config()
    state = load_state()

    if args.install:
        cmd_install(args, cfg, state)
    elif args.extract is not None:
        cmd_extract(args, cfg, state)
    elif args.enable is not None:
        cmd_enable(args, cfg, state)
    elif args.disable is not None:
        cmd_disable(args, cfg, state)
    elif args.uninstall is not None:
        cmd_uninstall(args, cfg, state)
    elif args.list:
        cmd_list(args, cfg, state)
    elif args.conflicts:
        cmd_conflicts(args, cfg, state)
    elif args.check:
        cmd_check(args, cfg, state)
    elif args.clean:
        cmd_clean(args, cfg, state)
    elif args.purge:
        cmd_purge(args, cfg, state)
    elif args.assetcheck:
        cmd_assetcheck(args, cfg, state)
