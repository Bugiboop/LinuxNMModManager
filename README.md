# Stellar Blade Mod Manager

> Symlink-based mod manager for **Stellar Blade (PC / Steam, Linux)** with a GUI and full CLI.

Your game directory is never directly overwritten. Every mod file is installed as a symlink, and any real game file that needs to be displaced is renamed to `.bak` first — restored automatically when you disable or uninstall.

---

## Features

- **GUI and CLI** — a clean dark-themed desktop app (`sbmm_gui.py`) alongside the full-featured command-line tool (`sbmm.py`)
- **Automatic mod structure detection** — handles all common Nexus Mods layouts, UE4SS mods, CNS `.json` configs, flat pak drops, and full game-tree paths
- **Variant selection** — detects mods with multiple version folders (e.g. `Transparent/`, `Thicc/`) and prompts you to pick one at extract or enable time
- **True asset-level conflict detection** — reads `.utoc` table-of-contents files to find mods that overwrite the exact same internal game assets, with no guesswork
- **Interactive conflict resolution** — `--clean` walks through each conflicting pair and lets you pick a winner; your choices are persisted so you're never asked twice
- **Integrity checking** — `--check` verifies every recorded symlink still exists and points to the right file
- **Safe uninstall** — all symlinks removed, all `.bak` files restored in one command

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.8+ | |
| `customtkinter` | GUI only — installed into `.venv` (see below) |
| `p7zip-full` | Only needed for `.rar` / `.7z` archives |

```bash
sudo apt install p7zip-full   # Debian / Ubuntu
```

---

## Installation

```bash
git clone https://github.com/your-username/StellarBladeModManager.git
cd StellarBladeModManager

# Create the virtual environment and install GUI dependencies
python3 -m venv .venv
.venv/bin/pip install customtkinter
```

Then edit `config.json` to point at your game:

```json
{
  "game_root": "/home/user/.local/share/Steam/steamapps/common/StellarBlade",
  "compressed_dir": "compressed",
  "mods_dir": "mods"
}
```

---

## Usage

### GUI

```bash
.venv/bin/python sbmm_gui.py
```

- Click a mod card to **select** it (blue highlight); click again to deselect
- Use **Enable Selected / Disable Selected** to batch-control any subset of mods
- Toggle switches for individual mods; **Enable All / Disable All** for everything
- Buttons at the bottom of the output panel run CLI commands and stream their output live
- Commands that need interactive input (Install, Extract, Clean) open a terminal window automatically

### CLI

```bash
# Drop archives into compressed/, then:
python sbmm.py --install          # extract + enable everything
python sbmm.py --extract          # extract only (choose variants interactively)

python sbmm.py --enable  "ModName"
python sbmm.py --disable "ModName"
python sbmm.py --enable           # all mods
python sbmm.py --disable          # all mods

python sbmm.py --list
python sbmm.py --conflicts        # symlink-level conflict report
python sbmm.py --assetcheck       # internal asset-level conflict report
python sbmm.py --clean            # interactive conflict resolution
python sbmm.py --check            # integrity check
python sbmm.py --purge            # remove stale state entries
python sbmm.py --uninstall        # remove all symlinks, restore backups
```

---

## Directory Layout

```
StellarBladeModManager/
├── sbmm.py               # CLI mod manager
├── sbmm_gui.py           # GUI frontend
├── config.json           # your configuration (edit this)
├── state.json            # auto-managed — tracks symlinks, backups, conflict choices
├── .venv/                # Python virtual environment (GUI dependency)
├── compressed/           # drop .zip / .rar / .7z archives here
├── compressed-disabled/  # archives moved here when --clean removes a mod
└── mods/                 # extracted mod folders
```

> `state.json`, `.venv/`, `compressed/`, and `mods/` are in `.gitignore` — they are local to each install and should not be committed.

---

## How Mod Structures Are Detected

The script resolves each file's game destination using a priority-ordered ruleset:

| Priority | Trigger | Destination |
|:---:|---|---|
| 1 | Path contains `SB/` | `<game_root>/SB/…` (verbatim) |
| 2 | Path contains `Binaries/` | `<game_root>/SB/Binaries/…` |
| 3 | Path contains `Win64/` | `<game_root>/SB/Binaries/Win64/…` |
| 4 | Path contains `ue4ss/` | `<game_root>/SB/Binaries/Win64/ue4ss/…` |
| 5 | Path contains `Content/` | `<game_root>/SB/Content/…` |
| 6 | Path contains `~mods/` | `<game_root>/SB/Content/Paks/~mods/…` |
| 7 | Suffix matches a real game file | That exact game path |
| 8 | Everything else | `<game_root>/SB/Content/Paks/~mods/` |

**Special cases:**
- `.json` files → `~mods/CustomNanosuitSystem/`
- `modinfo.ini` and `1.png` → silently ignored

---

## Conflict Detection

### Symlink-level — `--conflicts`

Read-only report of which mods are competing for the same target path right now. Fast, no scanning.

### Asset-level — `--assetcheck`

Reads the `.utoc` (IoStore table-of-contents) file inside each mod and extracts every internal asset path the mod modifies. Reports pairs of mods that overwrite the exact same UE5 asset — no filename heuristics, no guesswork.

```
ASSET-LEVEL CONFLICTS (2 mod pair(s), 10 shared asset(s))
──────────────────────────────────────────────────────────
  ModA
        - ModA_outfit_P
  ModB
        - ModB_outfit_P
  8 shared asset(s):
    Art/Character/PC/CH_P_EVE/
      CH_EVE_BaseBody_V02_F1_A.uasset
      CH_EVE_BaseBody_V02_F1_N.uasset
      ...
  Keep which? [1/2/s=skip once/a=always keep both]:
```

### Interactive resolution — `--clean`

Runs the same asset scan, then walks you through each conflict:

- **`1` or `2`** — delete the loser. Single mods lose their whole folder (archive moved to `compressed-disabled/`). Collection mods (>10 paks) only lose the specific conflicting pak triplets.
- **`s`** — skip this pair for now.
- **`a`** — permanently mark this pair as intentionally coexisting. Saved to `state.json`; won't prompt again.

---

## Variant Selection

When a mod ships with multiple version subfolders (e.g. `1 Heavier Physics/`, `2 Thicc/`, `3 Original/`), the script detects the pattern and prompts at **extract time** and **enable time**:

```
[variants] 'CNS TsMaids' contains 3 versions — pick one to keep:
  (1) 1 Heavier physics Thicc  (4 mod file(s))
  (2) 2 Thicc                  (4 mod file(s))
  (3) 3 Original body shape    (4 mod file(s))
  Keep which? [1-3/a=keep all/s=skip]:
```

Unchosen folders are deleted immediately so they can never create phantom conflicts.

---

## Integrity Check — `--check`

Verifies every symlink in `state.json`:
- Still exists on disk
- Still points to the correct source file (catches stale state after variant changes)

Also scans `~mods/` for orphaned symlinks not tracked by any mod.

---

## Backups

When a mod must replace a file that already exists in the game directory, the original is renamed `<filename>.bak` before the symlink is placed. `--disable` and `--uninstall` restore it automatically.

---

## Contributing

Issues and pull requests are welcome. The project is a single-file CLI (`sbmm.py`) plus a single-file GUI frontend (`sbmm_gui.py`) with no runtime dependencies beyond the standard library (and `customtkinter` for the GUI).

Before submitting a PR:
- Test `--install`, `--disable`, `--enable`, and `--check` against a real mod setup
- Make sure `--assetcheck` and `--clean` still produce correct output

---

## License

MIT
