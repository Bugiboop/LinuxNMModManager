#!/usr/bin/env python3
"""
sbmm.py – Stellar Blade Mod Manager
Manages mods via symlinks so the game directory is never written to directly.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "config.json"
STATE_FILE = SCRIPT_DIR / "state.json"

PAK_EXTENSIONS = {".pak", ".ucas", ".utoc", ".sig", ".json"}


def scan_game_tree(game_root: Path) -> set:
    """Return a set of relative path strings for every file under game_root."""
    paths = set()
    if not game_root.exists():
        print(f"[warn] game_root not found: {game_root} — rule-4 path matching disabled")
        return paths
    for root, _dirs, files in os.walk(game_root):
        for f in files:
            try:
                rel = (Path(root) / f).relative_to(game_root)
                paths.add(str(rel))
            except ValueError:
                pass
    return paths


# ---------------------------------------------------------------------------
# Config / State helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"[error] config.json not found at {CONFIG_FILE}")
        print("        Create it with at least: { \"game_root\": \"/path/to/StellarBlade\" }")
        sys.exit(1)

    with open(CONFIG_FILE) as f:
        cfg = json.load(f)

    if not cfg.get("game_root"):
        print("[error] config.json must contain a non-empty 'game_root' path.")
        sys.exit(1)

    cfg["game_root"] = Path(cfg["game_root"]).expanduser().resolve()
    cfg["compressed_dir"] = (SCRIPT_DIR / cfg.get("compressed_dir", "compressed")).resolve()
    cfg["mods_dir"] = (SCRIPT_DIR / cfg.get("mods_dir", "mods")).resolve()

    cfg["compressed_dir"].mkdir(parents=True, exist_ok=True)
    cfg["mods_dir"].mkdir(parents=True, exist_ok=True)

    return cfg


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"mods": {}}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Archive extraction
# ---------------------------------------------------------------------------

def _extract_zip(archive: Path, dest: Path):
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)


def _extract_7z(archive: Path, dest: Path):
    result = subprocess.run(
        ["7z", "x", str(archive), f"-o{dest}", "-y"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[error] 7z failed on {archive.name}:\n{result.stderr.strip()}")


def _has_mod_files(directory: Path) -> bool:
    """Return True if directory contains any PAK_EXTENSIONS file (recursively)."""
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if Path(f).suffix.lower() in PAK_EXTENSIONS:
                return True
    return False


def detect_variant_groups(mod_dir: Path) -> list:
    """
    Walk mod_dir and find directories whose sole purpose is to offer a choice
    between 2+ variant subdirectories (e.g. Transparent/, Opaque/, v1/, v2/).

    A directory qualifies as a variant-group parent when:
      - It contains no PAK_EXTENSIONS files directly
      - It has 2+ immediate subdirectories that each contain at least one mod file

    Returns list of (parent_dir, [variant_subdir, ...]) tuples, shallowest first.
    Stops recursing into a level once a variant group is found there.
    """
    results = []

    def _walk(directory: Path):
        try:
            entries = list(directory.iterdir())
        except PermissionError:
            return
        direct_mod_files = [e for e in entries if e.is_file() and e.suffix.lower() in PAK_EXTENSIONS]
        subdirs = sorted([e for e in entries if e.is_dir()], key=lambda p: p.name)
        if not direct_mod_files and len(subdirs) >= 2:
            variant_subdirs = [d for d in subdirs if _has_mod_files(d)]
            if len(variant_subdirs) >= 2:
                results.append((directory, variant_subdirs))
                return  # don't recurse; user will prune unwanted variants
        for subdir in subdirs:
            _walk(subdir)

    _walk(mod_dir)
    return results


def prompt_variant_choice(parent: Path, variants: list) -> Path:
    """
    Show a numbered list of variant subdirectories and ask the user to pick one.
    Returns the chosen Path, or None to keep all / skip.
    """
    print(f"\n[variants] '{parent.name}' contains {len(variants)} versions — pick one to keep:")
    for i, v in enumerate(variants, 1):
        pak_count = sum(
            1 for _, _, files in os.walk(v)
            for f in files if Path(f).suffix.lower() in PAK_EXTENSIONS
        )
        print(f"  ({i}) {v.name}  ({pak_count} mod file(s))")
    while True:
        try:
            raw = input(f"  Keep which? [1-{len(variants)}/a=keep all/s=skip]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if raw in ("a", "s"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(variants):
            return variants[int(raw) - 1]
        print(f"  Enter a number 1-{len(variants)}, 'a' to keep all, or 's' to skip.")


def extract_archives(cfg: dict, force: bool = False):
    compressed_dir: Path = cfg["compressed_dir"]
    mods_dir: Path = cfg["mods_dir"]

    archives = [
        p for p in compressed_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
    ]

    if not archives:
        print("[info] No archives found in compressed/.")
        return

    for archive in archives:
        dest = mods_dir / archive.stem
        if dest.exists() and not force:
            print(f"[skip] {archive.name} already extracted (use --force to re-extract)")
            continue

        print(f"[extract] {archive.name} → mods/{archive.stem}/")
        dest.mkdir(parents=True, exist_ok=True)

        if archive.suffix.lower() == ".zip":
            _extract_zip(archive, dest)
        else:
            if shutil.which("7z") is None:
                print(f"[error] 7z not found. Install p7zip-full to extract {archive.name}")
                shutil.rmtree(dest, ignore_errors=True)
                continue
            _extract_7z(archive, dest)

        # --- variant selection ---
        groups = detect_variant_groups(dest)
        for parent_dir, variants in groups:
            chosen = prompt_variant_choice(parent_dir, variants)
            if chosen is not None:
                removed = [v for v in variants if v != chosen]
                for v in removed:
                    shutil.rmtree(v)
                print(f"  [variants] Kept '{chosen.name}', removed {len(removed)} other variant(s).")


# ---------------------------------------------------------------------------
# Mod structure detection
# ---------------------------------------------------------------------------

def resolve_target(mod_root: Path, file_path: Path, game_root: Path, game_tree: set = None) -> Path:
    """
    Given a file inside a mod folder, return its absolute target path in the game tree.

    Priority:
      1. 'SB' anchor       – path contains an 'SB' component
      2. 'Binaries' anchor – path contains a 'Binaries' component (prepends SB/)
      3. 'Win64' anchor    – path contains a 'Win64' component (prepends SB/Binaries/)
      4. 'ue4ss' anchor    – path contains a 'ue4ss' component (prepends SB/Binaries/Win64/)
      5. 'Content' anchor  – path contains a 'Content' component (prepends SB/)
      6. '~mods' anchor    – path contains a '~mods' or '~Mods' component
      7. Game-tree match   – strip leading wrapper folders until a suffix matches a
                             real game file (requires game_tree scan)
      8. Pak/mod extensions catch-all → game_root/SB/Content/Paks/~mods/
    """
    rel = file_path.relative_to(mod_root)
    parts = rel.parts
    lower_parts = [p.lower() for p in parts]

    # Rule 1: SB anchor
    if "SB" in parts:
        idx = parts.index("SB")
        return game_root.joinpath(*parts[idx:])

    # Rule 2: Binaries anchor (UE4SS and other binary mods without the SB prefix)
    if "Binaries" in parts:
        idx = parts.index("Binaries")
        return game_root / "SB" / Path(*parts[idx:])

    # Rule 3: Win64 anchor
    if "Win64" in parts:
        idx = parts.index("Win64")
        return game_root / "SB" / "Binaries" / Path(*parts[idx:])

    # Rule 4: ue4ss anchor
    if "ue4ss" in lower_parts:
        idx = lower_parts.index("ue4ss")
        return game_root / "SB" / "Binaries" / "Win64" / Path(*parts[idx:])

    # Rule 5: Content anchor (without SB prefix)
    if "Content" in parts:
        idx = parts.index("Content")
        return game_root / "SB" / Path(*parts[idx:])

    # Rule 6: ~mods anchor (handles mods that ship with a ~mods/ subfolder directly)
    if "~mods" in lower_parts:
        idx = lower_parts.index("~mods")
        # Files sit inside ~mods, so take everything after it
        remaining = parts[idx + 1:]
        if remaining:
            return game_root / "SB" / "Content" / "Paks" / "~mods" / Path(*remaining)
        # Nothing after ~mods (shouldn't happen, but guard anyway)
        return None

    # Rule 7: game-tree match – strip wrapper folders and compare suffix against real paths
    # Runs before the pak catch-all so .json game-config files land in the right place.
    if game_tree:
        for i in range(1, len(parts)):
            if len(parts) - i < 2:  # need at least subdir/file to avoid false matches
                break
            candidate = str(Path(*parts[i:]))
            if candidate in game_tree:
                return game_root / Path(*parts[i:])

    # Rule 8: everything else → ~mods; .json files go into the CNS subfolder
    if file_path.suffix.lower() == ".json":
        return game_root / "SB" / "Content" / "Paks" / "~mods" / "CustomNanosuitSystem" / file_path.name
    return game_root / "SB" / "Content" / "Paks" / "~mods" / file_path.name


IGNORED_FILENAMES = {"modinfo.ini", "1.png"}

def iter_mod_files(mod_dir: Path):
    """Yield all regular files inside a mod directory, skipping known metadata files."""
    for root, _dirs, files in os.walk(mod_dir):
        for fname in files:
            if fname.lower() in IGNORED_FILENAMES:
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


# ---------------------------------------------------------------------------
# Enable / Disable
# ---------------------------------------------------------------------------

def conflict_key(mod_a: str, mod_b: str) -> str:
    """Stable, order-independent key for a pair of mod names."""
    return " vs ".join(sorted([mod_a, mod_b]))


def prompt_conflict_resolution(mod_name: str, other_mod: str, conflicting_targets: list) -> str:
    """Interactively ask the user which mod should win. Returns the winning mod name."""
    sample = [Path(t).name for t in conflicting_targets[:5]]
    extra = len(conflicting_targets) - 5
    file_list = ", ".join(sample) + (f"  (+{extra} more)" if extra > 0 else "")
    print()
    print(f"  [conflict] Two mods claim the same file(s): {file_list}")
    print(f"    (1) {other_mod}  (already enabled)")
    print(f"    (2) {mod_name}  (being enabled now)")
    while True:
        try:
            choice = input("  Which should take priority? [1/2]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print("[abort] No choice made — skipping mod.")
            return other_mod  # default: keep what's already enabled
        if choice == "1":
            return other_mod
        if choice == "2":
            return mod_name
        print("  Please enter 1 or 2.")


def enable_mod(mod_name: str, cfg: dict, state: dict, target_map: dict = None, game_tree: set = None):
    mod_dir = cfg["mods_dir"] / mod_name
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
    resolutions: dict = state.setdefault("conflict_resolutions", {})

    # --- Phase 1: pre-scan to find inter-mod conflicts ---
    conflicts_with: dict = {}  # {other_mod_name: [target_str, ...]}
    for src_file in iter_mod_files(mod_dir):
        target = resolve_target(mod_dir, src_file, game_root, game_tree)
        owner = target_map.get(str(target))
        if owner and owner != mod_name:
            conflicts_with.setdefault(owner, []).append(str(target))

    # --- Phase 2: resolve each conflict (prompt or recall stored choice) ---
    for other_mod, claimed in conflicts_with.items():
        key = conflict_key(mod_name, other_mod)
        if key in resolutions:
            winner = resolutions[key]
            print(f"  [conflict] {mod_name} vs {other_mod}  —  {winner} wins (stored choice)")
        else:
            winner = prompt_conflict_resolution(mod_name, other_mod, claimed)
            resolutions[key] = winner
            save_state(state)

        if winner != mod_name:
            print(f"[skip]   {mod_name}  —  loses to '{other_mod}' (skipping entirely)")
            return

        # This mod wins: disable the losing mod first, then rebuild the map
        print(f"  [conflict] {mod_name} wins over '{other_mod}'  —  disabling {other_mod}")
        disable_mod(other_mod, cfg, state)
        target_map.clear()
        target_map.update(build_target_map(state))

    # --- Phase 3: enable ---
    symlinks = []
    backups = []

    for src_file in iter_mod_files(mod_dir):
        target = resolve_target(mod_dir, src_file, game_root, game_tree)

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

    mod_state["enabled"] = True
    mod_state["symlinks"] = symlinks
    mod_state["backups"] = backups
    save_state(state)

    parts = [f"{len(symlinks)} linked"]
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

    mod_state["enabled"] = False
    mod_state["symlinks"] = []
    mod_state["backups"] = []
    save_state(state)

    parts = [f"{unlinked} unlinked"]
    if restored:
        parts.append(f"{restored} restored")
    print(f"[disabled] {mod_name}  —  {', '.join(parts)}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_extract(args, cfg, state):
    extract_archives(cfg, force=args.force)


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
        state.pop("conflict_resolutions", None)
        save_state(state)


def cmd_uninstall(args, cfg, state):
    print("[uninstall] Removing all symlinks and restoring backups...")
    for name in list(state["mods"].keys()):
        disable_mod(name, cfg, state)
    state.pop("conflict_resolutions", None)
    save_state(state)
    print("[done] Uninstall complete. Conflict choices have been cleared.")


def cmd_conflicts(args, cfg, state):
    game_root: Path = cfg["game_root"]
    mods_dir: Path = cfg["mods_dir"]

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
            target = resolve_target(mod_dir, src_file, game_root, game_tree)
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

    # --- Summary ---
    print()
    if issues == 0:
        print(f"[ok]  No issues found.")
    else:
        print(f"[warn]  {issues} issue(s) found.")


# ---------------------------------------------------------------------------
# Clean  (pak-level conflict resolution + removal)
# ---------------------------------------------------------------------------

# Mods with more pak files than this are treated as collections (e.g. SBPR):
# losers only have their conflicting pak triplets removed, not the whole folder.
COLLECTION_PAK_THRESHOLD = 10


def _mod_pak_count(mod_dir: Path) -> int:
    return sum(1 for _ in mod_dir.rglob("*.pak"))


def _build_asset_conflicts(mods_dir: Path) -> tuple:
    """
    Scan every .utoc file under mods_dir and return:
      asset_index : {asset_path: [(mod_name, utoc_path), ...]}
      pairs       : [(mod_a, mod_b, [shared_assets], {utoc_paths_a}, {utoc_paths_b})]
                    sorted by mod_a, mod_b
    Mods with no .utoc files are silently skipped (pak-only mods have no asset table).
    """
    asset_index: dict = {}
    for mod_dir in sorted(mods_dir.iterdir()):
        if not mod_dir.is_dir():
            continue
        for utoc in mod_dir.rglob("*.utoc"):
            for asset in _utoc_assets(utoc):
                asset_index.setdefault(asset, []).append((mod_dir.name, utoc))

    pair_data: dict = {}  # (ma, mb) → {assets, utocs_a, utocs_b}
    for asset, owners in asset_index.items():
        mods = sorted({m for m, _ in owners})
        if len(mods) < 2:
            continue
        for i, ma in enumerate(mods):
            for mb in mods[i + 1:]:
                entry = pair_data.setdefault((ma, mb), {"assets": [], "ua": set(), "ub": set()})
                entry["assets"].append(asset)
                entry["ua"].update(u for m, u in owners if m == ma)
                entry["ub"].update(u for m, u in owners if m == mb)

    pairs = [
        (ma, mb, d["assets"], d["ua"], d["ub"])
        for (ma, mb), d in sorted(pair_data.items())
    ]
    return asset_index, pairs


def _remove_loser(mod_name: str, mod_dir: Path, conflicting_utocs: set,
                  compressed_dir: Path, cfg: dict, state: dict):
    """
    Remove a losing mod.  Collections lose only the conflicting pak triplets
    (identified by their utoc paths); single-outfit mods lose their entire folder.
    """
    compressed_disabled = compressed_dir.parent / "compressed-disabled"
    compressed_disabled.mkdir(exist_ok=True)

    pak_count = _mod_pak_count(mod_dir)

    if pak_count > COLLECTION_PAK_THRESHOLD:
        # Collection: surgically remove only the conflicting pak triplets
        removed = 0
        for utoc_path in conflicting_utocs:
            stem = utoc_path.stem
            for ext in ("pak", "ucas", "utoc", "sig"):
                f = utoc_path.parent / (stem + "." + ext)
                if f.exists():
                    f.unlink()
                    removed += 1
        print(f"  [clean] {mod_name}  —  removed {removed} file(s) from collection")
    else:
        # Single mod: disable if needed, delete folder, move archive
        mod_state = state.get("mods", {}).get(mod_name, {})
        if mod_state.get("enabled"):
            disable_mod(mod_name, cfg, state)

        for archive in compressed_dir.iterdir():
            if archive.stem == mod_name and archive.is_file():
                dest = compressed_disabled / archive.name
                shutil.move(str(archive), str(dest))
                print(f"  [clean] archived → compressed-disabled/{archive.name}")
                break

        shutil.rmtree(mod_dir)
        state.get("mods", {}).pop(mod_name, None)
        save_state(state)
        print(f"  [clean] {mod_name}  —  folder deleted")


def cmd_clean(args, cfg, state):
    mods_dir: Path = cfg["mods_dir"]
    compressed_dir: Path = cfg["compressed_dir"]

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
            short_d = d.replace("../../../", "").replace("SB/Content/", "")
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

    # Clean conflict_resolutions keys that reference removed mods
    resolutions = state.get("conflict_resolutions", {})
    for key in [k for k in resolutions if any(m in k for m in stale_set)]:
        del resolutions[key]

    # Clean clean_ignored_pairs entries that reference removed mods
    ignored = state.get("clean_ignored_pairs", [])
    state["clean_ignored_pairs"] = [p for p in ignored if not any(m in p for m in stale_set)]

    save_state(state)
    print(f"[purge] Removed {len(stale)} record(s).")


def _utoc_assets(utoc_path: Path) -> list:
    """
    Extract internal UE5 asset paths from an IoStore .utoc file.

    The .utoc string table stores a directory prefix (e.g. '../../../SB/Content/...')
    followed by individual asset filenames (.uasset, .ubulk, .uexp, .umap).
    We scan for printable ASCII runs, then reconstruct full paths by pairing each
    directory entry with the filenames that follow it.
    """
    ASSET_EXTS = {".uasset", ".ubulk", ".uexp", ".umap", ".uptnl"}

    with open(utoc_path, "rb") as f:
        data = f.read()

    # Extract printable ASCII strings (min length 4)
    strings = []
    current = []
    for byte in data:
        if 0x20 <= byte < 0x7F:
            current.append(chr(byte))
        else:
            if len(current) >= 4:
                strings.append("".join(current))
            current = []
    if len(current) >= 4:
        strings.append("".join(current))

    assets = []
    current_dir = ""
    for s in strings:
        if s.startswith("../") or (s.startswith("/") and "/" in s[1:]):
            current_dir = s.rstrip("/")
        elif Path(s).suffix.lower() in ASSET_EXTS:
            assets.append(f"{current_dir}/{s}" if current_dir else s)

    return assets


def cmd_assetcheck(args, cfg, state):
    """Scan .utoc files in all mod folders and report internal asset-level conflicts."""
    mods_dir: Path = cfg["mods_dir"]

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
            short_d = d.replace("../../../", "").replace("SB/Content/", "")
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
        "--extract", action="store_true",
        help="Extract archives from compressed/ into mods/ without enabling",
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
        "--uninstall", action="store_true",
        help="Remove all symlinks and restore .bak files",
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
    elif args.extract:
        cmd_extract(args, cfg, state)
    elif args.enable is not None:
        cmd_enable(args, cfg, state)
    elif args.disable is not None:
        cmd_disable(args, cfg, state)
    elif args.uninstall:
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


if __name__ == "__main__":
    main()
