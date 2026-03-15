import json
import re
import urllib.request
import urllib.error
from pathlib import Path

import mm.gui.config as _gc


_nexus_id_cache: dict = {}


def _nexus_id(folder: str):
    """Extract the Nexus mod ID from a folder name like 'ModName-1234-1-0-...'"""
    if folder not in _nexus_id_cache:
        m = re.search(r"-(\d{1,7})(?:-[A-Za-z0-9]*)+-\d{9,}$", folder)
        _nexus_id_cache[folder] = m.group(1) if m else None
    return _nexus_id_cache[folder]


def _nexus_api_fetch(nid: str, api_key: str):
    url = f"{_gc._NEXUS_API_BASE}/{nid}.json"
    req = urllib.request.Request(
        url,
        headers={"apikey": api_key, "Accept": "application/json",
                 "User-Agent": _gc._UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": str(e.code)}
    except Exception as e:
        return {"_error": str(e)}


def _nexus_download_image(url: str, dest: Path) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": _gc._UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception:
        return False


def _display_name(folder: str) -> str:
    return re.sub(r"[-_]\d+(?:[-_]\d+)*$", "", folder).strip("-_ ") or folder


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()
