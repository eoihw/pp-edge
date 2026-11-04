"""Optional context multipliers (pace, minutes, esports map pool, series length).

READ THIS FIRST - double counting:
Sharp books already price pace, injuries, lineups and map pools. The bot anchors on the sharp market, so a
multiplier here is only valid when you know something the sharp line has NOT yet absorbed (a late scratch,
a map veto that just finished, a lineup change). Multipliers scale the market-implied MEAN, then the
probability at the PrizePicks line is recomputed (with a haircut). Default for everyone is 1.0 (no change).

context.csv format:  player,mult,note
"""
from __future__ import annotations

import csv


# ---------- helpers that produce a multiplier (ratio of NEW expected stat to what the line assumed) ----------
def pace_mult(team_pace: float, opp_pace: float, league_pace: float, assumed_pace: float | None = None) -> float:
    """College/pro basketball: expected game pace vs the pace the line implicitly assumed (default: league)."""
    expected = (team_pace + opp_pace) / 2
    return expected / (assumed_pace or league_pace)


def minutes_mult(old_minutes: float, new_minutes: float, usage_boost: float = 0.0) -> float:
    """A starter is out / role changed: scale by minutes, plus an optional usage bump (e.g. 0.05 = +5%)."""
    return (new_minutes / old_minutes) * (1 + usage_boost)


def esports_map_mult(maps: list[dict], baseline_kills: float) -> float:
    """CS2/Valorant after the veto. Each map: {"rounds": expected rounds, "kpr_ct": ..., "kpr_t": ...,
    "ct_share": share of rounds on CT/defence side (default 0.5)}. Returns expected kills / baseline_kills,
    where baseline_kills is the player's expected total the line assumed BEFORE the maps were known."""
    total = 0.0
    for m in maps:
        s = m.get("ct_share", 0.5)
        total += m["rounds"] * (s * m["kpr_ct"] + (1 - s) * m["kpr_t"])
    return total / baseline_kills


def series_scale(per_map_mean: float, maps_played_expected: float) -> float:
    """Per-map average -> total for a 'Map 1+2' or full-series stat. Bo1: 1 map; Bo3: ~2.4-2.6 maps."""
    return per_map_mean * maps_played_expected


# ---------- loader ----------
def load_context(path: str | None, normalizer) -> dict[str, float]:
    if not path:
        return {}
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            m = float(row["mult"])
            if not 0.5 <= m <= 1.5:
                raise ValueError(f"multiplier {m} for {row['player']} outside 0.5-1.5; check your inputs")
            out[normalizer(row["player"])] = m
    return out
