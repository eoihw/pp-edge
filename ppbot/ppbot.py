#!/usr/bin/env python3
"""PrizePicks +EV pick finder.

Idea: sharp sportsbooks are much better at pricing player props than PrizePicks is. For every PrizePicks
line, find the same player/stat/line at other books, strip the vig, blend into a fair probability, and
compare it to the break-even rate the PrizePicks payout needs. Then rank slips by true EV.

Usage:
  export ODDS_API_KEY=...            # https://the-odds-api.com (paid tier recommended)
  python ppbot.py --sport nba
  python ppbot.py --sport nba,nfl,nhl --watch 600 --exclude out_players.txt
Optional: export DISCORD_WEBHOOK_URL=... to get new slips pushed to Discord.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict

import requests

from context import load_context
from ev import (DISPERSION, Leg, best_slips, breakeven_leg_prob, fair_over_prob,
                high_value_threshold)

BASE = "https://api.the-odds-api.com/v4"

NFL_MARKETS = ["player_pass_yds", "player_pass_tds", "player_pass_completions", "player_pass_attempts",
               "player_rush_yds", "player_rush_attempts", "player_reception_yds", "player_receptions"]
SPORTS = {  # alias -> (odds-api sport keys, player-prop markets to price)
    "nba": (["basketball_nba"], ["player_points", "player_rebounds", "player_assists", "player_threes",
                                 "player_points_rebounds_assists", "player_points_rebounds",
                                 "player_points_assists", "player_rebounds_assists"]),
    "nfl": (["americanfootball_nfl"], NFL_MARKETS),
    "cfb": (["americanfootball_ncaaf"], NFL_MARKETS),
    "cbb": (["basketball_ncaab"], ["player_points", "player_rebounds", "player_assists", "player_threes", "player_points_rebounds_assists"]),
    "wnba": (["basketball_wnba"], ["player_points", "player_rebounds", "player_assists", "player_threes", "player_points_rebounds_assists"]),
    "nhl": (["icehockey_nhl"], ["player_shots_on_goal", "player_points", "player_assists",
                                "player_blocked_shots"]),
    "ucl": (["soccer_uefa_champs_league"], ["player_shots_on_target", "player_shots", "player_assists"]),
    "uel": (["soccer_uefa_europa_league"], ["player_shots_on_target", "player_shots", "player_assists"]),
    "epl": (["soccer_epl"], ["player_shots_on_target", "player_shots", "player_assists"]),
    "laliga": (["soccer_spain_la_liga"], ["player_shots_on_target", "player_shots", "player_assists"]),
    "seriea": (["soccer_italy_serie_a"], ["player_shots_on_target", "player_shots", "player_assists"]),
    "bundesliga": (["soccer_germany_bundesliga"], ["player_shots_on_target", "player_shots", "player_assists"]),
    "ligue1": (["soccer_france_ligue_one"], ["player_shots_on_target", "player_shots", "player_assists"]),
    "mls": (["soccer_usa_mls"], ["player_shots_on_target", "player_shots", "player_assists"]),
    "mlb": (["baseball_mlb"], ["pitcher_strikeouts", "batter_hits", "batter_total_bases", "pitcher_outs"]),
}
# PrizePicks categories with no sharp-book prop feed wired into this bot. Not priced, on purpose:
# without a sharp reference price there is no honest way to call an edge.
UNSUPPORTED = {
    "ufc": "fighter props (sig. strikes, fight time) have no sharp-book feed here",
    "tennis": "aces/games-won props have no sharp-book feed here",
    "tt": "table tennis: no sharp-book prop feed here",
    "bad": "badminton: no sharp-book prop feed here",
    "cs2": "esports player props: no sharp-book feed here (use niche.py with your own sharp board)",
    "lol": "esports player props: no sharp-book feed here (use niche.py with your own sharp board)",
    "dota2": "esports player props: no sharp-book feed here (use niche.py with your own sharp board)",
    "kbo": "no sharp-book player-prop feed here (and KBO is in its offseason)",
    "nba4q": "quarter props: no sharp-book feed here",
    "nfl1q": "quarter props: no sharp-book feed here",
    "nfl1h": "half props: no sharp-book feed here",
    "cfb1h": "half props: no sharp-book feed here",
}
# Which books to pull. PrizePicks is the target; the rest are the "truth" sources.
PP_KEY = "prizepicks"
REF_BOOKS = ["pinnacle", "fanduel", "draftkings", "betmgm", "williamhill_us", "betrivers", "bovada"]
WEIGHTS = {"pinnacle": 3.0, "fanduel": 1.5, "draftkings": 1.5}  # everything else = 1.0

MIN_BOOKS = 2          # need at least this many reference books (Pinnacle alone also counts)
MAX_SPREAD = 0.04      # skip if books disagree by more than 4 prob. points (stale/odd line)
MAX_PROB = 0.72        # a "75% prop" is almost always a stale line or a name mismatch, not an edge


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s)
    return re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s).strip()


class OddsClient:
    def __init__(self, key: str):
        self.key, self.s = key, requests.Session()
        self.first_remaining = self.remaining = None
        self.skipped_markets: set[str] = set()

    @property
    def used(self) -> int:
        if self.first_remaining is None or self.remaining is None:
            return 0
        return self.first_remaining - self.remaining

    def _get(self, path, **params):
        params["apiKey"] = self.key
        for attempt in range(3):
            r = self.s.get(BASE + path, params=params, timeout=30)
            if r.status_code == 429:  # rate limited: back off and retry
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            rem = r.headers.get("x-requests-remaining")
            if rem is not None:
                self.remaining = int(float(rem))
                if self.first_remaining is None:
                    self.first_remaining = self.remaining + int(float(r.headers.get("x-requests-last", 0)))
            return r.json()
        r.raise_for_status()

    def events(self, sport):
        try:
            return self._get(f"/sports/{sport}/events")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return []  # sport not active / unknown right now
            raise

    def _odds(self, sport, event_id, markets):
        return self._get(f"/sports/{sport}/events/{event_id}/odds", markets=",".join(markets),
                         bookmakers=",".join([PP_KEY] + REF_BOOKS), oddsFormat="american")

    def event_odds(self, sport, event_id, markets):
        """One call for all markets; if the API rejects a market name (422), retry market by market
        and skip the bad ones instead of crashing."""
        try:
            return self._odds(sport, event_id, markets)
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 422 or len(markets) == 1:
                raise
        merged: dict = {}
        for m in markets:
            try:
                part = self._odds(sport, event_id, [m])
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 422:
                    self.skipped_markets.add(m)
                    continue
                raise
            merged = merge_odds(merged, part)
        return merged


def merge_odds(a: dict, b: dict) -> dict:
    if not a:
        return b
    books = {bk["key"]: bk for bk in a.get("bookmakers", [])}
    for bk in b.get("bookmakers", []):
        if bk["key"] in books:
            books[bk["key"]]["markets"] = books[bk["key"]].get("markets", []) + bk.get("markets", [])
        else:
            books[bk["key"]] = bk
    a["bookmakers"] = list(books.values())
    return a


def build_legs(event_odds: dict, excluded: set[str], ctx: dict[str, float] | None = None,
               allow_shift: bool = True, diag: dict | None = None, min_books: int | None = None) -> list[Leg]:
    ctx = ctx or {}
    diag = diag if diag is not None else {}
    bump = lambda k: diag.__setitem__(k, diag.get(k, 0) + 1)
    game = f"{event_odds.get('away_team')} @ {event_odds.get('home_team')}"
    eid = event_odds["id"]
    pp_lines: dict[tuple[str, str], float] = {}
    quotes: dict[tuple[str, str], list] = defaultdict(list)  # (player, market) -> [(book, line, over, under)]

    for bk in event_odds.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            by_player: dict[tuple[str, float], dict[str, float]] = defaultdict(dict)
            for o in mkt.get("outcomes", []):
                if o.get("point") is None or not o.get("description"):
                    continue
                by_player[(norm_name(o["description"]), float(o["point"]))][o["name"]] = o.get("price")
            for (player, point), sides in by_player.items():
                if bk["key"] == PP_KEY:
                    pp_lines[(player, mkt["key"])] = point
                elif sides.get("Over") is not None and sides.get("Under") is not None:
                    quotes[(player, mkt["key"])].append((bk["key"], point, sides["Over"], sides["Under"]))

    legs = []
    for (player, market), line in pp_lines.items():
        bump("pp_props")
        if player in excluded:
            bump("excluded")
            continue
        if (player, market) not in quotes:
            bump("no_book_has_player")
            continue
        k = DISPERSION.get(market, DISPERSION["default"])
        res = fair_over_prob(quotes[(player, market)], line, WEIGHTS, k, ctx.get(player, 1.0), allow_shift)
        if not res:
            bump("line_too_far_from_books")
            continue
        p_over, spread, n_books, shifted = res
        books = {b for b, *_ in quotes[(player, market)]}
        if n_books < (MIN_BOOKS if min_books is None else min_books) and "pinnacle" not in books:
            bump("too_few_books")
            continue
        if spread > MAX_SPREAD:
            bump("books_disagree")
            continue
        bump("matched")
        for side, p in (("Over", p_over), ("Under", 1 - p_over)):
            if p <= MAX_PROB:
                legs.append(Leg(player.title(), market, side, line, p, eid, game, n_books, spread, shifted))
    return legs


def window(day: str | None, hours: int, tzname: str | None = None):
    """(start, end) in UTC. day = today | tomorrow | YYYY-MM-DD, in YOUR local time zone."""
    now = dt.datetime.now(dt.timezone.utc)
    if not day:
        return now, now + dt.timedelta(hours=hours)
    if tzname:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tzname)
    else:
        tz = dt.datetime.now().astimezone().tzinfo
    today = dt.datetime.now(tz).date()
    d = {"today": today, "tomorrow": today + dt.timedelta(days=1)}.get(day) or dt.date.fromisoformat(day)
    start = dt.datetime.combine(d, dt.time.min, tz).astimezone(dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    return (max(start, now) if d == today else start), end


def upcoming(client: OddsClient, key: str, start, end) -> list[dict]:
    out = []
    for ev in client.events(key):
        t = dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if start <= t < end:
            out.append(ev)
    return out


def gather(client: OddsClient, sports: list[str], start, end, excluded: set[str],
           ctx: dict[str, float] | None = None, allow_shift: bool = True, budget: int = 200,
           min_books: int | None = None):
    """Returns (legs, per-sport stats). Stops requesting odds once `budget` API credits are used."""
    legs, stats = [], []
    for sp in sports:
        keys, markets = SPORTS[sp]
        st = {"sport": sp, "games": 0, "pp_up": 0, "refs_up": 0, "legs": 0, "stopped": False, "diag": {}}
        for key in keys:
            for ev in upcoming(client, key, start, end):
                st["games"] += 1
                if client.used >= budget:
                    st["stopped"] = True
                    continue
                odds = client.event_odds(key, ev["id"], markets)
                keys_up = {b["key"] for b in odds.get("bookmakers", [])}
                st["pp_up"] += PP_KEY in keys_up
                st["refs_up"] += bool(keys_up - {PP_KEY})
                new = build_legs(odds, excluded, ctx, allow_shift, st["diag"], min_books)
                st["legs"] += len(new)
                legs += new
        stats.append(st)
    return legs, stats


def print_stats(stats, client, debug=False):
    print("\n--- coverage ---")
    for st in stats:
        note = "  (STOPPED: credit budget reached)" if st["stopped"] else ""
        print(f"  {st['sport'].upper():<4} games {st['games']:>2} | PrizePicks lines up {st['pp_up']:>2} | "
              f"sportsbook lines up {st['refs_up']:>2} | priced legs {st['legs']:>3}{note}")
        if debug:
            print("       why: " + (", ".join(f"{k}={v}" for k, v in sorted(st["diag"].items())) or "no PrizePicks props seen"))
    if client.skipped_markets:
        print("  (API rejected these market names, skipped: " + ", ".join(sorted(client.skipped_markets)) + ")")
    print("  If 'sportsbook lines up' is low, the books haven't posted props yet. Try again closer to game time.")


def report(legs: list[Leg], min_ev: float, top_legs: int) -> list:
    be2, be6 = breakeven_leg_prob(2, "power"), breakeven_leg_prob(6, "power")
    print(f"\n=== TOP LEGS (fair win prob; break-even per leg: {be2:.1%} on 2-pick Power, {be6:.1%} on 6-pick) ===")
    hv = high_value_threshold()
    print(f"  (HIGH VALUE = clears every Power break-even, >= {hv:.1%};  ~ = line shifted/context-adjusted, less certain)")
    for l in sorted(legs, key=lambda x: x.prob, reverse=True)[:top_legs]:
        tag = ("HIGH VALUE " if l.prob >= hv else "") + ("~" if l.shifted else "")
        print(f"  {l.prob:6.1%}  {l.label:<48} {l.game}  ({l.n_books} books) {tag}")
    slips = best_slips(legs, min_ev=min_ev)
    print(f"\n=== TOP SLIPS (EV per $1, suggested stake = capped quarter-Kelly of bankroll) ===")
    if not slips:
        print("  No slips clear the EV threshold right now. That is a normal, correct answer - don't force plays.")
    for s in slips[:12]:
        print(f"\n  {len(s.legs)}-pick {s.kind.upper():<5} EV {s.ev:+.1%}  hit-all {s.win_all:.1%}  stake {s.stake_frac:.2%}")
        for l in s.legs:
            print(f"      {l.prob:5.1%}  {l.label}{' ~' if l.shifted else ''}")
    return slips


def save_csv(slips, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["type", "picks", "ev", "hit_all", "stake_frac", "legs"])
        for s in slips:
            w.writerow([s.kind, len(s.legs), f"{s.ev:.4f}", f"{s.win_all:.4f}", f"{s.stake_frac:.4f}",
                        " | ".join(f"{l.label} ({l.prob:.1%})" for l in s.legs)])


def discord(slips, sent: set):
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    for s in slips[:5]:
        k = (s.kind, tuple(sorted(l.label for l in s.legs)))
        if k in sent:
            continue
        sent.add(k)
        body = f"**{len(s.legs)}-pick {s.kind.upper()}  EV {s.ev:+.1%}**\n" + "\n".join(
            f"- {l.label} ({l.prob:.1%})" for l in s.legs)
        requests.post(url, json={"content": body}, timeout=15)


def resolve_sports(arg: str) -> list[str]:
    wanted = [x.strip().lower() for x in arg.split(",") if x.strip()]
    SOCCER = ["ucl", "uel", "epl", "laliga", "seriea", "bundesliga", "ligue1", "mls"]
    wanted = [y for x in wanted for y in (SOCCER if x == "soccer" else [x])]
    if wanted == ["all"]:
        wanted = list(SPORTS) + list(UNSUPPORTED)
    ok = []
    for w in wanted:
        if w in SPORTS:
            ok.append(w)
        elif w in UNSUPPORTED:
            print(f"  skipping {w.upper()}: {UNSUPPORTED[w]}")
        else:
            sys.exit(f"Unknown sport '{w}'. Options: all, " + ", ".join(list(SPORTS) + list(UNSUPPORTED)))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="nba", help="comma list or 'all'. Priced: " + ",".join(SPORTS) +
                    ". Listed but not priced: " + ",".join(UNSUPPORTED))
    ap.add_argument("--day", help="today | tomorrow | YYYY-MM-DD (your local time). Overrides --hours")
    ap.add_argument("--hours", type=int, default=24, help="only games starting within N hours")
    ap.add_argument("--scan", action="store_true", help="just list games in the window; costs 0 credits")
    ap.add_argument("--budget", type=int, default=200, help="stop requesting odds after N API credits")
    ap.add_argument("--debug", action="store_true", help="show why PrizePicks props did not become legs")
    ap.add_argument("--min-ev", type=float, default=0.03)
    ap.add_argument("--top-legs", type=int, default=25)
    ap.add_argument("--exclude", help="text file, one player per line (injured/out/news you know about)")
    ap.add_argument("--context", help="CSV player,mult,note: context multipliers (see context.py)")
    ap.add_argument("--no-shift", action="store_true", help="exact-line matches only")
    ap.add_argument("--watch", type=int, default=0, help="re-run every N seconds")
    a = ap.parse_args()

    key = os.environ.get("ODDS_API_KEY")
    if not key:
        sys.exit("Set ODDS_API_KEY first (https://the-odds-api.com).")
    excluded = set()
    if a.exclude:
        excluded = {norm_name(x) for x in open(a.exclude) if x.strip()}
    ctx = load_context(a.context, norm_name)
    client, sent = OddsClient(key), set()
    sports = resolve_sports(a.sport)

    while True:
        start, end = window(a.day, a.hours)
        if a.scan:
            print(f"\nGames from {start.astimezone():%a %b %d %I:%M %p} to {end.astimezone():%a %b %d %I:%M %p} (your time):")
            for sp in sports:
                for k in SPORTS[sp][0]:
                    evs = upcoming(client, k, start, end)
                    print(f"  {sp.upper():<4} {len(evs)} games" + ("" if not evs else ": " + "; ".join(
                        f"{e['away_team']} @ {e['home_team']}" for e in evs[:6]) + (" ..." if len(evs) > 6 else "")))
            break
        legs, stats = gather(client, sports, start, end, excluded, ctx, not a.no_shift, a.budget)
        print(f"\n[{dt.datetime.now():%Y-%m-%d %H:%M}] {len(legs)} priced legs; credits used this run: "
              f"{client.used}; credits left: {client.remaining}")
        print_stats(stats, client, a.debug)
        slips = report(legs, a.min_ev, a.top_legs)
        save_csv(slips, f"picks_{dt.datetime.now():%Y%m%d_%H%M}.csv")
        discord(slips, sent)
        if not a.watch:
            break
        time.sleep(a.watch)


if __name__ == "__main__":
    main()
