# AFL Betting AI — real odds via Playwright (Sportsbet + Ladbrokes H2H)
# Runs on Render with headless Chromium. Streamlit UI shows EV% and 2-way arbs.

import streamlit as st, pandas as pd, asyncio, re, datetime, pytz
from playwright.async_api import async_playwright

st.set_page_config(page_title="AFL Betting AI — Live", layout="wide")
st.title("🏉 AFL Betting AI — Live (Playwright, Sportsbet + Ladbrokes)")

# ---- sidebar ----
with st.sidebar:
    bankroll = st.number_input("Bankroll (AUD)", 100.0, value=700.0, step=50.0)
    min_arb = st.slider("Min arbitrage margin (%)", 0.0, 20.0, 3.0, 0.5)
    min_ev  = st.slider("Min EV (%)", 0.0, 30.0, 6.0, 0.5)
    unit_ev_pct = st.slider("EV bet unit (% bankroll)", 0.5, 10.0, 2.5, 0.5)
    max_stake_arb_pct = st.slider("Max stake per arb (% bankroll)", 1.0, 50.0, 15.0, 1.0)

def ev_percent(p, odds): return ((p*odds)-1.0)*100.0
def implied_prob(o): return 1.0/o if o and o>0 else 0.0

# ---- headless scraping (best-effort H2H) ----
SPORTSBET_AFL = "https://www.sportsbet.com.au/betting/australian-rules/afl"
LADBROKES_AFL = "https://www.ladbrokes.com.au/sports/aussie-rules/afl"

PRICE_RE = re.compile(r"\$([0-9]+\.[0-9]{2})")

async def get_html(pw, url):
    browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = await browser.new_context(user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)")
    page = await ctx.new_page()
    try:
        await page.goto(url, timeout=45000)
        await page.wait_for_timeout(1500)
        html = await page.content()
    finally:
        await ctx.close(); await browser.close()
    return html

def parse_matches_generic(html, source_name):
    # Very generic: look for lines like "Team A vs Team B" and nearby $1.xx prices.
    # It’s not perfect, but Playwright ensures prices render.
    events = []
    # Find all “Something vs Something” lines
    for m in re.finditer(r"([A-Za-z\.\-\s]+)\s+vs\s+([A-Za-z\.\-\s]+)", html):
        a, b = m.group(1).strip(), m.group(2).strip()
        event = f"{a} vs {b}"
        # Look around that match chunk for two prices
        chunk = html[max(0, m.start()-1500): m.end()+1500]
        prices = [float(x) for x in PRICE_RE.findall(chunk)]
        # Take first two distinct odds as H2H
        uniq=[]
        for p in prices:
            if not uniq or abs(uniq[-1]-p)>1e-6:
                uniq.append(p)
        if len(uniq)>=2:
            events.append(
                {"event":event, "market":"H2H", "outcome":"Home", "odds":uniq[0], "bookie":source_name}
            )
            events.append(
                {"event":event, "market":"H2H", "outcome":"Away", "odds":uniq[1], "bookie":source_name}
            )
    return events

async def scrape_all():
    out=[]
    async with async_playwright() as pw:
        try:
            sb_html = await get_html(pw, SPORTSBET_AFL)
            out += parse_matches_generic(sb_html, "Sportsbet")
        except Exception as e:
            st.warning(f"Sportsbet fetch issue: {e}")
        try:
            lb_html = await get_html(pw, LADBROKES_AFL)
            out += parse_matches_generic(lb_html, "Ladbrokes")
        except Exception as e:
            st.warning(f"Ladbrokes fetch issue: {e}")
    return out

# ---- run scrape ----
st.info("Fetching real AFL odds… (if empty, tap the menu ⋮ → Rerun)")
rows = asyncio.run(scrape_all())
df = pd.DataFrame(rows)

if df.empty:
    st.error("No live odds parsed right now (site markup or throttle). Try Rerun once. If still empty, we’ll tweak selectors.")
    st.stop()

# ---- simple EV model for H2H (placeholder you can improve) ----
# Infer that the shorter price (between the two H2H odds for the same game) is the favourite.
# Assign base probs then clamp.
def base_probs_for_event(g):
    # g: dataframe rows for one event
    if len(g)<2: 
        return [0.5]*len(g)
    # pick min odds as fav
    fav_idx = g['odds'].idxmin()
    probs=[]
    for idx, r in g.iterrows():
        p = 0.58 if idx==fav_idx else 0.42
        probs.append(p)
    return probs

df['key'] = df['event'] + '|' + df['market']
probs=[]
for k,g in df.groupby('key'):
    probs += base_probs_for_event(g)
df['model_prob'] = probs
df['ev_percent'] = [round(ev_percent(p,o),2) for p,o in zip(df['model_prob'], df['odds'])]

# ---- arbitrage (2-way H2H across books) ----
import collections
arbs=[]
grouped = collections.defaultdict(list)
for r in df.to_dict(orient='records'):
    grouped[(r['event'],'H2H',None)].append(r)
for key, sub in grouped.items():
    home = [x for x in sub if x['outcome']=='Home']
    away = [x for x in sub if x['outcome']=='Away']
    if not home or not away: continue
    H = max(home, key=lambda x:x['odds'])
    A = max(away, key=lambda x:x['odds'])
    inv = (1/H['odds']) + (1/A['odds'])
    if inv < 1:
        margin = round((1-inv)*100,2)
        arbs.append({
            "event": key[0],
            "a_outcome":"Home","a_odds":H['odds'],"a_bookie":H['bookie'],
            "b_outcome":"Away","b_odds":A['odds'],"b_bookie":A['bookie'],
            "arb_margin_percent": margin
        })
arb_df = pd.DataFrame(sorted(arbs, key=lambda x:x['arb_margin_percent'], reverse=True))
if not arb_df.empty:
    total = bankroll*(max_stake_arb_pct/100.0)
    a_stake=[]; b_stake=[]
    for _,r in arb_df.iterrows():
        A,B = r['a_odds'], r['b_odds']
        aa = total*(1/A)/((1/A)+(1/B)); bb = total-aa
        a_stake.append(round(aa,2)); b_stake.append(round(bb,2))
    arb_df['stake_home']=a_stake; arb_df['stake_away']=b_stake; arb_df['stake_total']=round(total,2)
    arb_df = arb_df[arb_df['arb_margin_percent']>=min_arb]

st.subheader("📈 Arbitrage (H2H, ranked)")
st.dataframe(arb_df if not arb_df.empty else pd.DataFrame([{"status":"No arbs ≥ min margin right now"}]))

st.subheader("🎯 EV+ H2H")
ev_df = df[df['ev_percent']>=min_ev].copy()
if not ev_df.empty:
    ev_df['suggested_stake'] = round(bankroll*(unit_ev_pct/100.0),2)
    st.dataframe(ev_df[['event','outcome','bookie','odds','model_prob','ev_percent','suggested_stake']].sort_values('ev_percent', ascending=False))
else:
    st.write("No EV+ H2H bets ≥ your threshold yet.")
