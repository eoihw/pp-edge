# PrizePicks +EV bot (v2)

Two tools that share one math core (`ev.py`):

| Tool | Use it for | Data |
|---|---|---|
| `ppbot.py` | NBA / NFL / NHL: automatic, can run on a loop and push to Discord | The Odds API key (PrizePicks + sharp books) |
| `niche.py` | CS2, Valorant, LoL, college hoops: any board you can get as a CSV | `pp_board.csv` + `sharp_board.csv` (see `examples/`) |

## How the edge is computed
1. Strip the vig from each sharp book's Over/Under (power method), blend books (Pinnacle weighted 3x).
2. **Same line:** that is the fair probability ("juice edge").
3. **Different line** (PP 31.5 vs sharp 33.5): the sharp price is converted to an implied mean, then re-priced at the PP line with a
   normal model (`std = k*sqrt(mean)`) and a 20% shrink toward 50% for model risk ("line edge", marked `~`).
4. Compare to the true break-even for the entry type. Legs >= 58.5% (HIGH VALUE) clear every Power size.
   Slips are ranked by exact EV (Power and Flex), one leg per player and game, with a capped quarter-Kelly stake.

Break-evens per leg: Power 2-pick 57.7%, 3: 58.5%, 4: 56.2%, 5: 54.9%, 6: 54.7%. Flex 3: 59.1%, 4: 56.9%, 5: 54.3%, 6: 54.2%.
Edit the payout tables in `ev.py` when PrizePicks changes them.

## Run it
```
pip install -r requirements.txt pandas
python -m pytest
export ODDS_API_KEY=...            # the-odds-api.com
python ppbot.py --sport nba --exclude out_players.txt [--context context.csv] [--no-shift] [--watch 600]
python niche.py --pp examples/pp_board.csv --sharp examples/sharp_board.csv [--context examples/context.csv] [--out edges.csv]
```

## Context multipliers (`context.py`) - use sparingly
Pace, minutes/usage, esports map pool and series length are available as helper functions that output a multiplier.
Sharp books already price all of this. The multiplier is applied to the sharp-implied mean, so it only adds value when you
know something the line has not absorbed yet (late scratch, a veto that just finished). Piling a "pace" multiplier on top of a line that
already includes pace double counts and creates fake edges. Multipliers outside 0.5-1.5 are rejected.

## Limits you should know
- **Niche sports have thin sharp markets.** Pinnacle and the big US books rarely post deep CS2/Valorant/college props, and
  Bovada/BetOnline are soft, not sharp. If there is only one weak reference book you have a stale-line detector at best. `niche.py`
  is only as good as the sharp board you feed it.
- `k` (dispersion) values are assumptions. Calibrate them per stat on your own historical lines before trusting shifted edges.
- Only standard lines (no demons/goblins). Integer-line pushes are ignored. Verify late news before submitting.
- Small edges, large variance, and PrizePicks limits winning accounts. Track every slip.


## v3: tomorrow mode, many sports, free scan
```
py ppbot.py --sport all --day tomorrow --scan      # free: lists tomorrow's games per sport (0 credits)
py ppbot.py --sport all --day tomorrow             # prices everything it can, stops at --budget credits (default 200)
py ppbot.py --sport nhl,cfb,ucl --day tomorrow --budget 80
```
Priced against sportsbook props: NBA, NFL, CFB, NHL, UCL. Listed but NOT priced (no sharp-book prop feed wired up, so any "edge" would be made up):
UFC, tennis, table tennis, badminton, CS2, LoL, Dota2, KBO, NBA 4th quarter, NFL 1Q/1H, CFB 1H. For esports use `niche.py` with your own sharp board.
The "coverage" block after each run shows, per sport, how many games had PrizePicks lines and how many had sportsbook lines up.
Sportsbooks usually post props the day of the game, so tomorrow-morning/midday runs find far more than late-night runs.
