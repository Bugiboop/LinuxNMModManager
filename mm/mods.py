from pathlib import Path
import os
import shutil

from .config import save_state, IGNORED_FILENAMES as _IGNORED_FILENAMES
from .archive import detect_variant_groups, prompt_variant_choice
from .resolver import resolve_target, iter_mod_files, build_target_map, _normalize_path_case
from .ue4ss import _find_ue4ss_mod_names, _register_ue4ss_mods, _unregister_ue4ss_mods


def _apply_mirrors(target: Path, src_file: Path, profile: dict, game_root: Path,
                   target_map: dict, symlinks: list, backups: list):
    """Create extra symlinks defined by profile symlink_mirrors rules.

    When a file lands at Data/<from>/<rest>, also place it at Data/<to>/<rest>.
    Both symlinks point at the same source so they're tracked and cleaned up
    together on disable.
    """
    mirrors = profile.get("symlink_mirrors", [])
    if not mirrors:
        return
    install_base = profile.get("default_install_path", "") or "Data"
    data_dir = game_root / install_base
    try:
        rel_posix = target.relative_to(data_dir).as_posix()
    except ValueError:
        return
    rel_lower = rel_posix.lower()
    for rule in mirrors:
        from_lower = rule["from"].replace("\\", "/").rstrip("/").lower()
        if not rel_lower.startswith(from_lower + "/"):
            continue
        suffix = rel_posix[len(from_lower) + 1:]
        mirror_target = _normalize_path_case(
            data_dir / rule["to"].replace("\\", "/") / suffix)
        mirror_target.parent.mkdir(parents=True, exist_ok=True)
        if mirror_target.exists() and not mirror_target.is_symlink():
            bak = Path(str(mirror_target) + ".bak")
            mirror_target.rename(bak)
            backups.append({"original": str(mirror_target), "backup": str(bak)})
        if mirror_target.is_symlink():
            mirror_target.unlink()
        os.symlink(src_file, mirror_target)
        target_map[str(mirror_target)] = target_map.get(str(target), "")
        symlinks.append({"link": str(mirror_target), "target": str(src_file)})


def _prompt_conflict(mod_name: str, other_mod: str, claimed: list) -> str:
    """
    Ask the user how to handle a conflict between two mods (CLI path only).
    Returns: '1' = new mod wins, '2' = old mod keeps conflicting files.
    """
    sample    = [Path(t).name for t in claimed[:3]]
    extra     = len(claimed) - 3
    file_list = ", ".join(sample) + (f"  (+{extra} more)" if extra > 0 else "")
    print()
    print(f"  [conflict] '{other_mod}' owns {len(claimed)} file(s) also claimed by '{mod_name}':")
    print(f"    {file_list}")
    print(f"    (1) '{mod_name}' takes the conflicting file(s)")
    print(f"    (2) '{other_mod}' keeps the conflicting file(s)")
    while True:
        try:
            choice = input("  Resolve conflict [1/2]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "2"
        if choice in ("1", "2", ""):
            return choice or "2"
        print("  Please enter 1 or 2.")


def _detect_mod_root(mod_dir: Path, profile: dict) -> Path:
    """Return the effective mod root, stripping a single wrapper folder if present.

    Many archives ship as  ModName/  containing the actual files rather than
    placing files at the archive root.  If mod_dir has exactly one subdirectory
    and no top-level content files (after filtering metadata), and that subdir is
    not itself an anchor name (Data, f4se_…), return the subdir as the root so
    the resolver sees the correct relative paths.
    """
    ignored_files = {n.lower() for n in _IGNORED_FILENAMES}
    ignored_dirs  = {d.lower() for d in profile.get("ignored_directories", [])}
    anchor_names  = {rule["anchor"].lower() for rule in profile.get("install_rules", [])}
    anchor_names |= {s.lower() for s in profile.get("data_subdir_anchors", [])}

    try:
        entries = list(mod_dir.iterdir())
    except OSError:
        return mod_dir

    content_files = [e for e in entries if e.is_file() and e.name.lower() not in ignored_files]
    subdirs       = [e for e in entries if e.is_dir()  and e.name.lower() not in ignored_dirs]

    if len(subdirs) == 1 and not content_files and subdirs[0].name.lower() not in anchor_names:
        return subdirs[0]
    return mod_dir


def _expand_fomod_files(mod_dir: Path, fomod_files: list,
                        game_root: Path, profile: dict) -> dict:
    """
    Expand a FOMOD FomodFile list into a source→target mapping.

    Respects the FOMOD 'destination' field: the relative subdirectory structure
    within each source folder is preserved at the destination, so files like
      source='00 Required', destination=''
        00 Required/Tools/BodySlide/SliderSets/CBBE.osp
        → Data/Tools/BodySlide/SliderSets/CBBE.osp   (correct)
    rather than being flattened to Data/CBBE.osp by the generic resolver.
    """
    install_base = profile.get("default_install_path", "") or "Data"
    install_base_lower = install_base.lower()

    # target → (source, priority) — highest priority wins when targets collide
    by_target: dict[Path, tuple[Path, int]] = {}

    def _strip_data_prefix(dest: str) -> str:
        if dest.lower().startswith(install_base_lower + "/"):
            return dest[len(install_base) + 1:]
        return dest

    def _add(src: Path, target: Path, priority: int) -> None:
        target = _normalize_path_case(target)
        existing = by_target.get(target)
        if existing is None or priority >= existing[1]:
            by_target[target] = (src, priority)

    for ff in fomod_files:
        if ff.is_folder and not ff.source.strip():
            continue
        src_root = _normalize_path_case(mod_dir / ff.source.replace("\\", "/"))
        dest = _strip_data_prefix(
            (ff.destination or "").replace("\\", "/").strip("/")
        )

        if ff.is_folder:
            if not src_root.is_dir():
                continue
            for root, _, files in os.walk(src_root):
                for fname in files:
                    src_file = Path(root) / fname
                    rel = src_file.relative_to(src_root)
                    target = (game_root / install_base / dest / rel
                              if dest else game_root / install_base / rel)
                    _add(src_file, target, ff.priority)
        else:
            if not src_root.exists():
                continue
            target = (game_root / install_base / dest
                      if dest else game_root / install_base / src_root.name)
            _add(src_root, target, ff.priority)

    return {src: target for target, (src, _) in by_target.items()}


def enable_mod(mod_name: str, cfg: dict, state: dict, target_map: dict = None,
               game_tree: set = None, fomod_callback=None, skip_variants: bool = False,
               _no_save: bool = False):
    from .config import scan_game_tree
    mod_dir = cfg["mods_dir"] / mod_name
    print(f"[enabling] {mod_name}")
    if not mod_dir.is_dir():
        print(f"[error] Mod directory not found: {mod_dir}")
        return

    mod_state = state["mods"].setdefault(mod_name, {"enabled": False, "symlinks": [], "backups": []})

    # Use `deployed` to track actual disk state separately from user intent (`enabled`).
    # For state files written before this field existed, fall back to `enabled`.
    if mod_state.get("deployed", mod_state.get("enabled", False)):
        print(f"[skip] {mod_name} is already deployed.")
        return

    profile   = cfg.get("profile", {})
    game_root: Path = cfg["game_root"]

    # --- FOMOD installer detection ---
    # fomod_mapping: source_path → target_path (None = install all via resolver)
    fomod_mapping: dict | None = None
    from .fomod import find_fomod_xml
    fomod_xml = find_fomod_xml(mod_dir)
    if fomod_xml and fomod_callback:
        from .fomod import parse_fomod, resolve_fomod_files
        fomod_config = parse_fomod(fomod_xml)
        selections = fomod_callback(fomod_config, mod_state.get("fomod_selections"))
        if selections is None:
            print(f"[skip] FOMOD wizard cancelled for {mod_name}.")
            return
        fomod_files = resolve_fomod_files(fomod_config, selections)
        # FOMOD source paths are relative to the folder containing fomod/,
        # not necessarily the mod root (some mods have a wrapper folder).
        fomod_base = fomod_xml.parent.parent
        fomod_mapping = _expand_fomod_files(fomod_base, fomod_files, game_root, profile)
        mod_state["fomod_selections"] = {f"{k[0]},{k[1]}": v for k, v in selections.items()}
        print(f"  [fomod] {len(fomod_mapping)} file(s) selected via installer wizard")

    # --- Variant detection ---
    # Bethesda-engine games skip this entirely: FOMOD handles selective installation
    # for mods that need it; everything else installs wholesale. Variant detection
    # only makes sense for UE4/UE5 games where quality-variant packs (1K/4K etc.) exist.
    # skip_variants=True is set by the deploy path (variant selection already done at stage time).
    if not skip_variants and fomod_xml is None and not profile.get("plugin_extensions"):
        anchors = {rule["anchor"].lower() for rule in profile.get("install_rules", [])}
        groups = detect_variant_groups(mod_dir, anchor_names=anchors)
        for parent_dir, variants in groups:
            chosen = prompt_variant_choice(parent_dir, variants)
            if chosen is not None:
                removed = [v for v in variants if v != chosen]
                for v in removed:
                    shutil.rmtree(v)
                print(f"  [variants] Kept '{chosen.name}', removed {len(removed)} other variant(s).")

    if target_map is None:
        target_map = build_target_map(state)
    if game_tree is None:
        game_tree = scan_game_tree(cfg["game_root"])

    # Strip a single wrapper folder (e.g. "ModName v1.2/Data/…") so the resolver
    # sees paths relative to the real content root, not the archive wrapper.
    # Skip this when a FOMOD mapping is active — FOMOD already resolves its own paths.
    effective_root = mod_dir if fomod_mapping is not None else _detect_mod_root(mod_dir, profile)
    if effective_root != mod_dir:
        print(f"  [wrapper] Stripping wrapper folder: {effective_root.name}")

    def _resolve(src_file: Path):
        """Return target path: FOMOD mapping wins over generic resolver."""
        if fomod_mapping is not None:
            return fomod_mapping.get(src_file)
        return resolve_target(effective_root, src_file, game_root, profile, game_tree)

    # --- Pre-scan: identify conflicts and record sources for rule application ---
    from .conflicts import get_rule as _get_rule
    conflicts_with: dict   = {}   # {other_mod: [target_str, ...]}
    conflict_sources: dict = {}   # {target_str: str(src_file)} for this mod

    for src_file in iter_mod_files(effective_root, profile):
        if fomod_mapping is not None and src_file not in fomod_mapping:
            continue
        target = _resolve(src_file)
        if target is None:
            continue
        owner = target_map.get(str(target))
        if owner and owner != mod_name:
            conflicts_with.setdefault(owner, []).append(str(target))
            conflict_sources[str(target)] = str(src_file)

    skip_targets: set = set()   # targets to skip during placement
    rules = state.get("conflict_rules", [])

    for other_mod, claimed in conflicts_with.items():
        # Record this mod's sources for conflicted targets
        other_ms = state["mods"].get(other_mod, {})

        # Also capture the owner's current source paths before we might remove them
        owner_sym_map = {e["link"]: e["target"] for e in other_ms.get("symlinks", [])}
        if "conflict_sources" not in other_ms:
            other_ms["conflict_sources"] = {}
        for t in claimed:
            if t not in other_ms["conflict_sources"] and t in owner_sym_map:
                other_ms["conflict_sources"][t] = owner_sym_map[t]

        rule = _get_rule(mod_name, other_mod, rules)
        # rule="a" → this mod wins, rule="b" → other_mod wins, None → this mod wins (default)
        if rule == "b":
            skip_targets.update(claimed)
            print(f"  [conflict] '{other_mod}' keeps {len(claimed)} file(s) (priority rule)")
        else:
            # This mod wins: remove conflicting symlinks from the other mod
            claimed_set = set(claimed)
            surviving = []
            for entry in other_ms.get("symlinks", []):
                if entry["link"] in claimed_set:
                    link = Path(entry["link"])
                    if link.is_symlink():
                        link.unlink()
                    print(f"  [conflict] Replaced '{Path(entry['link']).name}' — '{other_mod}' → '{mod_name}'")
                else:
                    surviving.append(entry)
            other_ms["symlinks"] = surviving
            target_map.clear()
            target_map.update(build_target_map(state))

    symlinks = []
    backups = []
    skipped = 0
    disabled_stems = set(mod_state.get("disabled_files", []))

    for src_file in iter_mod_files(effective_root, profile):
        if fomod_mapping is not None and src_file not in fomod_mapping:
            continue
        target = _resolve(src_file)
        if target is None:
            continue

        if src_file.stem in disabled_stems:
            mod_state.setdefault("disabled_symlinks", [])
            entry = {"link": str(target), "target": str(src_file)}
            existing = [e["target"] for e in mod_state["disabled_symlinks"]]
            if str(src_file) not in existing:
                mod_state["disabled_symlinks"].append(entry)
            skipped += 1
            continue

        if str(target) in skip_targets:
            print(f"  [conflict] {src_file.name}  ←  skipping (conflict)")
            skipped += 1
            continue

        target.parent.mkdir(parents=True, exist_ok=True)

        # Backup real game files that would be overwritten
        if target.exists() and not target.is_symlink():
            bak = Path(str(target) + ".bak")
            print(f"  [backup]  {target.relative_to(game_root)}  →  {bak.name}")
            target.rename(bak)
            backups.append({"original": str(target), "backup": str(bak)})

        if target.is_symlink():
            target.unlink()

        os.symlink(src_file, target)
        target_map[str(target)] = mod_name
        symlinks.append({"link": str(target), "target": str(src_file)})
        _apply_mirrors(target, src_file, profile, game_root,
                       target_map, symlinks, backups)

    # Store conflict sources and counts for priority-rule application later
    if conflict_sources:
        existing_cs = mod_state.get("conflict_sources", {})
        existing_cs.update(conflict_sources)
        mod_state["conflict_sources"] = existing_cs
    if conflicts_with:
        existing_c = mod_state.get("conflicts", {})
        for other, claimed in conflicts_with.items():
            existing_c[other] = len(claimed)
        mod_state["conflicts"] = existing_c
        # Mirror counts into the other mods' records
        for other, claimed in conflicts_with.items():
            other_ms2 = state["mods"].get(other, {})
            if other_ms2:
                ec = other_ms2.get("conflicts", {})
                ec[mod_name] = len(claimed)
                other_ms2["conflicts"] = ec

    # Register any UE4SS mod subfolders in the game's mods.txt
    ue4ss_names = _find_ue4ss_mod_names(mod_dir)
    added_ue4ss = _register_ue4ss_mods(ue4ss_names, game_root, profile)
    if added_ue4ss:
        mod_state["ue4ss_mods"] = added_ue4ss
        print(f"  [ue4ss] Registered in mods.txt: {', '.join(added_ue4ss)}")

    # Register Bethesda plugins (.esp/.esm/.esl) in Plugins.txt
    added_plugins: list = []
    if profile.get("plugin_extensions"):
        from .plugins import find_plugins_in_mod, register_plugins
        if fomod_mapping is not None:
            # FOMOD mods: derive plugin names from installed target paths
            plugin_exts  = {e.lower() for e in profile.get("plugin_extensions", [])}
            install_base = profile.get("default_install_path", "") or "Data"
            data_dir     = game_root / install_base
            plugin_names = [
                target.name
                for target in fomod_mapping.values()
                if target.suffix.lower() in plugin_exts
                and target.parent == data_dir
            ]
        else:
            plugin_names = find_plugins_in_mod(mod_dir, profile)
        if plugin_names:
            added_plugins = register_plugins(plugin_names, profile, cfg)
            if added_plugins:
                mod_state["plugins"] = added_plugins
                print(f"  [plugins] Registered: {', '.join(added_plugins)}")

    mod_state["enabled"]  = True
    mod_state["deployed"] = True
    mod_state["symlinks"] = symlinks
    mod_state["backups"]  = backups
    if not _no_save:
        save_state(state)

    parts = [f"{len(symlinks)} linked"]
    if skipped:
        parts.append(f"{skipped} file(s) skipped (conflict)")
    if added_ue4ss:
        parts.append(f"{len(added_ue4ss)} ue4ss registered")
    if added_plugins:
        parts.append(f"{len(added_plugins)} plugin(s) registered")
    if backups:
        parts.append(f"{len(backups)} backed up")
    print(f"[enabled]  {mod_name}  —  {', '.join(parts)}")

    # Return any soft recommendations declared by the mod
    import json as _json
    meta_path = mod_dir / "mm_metadata.json"
    if meta_path.exists():
        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
            return meta.get("recommended_mods", [])
        except Exception:
            pass
    return []


def disable_mod(mod_name: str, cfg: dict, state: dict, _no_save: bool = False):
    mod_state = state["mods"].get(mod_name)
    if not mod_state:
        print(f"[skip] {mod_name} has no recorded state.")
        return
    if not mod_state.get("deployed", mod_state.get("enabled", False)):
        print(f"[skip] {mod_name} is not deployed.")
        return

    unlinked = 0
    restored = 0

    for entry in mod_state.get("symlinks", []):
        link = Path(entry["link"])
        if link.is_symlink():
            link.unlink()
            unlinked += 1
        elif link.exists():
            print(f"  [warn] {link.name} exists but is not a symlink — skipping removal")

    for entry in mod_state.get("backups", []):
        bak = Path(entry["backup"])
        original = Path(entry["original"])
        if bak.exists():
            bak.rename(original)
            restored += 1
        else:
            print(f"  [warn] backup not found: {bak.name}")

    # Unregister any UE4SS mods we previously added to mods.txt
    ue4ss_names = mod_state.pop("ue4ss_mods", [])
    if ue4ss_names:
        _unregister_ue4ss_mods(ue4ss_names, cfg["game_root"], cfg.get("profile", {}))
        print(f"  [ue4ss] Removed from mods.txt: {', '.join(ue4ss_names)}")

    # Deactivate Bethesda plugins in Plugins.txt
    plugin_names = mod_state.pop("plugins", [])
    if plugin_names:
        from .plugins import unregister_plugins
        unregister_plugins(plugin_names, cfg.get("profile", {}), cfg)
        print(f"  [plugins] Deactivated: {', '.join(plugin_names)}")

    mod_state["enabled"]  = False
    mod_state["deployed"] = False
    mod_state["symlinks"] = []
    mod_state["backups"]  = []
    mod_state.pop("disabled_symlinks", None)   # clear on full disable

    # Re-link files from mods that lost conflicts against this mod.
    # Those mods had their symlinks for the contested paths pruned when this mod
    # won; now that this mod is gone, restore what each loser originally owned.
    relinked = 0
    own_conflicts = mod_state.get("conflicts", {})   # {other_mod: count}
    for other_mod in list(own_conflicts.keys()):
        other_ms = state["mods"].get(other_mod, {})
        if not other_ms.get("enabled"):
            continue
        other_sources = other_ms.get("conflict_sources", {})
        if not other_sources:
            continue
        newly_active = []
        for target_str, src_str in other_sources.items():
            src_file = Path(src_str)
            target   = Path(target_str)
            if not src_file.exists():
                continue
            if target.exists() and not target.is_symlink():
                continue   # a real game file is there; leave it alone
            if target.is_symlink():
                continue   # already owned by someone else
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(src_file, target)
            newly_active.append({"link": target_str, "target": src_str})
            relinked += 1
            print(f"  [conflict-restore] {target.name}  ←  {other_mod}")
        if newly_active:
            other_ms.setdefault("symlinks", []).extend(newly_active)
            # Remove the restored entries from conflict_sources (they're active now)
            for entry in newly_active:
                other_sources.pop(entry["link"], None)
        # Remove this mod from the other mod's conflict count since we're gone
        other_ms.get("conflicts", {}).pop(mod_name, None)

    # Clean up this mod's own conflict records
    mod_state.pop("conflicts", None)
    mod_state.pop("conflict_sources", None)

    if not _no_save:
        save_state(state)

    parts = [f"{unlinked} unlinked"]
    if ue4ss_names:
        parts.append(f"{len(ue4ss_names)} ue4ss unregistered")
    if plugin_names:
        parts.append(f"{len(plugin_names)} plugin(s) deactivated")
    if restored:
        parts.append(f"{restored} restored")
    if relinked:
        parts.append(f"{relinked} conflict-restored")
    print(f"[disabled] {mod_name}  —  {', '.join(parts)}")


def toggle_mod_file_stem(mod_name: str, stem: str, enable: bool,
                         cfg: dict, state: dict) -> None:
    """Enable or disable all files with *stem* inside an already-enabled mod.

    Moves entries between ``symlinks`` (active) and ``disabled_symlinks``
    (parked) and creates / removes the physical symlinks accordingly.
    ``disabled_files`` (a list of stems) is kept in sync so that a subsequent
    ``enable_mod`` call respects the same choices.
    """
    ms = state["mods"].get(mod_name)
    if ms is None:
        return

    # Keep disabled_files (stems) in sync
    disabled_stems: set = set(ms.get("disabled_files", []))
    if enable:
        disabled_stems.discard(stem)
    else:
        disabled_stems.add(stem)
    ms["disabled_files"] = sorted(disabled_stems)

    if not ms.get("enabled", False):
        save_state(state)
        return

    active   = ms.get("symlinks", [])
    parked   = ms.get("disabled_symlinks", [])

    if enable:
        # Move matching entries from parked → active and recreate symlinks
        still_parked = []
        for entry in parked:
            if Path(entry["target"]).stem == stem:
                link   = Path(entry["link"])
                target = Path(entry["target"])
                if target.exists():
                    link.parent.mkdir(parents=True, exist_ok=True)
                    if link.is_symlink():
                        link.unlink()
                    os.symlink(target, link)
                    active.append(entry)
                    print(f"  [file-enabled]  {link.name}")
            else:
                still_parked.append(entry)
        ms["disabled_symlinks"] = still_parked
    else:
        # Move matching entries from active → parked and remove symlinks
        still_active = []
        for entry in active:
            if Path(entry["target"]).stem == stem:
                link = Path(entry["link"])
                if link.is_symlink():
                    link.unlink()
                parked.append(entry)
                print(f"  [file-disabled] {link.name}")
            else:
                still_active.append(entry)
        ms["symlinks"]          = still_active
        ms["disabled_symlinks"] = parked
    # Caller is responsible for saving state (GUI uses _gc._save_state to reach
    # the correct game-specific state file; CLI callers call mm.config.save_state)
