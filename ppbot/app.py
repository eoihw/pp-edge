"""PrizePicks edge finder - web app. Run locally:  py -m streamlit run app.py"""
import datetime as dt
import hmac
import os

import streamlit as st

import ppbot
import webcore

st.set_page_config(page_title="PrizePicks Edge Finder", layout="wide")


def secret(name):
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name)


# ---------- password gate (so strangers with the link can't spend your API credits) ----------
APP_PASSWORD = secret("APP_PASSWORD")
if APP_PASSWORD:
    if not st.session_state.get("authed"):
        st.title("PrizePicks Edge Finder")
        entered = st.text_input("Password", type="password")
        if entered:
            if hmac.compare_digest(str(entered), str(APP_PASSWORD)):
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("Wrong password.")
        st.stop()

st.title("PrizePicks Edge Finder")
st.caption("Compares PrizePicks lines to sportsbook prices, strips the vig, and ranks entries by expected value. "
           "Estimates only. 18+ only; never stake money you can't afford to lose.")
if not APP_PASSWORD:
    st.warning("No APP_PASSWORD is set. If this app is public, anyone with the link can spend your API credits.")

# ---------- sidebar ----------
api_key = secret("ODDS_API_KEY")
with st.sidebar:
    st.header("Settings")
    if not api_key:
        api_key = st.text_input("Odds API key", type="password")
    all_sports = list(ppbot.SPORTS)
    default_sports = [s for s in ["nba", "cfb", "cbb", "nhl", "ucl"] if s in all_sports]
    sports = st.multiselect("Sports", all_sports, default=default_sports)
    day_label = st.radio("Games", ["Tomorrow", "Today", "Next 24 hours", "Pick a date"], index=0)
    day = {"Tomorrow": "tomorrow", "Today": "today", "Next 24 hours": "next24", "Pick a date": "date"}[day_label]
    date_str = None
    if day == "date":
        date_str = st.date_input("Date", value=dt.date.today() + dt.timedelta(days=1)).isoformat()
    tzname = st.text_input("Your time zone", value="America/Los_Angeles")
    budget = st.slider("Max API credits to spend per run", 20, 300, 120, step=10)
    min_ev = st.slider("Minimum slip EV", 0, 15, 3) / 100.0
    min_books = st.radio("Sportsbooks required per pick", [2, 1], index=0,
                         help="2 is safer. 1 finds more picks but each is based on a single book.")
    allow_shift = st.checkbox("Price lines that differ from the sportsbook line (less certain)", value=True)
    bankroll = st.number_input("Bankroll ($) for stake sizing", min_value=1.0, value=100.0, step=10.0)
    excluded_text = st.text_area("Players to skip (one per line: injured / out)", height=100)

excluded_names = [x for x in excluded_text.splitlines() if x.strip()]

c1, c2, _ = st.columns([1, 1, 3])
analyze = c1.button("Analyze", type="primary")
scan = c2.button("Scan games (free)")

if not api_key:
    st.info("Add your Odds API key in the sidebar (or in the app's secrets) to begin.")
    st.stop()
if not sports:
    st.info("Pick at least one sport in the sidebar.")
    st.stop()


@st.cache_data(ttl=900, show_spinner=False)
def cached_analysis(_key, sports_t, day, date_str, tzname, budget, min_books, allow_shift, excluded_t):
    return webcore.run_analysis(_key, list(sports_t), day, date_str, tzname, budget, min_books,
                                allow_shift, list(excluded_t))


if scan:
    with st.spinner("Listing games (costs no credits)..."):
        try:
            st.subheader("Games in this window")
            st.dataframe(webcore.scan_games(api_key, sports, day, date_str, tzname), use_container_width=True)
        except Exception as e:
            st.error(f"Could not list games: {e}")

if analyze:
    with st.spinner("Pulling lines and pricing picks... (identical runs within 15 min reuse results and cost nothing)"):
        try:
            res = cached_analysis(api_key, tuple(sports), day, date_str, tzname, budget, int(min_books),
                                  allow_shift, tuple(excluded_names))
        except Exception as e:
            st.error(f"Analysis failed: {e}")
            st.stop()

    legs = res["legs"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Priced picks", len(legs))
    m2.metric("Credits used", res["used"])
    m3.metric("Credits left", res["remaining"] if res["remaining"] is not None else "?")
    st.caption(f"Ran {res['ran_at'].astimezone():%Y-%m-%d %H:%M} (server time).")

    views = webcore.slips_view(legs, min_ev, bankroll)
    st.subheader("Suggested entries")
    if not views:
        st.info("No entries clear the EV threshold right now. That is a normal, correct answer. "
                "Sportsbooks mostly post props the day of the game, so try again closer to game time.")
    for v in views:
        with st.expander(f"{v['title']}  |  suggested stake ${v['stake']:.2f}", expanded=v is views[0]):
            st.dataframe(v["legs"], use_container_width=True, hide_index=True)
    if views:
        st.download_button("Download entries (CSV)", webcore.slips_csv(views), "entries.csv", "text/csv")

    st.subheader("All priced picks")
    lt = webcore.legs_table(legs)
    if lt.empty:
        st.write("None yet.")
    else:
        st.dataframe(lt, use_container_width=True, hide_index=True)
        st.download_button("Download picks (CSV)", lt.to_csv(index=False), "picks.csv", "text/csv")

    st.subheader("Coverage")
    st.dataframe(webcore.coverage_table(res["stats"]), use_container_width=True, hide_index=True)
    if res["skipped_markets"]:
        st.caption("The odds API rejected these markets, so they were skipped: " + ", ".join(res["skipped_markets"]))

with st.expander("Categories that can't be priced here"):
    for k, why in ppbot.UNSUPPORTED.items():
        st.write(f"**{k.upper()}**: {why}")
