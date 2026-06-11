# Linux NM Mod Manager

![screenshot](https://github.com/Bugiboop/LinuxNMModManager/blob/main/screenshot.png?raw=true)

> Symlink-based mod manager with a GUI and full CLI. Game-specific behaviour is defined by a small JSON **game profile**, making it straightforward to add support for any title.

Your game directory is never directly overwritten. Every mod file is installed as a symlink, and any real game file that needs to be displaced is renamed to `.bak` first — restored automatically when you disable or uninstall.

---

## Supported Games

| Game | Engine | Built-in Profile |
|---|---|---|
| Stellar Blade (PC / Steam) | Unreal Engine 5 | ✓ |
| Fallout 4 (Steam / Proton) | Bethesda Creation Engine | ✓ |
| Palworld (PC / Steam) | Unreal Engine 5 | ✓ |

Additional profiles are downloaded automatically from GitHub on first launch. You can also [add your own](#adding-a-new-game).

---

## Features

### Core
- **GUI and CLI** — a clean dark-themed desktop app (`sbmm_gui.py`) and a full-featured command-line interface (`sbmm.py`), both backed by the `mm/` package
- **Multi-game support** — switch between games from the sidebar; each game gets its own state, mod folder, and Nexus cache
- **Game profiles** — all game-specific routing rules live in `game_profiles/<id>/<id>.json`; add a new game by dropping in a JSON file
- **Wrapper-folder detection** — mods packed as `ModName/files` instead of just `files` are transparently unwrapped at install time

### Nexus Mods
- **Nexus Mods integration** — fetches mod names, descriptions, authors, and cover art automatically using your API key; cached locally per game
- **NXM download handler** — registers as the `nxm://` protocol handler so clicking "Mod Manager Download" on Nexus sends the file directly to the app
- **Download manager** — a Downloads tab tracks in-progress and completed downloads; archives land in the game's `compressed/` folder automatically

### Mod Installation
- **FOMOD installer** — a built-in multi-page wizard reads `fomod/ModuleConfig.xml` for mods that ship with an installer, showing steps, option descriptions, and preview images; your choices are remembered and pre-populated on re-enable
- **Variant selection** — detects mods with multiple version subfolders (e.g. `Green/`, `Blue/`) and shows a dialog to pick one; unchosen folders are deleted so they can never create phantom conflicts
- **Automatic mod structure detection** — handles all common Nexus layouts, UE4SS mods, flat pak drops, full game-tree paths, and Bethesda `Data/`-rooted archives

### Bethesda Engine (Fallout 4 / Skyrim)
- **Plugin load order** — a Plugins tab shows all active `.esp`/`.esm`/`.esl` plugins in load order; enabling a mod registers its plugins in `Plugins.txt` automatically
- **FOMOD priority support** — correctly handles `<file priority="…">` attributes so conflicting FOMOD destinations resolve to the highest-priority source
- **External tool launcher** — detect and launch Wine/Proton tools (BodySlide, Outfit Studio, Nemesis, LOOT) directly from the app; the tool bar appears automatically when tools are found in your mod list
- **Script extender launch** — the Launch button switches to "Launch via F4SE / SKSE" automatically when the script extender executable is detected in your game root
- **Game Settings (INI editor)** — a Game Settings tab exposes common INI options (resolution presets, fullscreen/borderless, VSync, shadow quality, FOV, mouse acceleration) as simple controls; settings are defined in the game profile JSON and written directly to the INI files on save

### Conflict Detection
- **True asset-level conflict detection** — reads `.utoc` table-of-contents files to find mods that overwrite the exact same internal UE5 assets
- **Conflict panel** — a Conflicts tab in the GUI lists all conflicting mod pairs; rules you set (priority or coexist) are saved and applied at deploy time
- **Symlink-level report** — `--conflicts` gives a fast CLI report of mods competing for the same target path

### Info & Utilities
- **Files tab** — the info panel's Files tab lists every file a mod has installed, with per-file toggle switches for plugins and per-directory summaries for assets
- **Asset search** — search inside mod files for any internal game asset path or string without opening a file manager
- **Integrity checking** — `--check` verifies every recorded symlink still exists and points to the right file
- **Settings window** — configure your Nexus API key, game paths, Plugins.txt path, Wine/Proton command, and appearance without editing files directly

---

## Requirements

| Requirement | Notes |
|---|---|
| `p7zip-full` | Only needed for `.rar` / `.7z` archives |

```bash
sudo apt install p7zip-full   # Debian / Ubuntu
```

Python, pip, and a virtual environment are **not required** when using the pre-built executable. They are only needed if you are [running from source](#running-from-source).

---

## Installation

### Pre-built executable (recommended)

1. Download `ModManager-<version>-linux.zip` from the [Releases](https://github.com/Bugiboop/LinuxNMModManager/releases) page and extract it
2. Edit `config.json` and set the game root for each game you want to use:

```json
{
  "current_game": "stellar_blade",
  "games": {
    "stellar_blade": {
      "game_root": "/home/user/.local/share/Steam/steamapps/common/StellarBlade",
      "nexus_api_key": ""
    },
    "fallout4": {
      "game_root": "/home/user/.local/share/Steam/steamapps/common/Fallout 4",
      "nexus_api_key": ""
    }
  },
  "theme": "dark"
}
```

3. Run `./ModManager`

Game profiles are downloaded automatically from GitHub on first launch.

### Running from source

```bash
git clone https://github.com/Bugiboop/LinuxNMModManager.git
cd LinuxNMModManager

python3 -m venv .venv
.venv/bin/pip install customtkinter Pillow
```

Create `config.json` in the project root as shown above, then launch with:

```bash
.venv/bin/python sbmm_gui.py   # GUI
python sbmm.py                 # CLI
```

---

## Usage

### GUI

```bash
./ModManager                    # pre-built executable
.venv/bin/python sbmm_gui.py   # from source
```

**Sidebar**
- The **game selector** dropdown (top-left) switches between configured games; **+** adds a new one
- Each card has an enable/disable toggle switch; **check the checkbox** to batch-select
- **Enable All / Disable All** toggle everything; **Enable Selected / Disable Selected** act on checked mods
- The **⚙ settings** button opens the settings window for API key, paths, and theme

**Navigation tabs**
- **Mods** — mod list and info panel (default)
- **Plugins** — plugin load order for Bethesda games; shows owning mod beside each entry *(shown only for Bethesda-engine games)*
- **Conflicts** — lists conflicting mod pairs; set priority rules or mark pairs as intentionally coexisting *(shown when conflicts exist)*
- **Game Settings** — INI editor with common display, gameplay, and FOV options *(shown for games with INI settings defined in their profile)*
- **Downloads** — active and completed NXM downloads

**Info panel tabs**
- **Info** — mod metadata, cover art (fetched from Nexus), status, conflict notes
- **Files** — every installed file; toggle plugins on/off individually; directory summaries for asset files
- **Assets** — internal game asset paths extracted from `.utoc` files

**Other**
- Hovering the cover art shows it full-size in a floating overlay
- Arrow keys (↑ / ↓) navigate the mod list
- The **output panel** at the bottom streams live command output; interactive prompts (FOMOD wizard, variant selection) open GUI dialogs automatically
- The **Launch** button in the nav bar starts the game via Steam, or via F4SE/SKSE if the script extender is detected

### CLI

```bash
# Drop archives into game_profiles/<game_id>/compressed/, then:
./ModManager --install          # extract + enable everything
./ModManager --extract          # extract only (choose variants interactively)

./ModManager --enable  "ModName"
./ModManager --disable "ModName"
./ModManager --enable           # all mods
./ModManager --disable          # all mods

./ModManager --list
./ModManager --conflicts        # symlink-level conflict report
./ModManager --assetcheck       # internal asset-level conflict report (UE5)
./ModManager --clean            # interactive conflict resolution
./ModManager --check            # integrity check
./ModManager --purge            # remove stale state entries
./ModManager --uninstall        # remove all symlinks, restore backups
```

When running from source, replace `./ModManager` with `python sbmm.py`.

---

## Bethesda Engine Games (Fallout 4, Skyrim SE)

### Setup

Fallout 4 and Skyrim SE run through Steam/Proton on Linux. The mod manager needs two additional paths configured in **Settings**:

- **Plugins.txt** — inside the Proton prefix, usually at:
  `~/.local/share/Steam/steamapps/compatdata/<appid>/pfx/drive_c/users/steamuser/AppData/Local/<Game>/Plugins.txt`
- **Wine/Proton Command** — command used to launch `.exe` tools like BodySlide. Set this to `wine`, or a full path to a Proton binary (e.g. `~/.local/share/Steam/steamapps/common/Proton 9.0/proton`).

The app will auto-detect the paths from your `game_root` setting where possible.

### FOMOD installer

Mods that ship with a `fomod/ModuleConfig.xml` installer open the FOMOD wizard automatically when you enable them. The wizard shows each page of options with descriptions and preview images. Your selections are saved so the wizard reopens pre-populated if you re-enable the mod.

Mods with no install steps (required files only) install silently.

### Plugin load order

The **Plugins** tab lists all active plugins in load order, with the owning mod shown beside each entry. Load order is determined by the position of each `*PluginName.ext` line in `Plugins.txt`. Enabling a mod appends its plugins; disabling a mod deactivates them (removes the `*` prefix but preserves position).

### External tools

The mod manager detects tools installed as mods (BodySlide, Outfit Studio, Nemesis, LOOT) and shows a button bar below the action buttons when any are found. Clicking a tool launches it via the configured Wine/Proton command with the correct working directory.

### Game Settings (INI editor)

The **Game Settings** tab provides a form-based editor for common INI options, grouped by file and category. For Fallout 4 these include:

| Setting | INI file |
|---|---|
| Resolution (preset dropdown + custom W×H) | `Fallout4Prefs.ini` |
| Full Screen / Borderless Window | `Fallout4Prefs.ini` |
| VSync | `Fallout4Prefs.ini` |
| Shadow Resolution | `Fallout4Prefs.ini` |
| Mouse Acceleration | `Fallout4Prefs.ini` |
| 1st Person FOV / 3rd Person FOV | `Fallout4.ini` |

Click **Save** to write changes directly to the INI files. No game restart prompt — changes take effect on next game launch.

The settings exposed in this panel are defined by the `ini_files` array in the game profile JSON, so adding new settings requires only a profile edit.

---

## Adding a New Game

### 1. Create the profile

Create a directory `game_profiles/<game_id>/` and drop a file named `<game_id>.json` inside it. The `game_id` must be a lowercase, underscore-separated identifier.

**Unreal Engine example:**

```json
{
  "id": "my_game",
  "name": "My Game",
  "nexus_slug": "mygame",
  "steam_app_id": "123456",
  "pak_extensions": [".pak", ".ucas", ".utoc", ".sig"],
  "asset_extensions": [".uasset", ".ubulk", ".uexp", ".umap"],
  "utoc_strip_prefixes": ["../../../", "MyGame/Content/"],
  "ignored_filenames": ["modinfo.ini", "1.png"],
  "install_rules": [
    { "anchor": "MyGame",  "prefix": "",                    "case_insensitive": false },
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

**Bethesda Creation Engine example:**

```json
{
  "id": "skyrim_se",
  "name": "Skyrim Special Edition",
  "nexus_slug": "skyrimspecialedition",
  "steam_app_id": "489830",
  "launch_exe": "SkyrimSE.exe",
  "script_extender_exe": "skse64_loader.exe",
  "pak_extensions": [".esp", ".esm", ".esl", ".bsa", ".dll", ".pex", ".nif", ".dds"],
  "plugin_extensions": [".esp", ".esm", ".esl"],
  "plugins_txt_path": "~/.local/share/Steam/steamapps/compatdata/489830/pfx/drive_c/users/steamuser/AppData/Local/Skyrim Special Edition/Plugins.txt",
  "plugins_txt_relative": "users/steamuser/AppData/Local/Skyrim Special Edition/Plugins.txt",
  "ignored_filenames": ["modinfo.ini", "readme.txt", "moduleconfig.xml"],
  "ignored_directories": ["fomod"],
  "install_rules": [
    { "anchor": "Data", "prefix": "", "case_insensitive": true }
  ],
  "default_install_path": "Data",
  "data_subdir_anchors": ["Meshes", "Textures", "Scripts", "Interface", "Sound"],
  "external_tools": [
    {
      "id": "bodyslide",
      "name": "BodySlide",
      "detect_path": "Data/Tools/BodySlide/BodySlide x64.exe",
      "launcher": "wine",
      "launch_args": []
    }
  ]
}
```

### 2. Add the game in the GUI

Click the **+** button in the sidebar, pick your new profile from the dropdown, and set the game root path.

Or add it manually to `config.json`:

```json
{
  "current_game": "my_game",
  "games": {
    "stellar_blade": { "game_root": "/path/to/StellarBlade" },
    "my_game":       { "game_root": "/path/to/MyGame" }
  }
}
```

---

## Profile Reference

### Core fields

| Field | Type | Description |
|---|---|---|
| `id` | string | Must match the subdirectory name and filename (without `.json`) |
| `name` | string | Display name shown in the GUI |
| `nexus_slug` | string | Game identifier on Nexus Mods (from the URL) |
| `steam_app_id` | string | Steam application ID (used for the Launch button and Proton path derivation) |
| `pak_extensions` | array | File extensions treated as mod files |
| `asset_extensions` | array | Extensions recognised as UE5 assets inside `.utoc` |
| `utoc_strip_prefixes` | array | Path prefixes stripped from asset paths in the Assets tab |
| `ignored_filenames` | array | Files inside mod folders that are never symlinked |
| `ignored_directories` | array | Subdirectories inside mod folders that are skipped entirely (e.g. `fomod`) |
| `install_rules` | array | Ordered anchor rules — see below |
| `default_install_path` | string | Catch-all destination (relative to `game_root`) |
| `data_subdir_anchors` | array | Well-known subdirectory names used as routing anchors when no install rule matches |
| `special_extension_paths` | object | Extension → path overrides for the catch-all |
| `ue4ss` | object or null | UE4SS settings; omit or set to `null` to disable |

### Bethesda fields

| Field | Type | Description |
|---|---|---|
| `launch_exe` | string | Game executable filename |
| `script_extender_exe` | string | Script extender executable (e.g. `f4se_loader.exe`); enables the F4SE/SKSE launch button |
| `plugin_extensions` | array | Extensions treated as Bethesda plugins (shows Plugins tab) |
| `plugins_txt_path` | string | Default path to `Plugins.txt` (can be overridden in Settings) |
| `plugins_txt_relative` | string | Path relative to the Proton prefix user directory (used for auto-derivation) |
| `external_tools` | array | Tools to detect and launch — see below |
| `ini_files` | array | INI settings to expose in the Game Settings tab — see below |
| `ini_docs_relative` | string | Path relative to `steamuser/` for the game's documents folder (used to find INI files) |

### Install rules

Each entry in `install_rules` is checked in order. The first match wins.

| Key | Description |
|---|---|
| `anchor` | Folder name to look for in the mod file's path |
| `prefix` | Path prepended before the anchor in the output (empty string = `game_root` directly) |
| `case_insensitive` | Match the anchor case-insensitively |
| `bare_returns_none` | Skip files where the anchor is the last path component |
| `dest_root` | Set to `"game_root"` to place the file directly at game root (strips all subfolders; used for F4SE loader DLLs) |
| `anchor_offset` | Start the output path N steps before the anchor (preserves parent folders) |

**Example resolution** — `mod_root/wrapper/~mods/SubMod/file.pak` with `{ "anchor": "~mods", "prefix": "SB/Content/Paks" }`:
```
anchor found → tail = ~mods/SubMod/file.pak
output = game_root / "SB/Content/Paks" / "~mods/SubMod/file.pak"
```

If no rule matches, the engine tries a **game-tree scan** (strips leading wrapper folders until the suffix matches a real file in the game directory), then checks `data_subdir_anchors`, then falls back to `default_install_path`.

### External tools

Each entry in `external_tools` defines one tool button:

| Key | Description |
|---|---|
| `id` | Internal identifier |
| `name` | Button label |
| `detect_path` | Path relative to `game_root` to check for the executable |
| `detect_path_alt` | Alternate path to check (e.g. 32-bit vs 64-bit binary) |
| `launcher` | `"wine"` — run via Wine/Proton; `"native"` — run directly |
| `launch_args` | Extra command-line arguments |

### INI settings (Game Settings tab)

`ini_files` is an array of file definitions. Each file can expose multiple settings:

```json
"ini_files": [
  {
    "label": "Display",
    "file": "Fallout4Prefs.ini",
    "settings": [
      { "group": "Resolution", "label": "Resolution", "type": "resolution",
        "section": "Display", "key_w": "iSize W", "key_h": "iSize H" },
      { "group": "Display", "label": "VSync", "type": "bool",
        "section": "Display", "key": "iPresentInterval" },
      { "group": "Display", "label": "Shadow Resolution", "type": "choice",
        "section": "Display", "key": "iShadowMapResolution",
        "choices": [
          {"label": "1024 — Medium", "value": "1024"},
          {"label": "4096 — Ultra",  "value": "4096"}
        ]}
    ]
  }
]
```

| Setting type | Widget | Notes |
|---|---|---|
| `resolution` | Preset dropdown + optional W×H fields | Uses `key_w` and `key_h` instead of `key` |
| `bool` | Toggle switch | Reads/writes `0`/`1` |
| `choice` | Dropdown | `choices` array of `{"label", "value"}` |
| `int` / `float` | Text field | |

---

## Nexus Mods Integration

1. Open Settings (⚙) → paste your API key in the **API Key** field
2. A link to [nexusmods.com/settings/api-keys](https://www.nexusmods.com/settings/api-keys) is provided in the settings window
3. Click **Save** — the app fetches metadata for all mods with a recognised Nexus ID in their folder name

The Nexus game is determined by the `nexus_slug` in the active game profile. Responses and cover images are cached per game in `.nexus_cache/`. The cache can be cleared from the Settings window.

**NXM downloads** — to register the app as the `nxm://` protocol handler, click "Mod Manager Download" on any Nexus mod page while the app is running. The file downloads in the background and appears in the Downloads tab.

---

## Conflict Detection

### Asset-level (UE5 games) — `--assetcheck`

Reads the `.utoc` table-of-contents file inside each mod and extracts every internal asset path the mod modifies. Reports pairs of mods that overwrite the exact same UE5 asset.

### Symlink-level — `--conflicts`

Fast CLI report of mods competing for the same target path. Works for all games.

### Conflict panel (GUI)

The **Conflicts** tab lists all conflicting pairs. For each pair you can:
- **Set priority** — always prefer one mod over the other
- **Allow coexist** — mark the pair as intentionally sharing a file (suppresses future warnings)

Rules are saved in `state.json` and applied at deploy time.

### Interactive resolution — `--clean`

CLI version of conflict resolution: walks through each conflicting pair with a dialog and lets you delete the loser or mark the pair as coexisting.

---

## Directory Layout

```
LinuxNMModManager/
├── sbmm.py                  # CLI entry point
├── sbmm_gui.py              # GUI entry point
├── config.json              # your configuration (edit or use Settings)
├── mm/                      # backend + GUI packages
│   ├── archive.py           # extraction and archive handling
│   ├── commands.py          # CLI command implementations
│   ├── config.py            # config load/save, paths
│   ├── conflicts.py         # conflict detection logic
│   ├── external_tools.py    # Wine/Proton tool detection and launch
│   ├── fomod.py             # FOMOD XML parser and file resolver
│   ├── mods.py              # enable/disable/install logic
│   ├── plugins.py           # Plugins.txt read/write
│   ├── repair.py            # integrity repair utilities
│   ├── resolver.py          # file routing (install rules, game-tree scan)
│   └── gui/
│       ├── app.py           # main application window
│       ├── conflicts_panel.py / conflicts_dialog.py
│       ├── downloads.py     # download manager panel
│       ├── fomod_dialog.py  # FOMOD wizard dialog
│       ├── ini_panel.py     # Game Settings / INI editor panel
│       ├── panels.py        # info, log, settings panels
│       ├── plugins_panel.py # plugin load order panel
│       ├── sidebar.py       # mod list and game selector
│       └── …
└── game_profiles/
    └── <game_id>/
        ├── <game_id>.json   # game profile definition
        ├── state.json        # auto-managed (gitignored)
        ├── mods/             # extracted mod folders (gitignored)
        └── compressed/       # downloaded mod archives (gitignored)
```

---

## Contributing

Issues and pull requests are welcome. The backend lives in the `mm/` package and the GUI in `mm/gui/`. Entry points are `sbmm.py` (CLI) and `sbmm_gui.py` (GUI). Runtime dependencies beyond the standard library are `customtkinter` and `Pillow` (GUI only).

Before submitting a PR:
- Test `--install`, `--disable`, `--enable`, and `--check` against a real mod setup
- For Bethesda games, verify FOMOD installs, plugin registration, and INI save/load

---

## License

MIT
