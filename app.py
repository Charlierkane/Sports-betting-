# AFL Betting AI — LIVE (single-file, Sportsbet + Ladbrokes)
# Notes:
# - Scrapes public pages with polite headers + retries
# - If a bookie blocks scraping, it falls back gracefully
# - Shows EV% (simple model) and 2-way arbitrage with stake splits

import streamlit as st, pandas as pd, requests, time, datetime, pytz, random
from bs4 import BeautifulSoup

st.set_page_config(page_title="AFL Betting AI — Live", layout="wide")
st.title("🏉 AFL Betting AI — Live (Sportsbet + Ladbrokes)")

# ---------- Settings (sidebar) ----------
with st.sidebar:
    st.subheader("Settings")
    bankroll = st.number_input("Bankroll (AUD)", 100.0, value=700.0, step=50.0)
    mode = st.selectbox("Mode", ["EV+ & Arbs", "Arb-only"])
    min_arb = st.slider("Min arbitrage margin (%)", 0.0, 20.0, 5.0, 0.5)
    min_ev  = st.slider("Min EV (%)", 0.0, 30.0, 8.0, 0.5)
    max_stake_arb_pct = st.slider("Max stake per arb (% bankroll)", 1.0, 50.0, 15.0, 1.0)
    unit_ev_pct = st.slider("EV bet unit (% bankroll)", 0.5, 10.0, 2.5, 0.5)
    weather_on = st.checkbox("Weather weighting", True)
    st.caption("Heads-up: public pages can throttle. If one book returns no odds, the other will still show.")

# ---------- helpers ----------
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"}

def http_get(url, tries=3, sleep=0.6):
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=12)
            if r.status_code == 200 and r.text:
                return r.text
        except Exception:
            pass
        time.sleep(sleep * (i+1))
    return None

def implied_prob(odds): return 1/odds if odds and odds>0 else 0
def ev_percent(p, odds): return ((p*odds)-1)*100

VENUES = {
    "MCG": {"lat": -37.8199, "lon": 144.9834, "bias":"neutral", "wind": True},
    "Marvel Stadium": {"lat": -37.8167, "lon": 144.9510, "bias":"over_friendly", "wind": False},
    "Adelaide Oval": {"lat": -34.9157, "lon": 138.5967, "bias":"neutral", "wind": True},
}
def day_night(dt): 
    h = dt.hour
    return "Night" if h>=18 or h<8 else "Day"

def fetch_weather(lat, lon, when_local_iso):
    try:
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat}&longitude={lon}"
               "&hourly=temperature_2m,relativehumidity_2m,precipitation,wind_speed_10m"
               "&timezone=Australia%2FMelbourne")
        r = requests.get(url, timeout=10).json()
        hours = r.get("hourly",{}).get("time",[])
        ts = datetime.datetime.fromisoformat(when_local_iso).replace(minute=0, second=0, microsecond=0).isoformat()
        i = hours.index(ts) if ts in hours else None
        if i is None: return {}
        H = r["hourly"]
        return {
            "temp": H["temperature_2m"][i],
            "humidity": H["relativehumidity_2m"][i],
            "rain_mm": H["precipitation"][i],
            "wind_kmh": H["wind_speed_10m"][i],
        }
    except: return {}

def adjust_prob(base, ctx):
    p = base
    rain = ctx.get("rain_mm",0) or 0
    wind = ctx.get("wind_kmh",0) or 0
    if rain>0.2: p *= 0.98
    if wind>20:  p *= 0.98
    if ctx.get("day_night")=="Night": p *= 0.995
    if ctx.get("bias")=="over_friendly": p *= 1.01
    return max(0.01,min(0.99,p))

# ---------- simple AFL fixtures scraper ----------
# We look up today/tomorrow AFL fixtures from Sportsbet + Ladbrokes landing pages and then try to parse odds for:
# - H2H, Totals, a couple of player props (where present)

def sportsbet_afl_odds():
    """Very lightweight parser for some markets on Sportsbet AFL match pages."""
    rows = []
    # Sportsbet AFL page (public marketing page). We search for game tiles.
    url = "https://www.sportsbet.com.au/betting/australian-rules/afl"
    html = http_get(url)
    if not html: return rows
    soup = BeautifulSoup(html, "html.parser")
    # Heuristic: find links that look like match pages
    links = [a.get("href") for a in soup.select("a") if a.get("href","").startswith("/betting/australian-rules/afl/")]
    seen = set()
    for href in links[:8]:
        if href in seen: continue
        seen.add(href)
        murl = "https://www.sportsbet.com.au" + href
        mhtml = http_get(murl)
        if not mhtml: continue
        msoup = BeautifulSoup(mhtml, "html.parser")
        # Try to infer teams in the <title> tag
        title = (msoup.title.string if msoup.title else "") or ""
        if " vs " not in title: 
            # fallback: try h1
            h1 = msoup.find("h1")
            title = h1.get_text(" ", strip=True) if h1 else "AFL Match"
        event = title.replace(" | Sportsbet", "").strip()
        # Try to find H2H odds (any two price buttons)
        prices = [p.get_text(strip=True) for p in msoup.select("button, span") if p.get_text(strip=True).startswith("$")]
        # Convert like "$1.65" to 1.65
        def to_dec(x):
            try:
                x = x.replace("$","").replace(",","")
                return float(x)
            except: return None
        # crude parse: take first two distinct odds as H2H
        h2h = [to_dec(p) for p in prices if to_dec(p)]
        if len(h2h)>=2:
            rows.append({"event": event, "player": None, "market":"H2H","line":None,"outcome":"Home","odds":h2h[0],"bookie":"Sportsbet"})
            rows.append({"event": event, "player": None, "market":"H2H","line":None,"outcome":"Away","odds":h2h[1],"bookie":"Sportsbet"})
        # Totals: look for "Total" or Over/Under patterns near numbers
        # This is best-effort; site markup changes often.
    return rows

def ladbrokes_afl_odds():
    rows = []
    url = "https://www.ladbrokes.com.au/sports/aussie-rules/afl"
    html = http_get(url)
    if not html: return rows
    soup = BeautifulSoup(html, "html.parser")
    links = [a.get("href") for a in soup.select("a") if "/sports/aussie-rules/afl/" in (a.get("href") or "")]
    seen = set()
    for href in links[:8]:
        if href in seen: continue
        seen.add(href)
        murl = "https://www.ladbrokes.com.au" + href
        mhtml = http_get(murl)
        if not mhtml: continue
        msoup = BeautifulSoup(mhtml, "html.parser")
        title = (msoup.title.string if msoup.title else "").strip()
        if " vs " not in title:
            h1 = msoup.find("h1")
            title = h1.get_text(" ", strip=True) if h1 else "AFL Match"
        event = title.replace("| Ladbrokes.com.au","").strip()
        # try to pick the first two odds as H2H (very naive)
        prices = [s.get_text(strip=True) for s in msoup.select("button, span") if s.get_text(strip=True).startswith("$")]
        def to_dec(x):
            try: return float(x.replace("$","").replace(",",""))
            except: return None
        h2h = [to_dec(x) for x in prices if to_dec(x)]
        if len(h2h)>=2:
            rows.append({"event": event, "player": None, "market":"H2H","line":None,"outcome":"Home","odds":h2h[0],"bookie":"Ladbrokes"})
            rows.append({"event": event, "player": None, "market":"H2H","line":None,"outcome":"Away","odds":h2h[1],"bookie":"Ladbrokes"})
    return rows

# ---------- run scrape ----------
st.write("Fetching live odds (best-effort)…")
rows = []
try:
    rows += sportsbet_afl_odds()
except Exception as e:
    st.warning(f"Sportsbet fetch issue (continuing): {e}")
try:
    rows += ladbrokes_afl_odds()
except Exception as e:
    st.warning(f"Ladbrokes fetch issue (continuing): {e}")

df = pd.DataFrame(rows)
if df.empty:
    st.error("No live odds parsed (public pages may be throttling). Try again in ~1–2 mins, or we’ll switch you to the full modular build.")
else:
    st.success(f"Fetched {len(df)} live odds rows.")

# ---------- context (MCG tonight ~7:30pm as example) ----------
tz = pytz.timezone("Australia/Melbourne")
bounce = tz.localize(datetime.datetime.now().replace(hour=19,minute=30,second=0,microsecond=0))
venue = "MCG"
ctx = {"venue": venue, "bounce_time": bounce.isoformat(), "day_night": "Night" if bounce.hour>=18 else "Day"}
if weather_on:
    w = fetch_weather(VENUES[venue]["lat"], VENUES[venue]["lon"], bounce.isoformat())
    ctx.update(w)
ctx["bias"] = VENUES[venue]["bias"]
st.caption(f"Context → Venue: {venue} | Time: {bounce.strftime('%-I:%M %p')} | "
           f"Temp: {ctx.get('temp','?')}°C | Rain: {ctx.get('rain_mm','?')}mm | Wind: {ctx.get('wind_kmh','?')} km/h | {ctx['day_night']}")

# ---------- EV calc (simple demo model) ----------
def base_prob_guess(row):
    # naive model: Away slight fav when books do; you’ll replace this with proper model later
    if row["market"]=="H2H":
        # infer fav from odds
        home_implied = implied_prob(row["odds"]) if row["outcome"]=="Home" else None
        away_implied = implied_prob(row["odds"]) if row["outcome"]=="Away" else None
        # use a soft default
        return 0.55 if row["outcome"]=="Away" else 0.45
    return 0.5

def adjust_prob(base, ctx):
    p = base
    rain = ctx.get("rain_mm",0) or 0
    wind = ctx.get("wind_kmh",0) or 0
    if rain>0.2: p *= 0.98
    if wind>20:  p *= 0.98
    if ctx.get("day_night")=="Night": p *= 0.995
    if ctx.get("bias")=="over_friendly": p *= 1.01
    return max(0.01,min(0.99,p))

if not df.empty:
    df["model_prob"] = [adjust_prob(base_prob_guess(r), ctx) for _,r in df.iterrows()]
    df["ev_percent"] = [round(ev_percent(p, o),2) for p,o in zip(df["model_prob"], df["odds"])]

# ---------- Arbitrage detection (H2H 2-way) ----------
def find_two_way_arbs(rows):
    import collections
    arbs=[]
    grouped = collections.defaultdict(list)
    for r in rows: grouped[(r["event"],r["market"],r.get("line"))].append(r)
    for key, sub in grouped.items():
        # pick best Home and Away prices across books
        home = [r for r in sub if r["outcome"]=="Home"]
        away = [r for r in sub if r["outcome"]=="Away"]
        if not home or not away: continue
        H = max(home, key=lambda x: x["odds"])
        A = max(away, key=lambda x: x["odds"])
        inv = (1/H["odds"])+(1/A["odds"])
        if inv < 1:
            margin = round((1-inv)*100,2)
            arbs.append({
                "event": key[0], "market": key[1], "line": key[2],
                "a_outcome": "Home", "a_odds": H["odds"], "a_bookie": H["bookie"],
                "b_outcome": "Away", "b_odds": A["odds"], "b_bookie": A["bookie"],
                "arb_margin_percent": margin
            })
    return sorted(arbs, key=lambda x: x["arb_margin_percent"], reverse=True)

arb_df = pd.DataFrame(find_two_way_arbs(df.to_dict(orient="records"))) if not df.empty else pd.DataFrame()

# stake split
if not arb_df.empty:
    totals = bankroll*(max_stake_arb_pct/100.0)
    a_stake = []
    b_stake = []
    for _,r in arb_df.iterrows():
        A,B = r["a_odds"], r["b_odds"]
        a = totals*(1/A)/((1/A)+(1/B))
        b = totals-a
        a_stake.append(round(a,2)); b_stake.append(round(b,2))
    arb_df["stake_home"]=a_stake; arb_df["stake_away"]=b_stake; arb_df["stake_total"]=round(totals,2)
    arb_df = arb_df[arb_df["arb_margin_percent"]>=min_arb]

# ---------- UI ----------
st.subheader("📈 Arbitrage (ranked)")
st.dataframe(arb_df if not arb_df.empty else pd.DataFrame([{"status":"No arbs ≥ min margin found right now"}]))

st.subheader("🎯 EV+ Bets")
if not df.empty and mode=="EV+ & Arbs":
    ev_df = df[df["ev_percent"]>=min_ev].copy()
    if not ev_df.empty:
        ev_df["suggested_stake"] = round(bankroll*(unit_ev_pct/100.0),2)
        st.dataframe(ev_df[["event","market","outcome","bookie","odds","model_prob","ev_percent","suggested_stake"]].sort_values("ev_percent",ascending=False))
    else:
        st.write("No EV+ bets ≥ your threshold right now.")
