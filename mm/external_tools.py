"""
External tool detection and launcher for games that require Windows executables
(BodySlide, Nemesis, F4SE, etc.) or native Linux tools (LOOT).

On Linux/Ubuntu, Bethesda games run via Steam Proton. Windows .exe tools
should be launched using the same Proton/Wine prefix as the game so they share
the same DLL/registry environment. The wine_cmd is configurable per-game in
config.json but defaults to auto-detecting the game's Proton prefix.
"""
from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _is_windows_exe(path: Path) -> bool:
    return path.suffix.lower() == ".exe"


def _wine_cmd(cfg: dict) -> str:
    """Return the configured wine/proton command (default: 'wine')."""
    return cfg.get("wine_cmd", "wine") or "wine"


def _steam_library_paths() -> list[Path]:
    """
    Return all Steam library root folders by parsing libraryfolders.vdf.
    Always includes the default ~/.local/share/Steam path first.
    """
    default = Path.home() / ".local" / "share" / "Steam"
    paths = [default]
    vdf = default / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith('"path"'):
                    parts = line.split('"')
                    if len(parts) >= 4:
                        p = Path(parts[3])
                        if p not in paths:
                            paths.append(p)
        except Exception:
            pass
    return paths


def _proton_prefix(profile: dict, game_root: Path = None) -> Path | None:
    """
    Return the Steam Proton compat data prefix for this game.

    If game_root is provided, prefer the prefix that lives in the same
    Steam library as the game install — Steam always pairs a game with the
    compatdata folder in its own library, so this finds the right prefix
    even when the same app-ID has stale entries in other libraries.
    """
    app_id = profile.get("steam_app_id", "")
    if not app_id:
        return None

    libraries = _steam_library_paths()
    candidates = []
    for library in libraries:
        candidate = library / "steamapps" / "compatdata" / str(app_id) / "pfx"
        if candidate.is_dir():
            candidates.append((library, candidate))

    if not candidates:
        return None

    # If we know where the game lives, pick the prefix from the same library.
    if game_root:
        for library, candidate in candidates:
            try:
                game_root.relative_to(library)
                return candidate
            except ValueError:
                pass

    # Fallback: return the first found (original behaviour)
    return candidates[0][1]


def _wine_env(cfg: dict, profile: dict, game_root: Path = None) -> dict:
    """
    Build environment variables for launching a Wine process.
    Sets WINEPREFIX to the game's Proton compat data folder when available,
    preferring the prefix that lives in the same Steam library as game_root.
    """
    env = os.environ.copy()
    user_cmd = cfg.get("wine_cmd", "")
    if not user_cmd or user_cmd == "wine":
        prefix = _proton_prefix(profile, game_root=game_root)
        if prefix:
            env["WINEPREFIX"] = str(prefix)
    return env


def _to_windows_path(linux_path: Path) -> str:
    """Convert a Linux absolute path to a Wine Z:-drive Windows path."""
    return "Z:" + str(linux_path).replace("/", "\\")


# ── BodySlide config auto-setup ─────────────────────────────────────────────

def _configure_bodyslide(exe: Path, game_root: Path) -> None:
    """
    Ensure BodySlide's Config.xml has the correct GameDataPath/OutputDataPath
    set to the game's Data folder. Only fills in missing/empty values so user
    customisations are preserved.
    """
    config_path = exe.parent / "Config.xml"
    data_path = _to_windows_path(game_root / "Data") + "\\"

    if config_path.exists():
        try:
            tree = ET.parse(config_path)
            root = tree.getroot()
        except ET.ParseError:
            return

        changed = False
        for tag in ("GameDataPath", "OutputDataPath"):
            el = root.find(tag)
            if el is None:
                el = ET.SubElement(root, tag)
                el.text = data_path
                changed = True
            elif not (el.text or "").strip():
                el.text = data_path
                changed = True

        if changed:
            tree.write(config_path, encoding="unicode", xml_declaration=False)
    else:
        # Create a minimal Config.xml if none exists
        root = ET.Element("Config")
        for tag in ("GameDataPath", "OutputDataPath"):
            el = ET.SubElement(root, tag)
            el.text = data_path
        tree = ET.ElementTree(root)
        tree.write(config_path, encoding="unicode", xml_declaration=False)


# ── Wine Steam stubs ─────────────────────────────────────────────────────────

def _setup_wine_steam_stubs(profile: dict, game_root: Path) -> None:
    """
    Write a minimal libraryfolders.vdf inside the Proton prefix so Windows
    tools like LOOT can auto-detect Steam games without a real Steam install.

    LOOT's detection sequence:
      1. Read C:\\Program Files (x86)\\Steam\\config\\libraryfolders.vdf
      2. For each library path, scan for steamapps/appmanifest_<appid>.acf
      3. Read installdir from the acf → combine to get the game path

    The Z: drive in Wine maps to the Linux root, so actual Steam libraries on
    /media/... are reachable as Z:\\media\\....  The real appmanifest files
    already exist there — we only need to create the stub VDF that points to them.
    """
    app_id = profile.get("steam_app_id", "")
    if not app_id:
        return

    prefix = _proton_prefix(profile, game_root=game_root)
    if not prefix:
        return

    config_dir = prefix / "drive_c" / "Program Files (x86)" / "Steam" / "config"
    vdf_path = config_dir / "libraryfolders.vdf"

    # Collect Steam libraries that actually contain this game's appmanifest
    entries: list[str] = []
    for idx, lib in enumerate(_steam_library_paths()):
        if not (lib / "steamapps" / f"appmanifest_{app_id}.acf").exists():
            continue
        wine_path = "Z:" + str(lib).replace("/", "\\")
        entries.append(
            f'\t"{idx}"\n'
            f'\t{{\n'
            f'\t\t"path"\t\t"{wine_path}"\n'
            f'\t\t"apps"\n'
            f'\t\t{{\n'
            f'\t\t\t"{app_id}"\t\t"0"\n'
            f'\t\t}}\n'
            f'\t}}'
        )

    if not entries:
        return

    vdf_content = '"libraryfolders"\n{\n' + "\n".join(entries) + "\n}\n"

    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        vdf_path.write_text(vdf_content, encoding="utf-8")
    except Exception:
        pass  # non-fatal — tool may still work via manual config


# ── Tool discovery ───────────────────────────────────────────────────────────

def find_tool_exe(tool_cfg: dict, mods_dir: Path, game_root: Path) -> Path | None:
    """
    Search for a tool executable. Tries detect_path then detect_path_alt:
    1. game_root / path  (symlinked when the tool mod is enabled)
    2. Any mod folder under mods_dir / path
    """
    paths = [p for p in (tool_cfg.get("detect_path", ""),
                         tool_cfg.get("detect_path_alt", "")) if p]
    for raw_path in paths:
        norm = raw_path.replace("\\", "/")
        if game_root:
            candidate = game_root / norm
            if candidate.exists():
                return candidate
        if mods_dir and mods_dir.is_dir():
            for mod_dir in mods_dir.iterdir():
                if not mod_dir.is_dir():
                    continue
                candidate = mod_dir / norm
                if candidate.exists():
                    return candidate
    return None


# ── Launchers ────────────────────────────────────────────────────────────────

def launch_tool(tool_cfg: dict, mods_dir: Path, game_root: Path,
                cfg: dict, profile: dict = None) -> tuple[bool, str]:
    """
    Launch an external tool. Returns (success: bool, message: str).

    Launcher selection (from tool_cfg["launcher"]):
      "native"    — run directly
      "wine"      — run via wine with WINEPREFIX set to the game's Proton prefix
      "proton"    — alias for "wine"
      "steam_url" — open via xdg-open steam://...
      absent/auto — infer: .exe → "wine", else "native"
    """
    if profile is None:
        profile = {}

    launcher = tool_cfg.get("launcher", "auto")
    args = tool_cfg.get("launch_args", [])

    # System executable (e.g. native LOOT on PATH)
    sys_exe = tool_cfg.get("system_executable", "")
    if sys_exe and launcher in ("native", "auto"):
        try:
            subprocess.Popen([sys_exe] + args)
            return True, f"Launched {tool_cfg.get('name', sys_exe)}"
        except FileNotFoundError:
            return False, (f"'{sys_exe}' not found on PATH.\n"
                           f"Install {tool_cfg.get('name', sys_exe)} and ensure it is accessible.")

    # Find the exe file
    exe = find_tool_exe(tool_cfg, mods_dir, game_root)
    if exe is None:
        name = tool_cfg.get("name", tool_cfg.get("id", "tool"))
        return False, (f"Could not find {name}.\n"
                       f"Make sure the mod is enabled and the tool is installed.")

    # Determine effective launcher
    if launcher == "auto":
        launcher = "wine" if _is_windows_exe(exe) else "native"

    cwd = str(exe.parent)

    # Tool-specific pre-launch configuration
    tool_id = tool_cfg.get("id", "")
    if tool_id in ("bodyslide", "outfit_studio") and game_root:
        try:
            _configure_bodyslide(exe, game_root)
        except Exception:
            pass   # non-fatal — user can configure manually

    if launcher in ("wine", "proton"):
        if sys.platform == "win32":
            cmd = [str(exe)] + args
            env = None
        else:
            user_cmd = cfg.get("wine_cmd", "").strip()
            proton_script: Path | None = None
            if user_cmd and user_cmd != "wine":
                p = Path(user_cmd)
                if p.exists():
                    proton_script = p
            app_id = profile.get("steam_app_id", "")
            if proton_script is None and app_id:
                proton_script = _find_proton_script(app_id, game_root=game_root)

            if proton_script is not None:
                _setup_wine_steam_stubs(profile, game_root=game_root)
                prefix = _proton_prefix(profile, game_root=game_root)
                compat_data_dir = prefix.parent if prefix else None
                steam_client_dir = _steam_library_paths()[0]
                env = os.environ.copy()
                if compat_data_dir:
                    env["STEAM_COMPAT_DATA_PATH"] = str(compat_data_dir)
                env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(steam_client_dir)
                if prefix:
                    env["WINEPREFIX"] = str(prefix)
                cmd = [str(proton_script), "run", str(exe)] + args
            else:
                # Fallback: plain wine with WINEPREFIX
                wine = _wine_cmd(cfg)
                cmd = [wine, str(exe)] + args
                env = _wine_env(cfg, profile, game_root=game_root)

            # Apply per-tool env overrides (e.g. LIBGL_ALWAYS_SOFTWARE for BodySlide)
            tool_env = tool_cfg.get("env", {})
            if tool_env and env is not None:
                env.update(tool_env)
    elif launcher == "steam_url":
        steam_id = tool_cfg.get("steam_app_id", "")
        url = f"steam://rungameid/{steam_id}" if steam_id else ""
        if not url:
            return False, "No steam_app_id configured for this tool."
        try:
            subprocess.Popen(["xdg-open", url])
            return True, f"Opening Steam for {tool_cfg.get('name', 'tool')}"
        except FileNotFoundError:
            return False, "xdg-open not found."
    else:
        cmd = [str(exe)] + args
        env = None

    try:
        subprocess.Popen(cmd, cwd=cwd, env=env)
        tool_name = tool_cfg.get("name", exe.name)
        if launcher in ("wine", "proton") and sys.platform != "win32":
            runner = cmd[0] if cmd else "?"
            runner_name = Path(runner).parent.name if Path(runner).name == "proton" else Path(runner).name
            return True, f"Launched {tool_name} via {runner_name}"
        return True, f"Launched {tool_name}"
    except FileNotFoundError as e:
        if launcher in ("wine", "proton"):
            return False, (f"Could not find Proton or Wine to run {tool_cfg.get('name', exe.name)}.\n"
                           f"Set a Proton path in Settings → Wine/Proton Command.")
        return False, str(e)
    except Exception as e:
        return False, str(e)


def _read_compat_tool_for_app(app_id: str) -> str:
    """
    Return the configured Proton compat-tool name for app_id, or "" if unknown.

    Checks two locations Steam uses:
      1. appmanifest_{appid}.acf  (set when Properties → Compatibility is
         changed directly for that game)
      2. userdata/*/config/localconfig.vdf  (per-account compatibility mapping)
    """
    import re as _re
    libraries = _steam_library_paths()

    # --- appmanifest ---
    for lib in libraries:
        acf = lib / "steamapps" / f"appmanifest_{app_id}.acf"
        if not acf.exists():
            continue
        try:
            text = acf.read_text(errors="replace")
            m = _re.search(
                r'"CompatToolData"\s*\{[^}]*?"name"\s+"([^"]+)"',
                text, _re.DOTALL,
            )
            if m:
                return m.group(1)
        except Exception:
            pass

    # --- localconfig.vdf (per-account setting) ---
    steam_root = _steam_library_paths()[0]
    userdata = steam_root / "userdata"
    if userdata.is_dir():
        for user_dir in userdata.iterdir():
            vdf = user_dir / "config" / "localconfig.vdf"
            if not vdf.exists():
                continue
            try:
                text = vdf.read_text(errors="replace")
                # Find the CompatToolMapping block for this app
                m = _re.search(
                    rf'"{app_id}"\s*\{{[^}}]*?"name"\s+"([^"]+)"',
                    text, _re.DOTALL,
                )
                if m:
                    return m.group(1)
            except Exception:
                pass

    return ""


def _proton_version_key(script: Path) -> tuple:
    """Sort key for Proton installations: real version numbers before specials."""
    import re as _re
    name = script.parent.name
    # "Proton 10.0" → (10, 0, 0), "Proton 9.0 (Beta)" → (9, 0, 0)
    m = _re.search(r"(\d+)[\.\s](\d+)", name)
    if m:
        return (int(m.group(1)), int(m.group(2)), 0)
    # Specials like Experimental / Hotfix rank after numbered releases
    return (0, 0, 0)


def _pfx_proton_version(app_id: str, game_root: Path = None) -> str | None:
    """
    Read the version string written by Proton into the compatdata/version file.
    Returns e.g. '11.0-100', '10.0-4', or None if not found.
    Prefers the pfx in the same Steam library as game_root when provided.
    """
    for lib in _steam_library_paths():
        version_file = lib / "steamapps" / "compatdata" / str(app_id) / "version"
        if not version_file.exists():
            continue
        if game_root:
            try:
                game_root.relative_to(lib)
                return version_file.read_text().strip()
            except ValueError:
                continue
        else:
            return version_file.read_text().strip()
    # Retry without the library filter if nothing matched
    for lib in _steam_library_paths():
        version_file = lib / "steamapps" / "compatdata" / str(app_id) / "version"
        if version_file.exists():
            return version_file.read_text().strip()
    return None


def _find_proton_script(app_id: str, game_root: Path = None) -> Path | None:
    """
    Find the Proton run script for app_id.

    Priority:
      1. Per-game compat tool configured in Steam (appmanifest or localconfig)
      2. Proton whose version file matches the pfx version file — avoids pfx
         migrations caused by running a different Proton than Steam used last
      3. Newest numbered Proton version installed
    """
    import re as _re

    configured = _read_compat_tool_for_app(app_id)

    # Collect all Proton installs from all Steam libraries
    installs: list[Path] = []
    for lib in _steam_library_paths():
        common = lib / "steamapps" / "common"
        if not common.is_dir():
            continue
        for d in common.iterdir():
            if d.is_dir() and "proton" in d.name.lower() and (d / "proton").exists():
                installs.append(d / "proton")

    # Also check compatibilitytools.d for Proton-GE and similar
    for base in (_steam_library_paths()[0], Path.home() / ".steam" / "root"):
        compat_tools = base / "compatibilitytools.d"
        if compat_tools.is_dir():
            for d in compat_tools.iterdir():
                script = d / "proton"
                if d.is_dir() and script.exists():
                    installs.append(script)

    if not installs:
        return None

    # Sort: numbered versions newest-first, specials at the end
    installs.sort(key=_proton_version_key, reverse=True)

    if configured:
        ver = _re.sub(r"^proton[_ -]?", "", configured, flags=_re.IGNORECASE)
        ver = ver.replace("_", ".").lower()
        for script in installs:
            name_norm = script.parent.name.lower().replace(" ", ".").replace("-", ".")
            if ver in name_norm:
                return script

    # Match against the pfx version file to avoid migrating the prefix.
    # The version file contains a string like "11.0-100"; each Proton build
    # has its own version file containing e.g. "experimental-11.0-20260529".
    # Extract the major.minor and look for a Proton whose version file matches.
    pfx_ver = _pfx_proton_version(app_id, game_root=game_root)
    if pfx_ver:
        m = _re.match(r"(\d+\.\d+)", pfx_ver)
        if m:
            target = m.group(1)  # e.g. "11.0"
            for script in installs:
                ver_file = script.parent / "version"
                if ver_file.exists():
                    content = ver_file.read_text().strip()
                    if target in content:
                        return script

    # Pick the newest numbered release
    for script in installs:
        if _proton_version_key(script) > (0, 0, 0):
            return script

    return installs[0]


def launch_script_extender(profile: dict, game_root: Path, cfg: dict) -> tuple[bool, str]:
    """
    Launch the script extender executable (F4SE, SKSE64) as game launcher.

    On Linux, script extenders must run through Proton (not plain wine) so
    they have access to Steam's runtime DLLs (tier0_s64, steamclient64, …).
    We find the Proton version configured for the game, set the required
    STEAM_COMPAT_* environment variables, and invoke:
        proton waitforexitandrun f4se_loader.exe
    """
    se_exe = profile.get("script_extender_exe", "")
    if not se_exe:
        return False, "No script_extender_exe configured in the game profile."

    exe = game_root / se_exe
    if not exe.exists():
        return False, f"{se_exe} not found in game root. Enable the F4SE/SKSE mod first."

    cwd = str(game_root)

    if sys.platform == "win32":
        try:
            subprocess.Popen([str(exe)], cwd=cwd)
            return True, f"Launched {se_exe}"
        except Exception as e:
            return False, str(e)

    # ── Linux: Proton path (check user override first) ───────────────────
    user_cmd = cfg.get("wine_cmd", "").strip()
    proton_script: Path | None = None

    if user_cmd and user_cmd != "wine":
        # User pointed wine_cmd at a Proton script or a custom wrapper
        p = Path(user_cmd)
        if p.exists():
            proton_script = p

    app_id = profile.get("steam_app_id", "")
    if proton_script is None and app_id:
        proton_script = _find_proton_script(app_id, game_root=game_root)

    if proton_script is None:
        # Hard fallback: plain wine (better than nothing, but likely to fail
        # for script extenders that need Steam DLLs)
        wine = _wine_cmd(cfg)
        env  = _wine_env(cfg, profile, game_root=game_root)
        try:
            subprocess.Popen([wine, str(exe)], cwd=cwd, env=env)
            return True, f"Launched {se_exe} via wine (Proton not found — may not work)"
        except FileNotFoundError:
            return False, (
                f"Could not find Proton or wine.\n"
                f"Install Proton via Steam, or set the path in Settings → "
                f"Wine/Proton Command."
            )
        except Exception as e:
            return False, str(e)

    # ── Build the Proton environment ─────────────────────────────────────
    prefix = _proton_prefix(profile, game_root=game_root)
    compat_data_dir = prefix.parent if prefix else None  # .../compatdata/377160

    # STEAM_COMPAT_CLIENT_INSTALL_PATH = where the Steam client is installed
    # (usually ~/.local/share/Steam).  Use the first (default) library path.
    steam_client_dir = _steam_library_paths()[0]

    env = os.environ.copy()
    if compat_data_dir:
        env["STEAM_COMPAT_DATA_PATH"] = str(compat_data_dir)
    env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(steam_client_dir)
    if prefix:
        env["WINEPREFIX"] = str(prefix)

    cmd = [str(proton_script), "waitforexitandrun", str(exe)]
    try:
        subprocess.Popen(cmd, cwd=cwd, env=env)
        return True, (
            f"Launched {se_exe} via {proton_script.parent.name}"
            + (f" (prefix: {compat_data_dir})" if compat_data_dir else "")
        )
    except FileNotFoundError:
        return False, f"Proton script not found: {proton_script}"
    except Exception as e:
        return False, str(e)


def get_tool_buttons(profile: dict, mods_dir: Path, game_root: Path, cfg: dict) -> list[dict]:
    """
    Return a list of {id, name, available} dicts for tools that should show buttons.
    """
    result = []
    for tool_cfg in profile.get("external_tools", []):
        name = tool_cfg.get("name", tool_cfg.get("id", "?"))
        sys_exe = tool_cfg.get("system_executable", "")
        if sys_exe:
            available = True
        else:
            exe = find_tool_exe(tool_cfg, mods_dir, game_root)
            available = exe is not None
        result.append({"id": tool_cfg["id"], "name": name, "available": available,
                       "tool_cfg": tool_cfg})
    return result
