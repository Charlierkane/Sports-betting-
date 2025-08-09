# AFL Betting AI — Live (Sportsbet + Ladbrokes + TAB)
# Streamlit + Playwright (Chromium) with stealth flags for Render/Docker.
# Shows EV% and two-way arbitrage opportunities for H2H markets.

import streamlit as st
import pandas as pd
import asyncio
import datetime as dt
import pytz
import collections
import re
from playwright.async_api import async_playwright

# -------------------- UI CONFIG --------------------
st.set_page_config(page_title="AFL Betting AI — Live", layout="wide")
st.title("🏉 AFL Betting AI — Live (Playwright, Sportsbet + Ladbrokes + TAB)")

with st.sidebar:
    st.markdown("### Settings")
    bankroll = st.number_input("Bankroll (AUD)", 100.0, value=700.0, step=50.0)
    min_arb = st.slider("Min arbitrage margin (%)", 0.0, 20.0, 3.0, 0.5)
    min_ev = st.slider("Min EV (%)", 0.0, 30.0, 6.0, 0.5)
    unit_ev_pct = st.slider("EV bet unit (% bankroll)", 0.5, 10.0, 2.5, 0.5)
    max_stake_arb_pct = st.slider("Max stake per arb (% bankroll)", 1.0, 50.0, 15.0, 1.0)
    st.markdown("---")
    debug = st.toggle("Show debug info", value=False)
    desktop_ua = st.toggle("Use desktop site (often parses better)", value=True)

AFL_SPORTSBET  = "https://www.sportsbet.com.au/betting/australian-rules/afl"
AFL_LADBROKES  = "https://www.ladbrokes.com.au/sports/aussie-rules/afl"
AFL_TAB        = "https://www.tab.com.au/sports/betting/Australian-Rules/AFL"

# -------------------- UTILITIES --------------------
def ev_percent(p: float, odds: float) -> float:
    """Expected value in % given win prob p and decimal odds."""
    if odds <= 0:
        return -999.0
    return ((p * odds) - 1.0) * 100.0

VS_RE    = re.compile(r"([A-Za-z\.\- &/]+?)\s+(?:vs|v)\s+([A-Za-z\.\- &/]+?)", re.IGNORECASE)
PRICE_RE = re.compile(r"\$([0-9]+(?:\.[0-9]{2})?)")

# -------------------- PLAYWRIGHT SCRAPING --------------------
async def get_payload(pw, url: str):
    """Load page with stealth flags, dismiss cookie banners, return innerText + HTML."""
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )

    ua_mobile = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    )
    ua_desktop = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    ctx = await browser.new_context(
        user_agent=ua_desktop if desktop_ua else ua_mobile,
        viewport={"width": 1280, "height": 900} if desktop_ua else {"width": 390, "height": 844},
        locale="en-AU",
        timezone_id="Australia/Melbourne",
    )
    page = await ctx.new_page()
    try:
        await page.route("**/*", lambda r: r.continue_())
        await page.goto(url, wait_until="networkidle", timeout=90000)

        # Dismiss common cookie/consent buttons
        selectors = [
            'button:has-text("Accept")', 'button:has-text("I Accept")', 'button:has-text("I Agree")',
            '#onetrust-accept-btn-handler', '[data-testid="accept-all"]', 'button[aria-label="Accept all"]'
        ]
        for sel in selectors:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click(timeout=1000)
            except Exception:
                pass

        # Trigger lazy loads / hydration
        for _ in range(6):
            await page.mouse.wheel(0, 1200)
            await page.wait_for_timeout(700)
        await page.wait_for_timeout(1200)

        text = await page.evaluate("document.body.innerText")
        html = await page.content()

        if debug:
            st.caption(f"Fetched: {url}")
            st.caption(f"innerText len={len(text):,} | html len={len(html):,}")
            st.text(text[:1200])
    finally:
        await ctx.close()
        await browser.close()
    return {"text": text, "html": html}

def parse_matches_generic(payload: dict, source_name: str):
    """
    Best-effort parser for H2H tiles. Look for 'Team vs Team' (or 'v') and two nearby $X.XX prices.
    Uses visible text first, falls back to HTML.
    """
    text = payload.get("text") or ""
    html = payload.get("html") or ""
    bag  = text if text.count("$") >= 2 else (html or "")

    rows = []
    for m in VS_RE.finditer(bag):
        home, away = m.group(1).strip(), m.group(2).strip()
        event = f"{home} vs {away}"
        start = max(0, m.start() - 2400)
        end   = min(len(bag), m.end() + 2400)
        window = bag[start:end]
        prices = [float(x) for x in PRICE_RE.findall(window)]

        # de-dup while preserving order
        uniq = []
        for p in prices:
            if not uniq or abs(uniq[-1] - p) > 1e-9:
                uniq.append(p)

        if len(uniq) >= 2:
            rows.append({"event": event, "market": "H2H", "outcome": "Home", "odds": uniq[0], "bookie": source_name})
            rows.append({"event": event, "market": "H2H", "outcome": "Away", "odds": uniq[1], "bookie": source_name})
    return rows

async def scrape_all():
    out = []
    async with async_playwright() as pw:
        # Sportsbet
        try:
            sb = await get_payload(pw, AFL_SPORTSBET)
            out += parse_matches_generic(sb, "Sportsbet")
        except Exception as e:
            if debug: st.warning(f"Sportsbet fetch error: {e}")

        # Ladbrokes
        try:
            lb = await get_payload(pw, AFL_LADBROKES)
            out += parse_matches_generic(lb, "Ladbrokes")
        except Exception as e:
            if debug: st.warning(f"Ladbrokes fetch error: {e}")

        # TAB (often simpler)
        try:
            tb = await get_payload(pw, AFL_TAB)
            out += parse_matches_generic(tb, "TAB")
        except Exception as e:
            if debug: st.warning(f"TAB fetch error: {e}")
    return out

# -------------------- RUN SCRAPE --------------------
st.info("Fetching real AFL odds… (if empty, tap ⋮ → Rerun once)")
rows = asyncio.run(scrape_all())
df = pd.DataFrame(rows)

if df.empty:
    st.error("No live odds parsed right now. Sites can throttle or change markup. Try ⋮ → Rerun once; if still empty, tell me and I’ll tighten selectors.")
    st.stop()

# -------------------- SIMPLE MODEL + EV --------------------
# Placeholder H2H model: shorter price gets 0.58 win prob, other 0.42
df["key"] = df["event"] + "|" + df["market"]

def probs_for_group(g: pd.DataFrame):
    if len(g) < 2:
        return [0.5] * len(g)
    fav_idx = g["odds"].idxmin()
    return [0.58 if i == fav_idx else 0.42 for i in g.index]

df["model_prob"]  = sum((probs_for_group(g) for _, g in df.groupby("key")), [])
df["ev_percent"]  = [round(ev_percent(p, o), 2) for p, o in zip(df["model_prob"], df["odds"])]

# -------------------- ARBITRAGE (2-way H2H) --------------------
arbs = []
grouped = collections.defaultdict(list)
for r in df.to_dict(orient="records"):
    grouped[(r["event"], "H2H")].append(r)

for key, sub in grouped.items():
    home = [x for x in sub if x["outcome"] == "Home"]
    away = [x for x in sub if x["outcome"] == "Away"]
    if not home or not away:
        continue
    H = max(home, key=lambda x: x["odds"])
    A = max(away, key=lambda x: x["odds"])
    inv = (1.0 / H["odds"]) + (1.0 / A["odds"])
    if inv < 1.0:
        margin = round((1.0 - inv) * 100.0, 2)
        arbs.append({
            "event": key[0],
            "a_outcome": "Home", "a_odds": H["odds"], "a_bookie": H["bookie"],
            "b_outcome": "Away", "b_odds": A["odds"], "b_bookie": A["bookie"],
            "arb_margin_percent": margin
        })

arb_df = pd.DataFrame(sorted(arbs, key=lambda x: x["arb_margin_percent"], reverse=True))
if not arb_df.empty:
    total = bankroll * (max_stake_arb_pct / 100.0)
    a_stake, b_stake = [], []
    for _, r in arb_df.iterrows():
        A, B = r["a_odds"], r["b_odds"]
        stake_a = total * (1 / A) / ((1 / A) + (1 / B))
        stake_b = total - stake_a
        a_stake.append(round(stake_a, 2))
        b_stake.append(round(stake_b, 2))
    arb_df["stake_home"] = a_stake
    arb_df["stake_away"] = b_stake
    arb_df["stake_total"] = round(total, 2)
    arb_df = arb_df[arb_df["arb_margin_percent"] >= min_arb]

# -------------------- DISPLAY --------------------
tz = pytz.timezone("Australia/Melbourne")
st.caption(
    f"Context → Time: {dt.datetime.now(tz).strftime('%-I:%M %p')} | Sources: Sportsbet, Ladbrokes, TAB | Market: H2H"
)

st.subheader("📈 Arbitrage (H2H, ranked)")
st.dataframe(
    arb_df if not arb_df.empty else pd.DataFrame([{"status": "No arbs ≥ min margin right now"}]),
    use_container_width=True,
)

st.subheader("🎯 EV+ H2H")
ev_df = df[df["ev_percent"] >= min_ev].copy()
if not ev_df.empty:
    ev_df["suggested_stake"] = round(bankroll * (unit_ev_pct / 100.0), 2)
    cols = ["event", "outcome", "bookie", "odds", "model_prob", "ev_percent", "suggested_stake"]
    st.dataframe(ev_df[cols].sort_values("ev_percent", ascending=False), use_container_width=True)
else:
    st.write("No EV+ H2H bets ≥ your threshold yet.")
