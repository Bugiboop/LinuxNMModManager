"""
FOMOD ModuleConfig.xml parser and file resolver.

FOMOD is the standard XML-based installer wizard format used by Nexus Mods
for Bethesda-engine games (Fallout 4, Skyrim, etc.).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FomodFile:
    source: str       # relative to mod root (e.g. "01 Curvy" or "1 core\\Textures")
    destination: str  # relative install dest within Data/ (e.g. "Textures" or "")
    priority: int = 0
    is_folder: bool = True


@dataclass
class FomodPlugin:
    name: str
    description: str
    image_path: str          # relative path inside mod folder (may use backslashes)
    files: list[FomodFile]
    flags: dict[str, str]    # flag_name → value set when this plugin is chosen
    type_descriptor: str = "Optional"  # Optional/Required/Recommended/NotUsable/CouldBeUsable


@dataclass
class FomodGroup:
    name: str
    type: str                # SelectExactlyOne/SelectAtMostOne/SelectAny/SelectAll/SelectAtLeastOne
    plugins: list[FomodPlugin]


@dataclass
class FomodStep:
    name: str
    groups: list[FomodGroup]
    visible_condition: Optional[dict] = None  # {deps: [{flag, value}], operator: str}


@dataclass
class FomodConditionalPattern:
    dependencies: list[dict]  # [{"flag": str, "value": str}, ...]
    operator: str             # "And" / "Or"
    files: list[FomodFile]


@dataclass
class FomodConfig:
    module_name: str
    required_files: list[FomodFile]
    steps: list[FomodStep]
    conditional_patterns: list[FomodConditionalPattern]
    image_path: str = ""


def find_fomod_xml(mod_dir: Path) -> Optional[Path]:
    """Find fomod/ModuleConfig.xml case-insensitively anywhere within the mod folder."""
    for candidate in mod_dir.rglob("*"):
        if (candidate.is_file()
                and candidate.name.lower() == "moduleconfig.xml"
                and candidate.parent.name.lower() == "fomod"):
            return candidate
    return None


def parse_fomod(xml_path: Path) -> FomodConfig:
    """Parse a FOMOD ModuleConfig.xml and return a structured FomodConfig."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    def _parse_file_el(el) -> FomodFile:
        return FomodFile(
            source=el.get("source", ""),
            destination=el.get("destination", ""),
            priority=int(el.get("priority", 0)),
            is_folder=(el.tag.lower() == "folder"),
        )

    def _parse_files_section(parent_el) -> list[FomodFile]:
        if parent_el is None:
            return []
        return [_parse_file_el(el) for el in parent_el
                if el.tag.lower() in ("file", "folder")]

    def _parse_deps(deps_el) -> tuple[list[dict], str]:
        if deps_el is None:
            return [], "And"
        op = deps_el.get("operator", "And")
        deps = [{"flag": fd.get("flag", ""), "value": fd.get("value", "")}
                for fd in deps_el.findall("flagDependency")]
        return deps, op

    def _get_type_descriptor(plugin_el) -> str:
        # <typeDescriptor><type name="Required"/></typeDescriptor>
        td = plugin_el.find("typeDescriptor")
        if td is not None:
            t = td.find("type")
            if t is not None:
                return t.get("name", "Optional")
            # <typeDescriptor><dependencyType>...</dependencyType></typeDescriptor>
            dt = td.find("dependencyType")
            if dt is not None:
                default_el = dt.find("defaultType")
                if default_el is not None:
                    return default_el.get("name", "Optional")
        return "Optional"

    def _parse_plugin(plugin_el) -> FomodPlugin:
        name = plugin_el.get("name", "")
        desc_el = plugin_el.find("description")
        desc = ""
        if desc_el is not None and desc_el.text:
            desc = desc_el.text.strip()
        img_el = plugin_el.find("image")
        image_path = img_el.get("path", "") if img_el is not None else ""
        files = _parse_files_section(plugin_el.find("files"))
        flags = {}
        for fl in plugin_el.findall("conditionFlags/flag"):
            flags[fl.get("name", "")] = (fl.text or "").strip()
        return FomodPlugin(
            name=name,
            description=desc,
            image_path=image_path,
            files=files,
            flags=flags,
            type_descriptor=_get_type_descriptor(plugin_el),
        )

    # Module name
    module_name = ""
    name_el = root.find("moduleName")
    if name_el is not None and name_el.text:
        module_name = name_el.text.strip()

    # Module image
    image_path = ""
    img_el = root.find("moduleImage")
    if img_el is not None:
        image_path = img_el.get("path", "")

    # Required install files (always installed)
    req_files = _parse_files_section(root.find("requiredInstallFiles"))

    # Install steps
    steps: list[FomodStep] = []
    steps_el = root.find("installSteps")
    if steps_el is not None:
        for step_el in steps_el.findall("installStep"):
            step_name = step_el.get("name", "")
            groups: list[FomodGroup] = []
            for grp_el in step_el.findall("optionalFileGroups/group"):
                grp_name = grp_el.get("name", "")
                grp_type = grp_el.get("type", "SelectAny")
                plugins = [_parse_plugin(p) for p in grp_el.findall("plugins/plugin")]
                groups.append(FomodGroup(name=grp_name, type=grp_type, plugins=plugins))
            # Step visibility condition.
            # Two valid structures exist in the wild:
            #   Standard: <visible><dependencies operator="And"><flagDependency .../></dependencies></visible>
            #   Compact:  <visible operator="And"><flagDependency .../></visible>  (no <dependencies> wrapper)
            vis_cond = None
            vis_el = step_el.find("visible")
            if vis_el is not None:
                deps_el = vis_el.find("dependencies")
                if deps_el is None:
                    deps_el = vis_el   # compact form: <visible> itself carries operator + flagDependency
                deps, op = _parse_deps(deps_el)
                vis_cond = {"deps": deps, "operator": op}
            steps.append(FomodStep(name=step_name, groups=groups,
                                   visible_condition=vis_cond))

    # Conditional file installs
    cond_patterns: list[FomodConditionalPattern] = []
    cond_el = root.find("conditionalFileInstalls/patterns")
    if cond_el is not None:
        for pat_el in cond_el.findall("pattern"):
            deps, op = _parse_deps(pat_el.find("dependencies"))
            files = _parse_files_section(pat_el.find("files"))
            cond_patterns.append(FomodConditionalPattern(
                dependencies=deps, operator=op, files=files))

    return FomodConfig(
        module_name=module_name,
        required_files=req_files,
        steps=steps,
        conditional_patterns=cond_patterns,
        image_path=image_path,
    )


def _eval_condition(deps: list[dict], operator: str, flags: dict[str, str]) -> bool:
    if not deps:
        return True
    results = [flags.get(d["flag"], "") == d["value"] for d in deps]
    return all(results) if operator == "And" else any(results)


def _step_is_visible(step: FomodStep, flags: dict[str, str]) -> bool:
    if step.visible_condition is None:
        return True
    vc = step.visible_condition
    return _eval_condition(vc.get("deps", []), vc.get("operator", "And"), flags)


def resolve_fomod_files(
    config: FomodConfig,
    selections: dict,
) -> list[FomodFile]:
    """
    Given a FomodConfig and user selections, return the flat list of FomodFile
    objects to install.

    selections: {(step_idx, group_idx): [plugin_idx, ...]}
      Keys may be tuples or "step_idx,group_idx" strings (both accepted).
    """
    # Normalise string keys like "0,1" to tuple keys (0, 1)
    norm: dict[tuple, list] = {}
    for k, v in selections.items():
        if isinstance(k, str):
            parts = k.split(",")
            norm[(int(parts[0]), int(parts[1]))] = v
        else:
            norm[tuple(k)] = v

    active_flags: dict[str, str] = {}
    files: list[FomodFile] = list(config.required_files)

    for step_idx, step in enumerate(config.steps):
        if not _step_is_visible(step, active_flags):
            continue
        for grp_idx, group in enumerate(step.groups):
            chosen_idxs = norm.get((step_idx, grp_idx), [])
            for plugin_idx in chosen_idxs:
                if 0 <= plugin_idx < len(group.plugins):
                    plugin = group.plugins[plugin_idx]
                    files.extend(plugin.files)
                    active_flags.update(plugin.flags)

    # Conditional installs based on accumulated flags
    for pattern in config.conditional_patterns:
        if _eval_condition(pattern.dependencies, pattern.operator, active_flags):
            files.extend(pattern.files)

    return files
