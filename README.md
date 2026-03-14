# Linux NM Mod Manager

> Symlink-based mod manager with a GUI and full CLI. Game-specific behaviour is defined by a small JSON **game profile**, making it easy to add support for any title.

Currently includes a built-in profile for **Stellar Blade (PC / Steam, Linux)**.

> **Engine compatibility:** This tool is currently designed for **Unreal Engine games** (UE4/UE5). Mod detection, file routing, and asset conflict checking all rely on UE-specific file formats (`.pak`, `.utoc`, `.ucas`) and directory structures (`~mods/`, UE4SS). Games built on other engines (Unity, Godot, id Tech, etc.) use different mod formats and would require new profile logic beyond what the current profile schema supports.

Your game directory is never directly overwritten. Every mod file is installed as a symlink, and any real game file that needs to be displaced is renamed to `.bak` first — restored automatically when you disable or uninstall.

---

## Features

- **GUI and CLI** — a clean dark-themed desktop app (`sbmm_gui.py`) alongside the full-featured command-line tool (`sbmm.py`)
- **Multi-game support** — switch between games from the sidebar; each game gets its own state, mod folder, and Nexus cache
- **Game profiles** — all game-specific routing rules, extensions, and Nexus settings live in `game_profiles/<id>.json`; add a new game by dropping in a JSON file
- **Nexus Mods integration** — fetches mod names, descriptions, authors, and cover art automatically using your Nexus API key; results are cached locally per game
- **Automatic mod structure detection** — handles all common Nexus Mods layouts, UE4SS mods, CNS `.json` configs, flat pak drops, and full game-tree paths
- **Variant selection** — detects mods with multiple version folders (e.g. `Green/`, `Blue/`) and shows a GUI dialog to pick one at extract or enable time
- **True asset-level conflict detection** — reads `.utoc` table-of-contents files to find mods that overwrite the exact same internal game assets, with no guesswork
- **Interactive conflict resolution** — `--clean` walks through each conflicting pair with a radio-button dialog; your choices are persisted so you're never asked twice
- **Integrity checking** — `--check` verifies every recorded symlink still exists and points to the right file
- **Safe uninstall** — all symlinks removed, all `.bak` files restored in one command
- **Settings window** — configure your Nexus API key, game paths, and appearance without editing files directly

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.10+ | |
| `customtkinter` | GUI only — installed into `.venv` (see below) |
| `Pillow` | GUI only — for cover art display |
| `p7zip-full` | Only needed for `.rar` / `.7z` archives |

```bash
sudo apt install p7zip-full   # Debian / Ubuntu
```

---

## Installation

```bash
git clone https://github.com/Bugiboop/LinuxNMModManager.git
cd LinuxNMMofManager

# Create the virtual environment and install GUI dependencies
python3 -m venv .venv
.venv/bin/pip install customtkinter Pillow
```

On first launch, `config.json` is auto-created. You can also create it manually:

```json
{
  "current_game": "stellar_blade",
  "games": {
    "stellar_blade": {
      "game_root": "/home/user/.local/share/Steam/steamapps/common/StellarBlade",
      "nexus_api_key": ""
    }
  },
  "theme": "dark"
}
```

---

## Usage

### GUI

```bash
.venv/bin/python sbmm_gui.py
```

- The **game selector** dropdown (top-left of sidebar) switches between configured games; the **+** button adds a new one
- The left sidebar lists all mods — **click** a card to view its info; **check the checkbox** to batch-select
- **Enable All / Disable All** toggle everything; **Enable Selected / Disable Selected** act on checked mods
- Individual switches enable/disable single mods
- The **⚙ settings button** opens a settings window for API key, paths, and theme (per game)
- The **info panel** shows mod metadata, cover art (fetched from Nexus if an API key is set), a folder button, and a Nexus link
- Hovering the cover art shows it full-size in a floating overlay
- The **Assets tab** lists every internal game asset the mod affects, extracted from `.utoc` files
- The **output panel** at the bottom streams live command output; interactive prompts (variant selection, conflict resolution) open GUI dialogs automatically
- Arrow keys (↑ / ↓) navigate the mod list; the scroll wheel works throughout

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

## Adding a New Game

### 1. Create the profile

Drop a file into `game_profiles/<game_id>.json`. The `game_id` must be a lowercase, underscore-separated identifier (e.g. `black_myth_wukong`).

```json
{
  "id": "my_game",
  "name": "My Game",
  "nexus_slug": "mygame",
  "pak_extensions": [".pak", ".ucas", ".utoc", ".sig"],
  "asset_extensions": [".uasset", ".ubulk", ".uexp", ".umap"],
  "utoc_strip_prefixes": ["../../../", "MyGame/Content/"],
  "ignored_filenames": ["modinfo.ini", "1.png"],
  "install_rules": [
    { "anchor": "MyGame",  "prefix": "",                    "case_insensitive": false },
    { "anchor": "Content", "prefix": "MyGame",              "case_insensitive": false },
    { "anchor": "~mods",   "prefix": "MyGame/Content/Paks", "case_insensitive": true,
      "bare_returns_none": true }
  ],
  "default_install_path": "MyGame/Content/Paks/~mods",
  "special_extension_paths": {},
  "ue4ss": {
    "mods_txt_rel_path": "MyGame/Binaries/Win64/ue4ss/Mods/mods.txt"
  }
}
```

Fields marked with `"ue4ss": null` (or omitted entirely) disable UE4SS registration for that game.

### 2. Add the game in the GUI

Click the **+** button at the top of the sidebar, pick your new profile from the dropdown, and set the game root path. The app switches to the new game immediately.

Or add it manually to `config.json`:

```json
{
  "current_game": "my_game",
  "games": {
    "stellar_blade": { "game_root": "/path/to/StellarBlade" },
    "my_game":       { "game_root": "/path/to/MyGame" }
  },
  "theme": "dark"
}
```

---

## Profile Reference

| Field | Type | Description |
|---|---|---|
| `id` | string | Must match the filename (without `.json`) |
| `name` | string | Display name shown in the GUI |
| `nexus_slug` | string | Game identifier on Nexus Mods (from the URL) |
| `pak_extensions` | array | File extensions treated as mod files |
| `asset_extensions` | array | Extensions recognised as UE5 assets inside `.utoc` |
| `utoc_strip_prefixes` | array | Path prefixes stripped from asset paths in the Assets tab |
| `ignored_filenames` | array | Files inside mod folders that are never symlinked |
| `install_rules` | array | Ordered anchor rules — see below |
| `default_install_path` | string | Catch-all destination (relative to `game_root`) |
| `special_extension_paths` | object | Extension → path overrides for the catch-all |
| `ue4ss` | object or null | UE4SS settings; omit or set to `null` to disable |

### Install rules

Each entry in `install_rules` is checked in order. The first match wins.

```json
{ "anchor": "~mods", "prefix": "SB/Content/Paks", "case_insensitive": true, "bare_returns_none": true }
```

| Key | Description |
|---|---|
| `anchor` | Folder name to look for inside the mod's file path |
| `prefix` | Path prepended *before* the anchor in the output (empty string = game_root directly) |
| `case_insensitive` | Match the anchor case-insensitively (useful for `~mods` / `~Mods`) |
| `bare_returns_none` | Skip files where the anchor is the last component (i.e. the anchor is itself a directory with no children) |

**How a rule resolves a path:**

Given `mod_root/wrapper/~mods/SubMod/file.pak` with the rule above:
```
anchor found at index 1 ("~mods")
tail  = ~mods/SubMod/file.pak
output = game_root / "SB/Content/Paks" / "~mods/SubMod/file.pak"
       = <game_root>/SB/Content/Paks/~mods/SubMod/file.pak
```

If no rule matches, the engine tries a **game-tree scan** (strips leading wrapper folders until the suffix matches a real file in the game directory), then falls back to `default_install_path`.

---

## Nexus Mods Integration

The GUI can automatically fetch mod metadata (name, author, version, description, cover image) from the Nexus Mods API. To enable it:

1. Open Settings (⚙ button) → paste your API key in the **API Key** field
2. A link to [nexusmods.com/settings/api-keys](https://www.nexusmods.com/settings/api-keys) is provided in the settings window
3. Click **Save** — the app will start fetching data for all mods with a recognised Nexus ID in their folder name

The Nexus game is determined by the `nexus_slug` in the active game profile. API responses and cover images are cached in `.nexus_cache/` (per game) so subsequent launches are instant. You can clear the cache from the Settings window at any time.

---

## Directory Layout

```
ModManager/
├── sbmm.py               # CLI mod manager (backend)
├── sbmm_gui.py           # GUI frontend
├── config.json           # your configuration (edit or use Settings / + button)
├── state.json            # auto-managed — Stellar Blade symlinks, backups, choices
├── game_profiles/        # game profile definitions
│   └── stellar_blade.json
├── games/                # per-game data for non-Stellar-Blade games
│   └── <game_id>/
│       ├── state.json
│       ├── .nexus_cache/
│       ├── mods/
│       └── compressed/
├── .venv/                # Python virtual environment (GUI dependencies)
├── .nexus_cache/         # Stellar Blade Nexus cache (root for backward compat)
├── compressed/           # Stellar Blade archives (root for backward compat)
└── mods/                 # Stellar Blade mod folders (root for backward compat)
```

> `state.json`, `.venv/`, `.nexus_cache/`, `compressed/`, and `mods/` are in `.gitignore`.

---

## How Mod Structures Are Detected

For each file in a mod folder, the engine walks the `install_rules` list from the active game profile and returns the first match. For Stellar Blade the rules are:

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

**Special cases (Stellar Blade):**
- `.json` files → `~mods/CustomNanosuitSystem/`
- `modinfo.ini`, `1.png`, `mods.txt` → silently ignored (metadata only)

---

## Conflict Detection

### Symlink-level — `--conflicts`

Read-only report of which mods are competing for the same target path right now. Fast, no scanning.

### Asset-level — `--assetcheck`

Reads the `.utoc` (IoStore table-of-contents) file inside each mod and extracts every internal asset path the mod modifies. Reports pairs of mods that overwrite the exact same UE5 asset — no filename heuristics, no guesswork.

### Interactive resolution — `--clean`

Runs the same asset scan, then walks you through each conflict with a GUI radio-button dialog:

- **`1` or `2`** — delete the loser. Single mods lose their whole folder (archive moved to `compressed-disabled/`). Collection mods (>10 paks) only lose the specific conflicting pak triplets.
- **`s`** — skip this pair for now.
- **`a`** — permanently mark this pair as intentionally coexisting. Saved to `state.json`; won't prompt again.

---

## Variant Selection

When a mod ships with multiple version subfolders (e.g. `1 Heavier Physics/`, `2 Thicc/`, `3 Original/`), the script detects the pattern and shows a GUI dialog at **extract time** and **enable time**:

```
[variants] 'CNS TsMaids' contains 3 versions — pick one to keep:
  (1) 1 Heavier physics Thicc  (4 mod file(s))
  (2) 2 Thicc                  (4 mod file(s))
  (3) 3 Original body shape    (4 mod file(s))
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

Issues and pull requests are welcome. The core is a single-file CLI (`sbmm.py`) plus a single-file GUI frontend (`sbmm_gui.py`) with no runtime dependencies beyond the standard library (and `customtkinter` + `Pillow` for the GUI).

Before submitting a PR:
- Test `--install`, `--disable`, `--enable`, and `--check` against a real mod setup
- Make sure `--assetcheck` and `--clean` still produce correct output

---

## License

MIT
