import math

import ev
import ppbot


def test_breakevens_match_published():
    assert abs(ev.breakeven_leg_prob(2, "power") - 0.5774) < 1e-3   # 3x on 2 picks
    assert abs(ev.breakeven_leg_prob(3, "power") - 0.5848) < 1e-3   # 5x on 3 picks
    assert abs(ev.breakeven_leg_prob(6, "power") - 0.5465) < 1e-3   # 37.5x on 6 picks


def test_power_ev_example():
    # 4 legs at 58% on a 10x power play -> ~ +13% (matches the worked example in the research)
    s = ev.entry_stats([0.58] * 4, "power")
    assert abs(s[0] - (0.58**4 * 10 - 1)) < 1e-9
    assert 0.12 < s[0] < 0.14


def test_poisson_binomial_sums_to_one():
    d = ev.poisson_binomial([0.5, 0.6, 0.7, 0.55])
    assert abs(sum(d) - 1) < 1e-12


def test_devig_even_market():
    assert abs(ev.devig_two_way(-110, -110) - 0.5) < 1e-9
    assert abs(ev.devig_two_way(-110, -110, "multiplicative") - 0.5) < 1e-9


def test_devig_skew_and_sums():
    p = ev.devig_two_way(-150, +120)
    assert 0.55 < p < 0.60


def _payload():
    def outs(player, point, over, under):
        return [{"name": "Over", "description": player, "price": over, "point": point},
                {"name": "Under", "description": player, "price": under, "point": point}]
    games = []
    for g in range(6):
        pl = f"Player {chr(65 + g)}"
        pin = {"key": "pinnacle", "markets": [{"key": "player_points", "outcomes": outs(pl, 24.5, -150, +125)}]}
        fd = {"key": "fanduel", "markets": [{"key": "player_points", "outcomes": outs(pl, 24.5, -145, +120)}]}
        pp = {"key": "prizepicks", "markets": [{"key": "player_points", "outcomes": [
            {"name": "Over", "description": pl, "price": 100, "point": 24.5}]}]}
        games.append({"id": f"g{g}", "home_team": f"H{g}", "away_team": f"A{g}", "bookmakers": [pin, fd, pp]})
    return games


def test_build_legs_and_slips():
    legs = []
    for g in _payload():
        legs += ppbot.build_legs(g, set())
    overs = [l for l in legs if l.side == "Over"]
    assert len(overs) == 6 and all(0.56 < l.prob < 0.62 for l in overs)
    slips = ev.best_slips(legs, min_ev=0.0)
    assert slips and all(len({l.event_id for l in s.legs}) == len(s.legs) for s in slips)
    assert all(s.ev >= 0 for s in slips)


def test_line_mismatch_and_exclusion():
    g = _payload()[0]
    g["bookmakers"][2]["markets"][0]["outcomes"][0]["point"] = 25.5   # PP line differs from books
    assert ppbot.build_legs(g, set(), allow_shift=False) == []   # exact-line mode rejects it
    assert ppbot.build_legs(g, set())                                # shift mode prices it (flagged)
    g2 = _payload()[0]
    assert ppbot.build_legs(g2, {ppbot.norm_name("Player A")}) == []


def test_stale_line_filter():
    g = _payload()[0]
    for bk in g["bookmakers"][:2]:                                    # absurd 90% "edge" -> rejected
        bk["markets"][0]["outcomes"][0]["price"] = -900
        bk["markets"][0]["outcomes"][1]["price"] = +600
    assert [l for l in ppbot.build_legs(g, set()) if l.side == "Over"] == []


# ---------- line shifting / context / niche ----------
import pandas as pd
import context
import niche


def test_shift_round_trip_and_direction():
    p = 0.55
    assert abs(ev.prob_over_at(p, 24.5, 24.5, 1.3) - p) < 1e-12              # no shift -> unchanged
    mu, s = ev._mu_sigma(p, 24.5, 1.3)
    assert abs((1 - ev._N.cdf((24.5 - mu) / s)) - p) < 1e-6                   # implied mean reproduces p
    lower = ev.prob_over_at(p, 24.5, 22.5, 1.3, haircut=1.0)                  # PP line lower -> Over likelier
    higher = ev.prob_over_at(p, 24.5, 26.5, 1.3, haircut=1.0)
    assert lower > p > higher


def test_line_discrepancy_big_edge():
    # PP 31.5 vs sharp 33.5 at 50/50: Over 31.5 should be a clearly +EV leg even after the haircut
    pp = pd.DataFrame([{"player": "Zywoo", "stat": "map1+2 kills", "line": 31.5}])
    sharp = pd.DataFrame([{"player": "ZywOo", "stat": "Map1+2 Kills", "line": 33.5,
                           "over_odds": -110, "under_odds": -110, "book": "pinnacle"}])
    df = niche.compare(pp, sharp, k=1.0)
    over = df[df.side == "Over"].iloc[0]
    assert over.edge_type == "line" and over.fair_prob > 0.6 and over.line_diff == 2.0


def test_juice_edge_same_line():
    pp = pd.DataFrame([{"player": "A", "stat": "kills", "line": 32.5}])
    sharp = pd.DataFrame([{"player": "A", "stat": "kills", "line": 32.5, "over_odds": -140, "under_odds": 110}])
    df = niche.compare(pp, sharp)
    top = df.iloc[0]
    assert top.side == "Over" and top.edge_type == "juice" and 0.55 < top.fair_prob < 0.60


def test_high_value_threshold_is_not_the_videos_55():
    assert abs(ev.high_value_threshold() - 0.5848) < 1e-3


def test_context_mult_moves_prob_and_bounds():
    base = ev.prob_over_at(0.5, 20.5, 20.5, 1.2)
    up = ev.prob_over_at(0.5, 20.5, 20.5, 1.2, mult=1.10)
    assert up > base
    assert abs(context.minutes_mult(30, 36, 0.0) - 1.2) < 1e-9
    assert abs(context.pace_mult(70, 68, 68, 68) - 1.0147) < 1e-3
    assert abs(context.esports_map_mult([{"rounds": 24, "kpr_ct": 0.7, "kpr_t": 0.7}], 16.8) - 1.0) < 1e-9


def test_ppbot_shifted_leg_flagged():
    g = _payload()[0]
    for bk in g["bookmakers"][:2]:
        for o in bk["markets"][0]["outcomes"]:
            o["point"] = 26.5                                              # books at 26.5, PP at 24.5
    legs = ppbot.build_legs(g, set())
    assert legs and all(l.shifted for l in legs)
    assert ppbot.build_legs(g, set(), allow_shift=False) == []


# ---------- v3: day window, merge, multi-sport gather ----------
import datetime as dt


def test_window_tomorrow_is_24h_in_future_or_equal():
    s, e = ppbot.window("tomorrow", 24)
    assert e - s == dt.timedelta(days=1) and s > dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    s2, e2 = ppbot.window("2026-11-03", 24)
    assert (e2 - s2) == dt.timedelta(days=1)


def test_merge_odds_combines_markets_per_book():
    a = {"bookmakers": [{"key": "x", "markets": [{"key": "m1"}]}]}
    b = {"bookmakers": [{"key": "x", "markets": [{"key": "m2"}]}, {"key": "y", "markets": [{"key": "m2"}]}]}
    m = ppbot.merge_odds(a, b)
    by = {bk["key"]: [mm["key"] for mm in bk["markets"]] for bk in m["bookmakers"]}
    assert by == {"x": ["m1", "m2"], "y": ["m2"]}


class FakeClient:
    remaining = 400
    skipped_markets = set()

    def __init__(self):
        self.used = 0
        self.calls = 0

    def events(self, key):
        t = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5)).isoformat().replace("+00:00", "Z")
        return [{"id": f"g{i}", "commence_time": t, "home_team": f"H{i}", "away_team": f"A{i}"}
                for i in range(6)]

    def event_odds(self, key, event_id, markets):
        self.calls += 1
        self.used += 4
        g = next(x for x in _payload() if x["id"] == event_id)
        return g


def test_gather_multi_sport_and_budget():
    c = FakeClient()
    s, e = ppbot.window(None, 24)
    legs, stats = ppbot.gather(c, ["nba", "nhl"], s, e, set(), budget=10_000)
    assert len(stats) == 2 and stats[0]["games"] == 6 and stats[0]["pp_up"] == 6
    assert len(legs) >= 12
    c2 = FakeClient()
    legs2, stats2 = ppbot.gather(c2, ["nba"], s, e, set(), budget=8)     # budget: 2 calls then stop
    assert c2.calls == 2 and stats2[0]["stopped"]


def test_resolve_sports_all_skips_unsupported(capsys):
    got = ppbot.resolve_sports("all")
    assert set(got) == set(ppbot.SPORTS)
    assert "skipping CS2" in capsys.readouterr().out


def test_yardage_shift_is_modest():
    # a 5-yard line gap on a ~105 rush-yd prop is worth a few points of probability, not 9+
    k = ev.DISPERSION["player_rush_yds"]
    p = ev.prob_over_at(0.5, 110.5, 105.5, k)
    assert 0.50 < p < 0.56


def test_debug_counts():
    g = _payload()[0]
    d = {}
    ppbot.build_legs(g, set(), diag=d)
    assert d["pp_props"] == 1 and d["matched"] == 1


# ---------- web core ----------
import webcore


class _FakeOddsClient(FakeClient):
    def __init__(self, key=None):
        super().__init__()


def test_webcore_end_to_end(monkeypatch):
    monkeypatch.setattr(ppbot, "OddsClient", _FakeOddsClient)
    res = webcore.run_analysis("k", ["nba"], "next24", None, "America/Los_Angeles", 1000, 2, True, [])
    assert len(res["legs"]) >= 6 and res["stats"][0]["pp_up"] == 6
    lt = webcore.legs_table(res["legs"])
    assert {"Player", "Pick", "Fair win %", "Flags"} <= set(lt.columns)
    cov = webcore.coverage_table(res["stats"])
    assert cov.loc[0, "Sport"] == "NBA"
    views = webcore.slips_view(res["legs"], 0.0, 100.0)
    assert views and views[0]["stake"] >= 0
    assert "type,picks,ev,stake,legs" in webcore.slips_csv(views).splitlines()[0]
    rows = webcore.scan_games("k", ["nba", "nhl"], "next24", None, "America/Los_Angeles")
    assert rows[0]["Games"] == 6


def test_single_book_mode_and_flags():
    g = _payload()[0]
    g["bookmakers"] = [g["bookmakers"][1], g["bookmakers"][2]]          # only fanduel + prizepicks
    assert [l for l in ppbot.build_legs(g, set()) if l.side == "Over"] == []                 # default needs 2
    one = ppbot.build_legs(g, set(), min_books=1)
    assert one and all(l.n_books == 1 for l in one)
    assert all("single book" in webcore.legs_table(one).loc[i, "Flags"] for i in range(len(one)))
    assert not any("HIGH VALUE" in f for f in webcore.legs_table(one)["Flags"])


def test_window_with_timezone_name():
    s, e = ppbot.window("tomorrow", 24, "America/Los_Angeles")
    assert e - s == dt.timedelta(days=1)
