from pathlib import Path
import zipfile
import subprocess
import shutil
import os

from . import config


def _extract_zip(archive: Path, dest: Path):
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            # Normalize Windows backslashes so entries like "Data\F4SE\CBP.dll"
            # create proper subdirectories instead of a flat file with backslashes
            # in its name (Linux treats backslash as a valid filename character).
            normalized = member.filename.replace("\\", "/")
            if ".." in normalized.split("/"):
                continue  # skip path-traversal attempts
            target = dest / normalized
            if member.is_dir() or normalized.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)


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
            if Path(f).suffix.lower() in config.PAK_EXTENSIONS:
                return True
    return False


def _mod_filenames(directory: Path) -> set:
    """Return the set of lowercase mod filenames (basename only) within directory."""
    names = set()
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if Path(f).suffix.lower() in config.PAK_EXTENSIONS:
                names.add(f.lower())
    return names


def detect_variant_groups(mod_dir: Path, anchor_names: set = None) -> list:
    """
    Walk mod_dir and find directories whose sole purpose is to offer a choice
    between 2+ variant subdirectories (e.g. Transparent/, Opaque/, v1/, v2/).

    A directory qualifies as a variant-group parent when:
      - It contains no PAK_EXTENSIONS files directly
      - It has 2+ immediate subdirectories that each contain at least one mod file
        AND whose names are not known game-structure anchors (e.g. ~mods, LogicMods)

    anchor_names: optional set of lowercase anchor names from the game profile's
    install_rules.  Subdirs matching an anchor are treated as install-target folders
    (components), not as alternate versions of the same content, and are excluded
    from variant consideration.

    Returns list of (parent_dir, [variant_subdir, ...]) tuples, shallowest first.
    Stops recursing into a level once a variant group is found there.
    """
    _anchors = anchor_names or set()
    results = []

    def _walk(directory: Path):
        try:
            entries = list(directory.iterdir())
        except PermissionError:
            return
        direct_mod_files = [e for e in entries if e.is_file() and e.suffix.lower() in config.PAK_EXTENSIONS]
        subdirs = sorted([e for e in entries if e.is_dir()], key=lambda p: p.name)
        if not direct_mod_files and len(subdirs) >= 2:
            candidates = [
                d for d in subdirs
                if _has_mod_files(d) and d.name.lower() not in _anchors
            ]
            if len(candidates) >= 2:
                # Only treat as true variants if they share mod filenames.
                # Separate components (Animations/, Cosmetics/) have unique filenames
                # and should all be installed; true variants (1K/, 4K/) share names.
                subdir_names = {d: _mod_filenames(d) for d in candidates}
                from collections import Counter
                name_counts = Counter(n for names in subdir_names.values() for n in names)
                shared = {n for n, c in name_counts.items() if c >= 2}
                if shared:
                    variant_subdirs = [d for d in candidates if subdir_names[d] & shared]
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
            for f in files if Path(f).suffix.lower() in config.PAK_EXTENSIONS
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


def extract_archives(cfg: dict, force: bool = False, archive_name: str = None):
    compressed_dir: Path = cfg["compressed_dir"]
    mods_dir: Path = cfg["mods_dir"]

    archives = [
        p for p in compressed_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
    ]

    if archive_name:
        archives = [a for a in archives if a.stem == archive_name]
        if not archives:
            print(f"[error] No archive found for '{archive_name}' in compressed/")
            return

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
        # Bethesda-engine games (those with plugin_extensions) use FOMOD for selective
        # installation. Non-FOMOD mods install everything wholesale. Variant detection
        # only applies to UE4/UE5 games where quality-variant packs are common.
        profile = cfg.get("profile", {})
        if profile.get("plugin_extensions"):
            from .fomod import find_fomod_xml
            if find_fomod_xml(dest) is not None:
                print(f"  [fomod] FOMOD installer detected")
            # else: no variants for Bethesda mods — install all files
        else:
            from .fomod import find_fomod_xml
            if find_fomod_xml(dest) is not None:
                print(f"  [fomod] FOMOD installer detected — skipping variant selection")
            else:
                anchors = {rule["anchor"].lower() for rule in profile.get("install_rules", [])}
                groups = detect_variant_groups(dest, anchor_names=anchors)
                for parent_dir, variants in groups:
                    chosen = prompt_variant_choice(parent_dir, variants)
                    if chosen is not None:
                        removed = [v for v in variants if v != chosen]
                        for v in removed:
                            shutil.rmtree(v)
                        print(f"  [variants] Kept '{chosen.name}', removed {len(removed)} other variant(s).")
