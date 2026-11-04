"""Core math: de-vigging, consensus fair probability, PrizePicks slip EV, slip search.

Payout tables CHANGE over time. Always verify against the app and edit POWER / FLEX below.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from statistics import NormalDist, pstdev

# {picks: {number_correct: multiplier}}
POWER = {2: {2: 3.0}, 3: {3: 5.0}, 4: {4: 10.0}, 5: {5: 20.0}, 6: {6: 37.5}}
FLEX = {
    3: {3: 2.25, 2: 1.25},
    4: {4: 5.0, 3: 1.5},
    5: {5: 10.0, 4: 2.0, 3: 0.4},
    6: {6: 25.0, 5: 2.0, 4: 0.4},
}
TABLES = {"power": POWER, "flex": FLEX}


# ---------- odds -> fair probability ----------
def american_to_prob(a: float) -> float:
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def devig_two_way(price_over: float, price_under: float, method: str = "power") -> float:
    """Return the vig-free probability of the Over from two American prices."""
    po, pu = american_to_prob(price_over), american_to_prob(price_under)
    if method == "multiplicative":
        return po / (po + pu)
    # power method: find k so that po**k + pu**k == 1 (handles favourite/longshot skew better)
    lo, hi = 1.0, 20.0
    for _ in range(80):
        k = (lo + hi) / 2
        if po**k + pu**k > 1:
            lo = k
        else:
            hi = k
    return po ** ((lo + hi) / 2)


def consensus(book_probs: dict[str, float], weights: dict[str, float]) -> tuple[float, float]:
    """Weighted mean of per-book Over probabilities and the (unweighted) spread between books."""
    tot = sum(weights.get(b, 1.0) for b in book_probs)
    mean = sum(p * weights.get(b, 1.0) for b, p in book_probs.items()) / tot
    spread = pstdev(book_probs.values()) if len(book_probs) > 1 else 0.0
    return mean, spread


# ---------- line shifting (PrizePicks line != sportsbook line) ----------
_N = NormalDist()
# Rough std-dev ~= k * sqrt(mean) per stat. These are ASSUMPTIONS: calibrate them on your own data.
DISPERSION = {
    "default": 1.2, "player_points": 1.35, "player_rebounds": 1.15, "player_assists": 1.15,
    "player_threes": 0.95, "player_points_rebounds_assists": 1.4, "player_points_rebounds": 1.35,
    "player_points_assists": 1.35, "player_rebounds_assists": 1.3,
    "player_shots_on_goal": 1.0, "player_blocked_shots": 1.0, "player_shots_on_target": 1.0, "player_shots": 1.1,
    # football yardage is MUCH wider than sqrt(mean): ~100 rush yds has a std of ~35-40, not 10
    "player_pass_yds": 4.0, "player_rush_yds": 3.8, "player_reception_yds": 4.0,
    "player_receptions": 1.1, "player_pass_tds": 0.9, "player_pass_completions": 1.1,
    "player_pass_attempts": 1.0, "player_rush_attempts": 1.4,
    "pitcher_strikeouts": 1.1, "batter_hits": 0.9, "batter_total_bases": 1.4, "pitcher_outs": 1.2,
}
SHIFT_HAIRCUT = 0.8  # shrink shifted/adjusted probs toward 50%: model risk is real


def _mu_sigma(p_over: float, line: float, k: float) -> tuple[float, float]:
    """Mean/sigma of a normal-approx stat such that P(X > line) = p_over, with sigma = k*sqrt(mean)."""
    z = _N.inv_cdf(min(max(1 - p_over, 1e-6), 1 - 1e-6))
    mu = max(line, 0.5)
    for _ in range(50):
        sigma = k * math.sqrt(max(mu, 0.5))
        mu = max(line - sigma * z, 0.1)
    return mu, k * math.sqrt(max(mu, 0.5))


def prob_over_at(p_over: float, from_line: float, to_line: float, k: float,
                 mult: float = 1.0, haircut: float = SHIFT_HAIRCUT) -> float:
    """Move a vig-free P(over) from one line to another (and/or scale the mean by a context multiplier)."""
    if from_line == to_line and mult == 1.0:
        return p_over
    mu, _ = _mu_sigma(p_over, from_line, k)
    mu *= mult
    sigma = k * math.sqrt(max(mu, 0.5))
    p = 1 - _N.cdf((to_line - mu) / sigma)
    return 0.5 + (p - 0.5) * haircut


def fair_over_prob(quotes, target_line: float, weights: dict[str, float], k: float = 1.2,
                   mult: float = 1.0, allow_shift: bool = True):
    """quotes: [(book, line, over_price, under_price)]. Returns (p_over, spread, n_books, shifted) or None.
    Same-line books count as-is; nearby lines are shifted through the normal model (with a haircut)."""
    best: dict[str, tuple[float, float]] = {}  # keep each book's nearest line
    for book, line, po, pu in quotes:
        if book not in best or abs(line - target_line) < abs(best[book][0] - target_line):
            best[book] = (line, devig_two_way(po, pu))
    probs, shifted = {}, mult != 1.0
    for book, (line, p) in best.items():
        if line != target_line:
            if not allow_shift:
                continue
            _, sigma = _mu_sigma(p, line, k)
            if abs(line - target_line) > max(1.0, 0.5 * sigma):
                continue
            shifted = True
        probs[book] = prob_over_at(p, line, target_line, k, mult)
    if not probs:
        return None
    mean, spread = consensus(probs, weights)
    return mean, spread, len(probs), shifted


# ---------- slip math ----------
def poisson_binomial(ps: list[float]) -> list[float]:
    """P(exactly k legs hit) for independent legs."""
    dist = [1.0]
    for p in ps:
        new = [0.0] * (len(dist) + 1)
        for k, q in enumerate(dist):
            new[k] += q * (1 - p)
            new[k + 1] += q * p
        dist = new
    return dist


def entry_stats(ps: list[float], kind: str) -> tuple[float, float] | None:
    """(EV per $1, variance of net return per $1). None if that pick-count isn't offered."""
    payouts = TABLES[kind].get(len(ps))
    if not payouts:
        return None
    dist = poisson_binomial(ps)
    rets = [payouts.get(k, 0.0) - 1.0 for k in range(len(ps) + 1)]
    ev = sum(d * r for d, r in zip(dist, rets))
    var = sum(d * (r - ev) ** 2 for d, r in zip(dist, rets))
    return ev, var


def breakeven_leg_prob(n: int, kind: str) -> float | None:
    """Equal per-leg win prob at which the entry has EV = 0."""
    if n not in TABLES[kind]:
        return None
    lo, hi = 0.3, 0.95
    for _ in range(60):
        mid = (lo + hi) / 2
        ev, _ = entry_stats([mid] * n, kind)
        lo, hi = (mid, hi) if ev < 0 else (lo, mid)
    return (lo + hi) / 2


def kelly_fraction(ev: float, var: float, mult: float = 0.25, cap: float = 0.02) -> float:
    """Conservative stake as a fraction of bankroll: fractional (approx.) Kelly, hard-capped."""
    if ev <= 0 or var <= 0:
        return 0.0
    return min(cap, mult * ev / var)


# ---------- legs & search ----------
@dataclass
class Leg:
    player: str
    market: str
    side: str  # "Over" / "Under"
    line: float
    prob: float  # fair (vig-free) probability this side hits
    event_id: str
    game: str
    n_books: int
    spread: float
    shifted: bool = False  # True if any input line was moved to the PrizePicks line or context-adjusted

    @property
    def label(self) -> str:
        return f"{self.player} {self.side[0]}{self.line:g} {self.market.replace('player_', '')}"


def high_value_threshold() -> float:
    """A leg at or above the toughest Power break-even clears EVERY Power entry size on its own.
    (The video's flat '55%' flag is wrong: 2-pick Power needs 57.7%, 3-pick 58.5%.)"""
    return max(breakeven_leg_prob(n, "power") for n in POWER)


@dataclass
class Slip:
    kind: str
    legs: list[Leg]
    ev: float
    stake_frac: float

    @property
    def win_all(self) -> float:
        return math.prod(l.prob for l in self.legs)


def best_slips(legs: list[Leg], pool: int = 14, sizes=range(2, 7), min_ev: float = 0.02,
               per_size: int = 3) -> list[Slip]:
    """Search combos of the top `pool` legs. One leg per player, at most one leg per game
    (same-game legs are correlated and PrizePicks reduces/limits those payouts)."""
    seen, top = set(), []
    for l in sorted(legs, key=lambda x: x.prob, reverse=True):
        if l.player not in seen:
            seen.add(l.player)
            top.append(l)
        if len(top) >= pool:
            break
    out: list[Slip] = []
    for n in sizes:
        cands: list[Slip] = []
        for combo in itertools.combinations(top, n):
            if len({l.event_id for l in combo}) < n:
                continue
            ps = [l.prob for l in combo]
            for kind in ("power", "flex"):
                st = entry_stats(ps, kind)
                if st and st[0] >= min_ev:
                    cands.append(Slip(kind, list(combo), st[0], kelly_fraction(*st)))
        cands.sort(key=lambda s: s.ev, reverse=True)
        out.extend(cands[:per_size])
    return sorted(out, key=lambda s: s.ev, reverse=True)
