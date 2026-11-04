"""UI-free logic for the web app (so it can be tested without Streamlit)."""
from __future__ import annotations

import datetime as dt

import pandas as pd

import ppbot
from ev import best_slips, breakeven_leg_prob, high_value_threshold


def run_analysis(key: str, sports: list[str], day: str, date_str: str | None, tzname: str,
                 budget: int, min_books: int, allow_shift: bool, excluded_names: list[str]) -> dict:
    """day: 'today' | 'tomorrow' | 'date' | 'next24'."""
    client = ppbot.OddsClient(key)
    if day == "next24":
        start, end = ppbot.window(None, 24, tzname)
    else:
        start, end = ppbot.window(date_str if day == "date" else day, 24, tzname)
    excluded = {ppbot.norm_name(n) for n in excluded_names if n.strip()}
    legs, stats = ppbot.gather(client, sports, start, end, excluded, None, allow_shift, budget, min_books)
    return {
        "legs": legs, "stats": stats, "used": client.used, "remaining": client.remaining,
        "skipped_markets": sorted(client.skipped_markets), "window": (start, end),
        "ran_at": dt.datetime.now(dt.timezone.utc),
    }


def scan_games(key: str, sports: list[str], day: str, date_str: str | None, tzname: str) -> list[dict]:
    """Free: list games in the window per sport (the events endpoint costs 0 credits)."""
    client = ppbot.OddsClient(key)
    if day == "next24":
        start, end = ppbot.window(None, 24, tzname)
    else:
        start, end = ppbot.window(date_str if day == "date" else day, 24, tzname)
    rows = []
    for sp in sports:
        for k in ppbot.SPORTS[sp][0]:
            evs = ppbot.upcoming(client, k, start, end)
            rows.append({"Sport": sp.upper(), "Games": len(evs),
                         "Matchups": "; ".join(f"{e['away_team']} @ {e['home_team']}" for e in evs[:8])})
    return rows


def coverage_table(stats: list[dict]) -> pd.DataFrame:
    rows = []
    for st in stats:
        rows.append({
            "Sport": st["sport"].upper(), "Games": st["games"], "PrizePicks lines up": st["pp_up"],
            "Sportsbook lines up": st["refs_up"], "Priced legs": st["legs"],
            "Why props were dropped": ", ".join(f"{k}={v}" for k, v in sorted(st["diag"].items())) or "-",
            "Note": "stopped: credit budget reached" if st["stopped"] else "",
        })
    return pd.DataFrame(rows)


def legs_table(legs) -> pd.DataFrame:
    hv, be2 = high_value_threshold(), breakeven_leg_prob(2, "power")
    rows = []
    for l in sorted(legs, key=lambda x: x.prob, reverse=True):
        tag = []
        if l.prob >= hv and l.n_books >= 2:
            tag.append("HIGH VALUE")
        if l.shifted:
            tag.append("shifted line (less certain)")
        if l.n_books < 2:
            tag.append("single book")
        rows.append({
            "Player": l.player, "Pick": f"{l.side} {l.line:g}", "Stat": l.market.replace("player_", ""),
            "Fair win %": round(l.prob * 100, 1), "Edge vs 2-pick (pts)": round((l.prob - be2) * 100, 1),
            "Books": l.n_books, "Game": l.game, "Flags": ", ".join(tag),
        })
    return pd.DataFrame(rows)


def slips_view(legs, min_ev: float, bankroll: float, top: int = 12) -> list[dict]:
    out = []
    for s in best_slips(legs, min_ev=min_ev)[:top]:
        out.append({
            "title": f"{len(s.legs)}-pick {s.kind.upper()}  |  EV {s.ev:+.1%}  |  all hit {s.win_all:.1%}",
            "stake": round(s.stake_frac * bankroll, 2),
            "ev": s.ev, "kind": s.kind, "n": len(s.legs),
            "legs": pd.DataFrame([{"Pick": l.label, "Fair win %": round(l.prob * 100, 1), "Game": l.game,
                                   "Shifted": "yes" if l.shifted else "", "Books": l.n_books} for l in s.legs]),
        })
    return out


def slips_csv(views: list[dict]) -> str:
    rows = [{"type": v["kind"], "picks": v["n"], "ev": round(v["ev"], 4), "stake": v["stake"],
             "legs": " | ".join(v["legs"]["Pick"])} for v in views]
    return pd.DataFrame(rows).to_csv(index=False)
