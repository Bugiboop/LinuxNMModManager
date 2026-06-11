import json
import re
import urllib.request
import urllib.error
from pathlib import Path

import mm.gui.config as _gc


_nexus_id_cache: dict = {}
_requirements_cache: dict = {}   # mod_id → list of requirement dicts


def _nexus_id(folder: str):
    """Extract the Nexus mod ID from a Nexus-style folder/filename.

    Two strategies, tried in order:

    1. Vortex/NXM format with 10-digit timestamp:
         {name}-{modId}-{ver...}-{timestamp10}
       Version components may end with a letter (e.g. "1-36b", "4-02a", "1-0f").
       The 1-4 digit constraint on version parts still blocks large embedded
       numbers (like the "12631" in "LooksMenu v1-7-0-2-12631-…") from being
       mistaken for a version component, pushing the regex forward to the real ID.

    2. NMM / legacy format without a timestamp:
         {name}-{modId}-{version}
       Take the first segment of 3+ consecutive digits preceded by a dash.
       Three digits is the lower bound because NMM IDs ≥ 100 are common while
       a plain 1- or 2-digit number in a display name (e.g. "X-92", "v1-3")
       should not be mistaken for an ID.
    """
    if folder not in _nexus_id_cache:
        # Strategy 1: timestamp-based (Vortex/NXM downloads)
        m = re.search(r'-(\d+)-(?:\d{1,4}[a-zA-Z]?-){1,6}\d{10}$', folder)
        if not m:
            # Strategy 2: NMM / no-timestamp downloads
            m = re.search(r'-(\d{3,})(?:-|$)', folder)
        _nexus_id_cache[folder] = m.group(1) if m else None
    return _nexus_id_cache[folder]


def _nexus_api_fetch(nid: str, api_key: str):
    url = f"{_gc._NEXUS_API_BASE}/{nid}.json"
    req = urllib.request.Request(
        url,
        headers={"apikey": api_key.strip(), "Accept": "application/json",
                 "User-Agent": _gc._UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": str(e.code)}
    except Exception as e:
        return {"_error": str(e)}


def _nexus_fetch_requirements(mod_id: str, api_key: str) -> list:
    """Return the requirements list for a Nexus mod, with caching.

    Each entry is a dict with at least 'name' (str) and 'mod_id' (int).
    Returns [] on any error or when no API key is available.
    """
    if not api_key or not mod_id or not _gc._NEXUS_API_BASE:
        return []
    if mod_id in _requirements_cache:
        return _requirements_cache[mod_id]

    url = f"{_gc._NEXUS_API_BASE}/{mod_id}/requirements.json"
    req = urllib.request.Request(
        url,
        headers={"apikey": api_key.strip(), "Accept": "application/json",
                 "User-Agent": _gc._UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            result = data if isinstance(data, list) else data.get("requirements", [])
    except Exception:
        result = []

    _requirements_cache[mod_id] = result
    return result


def _installed_nexus_ids(mods_dir: Path) -> set:
    """Return the set of Nexus mod IDs (as strings) present in mods_dir."""
    ids = set()
    try:
        for entry in mods_dir.iterdir():
            nid = _nexus_id(entry.name)
            if nid:
                ids.add(nid)
    except OSError:
        pass
    return ids


def _nexus_download_image(url: str, dest: Path) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": _gc._UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception:
        return False


def _display_name(folder: str) -> str:
    """Return a human-readable name by stripping the Nexus ID and version suffix."""
    # Best strategy: find where the mod ID starts and strip from there.
    # e.g. "AWKCR v5.0 - CBBE Patch v2-6091-v5-0" → "AWKCR v5.0 - CBBE Patch v2"
    nid = _nexus_id(folder)
    if nid:
        idx = folder.find(f"-{nid}-")
        if idx > 0:
            return folder[:idx].strip("-_ ") or folder
        # NMM format with no trailing version: "ModName-1234"
        idx = folder.rfind(f"-{nid}")
        if idx > 0:
            return folder[:idx].strip("-_ ") or folder
    # Fallback: strip trailing all-digit/dash groups
    return re.sub(r"[-_]\d+(?:[-_]\d+)*$", "", folder).strip("-_ ") or folder


def _nexus_file_version(folder: str) -> str | None:
    """Extract the version string from a Nexus-style folder name.

    Vortex: 'LooksMenu v1-7-0-2-12631-1-7-0-2-1768673860' → '1.7.0.2'
    NMM:    'AWKCR-6091-4-02a'                             → '4.02a'
            'AWKCR-6091-v5-0'                              → 'v5.0'
    """
    nid = _nexus_id(folder)
    if not nid:
        return None

    def _normalise(v: str) -> str:
        # Pure digit-dash → dotted (e.g. "1-0-2" → "1.0.2")
        if re.fullmatch(r"[\d-]+", v):
            return v.replace("-", ".")
        # Digit-dash with trailing letter(s) (e.g. "4-02a") → dots + letters
        v = re.sub(r"(\d)-(\d)", r"\1.\2", v)
        return v

    # Vortex: name-{nid}-{version}-{10digitstamp}
    m = re.search(rf"-{re.escape(nid)}-(.+)-\d{{9,}}$", folder)
    if m and m.group(1):
        return _normalise(m.group(1))

    # NMM: name-{nid}-{version}  (no timestamp)
    m = re.search(rf"-{re.escape(nid)}-(.+)$", folder)
    if m and m.group(1):
        return _normalise(m.group(1))

    return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()
