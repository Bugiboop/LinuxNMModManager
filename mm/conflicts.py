"""
Conflict detection and priority-rule resolution between mods.

Rules are stored in state.json as:
  "conflict_rules": [{"loser": "ModA", "winner": "ModB"}, ...]

A "winner" loads after (and overwrites) the "loser".
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import os


class CycleError(Exception):
    def __init__(self, cycles: list[list[str]]):
        self.cycles = cycles
        chains = " | ".join(" → ".join(c) for c in cycles)
        super().__init__(f"Circular conflict rules: {chains}")


# ── Rule CRUD ────────────────────────────────────────────────────────────────

def get_rule(mod_a: str, mod_b: str, rules: list[dict]) -> str | None:
    """
    Return which mod wins a conflict between mod_a and mod_b.
    Returns "a" (mod_a wins), "b" (mod_b wins), or None (no rule set).
    """
    for rule in rules:
        l, w = rule.get("loser", ""), rule.get("winner", "")
        if l == mod_a and w == mod_b:
            return "b"
        if l == mod_b and w == mod_a:
            return "a"
    return None


def set_rule(loser: str, winner: str, state: dict) -> None:
    """Add or replace a conflict rule. Removes any existing rule between these two mods."""
    rules = state.setdefault("conflict_rules", [])
    state["conflict_rules"] = [
        r for r in rules
        if not ({r.get("loser"), r.get("winner")} == {loser, winner})
    ]
    state["conflict_rules"].append({"loser": loser, "winner": winner})


def remove_rule(mod_a: str, mod_b: str, state: dict) -> None:
    """Remove any conflict rule between two mods (regardless of direction)."""
    rules = state.get("conflict_rules", [])
    state["conflict_rules"] = [
        r for r in rules
        if not ({r.get("loser"), r.get("winner")} == {mod_a, mod_b})
    ]


# ── Conflict queries ─────────────────────────────────────────────────────────

def get_conflicts_for_mod(mod_name: str, state: dict) -> list[tuple[str, int]]:
    """
    Return [(other_mod, file_count), ...] for every enabled mod that shares
    files with mod_name. Counts are the maximum seen from either side.
    """
    result: dict[str, int] = defaultdict(int)
    ms = state.get("mods", {}).get(mod_name, {})
    for other, count in ms.get("conflicts", {}).items():
        result[other] = max(result[other], count)
    for other_name, other_ms in state.get("mods", {}).items():
        if other_name == mod_name:
            continue
        c = other_ms.get("conflicts", {}).get(mod_name, 0)
        if c:
            result[other_name] = max(result[other_name], c)
    return [(k, v) for k, v in result.items() if v > 0]


# ── Cycle detection ──────────────────────────────────────────────────────────

def detect_cycles(rules: list[dict]) -> list[list[str]]:
    """Return a list of cycles found in the winner-graph (loser → winner edges)."""
    graph: dict[str, list[str]] = defaultdict(list)
    nodes: set[str] = set()
    for rule in rules:
        loser  = rule.get("loser",  "")
        winner = rule.get("winner", "")
        if loser and winner:
            graph[loser].append(winner)
            nodes.update([loser, winner])

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in nodes}
    cycles: list[list[str]] = []
    path: list[str] = []

    def _dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for nb in graph.get(node, []):
            if color.get(nb, WHITE) == GRAY:
                start = path.index(nb)
                cycle = path[start:] + [nb]
                if cycle not in cycles:
                    cycles.append(cycle)
            elif color.get(nb, WHITE) == WHITE:
                _dfs(nb)
        path.pop()
        color[node] = BLACK

    for node in list(nodes):
        if color.get(node, WHITE) == WHITE:
            _dfs(node)
    return cycles


# ── Live re-linking ──────────────────────────────────────────────────────────

def apply_rule(loser: str, winner: str, state: dict, _cfg: dict) -> int:
    """
    Update symlinks so that *winner* owns every file it shares with *loser*.

    Uses conflict_sources recorded at enable time.  Both mods must be enabled.
    Returns the number of symlinks re-created.  Caller must save state.
    """
    loser_ms  = state.get("mods", {}).get(loser,  {})
    winner_ms = state.get("mods", {}).get(winner, {})
    if not loser_ms.get("enabled") or not winner_ms.get("enabled"):
        return 0

    loser_sources:  dict[str, str] = loser_ms.get("conflict_sources",  {})
    winner_sources: dict[str, str] = winner_ms.get("conflict_sources", {})
    shared = set(loser_sources) & set(winner_sources)
    if not shared:
        return 0

    winner_sym_set = {e["link"] for e in winner_ms.get("symlinks", [])}
    winner_syms    = list(winner_ms.get("symlinks", []))
    new_loser_syms = []
    relinked = 0

    for entry in loser_ms.get("symlinks", []):
        t = entry["link"]
        if t not in shared:
            new_loser_syms.append(entry)
            continue
        winner_src = Path(winner_sources[t])
        if not winner_src.exists():
            new_loser_syms.append(entry)
            continue
        target = Path(t)
        if target.is_symlink():
            target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(winner_src, target)
        relinked += 1
        if t not in winner_sym_set:
            winner_syms.append({"link": t, "target": str(winner_src)})
            winner_sym_set.add(t)

    loser_ms["symlinks"]  = new_loser_syms
    winner_ms["symlinks"] = winner_syms
    return relinked


def revert_rule(loser: str, winner: str, state: dict, _cfg: dict) -> int:
    """
    Re-link files back to *loser* that were previously taken by *winner*.
    Useful when removing a rule that had been applied.
    Returns the number of symlinks re-created.  Caller must save state.
    """
    # Symmetric to apply_rule but in the other direction
    return apply_rule(winner, loser, state, _cfg)
