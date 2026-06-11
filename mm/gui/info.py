import configparser
from pathlib import Path


def _read_mod_info(mod_dir: Path) -> dict:
    """
    Collect display metadata for a mod folder.
    Returns a dict with keys: name, author, version, description,
    bundle, image_path, pak_stems, readme_text.
    """
    info: dict = {}

    # ── Find modinfo.ini (may be in a subfolder) ───────────────────────
    ini_candidates = list(mod_dir.rglob("modinfo.ini")) + list(mod_dir.rglob("Modinfo.ini"))
    if ini_candidates:
        ini_path = ini_candidates[0]
        ini_dir  = ini_path.parent
        # modinfo.ini is often section-less; wrap it before parsing
        raw_text = ini_path.read_text(errors="replace")
        if not any(line.strip().startswith("[") for line in raw_text.splitlines()):
            raw_text = "[mod]\n" + raw_text
        cp2 = configparser.RawConfigParser()
        try:
            cp2.read_string(raw_text)
        except Exception:
            cp2 = configparser.RawConfigParser()
        section = cp2[cp2.sections()[0]] if cp2.sections() else {}

        def _get(*keys):
            for k in keys:
                v = section.get(k, "").strip()
                if v:
                    return v
            return ""

        info["name"]        = _get("name")
        info["author"]      = _get("author")
        info["version"]     = _get("version")
        info["description"] = _get("description")
        info["bundle"]      = _get("nameasbundle")

        # Image referenced by modinfo
        screenshot = _get("screenshot", "preview", "image")
        if screenshot:
            candidate = ini_dir / screenshot
            if candidate.exists():
                info["image_path"] = candidate

    # ── Find preview image if not yet set ─────────────────────────────
    if "image_path" not in info:
        priority_names = ["1.png", "preview.png", "thumbnail.png",
                          "cover.png", "screenshot.png"]
        for name in priority_names:
            p = mod_dir / name
            if p.exists():
                info["image_path"] = p
                break
        if "image_path" not in info:
            # Any image at root level
            for ext in ("*.png", "*.jpg", "*.jpeg"):
                hits = sorted(mod_dir.glob(ext))
                if hits:
                    info["image_path"] = hits[0]
                    break
        if "image_path" not in info:
            # Recursive search — skip fomod/ directories (those are installer preview
            # images, not mod preview images, and can belong to unrelated tools)
            def _is_fomod_path(p: Path) -> bool:
                try:
                    return any(part.lower() == "fomod"
                               for part in p.relative_to(mod_dir).parts[:-1])
                except ValueError:
                    return False

            for ext in ("*.png", "*.jpg", "*.jpeg"):
                hits = [p for p in sorted(mod_dir.rglob(ext))
                        if not _is_fomod_path(p)]
                if hits:
                    info["image_path"] = hits[0]
                    break

    # ── README ─────────────────────────────────────────────────────────
    readme_names = ["README.md", "README.txt", "readme.txt",
                    "readme.md", "Readme.txt", "README - Installation.txt"]
    for rn in readme_names:
        p = mod_dir / rn
        if not p.exists():
            hits = list(mod_dir.rglob(rn))
            p = hits[0] if hits else None
        if p and p.exists():
            try:
                text = p.read_text(errors="replace").strip()
                if text:
                    info["readme_text"] = text[:800]
                    break
            except Exception:
                pass

    # ── Pak stems ──────────────────────────────────────────────────────
    paks  = sorted(mod_dir.rglob("*.pak"))
    stems = sorted({p.stem for p in paks})
    info["pak_stems"] = stems

    # ── UE4SS mod folders ──────────────────────────────────────────────
    ue4ss_mods = []
    for p in mod_dir.rglob("*"):
        if p.is_dir() and p.name.lower() == "mods" and p.parent.name.lower() == "ue4ss":
            ue4ss_mods = sorted(d.name for d in p.iterdir() if d.is_dir())
            break
    info["ue4ss_mods"] = ue4ss_mods

    # ── Non-pak script/config files (for UE4SS mods with no paks) ──────
    if not stems and ue4ss_mods:
        script_exts = {".lua", ".txt", ".ini", ".cfg", ".json"}
        scripts = sorted({
            f.name for f in mod_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in script_exts
            and f.name.lower() not in ("mods.txt", "modinfo.ini")
        })
        info["script_files"] = scripts

    return info


def _utoc_assets(utoc_path: Path) -> list:
    """Extract internal UE5 asset paths from an IoStore .utoc file."""
    ASSET_EXTS = {".uasset", ".ubulk", ".uexp", ".umap", ".uptnl"}
    with open(utoc_path, "rb") as f:
        data = f.read()
    strings, current = [], []
    for byte in data:
        if 0x20 <= byte < 0x7F:
            current.append(chr(byte))
        else:
            if len(current) >= 4:
                strings.append("".join(current))
            current = []
    if len(current) >= 4:
        strings.append("".join(current))
    assets, current_dir = [], ""
    for s in strings:
        if s.startswith("../") or (s.startswith("/") and "/" in s[1:]):
            current_dir = s.rstrip("/")
        elif Path(s).suffix.lower() in ASSET_EXTS:
            assets.append(f"{current_dir}/{s}" if current_dir else s)
    return assets
