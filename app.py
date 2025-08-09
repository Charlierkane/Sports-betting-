streamlit single-file AFL Betting AI (EV+ & Arbs) — no folders needed
import streamlit as st, pandas as pd, requests, datetime, pytz

st.set_page_config(page_title="AFL Betting AI", layout="wide")
st.title("🏉 AFL Betting AI — EV+ & Arbitrage (single-file demo)")

# -------- helpers --------
def implied_prob(odds): return 1/odds if odds and odds>0 else 0
def ev_percent(p, odds): return ((p*odds)-1)*100

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

VENUES = {
    "MCG": {"lat": -37.8199, "lon": 144.9834, "bias":"neutral", "wind": True},
    "Marvel Stadium": {"lat": -37.8167, "lon": 144.9510, "bias":"over_friendly", "wind": False},
    "Adelaide Oval": {"lat": -34.9157, "lon": 138.5967, "bias":"neutral", "wind": True},
}

def day_night(dt): 
    h = dt.hour
    return "Night" if h>=18 or h<8 else "Day"

def adjust_prob(base, ctx):
    p = base
    rain = ctx.get("rain_mm",0) or 0
    wind = ctx.get("wind_kmh",0) or 0
    if rain>0.2: p *= 0.98
    if wind>20:  p *= 0.98
    if ctx.get("day_night")=="Night": p *= 0.995
    if ctx.get("bias")=="over_friendly": p *= 1.01
    return max(0.01,min(0.99,p))

# -------- sidebar --------
with st.sidebar:
    st.subheader("Settings")
    bankroll = st.number_input("Bankroll (AUD)", 100.0, value=700.0, step=50.0)
    mode = st.selectbox("Mode", ["EV+ & Arbs", "Arb-only"])
    min_arb = st.slider("Min arbitrage margin (%)", 0.0, 20.0, 5.0, 0.5)
    min_ev  = st.slider("Min EV (%)", 0.0, 30.0, 8.0, 0.5)
    max_stake_arb_pct = st.slider("Max stake per arb (% bankroll)", 1.0, 50.0, 15.0, 1.0)
    unit_ev_pct = st.slider("EV bet unit (% bankroll)", 0.5, 10.0, 2.5, 0.5)
    weather_on = st.checkbox("Weather weighting", True)
    st.caption("This demo uses sample odds so it runs anywhere. We’ll swap to real scrapers next.")

# -------- sample odds (keeps app working). Real scrapers come next. --------
now = datetime.datetime.now().isoformat()
sample = [
    # Disposals Over/Under
    {"event":"Hawthorn vs Collingwood","player":"Nick Daicos","market":"Disposals","line":30.5,"outcome":"Over","odds":1.85,"bookie":"Sportsbet","ts":now},
    {"event":"Hawthorn vs Collingwood","player":"Nick Daicos","market":"Disposals","line":30.5,"outcome":"Under","odds":1.95,"bookie":"Sportsbet","ts":now},
    {"event":"Hawthorn vs Collingwood","player":"Nick Daicos","market":"Disposals","line":30.5,"outcome":"Over","odds":1.92,"bookie":"Ladbrokes","ts":now},
    {"event":"Hawthorn vs Collingwood","player":"Nick Daicos","market":"Disposals","line":30.5,"outcome":"Under","odds":1.88,"bookie":"Ladbrokes","ts":now},
    {"event":"Hawthorn vs Collingwood","player":"Nick Daicos","market":"Disposals","line":30.5,"outcome":"Over","odds":1.90,"bookie":"TAB","ts":now},
    {"event":"Hawthorn vs Collingwood","player":"Nick Daicos","market":"Disposals","line":30.5,"outcome":"Under","odds":1.90,"bookie":"TAB","ts":now},
    # H2H
    {"event":"Hawthorn vs Collingwood","player":None,"market":"H2H","line":None,"outcome":"Home","odds":2.35,"bookie":"Sportsbet","ts":now},
    {"event":"Hawthorn vs Collingwood","player":None,"market":"H2H","line":None,"outcome":"Away","odds":1.62,"bookie":"Sportsbet","ts":now},
    {"event":"Hawthorn vs Collingwood","player":None,"market":"H2H","line":None,"outcome":"Home","odds":2.30,"bookie":"Ladbrokes","ts":now},
    {"event":"Hawthorn vs Collingwood","player":None,"market":"H2H","line":None,"outcome":"Away","odds":1.65,"bookie":"Ladbrokes","ts":now},
    {"event":"Hawthorn vs Collingwood","player":None,"market":"H2H","line":None,"outcome":"Home","odds":2.28,"bookie":"TAB","ts":now},
    {"event":"Hawthorn vs Collingwood","player":None,"market":"H2H","line":None,"outcome":"Away","odds":1.66,"bookie":"TAB","ts":now},
    # Total
    {"event":"Hawthorn vs Collingwood","player":None,"market":"Total","line":165.5,"outcome":"Over","odds":1.91,"bookie":"Sportsbet","ts":now},
    {"event":"Hawthorn vs Collingwood","player":None,"market":"Total","line":165.5,"outcome":"Under","odds":1.91,"bookie":"Sportsbet","ts":now},
    {"event":"Hawthorn vs Collingwood","player":None,"market":"Total","line":165.5,"outcome":"Over","odds":1.95,"bookie":"Ladbrokes","ts":now},
    {"event":"Hawthorn vs Collingwood","player":None,"market":"Total","line":165.5,"outcome":"Under","odds":1.87,"bookie":"Ladbrokes","ts":now},
    {"event":"Hawthorn vs Collingwood","player":None,"market":"Total","line":165.5,"outcome":"Over","odds":1.89,"bookie":"TAB","ts":now},
    {"event":"Hawthorn vs Collingwood","player":None,"market":"Total","line":165.5,"outcome":"Under","odds":1.93,"bookie":"TAB","ts":now},
]
df = pd.DataFrame(sample)

# -------- context (MCG tonight 19:30 AEST) --------
tz = pytz.timezone("Australia/Melbourne")
bounce = tz.localize(datetime.datetime.now().replace(hour=19,minute=30,second=0,microsecond=0))
venue = "MCG"
ctx = {"venue": venue, "bounce_time": bounce.isoformat(), "day_night": day_night(bounce)}
if weather_on:
    w = fetch_weather(VENUES[venue]["lat"], VENUES[venue]["lon"], bounce.isoformat())
    ctx.update(w)
ctx["bias"] = VENUES[venue]["bias"]
st.caption(f"Context → Venue: {venue} | Time: {bounce.strftime('%-I:%M %p')} | "
           f"Temp: {ctx.get('temp','?')}°C | Rain: {ctx.get('rain_mm','?')}mm | Wind: {ctx.get('wind_kmh','?')} km/h | {ctx['day_night']}")

# -------- EV calc (simple demo model) --------
def base_prob_guess(row):
    if row["market"]=="H2H":   return 0.6 if row["outcome"]=="Away" else 0.4
    if row["market"]=="Total": return 0.5
    if row["market"] in ["Disposals","Goals"]: return 0.62
    return 0.5

df["model_prob"] = [adjust_prob(base_prob_guess(r), ctx) for _,r in df.iterrows()]
df["ev_percent"] = [round(ev_percent(p, o),2) for p,o in zip(df["model_prob"], df["odds"])]

# -------- Arbitrage (2-way) --------
def find_two_way_arbs(rows):
    import itertools, collections
    arbs=[]
    keycols=["event","market","line"]
    # group by (event, market, line)
    grouped = collections.defaultdict(list)
    for r in rows: grouped[(r["event"],r["market"],r["line"])].append(r)
    for key, sub in grouped.items():
        # try Over/Under and Home/Away pairs
        def best(outcome):
            c = [r for r in sub if r["outcome"]==outcome]
            return max(c, key=lambda x: x["odds"]) if c else None
        for a,b in [("Over","Under"),("Home","Away")]:
            A,B = best(a), best(b)
            if A and B:
                inv = (1/A["odds"])+(1/B["odds"])
                if inv<1:
                    margin = round((1-inv)*100,2)
                    arbs.append({
                        "event":key[0],"market":key[1],"line":key[2],
                        "a_outcome":a,"a_odds":A["odds"],"a_bookie":A["bookie"],
                        "b_outcome":b,"b_odds":B["odds"],"b_bookie":B["bookie"],
                        "arb_margin_percent":margin
                    })
    return sorted(arbs, key=lambda x: x["arb_margin_percent"], reverse=True)

arbs = pd.DataFrame(find_two_way_arbs(df.to_dict(orient="records")))
if not arbs.empty:
    # stake split
    totals = bankroll*(max_stake_arb_pct/100.0)
    a_stake = []
    b_stake = []
    for _,r in arbs.iterrows():
        A,B = r["a_odds"], r["b_odds"]
        a = totals*(1/A)/((1/A)+(1/B))
        b = totals-a
        a_stake.append(round(a,2)); b_stake.append(round(b,2))
    arbs["stake_a"]=a_stake; arbs["stake_b"]=b_stake; arbs["stake_total"]=round(totals,2)
    arbs = arbs[arbs["arb_margin_percent"]>=min_arb]

st.subheader("📈 Arbitrage (ranked)")
st.dataframe(arbs if not arbs.empty else pd.DataFrame([{"status":"No arbs ≥ min margin (demo data)"}]))

st.subheader("🎯 EV+ Bets")
ev_df = df[df["ev_percent"]>=min_ev].copy() if mode=="EV+ & Arbs" else pd.DataFrame(columns=df.columns)
if not ev_df.empty:
    ev_df["suggested_stake"] = round(bankroll*(unit_ev_pct/100.0),2)
    st.dataframe(ev_df[["event","player","market","line","outcome","bookie","odds","model_prob","ev_percent","suggested_stake"]].sort_values("ev_percent",ascending=False))
else:
    st.write("No EV+ bets ≥ your threshold yet (demo).")
