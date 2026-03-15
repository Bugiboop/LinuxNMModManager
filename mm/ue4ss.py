from pathlib import Path
import os


def _find_ue4ss_mod_names(mod_dir: Path) -> list:
    """
    Return the UE4SS mod folder names to register in mods.txt.

    Detects two layouts:
    - Structured: any ue4ss/Mods/<Name>/ subtree (e.g. mods shipped with the full Pal/ tree)
    - Bare: direct subdirectories of mod_dir that contain a Scripts/ folder
      (e.g. mods that ship as just <ModName>/Scripts/main.lua with no prefix)
    """
    names = []

    # Structured layout: ue4ss/Mods/<Name>/
    for root, dirs, _files in os.walk(mod_dir):
        root_path = Path(root)
        if root_path.name.lower() == "mods" and root_path.parent.name.lower() == "ue4ss":
            names.extend(dirs)
            break

    # Bare layout: mod_dir/<Name>/Scripts/
    if not names:
        for d in mod_dir.iterdir():
            if d.is_dir() and (d / "Scripts").is_dir():
                names.append(d.name)

    return names


def _game_mods_txt(game_root: Path, profile: dict) -> Path | None:
    ue4ss = (profile or {}).get("ue4ss")
    if not ue4ss:
        return None
    return game_root / ue4ss["mods_txt_rel_path"]


def _register_ue4ss_mods(names: list, game_root: Path, profile: dict) -> list:
    """Add 'Name : 1' entries to the game's mods.txt for any name not already present.
    Returns the list of names actually added."""
    if not names:
        return []
    mods_txt = _game_mods_txt(game_root, profile)
    if mods_txt is None:
        return []
    if not mods_txt.exists() or mods_txt.is_symlink():
        return []
    try:
        text = mods_txt.read_text()
    except Exception:
        return []
    existing = {line.split(":")[0].strip().lower()
                for line in text.splitlines()
                if line.strip() and not line.strip().startswith(";")}
    added = []
    new_lines = text.rstrip("\n") + "\n"
    for name in names:
        if name.lower() not in existing:
            new_lines += f"{name} : 1\n"
            added.append(name)
    if added:
        try:
            mods_txt.write_text(new_lines)
        except Exception as e:
            print(f"  [warn] Could not update mods.txt: {e}")
            return []
    return added


def _unregister_ue4ss_mods(names: list, game_root: Path, profile: dict):
    """Remove named entries from the game's mods.txt."""
    if not names:
        return
    mods_txt = _game_mods_txt(game_root, profile)
    if mods_txt is None:
        return
    if not mods_txt.exists() or mods_txt.is_symlink():
        return
    try:
        lines = mods_txt.read_text().splitlines(keepends=True)
    except Exception:
        return
    lower_names = {n.lower() for n in names}
    new_lines = [l for l in lines
                 if not (l.strip() and not l.strip().startswith(";")
                         and l.split(":")[0].strip().lower() in lower_names)]
    try:
        mods_txt.write_text("".join(new_lines))
    except Exception as e:
        print(f"  [warn] Could not update mods.txt: {e}")
