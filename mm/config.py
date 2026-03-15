from pathlib import Path
import json
import sys
import os

# When running as a PyInstaller bundle, __file__ lives inside the extracted
# archive; use the directory that contains the executable instead.
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).parent.resolve()
else:
    SCRIPT_DIR = Path(__file__).parent.parent.resolve()
CONFIG_FILE   = SCRIPT_DIR / "config.json"
STATE_FILE    = SCRIPT_DIR / "state.json"   # may be overwritten by load_config()
PROFILES_DIR  = SCRIPT_DIR / "game_profiles"

PAK_EXTENSIONS    = {".pak", ".ucas", ".utoc", ".sig", ".json"}  # overwritten by load_config()
IGNORED_FILENAMES = {"modinfo.ini", "1.png", "mods.txt"}          # overwritten by load_config()


def load_profile(game_id: str) -> dict:
    """Load a game profile JSON from game_profiles/<game_id>/<game_id>.json."""
    path = PROFILES_DIR / game_id / f"{game_id}.json"
    if not path.exists():
        print(f"[warn] Game profile not found: {path}  —  using empty profile")
        return {}
    with open(path) as f:
        return json.load(f)


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
    global STATE_FILE

    if not CONFIG_FILE.exists():
        print(f"[error] config.json not found at {CONFIG_FILE}")
        print("        Create it with at least: { \"game_root\": \"/path/to/game\" }")
        sys.exit(1)

    with open(CONFIG_FILE) as f:
        raw = json.load(f)

    # Auto-migrate old single-game format (game_root at top level)
    if "game_root" in raw:
        raw = {
            "current_game": "stellar_blade",
            "games": {"stellar_blade": {k: v for k, v in raw.items() if k != "theme"}},
            "theme": raw.get("theme", "dark"),
        }

    current_game = raw.get("current_game", "stellar_blade")
    game_cfg     = raw.get("games", {}).get(current_game, {})

    if not game_cfg.get("game_root"):
        print(f"[error] No game_root configured for game '{current_game}'.")
        sys.exit(1)

    # Per-game data directory: game_profiles/<game_id>/
    data_dir = PROFILES_DIR / current_game
    data_dir.mkdir(parents=True, exist_ok=True)

    # Migrate root-level state.json (old layout) into the game's data dir
    old_state = SCRIPT_DIR / "state.json"
    new_state  = data_dir / "state.json"
    if old_state.exists() and not new_state.exists():
        old_state.rename(new_state)

    STATE_FILE = new_state

    profile = load_profile(current_game)
    if profile.get("pak_extensions"):
        PAK_EXTENSIONS.clear(); PAK_EXTENSIONS.update(set(profile["pak_extensions"]))
    if profile.get("ignored_filenames"):
        IGNORED_FILENAMES.clear(); IGNORED_FILENAMES.update(set(profile["ignored_filenames"]))

    cfg = {
        "game_id":        current_game,
        "game_root":      Path(game_cfg["game_root"]).expanduser().resolve(),
        "mods_dir":       (data_dir / game_cfg.get("mods_dir", "mods")).resolve(),
        "compressed_dir": (data_dir / game_cfg.get("compressed_dir", "compressed")).resolve(),
        "nexus_api_key":  raw.get("nexus_api_key", game_cfg.get("nexus_api_key", "")),
        "profile":        profile,
        "data_dir":       data_dir,
    }
    cfg["mods_dir"].mkdir(parents=True, exist_ok=True)
    cfg["compressed_dir"].mkdir(parents=True, exist_ok=True)
    return cfg


def load_state() -> dict:
    if STATE_FILE.exists():  # STATE_FILE may have been updated by load_config()
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"mods": {}}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
