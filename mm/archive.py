from pathlib import Path
import zipfile
import subprocess
import shutil
import os

from . import config


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
            if Path(f).suffix.lower() in config.PAK_EXTENSIONS:
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
        direct_mod_files = [e for e in entries if e.is_file() and e.suffix.lower() in config.PAK_EXTENSIONS]
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
        groups = detect_variant_groups(dest)
        for parent_dir, variants in groups:
            chosen = prompt_variant_choice(parent_dir, variants)
            if chosen is not None:
                removed = [v for v in variants if v != chosen]
                for v in removed:
                    shutil.rmtree(v)
                print(f"  [variants] Kept '{chosen.name}', removed {len(removed)} other variant(s).")
