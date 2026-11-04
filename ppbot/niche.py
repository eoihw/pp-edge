#!/usr/bin/env python3
"""Line comparator for niche sports (CS2, Valorant, LoL, college basketball...) from CSV boards.

  python niche.py --pp pp_board.csv --sharp sharp_board.csv [--context context.csv] [--k 1.2] [--out edges.csv]

pp_board.csv   : player,stat,line
sharp_board.csv: player,stat,line,over_odds,under_odds[,book]     (American odds; multiple rows/books OK)
context.csv    : player,mult,note                                  (optional, see context.py)

Edge types in the output:
  juice  - same line as the sharp book; the edge is purely the sharp book's price on one side
  line   - PrizePicks line differs from the sharp line (e.g. PP 31.5 vs sharp 33.5); shifted via the normal
           model (haircut applied), so treat as lower-confidence and verify
"""
from __future__ import annotations

import argparse

import pandas as pd

from context import load_context
from ev import DISPERSION, breakeven_leg_prob, fair_over_prob, high_value_threshold
from ppbot import norm_name


def compare(pp: pd.DataFrame, sharp: pd.DataFrame, k: float = 1.2, ctx: dict | None = None,
            weights: dict | None = None, min_books: int = 1) -> pd.DataFrame:
    ctx, weights = ctx or {}, weights or {}
    pp, sharp = pp.copy(), sharp.copy()
    for df in (pp, sharp):
        df["pkey"] = df["player"].map(norm_name)
        df["skey"] = df["stat"].str.lower().str.strip()
    if "book" not in sharp:
        sharp["book"] = "sharp"
    be2, hv = breakeven_leg_prob(2, "power"), high_value_threshold()

    rows = []
    for _, r in pp.iterrows():
        sub = sharp[(sharp.pkey == r.pkey) & (sharp.skey == r.skey)]
        if sub.empty:
            continue
        quotes = [(b.book, float(b.line), float(b.over_odds), float(b.under_odds)) for b in sub.itertuples()]
        res = fair_over_prob(quotes, float(r.line), weights, k, ctx.get(r.pkey, 1.0))
        if not res or res[2] < min_books:
            continue
        p_over, spread, n, shifted = res
        sharp_line = sorted({q[1] for q in quotes}, key=lambda x: abs(x - r.line))[0]
        for side, p in (("Over", p_over), ("Under", 1 - p_over)):
            rows.append({
                "player": r.player, "stat": r.stat, "side": side, "pp_line": r.line, "sharp_line": sharp_line,
                "line_diff": round(sharp_line - r.line, 2) if side == "Over" else round(r.line - sharp_line, 2),
                "edge_type": "line" if shifted or sharp_line != r.line else "juice",
                "fair_prob": round(p, 4), "edge_pts_vs_2pick": round(p - be2, 4),
                "ev_2pick_pct": round((3 * p * p - 1) * 100, 1),  # EV of a 2-pick Power with two legs this good
                "books": n, "high_value": p >= hv,
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["ev_2pick_pct", "line_diff"], ascending=False).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pp", required=True)
    ap.add_argument("--sharp", required=True)
    ap.add_argument("--context")
    ap.add_argument("--k", type=float, default=DISPERSION["default"],
                    help="std-dev ~= k*sqrt(mean). Calibrate per stat from your own data!")
    ap.add_argument("--out")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    df = compare(pd.read_csv(a.pp), pd.read_csv(a.sharp), a.k, load_context(a.context, norm_name))
    if df.empty:
        print("No matching player/stat rows between the two boards (check spelling of player and stat).")
        return
    pd.set_option("display.width", 200)
    print(df.head(a.top).to_string())
    if a.out:
        df.to_csv(a.out, index=False)


if __name__ == "__main__":
    main()
