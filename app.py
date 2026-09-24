"""
EIA Energy Dashboard — Natural Gas & Biofuels
Natural gas production, storage, prices, consumption + fuel ethanol,
biodiesel/renewable diesel capacity and feedstock usage.

John Stewart & Associates
Data source: U.S. EIA Open Data API v2 (https://api.eia.gov/v2)
"""

import base64
import os
import re
from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from eia_ethanol_cache_client import fetch_ethanol_cached, cache_freshness as ethanol_cache_freshness

# ── Theme (light, matching the other JSA dashboards) ──────────────────────────
DM_BG        = "#f7f9fa"
DM_SURFACE   = "#ffffff"
DM_SURFACE2  = "#f0f2f4"
DM_BORDER    = "#dde3e8"
DM_TEXT      = "#1f2328"
DM_MUTED     = "#6b7280"

JSA_BLUE     = "#0693e3"
COL_NATGAS   = "#3fa9f5"
COL_ETHANOL  = "#f2b134"
COL_BIODSL   = "#5ec48c"
COL_REDSL    = "#8db8e0"
POS          = "#5ec48c"
NEG          = "#e0716f"

REGION_COLORS = {
    "Lower 48 States (Total)": "#3fa9f5",
    "East":          "#f2b134",
    "Midwest":       "#5ec48c",
    "South Central": "#e0716f",
    "Mountain":      "#8db8e0",
    "Pacific":       "#c792ea",
}

FAMILY_COLORS = {
    "Vegetable Oils":         "#5ec48c",
    "Animal Fats & Greases":  "#f2b134",
    "Other Feedstocks":       "#8db8e0",
}

PLANT_FUEL_COLORS = {
    "Natural Gas":        "#3fa9f5",
    "Biogas":             "#5ec48c",
    "Liquefied Petroleum Gases": "#f2b134",
    "Purchased Electricity": "#8db8e0",
    "Coal":               "#e0716f",
    "Purchased Steam":    "#c792ea",
    "Purchased Hydrogen": "#84939f",
    "Other fuels including renewable fuels": "#5a6a78",
    "Natural Gas for Hydrogen Feedstock": "#1f6fb2",
}

JSA_LOGO_FULL = "https://www.jpsi.com/wp-content/themes/gate39media/img/logo-full.png"


@st.cache_data
def _asset_uri(filename: str) -> str:
    p = os.path.join(os.path.dirname(__file__), "assets", filename)
    try:
        with open(p, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except OSError:
        return ""


WATERMARK = _asset_uri("jsa_50yr.png")

st.set_page_config(
    page_title="JSA EIA Energy Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(f"""
<style>
  html, body, [data-testid="stAppViewContainer"] {{
      background-color: {DM_BG};
      color: {DM_TEXT};
  }}
  [data-testid="stSidebar"] {{
      background-color: {DM_SURFACE};
      border-right: 1px solid {DM_BORDER};
  }}
  [data-testid="stSidebar"] * {{ color: {DM_TEXT} !important; }}
  [data-testid="stMetric"] {{
      background: {DM_SURFACE};
      border: 1px solid {DM_BORDER};
      border-radius: 8px;
      padding: 12px 16px;
  }}
  [data-testid="stMetricLabel"] {{ color: {DM_MUTED} !important; font-size: 0.78rem !important; }}
  [data-testid="stMetricValue"] {{ color: {DM_TEXT} !important; font-size: 1.35rem !important; }}
  [data-testid="stMetricDelta"] {{ font-size: 0.78rem !important; }}
  .stTabs [data-baseweb="tab-list"] {{
      background: {DM_SURFACE};
      border-bottom: 1px solid {DM_BORDER};
      gap: 4px;
  }}
  .stTabs [data-baseweb="tab"] {{
      color: {DM_MUTED};
      background: transparent;
      border-radius: 6px 6px 0 0;
      padding: 8px 20px;
  }}
  .stTabs [aria-selected="true"] {{
      color: {DM_TEXT} !important;
      background: {DM_SURFACE2} !important;
      border-bottom: 2px solid {JSA_BLUE} !important;
  }}
  div[data-testid="stSelectbox"] > div,
  div[data-testid="stMultiSelect"] > div {{
      background: {DM_SURFACE2};
      border-color: {DM_BORDER};
      color: {DM_TEXT};
  }}
  .stDataFrame, [data-testid="stTable"] {{
      background: {DM_SURFACE};
      border: 1px solid {DM_BORDER};
      border-radius: 6px;
  }}
  h1, h2, h3 {{ color: {DM_TEXT}; }}
  .section-header {{
      font-size: 0.75rem;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: {DM_MUTED};
      margin: 20px 0 6px 0;
  }}
  .note-text {{ color: {DM_MUTED}; font-size: 0.78rem; }}

  /* JSA watermark on charts and snapshot tables. Plotly draws an opaque
     background rect in its own SVG, which paints over anything placed
     behind it (a ::before at z-index:0) — so, same as the tables below,
     the watermark has to sit ON TOP via ::after at low opacity rather
     than behind the chart. */
  [data-testid="stPlotlyChart"] {{ position: relative; }}
  [data-testid="stPlotlyChart"]::after {{
      content: ""; position: absolute; inset: 0;
      background: url('{WATERMARK}') center 50% / 28% auto no-repeat;
      opacity: 0.12; pointer-events: none; z-index: 5;
  }}
  table[id^="snap_"] {{ position: relative; }}
  table[id^="snap_"]::after {{
      content: ""; position: absolute; inset: 0;
      background: url('{WATERMARK}') center 50% / 24% auto no-repeat;
      opacity: 0.09; pointer-events: none; z-index: 5;
  }}
</style>
""", unsafe_allow_html=True)

# ── API key ───────────────────────────────────────────────────────────────────
try:
    API_KEY = st.secrets.get("EIA_API_KEY", "")
except Exception:
    API_KEY = ""
API_KEY = API_KEY or os.environ.get("EIA_API_KEY", "")

BASE_URL = "https://api.eia.gov/v2"

if not API_KEY:
    st.error("No EIA_API_KEY found in Streamlit secrets or environment. Add it to .streamlit/secrets.toml.")
    st.stop()

# ── HTTP session ─────────────────────────────────────────────────────────────
@st.cache_resource
def get_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

SESSION = get_session()


def _facet_params(facets: dict) -> list:
    out = []
    for key, vals in facets.items():
        if isinstance(vals, str):
            vals = [vals]
        for v in vals:
            out.append((f"facets[{key}][]", v))
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def eia_get(route: str, facets: dict | None = None, start: str | None = None,
            end: str | None = None, max_rows: int = 30000) -> pd.DataFrame:
    """Fetch every row for a route/facet combo from the EIA v2 API, paginating as needed."""
    params_base = [("api_key", API_KEY), ("data[0]", "value"),
                   ("sort[0][column]", "period"), ("sort[0][direction]", "desc")]
    if facets:
        params_base += _facet_params(facets)
    if start:
        params_base.append(("start", start))
    if end:
        params_base.append(("end", end))

    length = 5000
    offset = 0
    rows = []
    while True:
        params = params_base + [("offset", offset), ("length", length)]
        try:
            r = SESSION.get(f"{BASE_URL}/{route}/data/", params=params, timeout=30)
            r.raise_for_status()
            js = r.json()
        except Exception as e:
            st.warning(f"EIA API error on {route}: {e}")
            break
        data = js.get("response", {}).get("data", [])
        rows.extend(data)
        total = int(js.get("response", {}).get("total", len(rows)) or 0)
        offset += length
        if len(data) < length or offset >= total or len(rows) >= max_rows:
            break

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["period"] = pd.to_datetime(df["period"])
    return df.sort_values("period").reset_index(drop=True)


# ── Formatting helpers ────────────────────────────────────────────────────────
def fmt_num(v, decimals=1):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:,.{decimals}f}"


def latest_and_delta(df: pd.DataFrame, n_back: int = 1):
    """Latest value plus the change vs n_back periods prior."""
    if df.empty or len(df) <= n_back:
        return None, None, None
    s = df.sort_values("period")["value"].dropna()
    if len(s) <= n_back:
        return None, None, None
    latest = s.iloc[-1]
    prior = s.iloc[-1 - n_back]
    delta = latest - prior
    pct = (delta / prior * 100) if prior else None
    return latest, delta, pct


def line_fig(df, x, y, name, color, yaxis_title="", hover_fmt=",.2f"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df[x], y=df[y], mode="lines", name=name,
                              line=dict(color=color, width=2),
                              hovertemplate=f"%{{x|%b %d, %Y}}<br>%{{y:{hover_fmt}}}<extra></extra>"))
    style_fig(fig, yaxis_title)
    return fig


def style_fig(fig, yaxis_title="", legend=True):
    fig.update_layout(
        plot_bgcolor=DM_SURFACE, paper_bgcolor=DM_SURFACE,
        font=dict(color=DM_TEXT, size=12),
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis=dict(gridcolor=DM_BORDER, showgrid=True, zeroline=False),
        yaxis=dict(gridcolor=DM_BORDER, showgrid=True, zeroline=False, title=yaxis_title),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    bgcolor="rgba(0,0,0,0)") if legend else None,
        height=420,
        hovermode="x unified",
    )


def section_header(title: str):
    st.markdown(f"<div class='section-header'>{title}</div>", unsafe_allow_html=True)


# ── Chart/table snapshot toolbar (PNG download + clipboard copy) ─────────────
# Client-side html2canvas screenshot of the actual rendered element — a true
# on-screen snapshot rather than a server-rebuilt image. Each toolbar instance
# is namespaced by snap_id (button/message ids) and wrapped in an IIFE so
# multiple instances on one page don't collide.
def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", str(name)).strip() or "chart"


def _snap_anchor(snap_id: str):
    """Zero-height marker so a following Plotly chart can be found by the
    snapshot tool (st.plotly_chart can't carry an id of its own)."""
    st.markdown(f'<div id="{snap_id}" style="height:0"></div>', unsafe_allow_html=True)


_SNAP_JS = """
<div style="display:flex;gap:6px;align-items:center;margin:2px 0 14px 0">
  <button id="dl__ID__" style="font:600 13px system-ui,sans-serif;color:#fff;
    background:__ACCENT__;border:none;border-radius:6px;padding:6px 12px;
    cursor:pointer">\U0001F4E5 PNG</button>
  <button id="cp__ID__" style="font:600 13px system-ui,sans-serif;color:__ACCENT__;
    background:transparent;border:1.5px solid __ACCENT__;border-radius:6px;padding:6px 12px;
    cursor:pointer">\U0001F4CB Copy</button>
  <span id="msg__ID__" style="font:12px system-ui,sans-serif;color:__MUTED__"></span>
</div>
<script>
(function(){
  const ANCHOR="__ID__", FN="__FN__";
  function ensureH2C(){
    if(window.html2canvas) return Promise.resolve();
    if(window.__h2c) return window.__h2c;
    window.__h2c=new Promise((res,rej)=>{
      const s=document.createElement("script");
      s.src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js";
      s.onload=()=>res(); s.onerror=()=>rej(new Error("load failed"));
      document.head.appendChild(s); setTimeout(()=>rej(new Error("timeout")),10000);
    });
    return window.__h2c;
  }
  // Resolve the element to shoot: the tagged wrapper itself if it holds
  // content (tables), else the first Plotly chart that appears AFTER the
  // anchor in document order (a chart can't carry an id of its own).
  function target(){
    const el=document.getElementById(ANCHOR); if(!el) return null;
    if(el.querySelector("table,canvas,svg")) return el;
    const charts=[...document.querySelectorAll('[data-testid="stPlotlyChart"]')];
    for(const c of charts){
      if(el.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING) return c;
    }
    return charts[0]||el;
  }
  async function shoot(){
    await ensureH2C();
    const el=target(); if(!el) throw new Error("nothing to capture");
    return await window.html2canvas(el,{scale:2,backgroundColor:"__BG__",logging:false,useCORS:true});
  }
  const msgEl=document.getElementById("msg__ID__");
  const msg=t=>{ if(msgEl){ msgEl.textContent=t; if(t) setTimeout(()=>{msgEl.textContent="";},2500);} };
  document.getElementById("dl__ID__").onclick=async()=>{
    msg("…"); try{ const c=await shoot(); const a=document.createElement("a");
      a.download=FN+".png"; a.href=c.toDataURL("image/png"); a.click(); msg("✓ saved"); }
    catch(e){ msg("⚠ "+e.message); }
  };
  // Promise-based ClipboardItem so the async html2canvas work keeps the
  // click's user-gesture (a plain await before clipboard.write drops it).
  document.getElementById("cp__ID__").onclick=async()=>{
    msg("…");
    try{
      await navigator.clipboard.write([new ClipboardItem({"image/png":(async()=>{
        const c=await shoot();
        return await new Promise(r=>c.toBlob(r,"image/png"));
      })()})]);
      msg("✓ copied");
    }catch(e){ msg("⚠ "+(e.name||e.message)); }
  };
})();
</script>
"""


def _snap_toolbar(snap_id: str, filename: str):
    """📥 PNG + 📋 Copy that screenshot the actual on-screen element (styled
    table or rendered chart) client-side via html2canvas. `snap_id` is a
    wrapper element's id (tables) or a _snap_anchor placed just before a
    Plotly chart."""
    st.html(
        _SNAP_JS.replace("__ID__", snap_id).replace("__FN__", _safe_filename(filename))
                .replace("__ACCENT__", JSA_BLUE).replace("__MUTED__", DM_MUTED)
                .replace("__BG__", DM_SURFACE),
        unsafe_allow_javascript=True,
    )


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image(JSA_LOGO_FULL, width=180)
    st.markdown("### EIA Energy Dashboard")
    section = st.radio("Section", ["Weekly EIA Report", "Natural Gas", "Ethanol (Weekly)",
                                    "Biofuel Feedstocks (Monthly)"], index=0)
    st.markdown("---")
    if st.button("🔄 Refresh Data Now", width='stretch'):
        eia_get.clear()
        st.rerun()
    st.caption("Forces a fresh pull from EIA, bypassing the 30-min cache, for natural gas / "
               "feedstocks / capacity. Ethanol data comes from the Droplet's own scheduled "
               "pull instead (see the 'as of' line below) — this button doesn't affect it.")
    st.markdown("---")
    LOOKBACK_OPTIONS = {
        "Since Jan 2021": "2021-01-01",
        "1 Year": None, "2 Years": None, "5 Years": None, "Max": None,
    }
    lookback = st.selectbox("Chart lookback", list(LOOKBACK_OPTIONS.keys()), index=0)
    st.markdown("---")
    _ethanol_freshness = ethanol_cache_freshness()
    _source_note = "Source: U.S. Energy Information Administration<br>api.eia.gov/v2 — cached 30 min"
    if _ethanol_freshness:
        _source_note += f"<br>Ethanol data as of {_ethanol_freshness} UTC (Droplet ETL)"
    st.markdown(f"<div class='note-text'>{_source_note}</div>", unsafe_allow_html=True)

start_date = LOOKBACK_OPTIONS[lookback]
if start_date is None and lookback != "Max":
    lookback_years = {"1 Year": 1, "2 Years": 2, "5 Years": 5}[lookback]
    start_date = (pd.Timestamp.today() - pd.DateOffset(years=lookback_years)).strftime("%Y-%m-%d")

st.title("⚡ EIA Energy Dashboard")
st.caption("Natural gas markets and U.S. biofuel production & feedstock usage — John Stewart & Associates")

# ══════════════════════════════════════════════════════════════════════════════
# WEEKLY EIA REPORT
# ══════════════════════════════════════════════════════════════════════════════
if section == "Weekly EIA Report":
    PADD_CODES = ["R10", "R20", "R30", "R40", "R50"]
    PADD_LABELS = {"R10": "East Coast", "R20": "Midwest", "R30": "Gulf Coast",
                   "R40": "Rocky Mountains", "R50": "West Coast"}
    PADD_MOVE_CODES = {"R10": "R10-Z00", "R20": "R20-Z00", "R30": "R30-Z00",
                        "R40": "R40-Z00", "R50": "R50-Z00"}

    _sndw_padd_facets = {"duoarea": PADD_CODES + ["NUS"], "product": "EPOOXE"}
    eth_prod_padd = fetch_ethanol_cached(
        "petroleum/sum/sndw", {**_sndw_padd_facets, "process": "YOP"},
        lambda: eia_get("petroleum/sum/sndw", {**_sndw_padd_facets, "process": "YOP"}))
    eth_stock_padd = fetch_ethanol_cached(
        "petroleum/sum/sndw", {**_sndw_padd_facets, "process": "SAE"},
        lambda: eia_get("petroleum/sum/sndw", {**_sndw_padd_facets, "process": "SAE"}))
    eth_blend_padd = fetch_ethanol_cached(
        "petroleum/sum/sndw", {**_sndw_padd_facets, "process": "YIR"},
        lambda: eia_get("petroleum/sum/sndw", {**_sndw_padd_facets, "process": "YIR"}))
    _move_padd_facets = {"duoarea": list(PADD_MOVE_CODES.values()) + ["NUS-Z00"], "product": "EPOOXE"}
    eth_imports_padd = fetch_ethanol_cached(
        "petroleum/move/wkly", {**_move_padd_facets, "process": "IM0"},
        lambda: eia_get("petroleum/move/wkly", {**_move_padd_facets, "process": "IM0"}))
    eth_exports_nat = fetch_ethanol_cached(
        "petroleum/move/wkly", {"duoarea": "NUS-Z00", "product": "EPOOXE", "process": "EEX"},
        lambda: eia_get("petroleum/move/wkly", {"duoarea": "NUS-Z00", "product": "EPOOXE", "process": "EEX"}))
    gas_demand = eia_get("petroleum/cons/wpsup", {"duoarea": "NUS", "product": "EPM0F"})

    crude_stock = eia_get("petroleum/sum/sndw", {"duoarea": "NUS", "product": "EPC0", "process": "SAX"})
    cushing_stock = eia_get("petroleum/sum/sndw", {"duoarea": "YCUOK", "product": "EPC0", "process": "SAX"})
    gasoline_stock = eia_get("petroleum/sum/sndw", {"duoarea": "NUS", "product": "EPM0", "process": "SAE"})
    distillate_stock = eia_get("petroleum/sum/sndw", {"duoarea": "NUS", "product": "EPD0", "process": "SAE"})
    propane_stock = eia_get("petroleum/sum/sndw", {"duoarea": "NUS", "product": "EPLLPZ", "process": "SAE"})

    crude_input = eia_get("petroleum/pnp/wiup", {"duoarea": "NUS", "product": "EPC0", "process": "YIY"})
    refinery_capacity = eia_get("petroleum/pnp/wiup", {"duoarea": "NUS", "process": "YRL"})
    gasoline_prod = eia_get("petroleum/pnp/wprodrb", {"duoarea": "NUS", "product": "EPM0F", "process": "YPR"})
    distillate_prod = eia_get("petroleum/pnp/wprodrb", {"duoarea": "NUS", "product": "EPD0", "process": "YPR"})

    def wow(df):
        """(this_period, this_val, last_val, delta) for the two most recent weeks."""
        if df.empty:
            return None, None, None, None
        s = df.sort_values("period").dropna(subset=["value"])
        if len(s) < 2:
            return None, None, None, None
        this_row, last_row = s.iloc[-1], s.iloc[-2]
        return this_row["period"], this_row["value"], last_row["value"], this_row["value"] - last_row["value"]

    def padd_wow(df, padd_col="duoarea"):
        """{PADD label: (this_val, last_val, delta)} for the two most recent weeks per area."""
        out = {}
        if df.empty:
            return out
        for area, sub in df.groupby(padd_col):
            _, tv, lv, d = wow(sub)
            if tv is not None:
                out[area] = (tv, lv, d)
        return out

    def mover_phrase(deltas: dict, label_map: dict, unit="k bpd", decimals=0):
        """Turn {area_code: delta} into a natural-language driver sentence fragment."""
        items = [(label_map.get(k, k), v) for k, v in deltas.items() if v is not None and abs(v) >= 0.5]
        if not items:
            return "with little change across regions"
        ups = sorted([it for it in items if it[1] > 0], key=lambda x: -x[1])
        downs = sorted([it for it in items if it[1] < 0], key=lambda x: x[1])

        def fmt_group(grp):
            return ", ".join(f"{name} ({v:+,.{decimals}f}{unit})" for name, v in grp)

        if ups and downs:
            lead, other = (ups, downs) if sum(v for _, v in ups) >= -sum(v for _, v in downs) else (downs, ups)
            verb = "gains" if lead is ups else "declines"
            other_verb = "declines" if lead is ups else "gains"
            return (f"as {other_verb} in {fmt_group(other)} were outweighed by {verb} in {fmt_group(lead)}")
        elif ups:
            return f"led by gains in {fmt_group(ups)}"
        else:
            return f"led by declines in {fmt_group(downs)}"

    def marketing_year_week(ts: pd.Timestamp):
        my_start_year = ts.year if ts.month >= 9 else ts.year - 1
        my_start = pd.Timestamp(year=my_start_year, month=9, day=1)
        week = ((ts - my_start).days // 7) + 1
        label = f"{my_start_year}-{str(my_start_year + 1)[-2:]}"
        return label, week

    def seasonal_status(df, current_val, current_period):
        """Is current_val a seasonal (marketing-year-to-date) high or low?"""
        if df.empty or current_val is None:
            return ""
        my_label, _ = marketing_year_week(current_period)
        s = df.copy()
        s["my_label"] = s["period"].apply(lambda p: marketing_year_week(p)[0])
        ytd = s[(s["my_label"] == my_label) & (s["period"] <= current_period)]
        if ytd.empty:
            return ""
        if current_val >= ytd["value"].max():
            return " to a seasonal high"
        if current_val <= ytd["value"].min():
            return " to a seasonal low"
        return ""

    def pct_vs_5yr_avg(df, current_val, current_period):
        if df.empty or current_val is None:
            return None
        s = df.copy()
        s["year"] = s["period"].dt.year
        s["doy"] = s["period"].dt.dayofyear
        band_years = [current_period.year - n for n in range(1, 6)]
        hist = s[s["year"].isin(band_years)]
        near = hist.iloc[(hist["doy"] - current_period.dayofyear).abs().argsort()[:5]] if not hist.empty else hist
        if near.empty:
            return None
        avg = near["value"].mean()
        return (current_val / avg - 1) * 100 if avg else None

    eth_prod_nat = eth_prod_padd[eth_prod_padd["duoarea"] == "NUS"] if not eth_prod_padd.empty else pd.DataFrame()
    eth_stock_nat = eth_stock_padd[eth_stock_padd["duoarea"] == "NUS"] if not eth_stock_padd.empty else pd.DataFrame()
    eth_blend_nat = eth_blend_padd[eth_blend_padd["duoarea"] == "NUS"] if not eth_blend_padd.empty else pd.DataFrame()

    prod_period, prod_this, prod_last, prod_delta = wow(eth_prod_nat)
    stock_period, stock_this, stock_last, stock_delta = wow(eth_stock_nat)
    blend_period, blend_this, blend_last, blend_delta = wow(eth_blend_nat)
    _, exp_this, exp_last, exp_delta = wow(eth_exports_nat)
    _, gas_this, gas_last, gas_delta = wow(gas_demand)

    dos_this = (stock_this / blend_this) if (stock_this and blend_this) else None
    dos_last = (stock_last / blend_last) if (stock_last and blend_last) else None
    dos_delta = (dos_this - dos_last) if (dos_this is not None and dos_last is not None) else None

    prod_deltas_padd = {k: v[2] for k, v in padd_wow(eth_prod_padd).items() if k in PADD_LABELS}
    stock_deltas_padd = {k: v[2] for k, v in padd_wow(eth_stock_padd).items() if k in PADD_LABELS}

    report_date = f"{prod_period.strftime('%B')} {prod_period.day}" if prod_period is not None else "this week"
    prod_seasonal = seasonal_status(eth_prod_nat, prod_this, prod_period) if prod_period is not None else ""
    stock_seasonal = seasonal_status(eth_stock_nat, stock_this, stock_period) if stock_period is not None else ""

    st.title("Weekly EIA Report")
    if prod_period is not None:
        st.caption(f"Week ending {prod_period.strftime('%B')} {prod_period.day}, {prod_period.year} "
                   f"— John Stewart & Associates")

    section_header("Summary")
    if stock_delta is not None:
        st.markdown(
            f"**EIA Fuel Ethanol Stocks:** {'Increased' if stock_delta >= 0 else 'Decreased'} "
            f"{abs(stock_delta):,.0f}k to {stock_this / 1000:,.3f} million barrels"
        )
    if prod_delta is not None:
        st.markdown(
            f"**EIA Fuel Ethanol Production:** {'Increased' if prod_delta >= 0 else 'Decreased'} "
            f"{abs(prod_delta):,.0f}k to {prod_this:,.0f}k barrels/day"
        )

    if prod_delta is not None and stock_delta is not None:
        narrative = (
            f"Ethanol production {'increased' if prod_delta >= 0 else 'decreased'} "
            f"{abs(prod_delta):,.0f}k bpd{prod_seasonal} of {prod_this:,.0f}k bpd for the week ending "
            f"{report_date}, {mover_phrase(prod_deltas_padd, PADD_LABELS)}. "
            f"Ethanol stocks {'also increased' if stock_delta >= 0 else 'decreased'} "
            f"{abs(stock_delta):,.0f}k barrels on the week{stock_seasonal} of "
            f"{stock_this / 1000:,.3f} million barrels, "
            f"{mover_phrase(stock_deltas_padd, PADD_LABELS, unit='k bbl')}. "
        )
        if gas_delta is not None:
            narrative += (
                f"Gasoline demand {'bounced back, increasing' if gas_delta >= 0 else 'eased,  decreasing'} "
                f"{abs(gas_delta):,.0f}k bpd on the week to {gas_this:,.0f}k bpd. "
            )
        if exp_delta is not None:
            narrative += (
                f"Ethanol exports {'increased' if exp_delta >= 0 else 'decreased'} by {abs(exp_delta):,.0f}k bpd "
                f"to {exp_this:,.0f}k bpd. "
            )
        if dos_delta is not None:
            narrative += (
                f"Days of ethanol supply {'increased' if dos_delta >= 0 else 'decreased'} by {abs(dos_delta):.2f} "
                f"days this week to {dos_this:.2f} days."
            )
        st.markdown(narrative)
        st.markdown("<div class='note-text'>Days of supply = national ending stocks ÷ national blender net "
                    "input — a JSA-style estimate, not an EIA-published series.</div>", unsafe_allow_html=True)

    # ── PADD breakdown table ─────────────────────────────────────────────────
    section_header("EIA Weekly Fuel Ethanol — PADD Breakdown")

    def build_padd_block(title, padd_data, unit, decimals=0, snap_id=""):
        rows_html = ""
        nat = padd_data.get("NUS")
        for code in PADD_CODES:
            if code not in padd_data:
                continue
            tv, lv, d = padd_data[code]
            rows_html += (f"<tr><td style='padding:4px 10px'>{PADD_LABELS[code]}</td>"
                          f"<td style='padding:4px 10px;text-align:right'>{tv:,.{decimals}f}</td>"
                          f"<td style='padding:4px 10px;text-align:right'>{lv:,.{decimals}f}</td>"
                          f"<td style='padding:4px 10px;text-align:right;color:{POS if d >= 0 else NEG}'>{d:+,.{decimals}f}</td></tr>")
        total_html = ""
        if nat:
            tv, lv, d = nat
            total_html = (f"<tr style='border-top:1px solid {DM_BORDER};font-weight:700'>"
                          f"<td style='padding:4px 10px'>U.S. Total</td>"
                          f"<td style='padding:4px 10px;text-align:right'>{tv:,.{decimals}f}</td>"
                          f"<td style='padding:4px 10px;text-align:right'>{lv:,.{decimals}f}</td>"
                          f"<td style='padding:4px 10px;text-align:right;color:{POS if d >= 0 else NEG}'>{d:+,.{decimals}f}</td></tr>")
        header_row = (f"<tr style='color:{DM_MUTED};font-size:0.72rem;text-transform:uppercase'>"
                      f"<td style='padding:4px 10px'>{title}</td>"
                      f"<td style='padding:4px 10px;text-align:right'>This Week</td>"
                      f"<td style='padding:4px 10px;text-align:right'>Last Week</td>"
                      f"<td style='padding:4px 10px;text-align:right'>Change</td></tr>")
        return (f"<table id='{snap_id}' style='width:100%;border-collapse:collapse;background:{DM_SURFACE};"
                f"border:1px solid {DM_BORDER};border-radius:6px;margin-bottom:6px'>"
                f"{header_row}{rows_html}{total_html}</table>"
                f"<div class='note-text' style='margin-bottom:16px'>{unit}</div>")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(build_padd_block("Ethanol Production", padd_wow(eth_prod_padd), "Thousand Barrels / Day",
                                      snap_id="snap_padd_prod"), unsafe_allow_html=True)
        _snap_toolbar("snap_padd_prod", "Ethanol Production by PADD")
        st.markdown(build_padd_block("Ethanol Blender Demand", padd_wow(eth_blend_padd), "Thousand Barrels / Day",
                                      snap_id="snap_padd_blend"), unsafe_allow_html=True)
        _snap_toolbar("snap_padd_blend", "Ethanol Blender Demand by PADD")
    with col_b:
        imports_data = {k: v for area, v in padd_wow(eth_imports_padd).items()
                        for k, mv in PADD_MOVE_CODES.items() if area == mv}
        imports_data["NUS"] = padd_wow(eth_imports_padd).get("NUS-Z00")
        st.markdown(build_padd_block("Ethanol Imports", imports_data, "Thousand Barrels / Day",
                                      snap_id="snap_padd_imports"), unsafe_allow_html=True)
        _snap_toolbar("snap_padd_imports", "Ethanol Imports by PADD")
        stock_padd_data = {k: (v[0] / 1000, v[1] / 1000, v[2] / 1000)
                           for k, v in padd_wow(eth_stock_padd).items()}
        st.markdown(build_padd_block("Fuel Ethanol Stocks", stock_padd_data, "Million Barrels", decimals=1,
                                      snap_id="snap_padd_stocks"), unsafe_allow_html=True)
        _snap_toolbar("snap_padd_stocks", "Fuel Ethanol Stocks by PADD")

    # ── Seasonal charts ──────────────────────────────────────────────────────
    section_header("Seasonal Charts (Marketing Year, Sept–Aug)")

    def seasonal_chart(df, title, yaxis_title, snap_id):
        if df.empty:
            st.info(f"No data for {title}.")
            return
        s = df.copy()
        s[["my_label", "week"]] = s["period"].apply(lambda p: pd.Series(marketing_year_week(p)))
        years = sorted(s["my_label"].unique())[-6:]
        current_my = years[-1] if years else None
        fig = go.Figure()
        palette = ["#8a7233", "#3fa9f5", "#5ec48c", "#8db8e0", "#e0716f", "#f2b134"]
        for i, my in enumerate(years):
            sub = s[s["my_label"] == my].sort_values("week")
            is_current = my == current_my
            fig.add_trace(go.Scatter(
                x=sub["week"], y=sub["value"], mode="lines+markers" if is_current else "lines",
                name=my, line=dict(width=3 if is_current else 1.5,
                                    color=COL_ETHANOL if is_current else palette[i % len(palette)]),
                marker=dict(size=5) if is_current else None,
            ))
        style_fig(fig, yaxis_title=yaxis_title)
        fig.update_xaxes(title="Marketing-Year Week")
        _snap_anchor(snap_id)
        st.plotly_chart(fig, width='stretch')
        st.caption(title)
        _snap_toolbar(snap_id, title)

    col_a, col_b = st.columns(2)
    with col_a:
        seasonal_chart(eth_prod_nat, "Weekly Ethanol Plant Production", "Thousand bbl/day",
                       "snap_seasonal_prod")
    with col_b:
        seasonal_chart(eth_stock_nat, "Weekly U.S. Ending Stocks of Fuel Ethanol", "Thousand bbl",
                       "snap_seasonal_stock")

    # ── Refinery narrative + petroleum stocks ────────────────────────────────
    section_header("Refinery Operations")
    _, ci_this, ci_last, ci_delta = wow(crude_input)
    _, cap_this, cap_last, _ = wow(refinery_capacity)
    ut_this = (ci_this / cap_this * 100) if (ci_this and cap_this) else None
    ut_last = (ci_last / cap_last * 100) if (ci_last and cap_last) else None
    ut_delta = (ut_this - ut_last) if (ut_this is not None and ut_last is not None) else None
    _, gp_this, gp_last, gp_delta = wow(gasoline_prod)
    _, dp_this, dp_last, dp_delta = wow(distillate_prod)
    if ci_this is not None:
        refinery_narrative = (
            f"U.S. crude oil refinery inputs averaged {ci_this / 1000:,.1f} million barrels per day for the week "
            f"ending {report_date}, {abs(ci_delta):,.0f}k bpd {'more' if ci_delta >= 0 else 'less'} than the "
            f"previous week. "
        )
        if ut_this is not None:
            refinery_narrative += (
                f"Refineries operated at {ut_this:.1f}% of operable capacity"
                f"{f' ({ut_delta:+.1f} pts WoW)' if ut_delta is not None else ''}. "
            )
        if gp_this is not None:
            refinery_narrative += (
                f"Gasoline production {'increased' if gp_delta >= 0 else 'decreased'} to "
                f"{gp_this / 1000:,.1f} million bpd. "
            )
        if dp_this is not None:
            refinery_narrative += (
                f"Distillate fuel production {'increased' if dp_delta >= 0 else 'decreased'} to "
                f"{dp_this / 1000:,.1f} million bpd."
            )
        st.markdown(refinery_narrative)

    section_header("EIA Petroleum Stocks")
    stocks_rows = {
        "Comm. Crude Oil": wow(crude_stock),
        "Cushing, OK": wow(cushing_stock),
        "Total Motor Gasoline": wow(gasoline_stock),
        "Distillate Fuel Oil": wow(distillate_stock),
        "Propane/Propylene": wow(propane_stock),
    }
    rows_html = (f"<tr style='color:{DM_MUTED};font-size:0.72rem;text-transform:uppercase'>"
                f"<td style='padding:4px 10px'>Million Barrels</td>"
                f"<td style='padding:4px 10px;text-align:right'>This Week</td>"
                f"<td style='padding:4px 10px;text-align:right'>Last Week</td>"
                f"<td style='padding:4px 10px;text-align:right'>Change</td>"
                f"<td style='padding:4px 10px;text-align:right'>vs 5-Yr Avg</td></tr>")
    for label, (per, tv, lv, d) in stocks_rows.items():
        if tv is None:
            continue
        df_map = {"Comm. Crude Oil": crude_stock, "Cushing, OK": cushing_stock,
                  "Total Motor Gasoline": gasoline_stock, "Distillate Fuel Oil": distillate_stock,
                  "Propane/Propylene": propane_stock}
        pct5 = pct_vs_5yr_avg(df_map[label], tv, per)
        pct5_html = f"{pct5:+.0f}%" if pct5 is not None else "—"
        rows_html += (f"<tr><td style='padding:4px 10px'>{label}</td>"
                      f"<td style='padding:4px 10px;text-align:right'>{tv / 1000:,.1f}</td>"
                      f"<td style='padding:4px 10px;text-align:right'>{lv / 1000:,.1f}</td>"
                      f"<td style='padding:4px 10px;text-align:right;color:{POS if d >= 0 else NEG}'>{d / 1000:+,.1f}</td>"
                      f"<td style='padding:4px 10px;text-align:right'>{pct5_html}</td></tr>")
    st.markdown(
        f"<table id='snap_petro_stocks' style='width:100%;border-collapse:collapse;background:{DM_SURFACE};"
        f"border:1px solid {DM_BORDER};border-radius:6px'>{rows_html}</table>",
        unsafe_allow_html=True,
    )
    _snap_toolbar("snap_petro_stocks", "EIA Petroleum Stocks")

    petro_narrative_parts = []
    for label, df in [("crude oil", crude_stock), ("total motor gasoline", gasoline_stock),
                       ("distillate fuel", distillate_stock), ("propane/propylene", propane_stock)]:
        per, tv, lv, d = wow(df)
        if tv is None:
            continue
        pct5 = pct_vs_5yr_avg(df, tv, per)
        clause = (f"{label.capitalize()} inventories {'increased' if d >= 0 else 'decreased'} by "
                  f"{abs(d) / 1000:,.1f} million barrels last week")
        if pct5 is not None:
            clause += f" and are {abs(pct5):.0f}% {'above' if pct5 >= 0 else 'below'} the five-year average"
        petro_narrative_parts.append(clause + ".")
    if petro_narrative_parts:
        st.markdown(" ".join(petro_narrative_parts))

    st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# NATURAL GAS
# ══════════════════════════════════════════════════════════════════════════════
elif section == "Natural Gas":

    henry_hub = eia_get("natural-gas/pri/fut", {"series": "RNGWHHD"}, start=start_date)
    storage_regions = ["R48", "R31", "R32", "R33", "R34", "R35"]
    storage = eia_get("natural-gas/stor/wkly", {"duoarea": storage_regions, "process": "SWO"})
    ng_prod = eia_get("natural-gas/prod/sum", {"duoarea": "NUS", "process": ["FPD", "FGW", "VGM"]},
                       start=start_date)
    ng_cons = eia_get("natural-gas/cons/sum",
                       {"duoarea": "NUS",
                        "process": ["VRS", "VCS", "VIN", "VEU", "VDV", "VGP", "VGL", "VC0"]},
                       start=start_date)
    ng_price = eia_get("natural-gas/pri/sum",
                        {"duoarea": "NUS", "process": ["PRS", "PCS", "PIN", "PEU", "PG1"]},
                        start=start_date)

    REGION_NAME = {
        "R48": "Lower 48 States (Total)", "R31": "East", "R32": "Midwest",
        "R33": "South Central", "R34": "Mountain", "R35": "Pacific",
    }
    if not storage.empty:
        storage["region"] = storage["duoarea"].map(REGION_NAME)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        v, d, pct = latest_and_delta(henry_hub)
        st.metric("Henry Hub Spot ($/MMBtu)", fmt_num(v, 2),
                   f"{d:+.2f} vs prior day" if d is not None else None)
    with c2:
        l48 = storage[storage["duoarea"] == "R48"] if not storage.empty else pd.DataFrame()
        v, d, pct = latest_and_delta(l48)
        st.metric("Lower 48 Storage (Bcf)", fmt_num(v, 0),
                   f"{d:+,.0f} Bcf WoW" if d is not None else None)
    with c3:
        dry = ng_prod[ng_prod["process"] == "FPD"] if not ng_prod.empty else pd.DataFrame()
        v, d, pct = latest_and_delta(dry)
        v_bcfd = v / 1000 / 30.4 if v is not None else None
        st.metric("US Dry Gas Production (Bcf/d)", fmt_num(v_bcfd, 1),
                   f"{pct:+.1f}% vs prior month" if pct is not None else None)
    with c4:
        totc = ng_cons[ng_cons["process"] == "VC0"] if not ng_cons.empty else pd.DataFrame()
        v, d, pct = latest_and_delta(totc)
        v_bcfd = v / 1000 / 30.4 if v is not None else None
        st.metric("US Total Consumption (Bcf/d)", fmt_num(v_bcfd, 1),
                   f"{pct:+.1f}% vs prior month" if pct is not None else None)

    # ── Henry Hub price ─────────────────────────────────────────────────────
    section_header("Henry Hub Natural Gas Spot Price")
    if not henry_hub.empty:
        fig = line_fig(henry_hub, "period", "value", "Henry Hub Spot", COL_NATGAS,
                        yaxis_title="$/MMBtu")
        _snap_anchor("snap_henry_hub")
        st.plotly_chart(fig, width='stretch')
        _snap_toolbar("snap_henry_hub", "Henry Hub Natural Gas Spot Price")
    else:
        st.info("No Henry Hub price data returned.")

    # ── Storage ──────────────────────────────────────────────────────────────
    section_header("Weekly Working Gas in Underground Storage")
    region_pick = st.selectbox("Region", list(REGION_NAME.values()), index=0, key="region_pick")
    if not storage.empty:
        reg_df = storage[storage["region"] == region_pick].copy()
        reg_df["year"] = reg_df["period"].dt.year
        reg_df["doy"] = reg_df["period"].dt.dayofyear
        this_year = date.today().year
        band_years = [y for y in range(this_year - 5, this_year)]
        band = reg_df[reg_df["year"].isin(band_years)].groupby("doy")["value"].agg(
            ["min", "max", "mean"]).reset_index()
        band["x"] = pd.Timestamp(f"{this_year}-01-01") + pd.to_timedelta(band["doy"] - 1, unit="D")

        cur = reg_df[reg_df["year"] == this_year].sort_values("period")
        prev = reg_df[reg_df["year"] == this_year - 1].sort_values("period").copy()
        if not prev.empty:
            prev["x"] = pd.Timestamp(f"{this_year}-01-01") + pd.to_timedelta(prev["doy"] - 1, unit="D")

        fig = go.Figure()
        if not band.empty:
            fig.add_trace(go.Scatter(x=band["x"], y=band["max"], mode="lines",
                                      line=dict(width=0), showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=band["x"], y=band["min"], mode="lines",
                                      line=dict(width=0), fill="tonexty",
                                      fillcolor="rgba(132,147,159,0.18)",
                                      name="5-Yr Range", hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=band["x"], y=band["mean"], mode="lines",
                                      line=dict(color=DM_MUTED, width=1.5, dash="dot"),
                                      name="5-Yr Average"))
        if not prev.empty:
            fig.add_trace(go.Scatter(x=prev["x"], y=prev["value"], mode="lines",
                                      name=f"{this_year - 1}",
                                      line=dict(color=COL_REDSL, width=1.5, dash="dash")))
        if not cur.empty:
            cur_x = pd.Timestamp(f"{this_year}-01-01") + pd.to_timedelta(cur["doy"] - 1, unit="D")
            fig.add_trace(go.Scatter(x=cur_x, y=cur["value"], mode="lines",
                                      name=f"{this_year}",
                                      line=dict(color=COL_NATGAS, width=3)))
        style_fig(fig, yaxis_title="Bcf")
        fig.update_xaxes(tickformat="%b")
        _snap_anchor("snap_storage")
        st.plotly_chart(fig, width='stretch')
        st.markdown("<div class='note-text'>Shaded band = min–max over the prior 5 full years, "
                    "aligned by day of year.</div>", unsafe_allow_html=True)
        _snap_toolbar("snap_storage", f"Weekly Working Gas Storage — {region_pick}")
    else:
        st.info("No storage data returned.")

    # ── Production ───────────────────────────────────────────────────────────
    section_header("U.S. Natural Gas Production")
    col_a, col_b = st.columns([2, 1])
    with col_a:
        if not ng_prod.empty:
            fig = go.Figure()
            for proc, label, color in [("FGW", "Gross Withdrawals", COL_REDSL),
                                        ("FPD", "Dry Production", COL_NATGAS)]:
                sub = ng_prod[ng_prod["process"] == proc]
                if not sub.empty:
                    fig.add_trace(go.Scatter(x=sub["period"], y=sub["value"] / 1000, mode="lines",
                                              name=label, line=dict(width=2, color=color)))
            style_fig(fig, yaxis_title="Bcf / month")
            _snap_anchor("snap_ng_prod")
            st.plotly_chart(fig, width='stretch')
            _snap_toolbar("snap_ng_prod", "U.S. Natural Gas Production")
        else:
            st.info("No production data returned.")
    with col_b:
        states = eia_get("natural-gas/prod/sum", {"process": "FGW"})
        if not states.empty:
            latest_month = states["period"].max()
            snap = states[(states["period"] == latest_month) &
                           (~states["duoarea"].isin(["NUS"]))].nlargest(8, "value")
            fig = go.Figure(go.Bar(x=snap["value"] / 1000, y=snap["area-name"],
                                    orientation="h", marker_color=COL_NATGAS))
            style_fig(fig, yaxis_title="", legend=False)
            fig.update_layout(height=420, xaxis_title="Bcf")
            fig.update_yaxes(autorange="reversed")
            _snap_anchor("snap_ng_top_states")
            st.plotly_chart(fig, width='stretch')
            st.caption(f"Top gross withdrawal areas, {latest_month.strftime('%b %Y')}")
            _snap_toolbar("snap_ng_top_states", "Top Natural Gas Producing Areas")

    # ── Consumption ──────────────────────────────────────────────────────────
    section_header("U.S. Natural Gas Consumption by End Use")
    if not ng_cons.empty:
        sector_map = {"VRS": "Residential", "VCS": "Commercial", "VIN": "Industrial",
                      "VEU": "Electric Power", "VDV": "Vehicle Fuel",
                      "VGP": "Pipeline Fuel", "VGL": "Lease & Plant Fuel"}
        fig = go.Figure()
        for proc, label in sector_map.items():
            sub = ng_cons[ng_cons["process"] == proc]
            if not sub.empty:
                fig.add_trace(go.Scatter(x=sub["period"], y=sub["value"] / 1000, mode="lines",
                                          stackgroup="one", name=label))
        style_fig(fig, yaxis_title="Bcf / month")
        _snap_anchor("snap_ng_cons")
        st.plotly_chart(fig, width='stretch')
        _snap_toolbar("snap_ng_cons", "U.S. Natural Gas Consumption by End Use")
    else:
        st.info("No consumption data returned.")

    # ── Prices by sector ─────────────────────────────────────────────────────
    section_header("U.S. Natural Gas Prices by Sector")
    if not ng_price.empty:
        price_map = {"PRS": "Residential", "PCS": "Commercial", "PIN": "Industrial",
                     "PEU": "Electric Power", "PG1": "City Gate"}
        fig = go.Figure()
        for proc, label in price_map.items():
            sub = ng_price[ng_price["process"] == proc]
            if not sub.empty:
                fig.add_trace(go.Scatter(x=sub["period"], y=sub["value"], mode="lines",
                                          name=label, line=dict(width=2)))
        style_fig(fig, yaxis_title="$/Mcf")
        _snap_anchor("snap_ng_price")
        st.plotly_chart(fig, width='stretch')
        _snap_toolbar("snap_ng_price", "U.S. Natural Gas Prices by Sector")
    else:
        st.info("No price data returned.")

# ══════════════════════════════════════════════════════════════════════════════
# ETHANOL & BIOFUELS
# ══════════════════════════════════════════════════════════════════════════════
elif section == "Ethanol (Weekly)":
    def _since(df):
        """Apply the sidebar's chart lookback after a full-history cache read
        (the cache itself always holds full history, same as feedstocks_hist)."""
        if df.empty or not start_date:
            return df
        return df[df["period"] >= start_date].reset_index(drop=True)

    _weekly_facets = {"duoarea": "NUS", "product": "EPOOXE", "process": ["YOP", "SAE", "YIR"]}
    eth_weekly = _since(fetch_ethanol_cached(
        "petroleum/sum/sndw", _weekly_facets,
        lambda: eia_get("petroleum/sum/sndw", _weekly_facets, start=start_date)))
    eth_padd = _since(fetch_ethanol_cached(
        "petroleum/pnp/wprode", {"product": "EPOOXE"},
        lambda: eia_get("petroleum/pnp/wprode", {"product": "EPOOXE"}, start=start_date)))
    _exports_facets = {"duoarea": "NUS-Z00", "product": "EPOOXE", "process": "EEX"}
    eth_exports = _since(fetch_ethanol_cached(
        "petroleum/move/wkly", _exports_facets,
        lambda: eia_get("petroleum/move/wkly", _exports_facets, start=start_date)))

    prod = eth_weekly[eth_weekly["process"] == "YOP"] if not eth_weekly.empty else pd.DataFrame()
    stocks = eth_weekly[eth_weekly["process"] == "SAE"] if not eth_weekly.empty else pd.DataFrame()

    st.caption("EIA's weekly petroleum status report — production, stocks, and exports "
               "typically lag only about a week.")

    c1, c2, c3 = st.columns(3)
    with c1:
        v, d, pct = latest_and_delta(prod)
        st.metric("Ethanol Production (kb/d)", fmt_num(v, 0),
                   f"{d:+,.0f} kb/d WoW" if d is not None else None)
    with c2:
        v, d, pct = latest_and_delta(stocks)
        st.metric("Ethanol Stocks (kbbl)", fmt_num(v, 0),
                   f"{d:+,.0f} kbbl WoW" if d is not None else None)
    with c3:
        v, d, pct = latest_and_delta(eth_exports)
        st.metric("Ethanol Exports (kb/d)", fmt_num(v, 0),
                   f"{d:+,.0f} kb/d WoW" if d is not None else None)

    # ── Weekly ethanol production, stocks & exports ──────────────────────────
    section_header("Weekly U.S. Fuel Ethanol Production, Stocks & Exports")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if not prod.empty:
            fig = line_fig(prod, "period", "value", "Production", COL_ETHANOL,
                            yaxis_title="Thousand bbl/day")
            _snap_anchor("snap_eth_prod")
            st.plotly_chart(fig, width='stretch')
            st.caption("Oxygenate plant production, national")
            _snap_toolbar("snap_eth_prod", "Weekly U.S. Fuel Ethanol Production")
        else:
            st.info("No weekly ethanol production data returned.")
    with col_b:
        if not stocks.empty:
            fig = line_fig(stocks, "period", "value", "Ending Stocks", COL_REDSL,
                            yaxis_title="Thousand bbl")
            _snap_anchor("snap_eth_stocks")
            st.plotly_chart(fig, width='stretch')
            st.caption("Ending stocks, national")
            _snap_toolbar("snap_eth_stocks", "Weekly U.S. Fuel Ethanol Stocks")
        else:
            st.info("No weekly ethanol stocks data returned.")
    with col_c:
        if not eth_exports.empty:
            fig = line_fig(eth_exports, "period", "value", "Exports", COL_BIODSL,
                            yaxis_title="Thousand bbl/day")
            _snap_anchor("snap_eth_exports")
            st.plotly_chart(fig, width='stretch')
            st.caption("U.S. exports, national")
            _snap_toolbar("snap_eth_exports", "Weekly U.S. Fuel Ethanol Exports")
        else:
            st.info("No weekly ethanol export data returned.")

    # ── Ethanol production by PADD ──────────────────────────────────────────
    section_header("Weekly Ethanol Production by PADD")
    if not eth_padd.empty:
        padd_map = {"R10": "PADD 1", "R20": "PADD 2", "R30": "PADD 3",
                    "R40": "PADD 4", "R50": "PADD 5"}
        fig = go.Figure()
        for code, label in padd_map.items():
            sub = eth_padd[eth_padd["duoarea"] == code]
            if not sub.empty:
                fig.add_trace(go.Scatter(x=sub["period"], y=sub["value"], mode="lines",
                                          stackgroup="one", name=label))
        style_fig(fig, yaxis_title="Thousand bbl/day")
        _snap_anchor("snap_eth_padd")
        st.plotly_chart(fig, width='stretch')
        st.markdown("<div class='note-text'>PADD 2 (Midwest) is the historical core of U.S. "
                    "ethanol production capacity.</div>", unsafe_allow_html=True)
        _snap_toolbar("snap_eth_padd", "Weekly Ethanol Production by PADD")
    else:
        st.info("No PADD-level ethanol data returned.")

# ══════════════════════════════════════════════════════════════════════════════
# BIOFUEL FEEDSTOCKS (MONTHLY)
# ══════════════════════════════════════════════════════════════════════════════
else:
    capbio = eia_get("petroleum/pnp/capbio", start=start_date)
    feedstocks = eia_get("petroleum/pnp/feedbiofuel", start=start_date)
    plant_fuel = eia_get("petroleum/pnp/bioplfuel", start=start_date)
    # Full, unfiltered history for the comparison tables below — YoY, YTD, and the
    # 3-yr seasonal average all need years of lookback regardless of the sidebar's
    # chart-display lookback setting.
    feedstocks_hist = eia_get("petroleum/pnp/feedbiofuel")

    cap_latest_month = capbio["period"].max() if not capbio.empty else None
    cap_snap = capbio[capbio["period"] == cap_latest_month] if cap_latest_month is not None else pd.DataFrame()
    total_cap = cap_snap["value"].sum() if not cap_snap.empty else None

    feed_latest_month = feedstocks["period"].max() if not feedstocks.empty else None
    ng_at_biofuel = plant_fuel[plant_fuel["process"] == "819NG0"] if not plant_fuel.empty else pd.DataFrame()

    if feed_latest_month is not None:
        today = pd.Timestamp.today()
        lag_months = (today.year - feed_latest_month.year) * 12 + (today.month - feed_latest_month.month)
        st.info(
            f"📅 EIA's biofuels feedstock survey is monthly and published with a lag — the "
            f"latest available month is **{feed_latest_month.strftime('%B %Y')}**, "
            f"{lag_months} month{'s' if lag_months != 1 else ''} behind today "
            f"({today.strftime('%B %Y')}). Capacity data lags similarly "
            f"({cap_latest_month.strftime('%B %Y') if cap_latest_month is not None else '—'}). "
            f"Use **🔄 Refresh Data Now** in the sidebar after a new report has published to "
            f"bypass the 30-minute cache."
        )

    c1, c2 = st.columns(2)
    with c1:
        st.metric("Ethanol + Biodiesel + RD Capacity (MMgal/yr)", fmt_num(total_cap, 0),
                   cap_latest_month.strftime("%b %Y") if cap_latest_month is not None else None)
    with c2:
        v, d, pct = latest_and_delta(ng_at_biofuel)
        v_bcf = v / 1000 if v is not None else None
        st.metric("Nat Gas Used at Biofuel Plants (Bcf/yr)", fmt_num(v_bcf, 0),
                   f"{pct:+.1f}% YoY" if pct is not None else None)

    # ── Biofuels production capacity ────────────────────────────────────────
    section_header("Biofuels Operable Production Capacity")
    col_a, col_b = st.columns(2)
    with col_a:
        eth_cap = capbio[capbio["product"] == "EPOOXE"] if not capbio.empty else pd.DataFrame()
        if not eth_cap.empty:
            fig = line_fig(eth_cap, "period", "value", "Fuel Ethanol Capacity", COL_ETHANOL,
                            yaxis_title="Million gallons/year")
            _snap_anchor("snap_cap_ethanol")
            st.plotly_chart(fig, width='stretch')
            _snap_toolbar("snap_cap_ethanol", "Fuel Ethanol Production Capacity")
        else:
            st.info("No ethanol capacity data returned.")
    with col_b:
        bio_cap = capbio[capbio["product"].isin(["EPOORDB", "EPOOROO"])] if not capbio.empty else pd.DataFrame()
        if not bio_cap.empty:
            fig = go.Figure()
            for code, label, color in [("EPOORDB", "Biodiesel", COL_BIODSL),
                                        ("EPOOROO", "Renewable Diesel & Other", COL_REDSL)]:
                sub = bio_cap[bio_cap["product"] == code]
                if not sub.empty:
                    fig.add_trace(go.Scatter(x=sub["period"], y=sub["value"], mode="lines",
                                              stackgroup="one", name=label, line=dict(color=color)))
            style_fig(fig, yaxis_title="Million gallons/year")
            _snap_anchor("snap_cap_biodiesel")
            st.plotly_chart(fig, width='stretch')
            _snap_toolbar("snap_cap_biodiesel", "Biodiesel & Renewable Diesel Production Capacity")
        else:
            st.info("No biodiesel/renewable diesel capacity data returned.")

    # ── Feedstocks ───────────────────────────────────────────────────────────
    section_header("Feedstocks Consumed for Biofuels Production")
    st.markdown("<div class='note-text'>EIA's biofuels feedstock survey spans both corn/grain "
                "for ethanol and the oils, fats and waste streams behind biodiesel and "
                "renewable diesel. Corn runs 15–20x larger by weight than any single biodiesel "
                "feedstock, so it's broken out on its own scale below.</div>",
                unsafe_allow_html=True)
    FEEDSTOCK_GROUPS = {
        "EPOOBDAFC": ("Corn", "Grains (Ethanol)"),
        "EPOOBDAFS": ("Grain Sorghum", "Grains (Ethanol)"),
        "EPOOBDSO": ("Soybean Oil", "Vegetable Oils"),
        "EPOOBDCNO": ("Corn Oil", "Vegetable Oils"),
        "EPOOBDCO": ("Canola Oil", "Vegetable Oils"),
        "EPOOBDVOO": ("Other Vegetable Oils", "Vegetable Oils"),
        "EPOOBDFSYG": ("Yellow Grease", "Animal Fats & Greases"),
        "EPOOBDFSWG": ("White Grease", "Animal Fats & Greases"),
        "EPOOBDFSTL": ("Tallow", "Animal Fats & Greases"),
        "EPOOBDFSPT": ("Poultry Fat", "Animal Fats & Greases"),
        "EPOOBDAFO": ("Other Animal Fats", "Animal Fats & Greases"),
        "EPOOBDAL": ("Algae", "Other Feedstocks"),
        "EPOOBDBG": ("Biogas", "Other Feedstocks"),
        "EPOOBDAFD": ("Dedicated Energy Crops", "Other Feedstocks"),
        "EPOOBDAFR": ("Ag & Forestry Residues", "Other Feedstocks"),
        "EPOOBDRFWM": ("Municipal Solid Waste", "Other Feedstocks"),
        "EPOOBDRFWY": ("Yard & Food Waste", "Other Feedstocks"),
        "EPOOBDRFWO": ("Other Recycled Feeds", "Other Feedstocks"),
        "EPOOBDAFPO": ("Other Ag/Forestry Products", "Other Feedstocks"),
        "EPOOBDOB": ("Other Biofuel Feedstocks", "Other Feedstocks"),
    }
    FEED8_MAP = {
        "Soybean Oil": "Soybean Oil", "Corn Oil": "Corn Oil", "Canola Oil": "Canola Oil",
        "Yellow Grease": "Yellow Grease", "Tallow": "Tallow", "White Grease": "White Grease",
        "Poultry Fat": "Poultry",
    }
    FEED8_ORDER = ["Soybean Oil", "Corn Oil", "Canola Oil", "Yellow Grease",
                   "Tallow", "White Grease", "Poultry", "Other"]
    FEED8_COLORS = {
        "Soybean Oil": "#b5651d", "Corn Oil": "#2f4f2f", "Canola Oil": "#deb887",
        "Yellow Grease": "#6a5acd", "Tallow": "#c0392b", "White Grease": "#9acd32",
        "Poultry": "#4169e1", "Other": "#87ceeb",
    }

    def build_feedstock_compare_table(df, cat_col, categories, snap_id, decimals=0,
                                       value_divisor=1.0, ytd_mode="calendar"):
        """Monthly comparison table: last 4 months, same month LY, M/M %, Y/Y %,
        3-yr seasonal average (same calendar month, prior 3 years), vs-3yr %,
        and a YTD vs YTD-LY comparison. `df` must have a `period`, `cat_col`, and
        `value` column and carry full (unfiltered) history. `value_divisor` rescales
        the raw value (e.g. 56 to convert million lbs to million bushels).
        `ytd_mode` is "calendar" (Jan-Dec) or "marketing" (Sep-Aug, corn/sorghum
        marketing year)."""
        if df.empty:
            return None
        df = df.assign(value=df["value"] / value_divisor)
        latest_m = df["period"].max()
        month_cols = [latest_m - pd.DateOffset(months=n) for n in (3, 2, 1, 0)]
        ly_month = latest_m - pd.DateOffset(months=12)
        seasonal_months = [pd.Timestamp(year=latest_m.year - n, month=latest_m.month, day=1)
                           for n in (1, 2, 3)]
        if ytd_mode == "marketing":
            my_start_year = latest_m.year if latest_m.month >= 9 else latest_m.year - 1
            ytd_start = pd.Timestamp(my_start_year, 9, 1)
            ytd_start_ly = pd.Timestamp(my_start_year - 1, 9, 1)
            ytd_label = f"MYTD '{str(my_start_year)[-2:]}-{str(my_start_year + 1)[-2:]}"
            ytd_label_ly = f"MYTD '{str(my_start_year - 1)[-2:]}-{str(my_start_year)[-2:]}"
            ytd_caption = (f"Marketing YTD = {ytd_start.strftime('%b %Y')}–{latest_m.strftime('%b %Y')} "
                           f"vs. {ytd_start_ly.strftime('%b %Y')}–{ly_month.strftime('%b %Y')} "
                           f"(Sep 1–Aug 31 marketing year).")
        else:
            ytd_start = pd.Timestamp(latest_m.year, 1, 1)
            ytd_start_ly = pd.Timestamp(latest_m.year - 1, 1, 1)
            ytd_label = f"YTD '{str(latest_m.year)[-2:]}"
            ytd_label_ly = f"YTD '{str(latest_m.year - 1)[-2:]}"
            ytd_caption = (f"YTD = Jan–{latest_m.strftime('%b %Y')} vs. "
                           f"Jan–{ly_month.strftime('%b %Y')}.")

        def val_at(cat, period):
            sub = df[(df[cat_col] == cat) & (df["period"] == period)] if cat is not None \
                else df[df["period"] == period]
            return sub["value"].sum() if not sub.empty else None

        def sum_range(cat, start, end):
            mask = (df["period"] >= start) & (df["period"] <= end)
            if cat is not None:
                mask &= df[cat_col] == cat
            sub = df[mask]
            return sub["value"].sum() if not sub.empty else None

        def pct(a, b):
            if a is None or b is None or b == 0:
                return None
            return (a / b - 1) * 100

        def fmt_val(v):
            return f"{v:,.{decimals}f}" if v is not None else "—"

        def fmt_pct(v):
            if v is None:
                return "<span style='color:{}'>—</span>".format(DM_MUTED)
            return f"<span style='color:{POS if v >= 0 else NEG}'>{v:+.1f}%</span>"

        headers = (["Feedstock"] + [m.strftime("%b '%y") for m in month_cols] +
                   [f"{ly_month.strftime('%b')} '{str(ly_month.year)[-2:]} (LY)", "M/M %", "Y/Y %",
                    "3-Yr Avg", "vs 3-Yr", ytd_label, ytd_label_ly, "YTD %"])
        header_html = "".join(
            f"<th style='padding:4px 8px;text-align:{'left' if i == 0 else 'right'};"
            f"color:{DM_MUTED};font-size:0.68rem;text-transform:uppercase;white-space:nowrap'>"
            f"{h}</th>" for i, h in enumerate(headers))

        rows_html = ""
        row_specs = [(c, c) for c in categories]
        if len(categories) > 1:
            row_specs.append(("__TOTAL__", "Total"))
        for cat_key, label in row_specs:
            cat = None if cat_key == "__TOTAL__" else cat_key
            vals = [val_at(cat, m) for m in month_cols]
            ly_val = val_at(cat, ly_month)
            mom = pct(vals[-1], vals[-2])
            yoy = pct(vals[-1], ly_val)
            seasonal_vals = [v for v in (val_at(cat, m) for m in seasonal_months) if v is not None]
            seasonal_avg = sum(seasonal_vals) / len(seasonal_vals) if seasonal_vals else None
            vs_seasonal = pct(vals[-1], seasonal_avg)
            ytd = sum_range(cat, ytd_start, latest_m)
            ytd_ly = sum_range(cat, ytd_start_ly, ly_month)
            ytd_chg = pct(ytd, ytd_ly)

            bold = "font-weight:700;border-top:1px solid " + DM_BORDER if cat_key == "__TOTAL__" else ""
            cells = [f"<td style='padding:4px 8px;{bold}'>{label}</td>"]
            for v in vals:
                cells.append(f"<td style='padding:4px 8px;text-align:right;{bold}'>{fmt_val(v)}</td>")
            cells.append(f"<td style='padding:4px 8px;text-align:right;{bold}'>{fmt_val(ly_val)}</td>")
            cells.append(f"<td style='padding:4px 8px;text-align:right;{bold}'>{fmt_pct(mom)}</td>")
            cells.append(f"<td style='padding:4px 8px;text-align:right;{bold}'>{fmt_pct(yoy)}</td>")
            cells.append(f"<td style='padding:4px 8px;text-align:right;{bold}'>{fmt_val(seasonal_avg)}</td>")
            cells.append(f"<td style='padding:4px 8px;text-align:right;{bold}'>{fmt_pct(vs_seasonal)}</td>")
            cells.append(f"<td style='padding:4px 8px;text-align:right;{bold}'>{fmt_val(ytd)}</td>")
            cells.append(f"<td style='padding:4px 8px;text-align:right;{bold}'>{fmt_val(ytd_ly)}</td>")
            cells.append(f"<td style='padding:4px 8px;text-align:right;{bold}'>{fmt_pct(ytd_chg)}</td>")
            rows_html += f"<tr>{''.join(cells)}</tr>"

        table_html = (
            f"<div style='overflow-x:auto'>"
            f"<table id='{snap_id}' style='width:100%;border-collapse:collapse;background:{DM_SURFACE};"
            f"border:1px solid {DM_BORDER};border-radius:6px;font-size:0.82rem'>"
            f"<tr>{header_html}</tr>{rows_html}</table></div>"
            f"<div class='note-text' style='margin:4px 0 16px 0'>"
            f"{ytd_caption} "
            f"3-Yr Avg = average {latest_m.strftime('%B')} across "
            f"{seasonal_months[-1].year}–{seasonal_months[0].year}.</div>"
        )
        return table_html

    if not feedstocks.empty:
        fs = feedstocks[feedstocks["product"].isin(FEEDSTOCK_GROUPS.keys())].copy()
        fs["feedstock"] = fs["product"].map(lambda p: FEEDSTOCK_GROUPS[p][0])
        fs["family"] = fs["product"].map(lambda p: FEEDSTOCK_GROUPS[p][1])
        latest_m = fs["period"].max()

        fs_hist = feedstocks_hist[feedstocks_hist["product"].isin(FEEDSTOCK_GROUPS.keys())].copy()
        fs_hist["feedstock"] = fs_hist["product"].map(lambda p: FEEDSTOCK_GROUPS[p][0])
        fs_hist["family"] = fs_hist["product"].map(lambda p: FEEDSTOCK_GROUPS[p][1])
        bd_hist = fs_hist[fs_hist["family"] != "Grains (Ethanol)"].copy()
        bd_hist["bucket8"] = bd_hist["feedstock"].map(FEED8_MAP).fillna("Other")

        st.markdown("**Corn & Grain Sorghum Inputs to Ethanol Production**")
        GRAIN_BUSHEL_LBS = 56.0  # standard test weight for both corn and grain sorghum
        ctrl_a, ctrl_b, ctrl_c = st.columns(3)
        with ctrl_a:
            grain_unit = st.segmented_control("Units", ["Million lbs", "Million bushels"],
                                              default="Million lbs", required=True, key="grain_unit")
        with ctrl_b:
            grain_series = st.segmented_control("Series", ["Both", "Corn Only", "Grain Sorghum Only"],
                                                default="Both", required=True, key="grain_series")
        with ctrl_c:
            grain_ytd_basis = st.segmented_control(
                "YTD basis", ["Calendar (Jan–Dec)", "Marketing Year (Sep–Aug)"],
                default="Calendar (Jan–Dec)", required=True, key="grain_ytd_basis")
        grain_divisor = GRAIN_BUSHEL_LBS if grain_unit == "Million bushels" else 1.0
        grain_ytd_mode = "marketing" if grain_ytd_basis == "Marketing Year (Sep–Aug)" else "calendar"
        grain_feeds = {"Both": ["Corn", "Grain Sorghum"], "Corn Only": ["Corn"],
                       "Grain Sorghum Only": ["Grain Sorghum"]}[grain_series]

        grains = fs[fs["family"] == "Grains (Ethanol)"]
        by_grain = grains.groupby(["period", "feedstock"])["value"].sum().reset_index()
        fig = go.Figure()
        for feed, color in [("Corn", "#c9a24b"), ("Grain Sorghum", "#8a7233")]:
            if feed not in grain_feeds:
                continue
            sub = by_grain[by_grain["feedstock"] == feed]
            if not sub.empty:
                fig.add_trace(go.Scatter(x=sub["period"], y=sub["value"] / grain_divisor, mode="lines",
                                          stackgroup="one", name=feed, line=dict(color=color)))
        style_fig(fig, yaxis_title=f"{grain_unit} / month")
        _snap_anchor("snap_feed_grains")
        st.plotly_chart(fig, width='stretch')
        _snap_toolbar("snap_feed_grains", "Corn & Grain Sorghum Inputs to Ethanol Production")

        grains_table = build_feedstock_compare_table(
            fs_hist[fs_hist["family"] == "Grains (Ethanol)"], "feedstock", grain_feeds,
            "snap_grains_table", decimals=(1 if grain_divisor != 1 else 0),
            value_divisor=grain_divisor, ytd_mode=grain_ytd_mode)
        if grains_table:
            st.markdown(grains_table, unsafe_allow_html=True)
            _snap_toolbar("snap_grains_table", f"Corn & Grain Sorghum — Monthly Comparison ({grain_unit})")

        st.markdown("**Feedstocks for Biodiesel & Renewable Diesel Production**")
        bd = fs[fs["family"] != "Grains (Ethanol)"]
        bd = bd.copy()
        bd["bucket8"] = bd["feedstock"].map(FEED8_MAP).fillna("Other")

        col_a, col_b = st.columns([3, 2])
        with col_a:
            by_bucket = bd.groupby(["period", "bucket8"])["value"].sum().reset_index()
            fig = go.Figure()
            for bucket in FEED8_ORDER:
                sub = by_bucket[by_bucket["bucket8"] == bucket]
                if not sub.empty:
                    fig.add_trace(go.Scatter(x=sub["period"], y=sub["value"], mode="lines",
                                              stackgroup="one", name=bucket,
                                              line=dict(color=FEED8_COLORS[bucket])))
            style_fig(fig, yaxis_title="Million lbs / month")
            _snap_anchor("snap_feed_demand")
            st.plotly_chart(fig, width='stretch')
            st.caption("Feedstock demand for biofuel production, monthly")
            _snap_toolbar("snap_feed_demand", "Feedstock Demand for Biofuel Production")
        with col_b:
            snap = bd[bd["period"] == latest_m].groupby("feedstock")["value"].sum().reset_index()
            snap = snap.sort_values("value", ascending=True).tail(10)
            fig = go.Figure(go.Bar(x=snap["value"], y=snap["feedstock"],
                                    orientation="h", marker_color=COL_BIODSL))
            style_fig(fig, legend=False)
            fig.update_layout(height=420, xaxis_title="Million lbs")
            _snap_anchor("snap_feed_top10")
            st.plotly_chart(fig, width='stretch')
            st.caption(f"Top biodiesel/RD feedstocks, {latest_m.strftime('%b %Y')}")
            _snap_toolbar("snap_feed_top10", f"Top Biodiesel-RD Feedstocks {latest_m.strftime('%b %Y')}")

        bd_table = build_feedstock_compare_table(bd_hist, "bucket8", FEED8_ORDER, "snap_bd_table")
        if bd_table:
            st.markdown(bd_table, unsafe_allow_html=True)
            _snap_toolbar("snap_bd_table", "Biodiesel-RD Feedstocks — Monthly Comparison (Million lbs)")

        # ── Market share (% of the 8-bucket total) ────────────────────────────
        st.markdown("**Feedstocks Demand Market Share for Biofuel Production**")
        fig = go.Figure()
        for bucket in FEED8_ORDER:
            sub = by_bucket[by_bucket["bucket8"] == bucket]
            if not sub.empty:
                fig.add_trace(go.Scatter(x=sub["period"], y=sub["value"], mode="lines",
                                          stackgroup="one", groupnorm="percent", name=bucket,
                                          line=dict(color=FEED8_COLORS[bucket])))
        style_fig(fig, yaxis_title="Share of feedstock demand (%)")
        _snap_anchor("snap_feed_share")
        st.plotly_chart(fig, width='stretch')
        _snap_toolbar("snap_feed_share", "Feedstocks Demand Market Share for Biofuel Production")

        # ── Soybean Oil vs. Used Cooking Oil share trend ──────────────────────
        st.markdown("**Biofuel Feedstock Demand Market Share Trends**")
        st.markdown("<div class='note-text'>\"Used Cooking Oil\" here = Yellow Grease + White "
                    "Grease. The stacked area shows each feedstock's share of the Soybean Oil / "
                    "Corn Oil / Canola Oil / UCO subgroup only (left axis); the SBO/UCO share "
                    "lines instead show each as a share of <em>all</em> biodiesel-RD feedstock "
                    "demand, including tallow, poultry fat, and other (right axis) — a "
                    "best-effort match to your sheet pending the source file.</div>",
                    unsafe_allow_html=True)
        core = bd[bd["bucket8"].isin(["Soybean Oil", "Corn Oil", "Canola Oil",
                                       "Yellow Grease", "White Grease"])].copy()
        core["core_bucket"] = core["bucket8"].replace(
            {"Yellow Grease": "Used Cooking Oil", "White Grease": "Used Cooking Oil"})
        core_by_bucket = core.groupby(["period", "core_bucket"])["value"].sum().reset_index()

        full_totals = by_bucket.groupby("period")["value"].sum()
        sbo_full = by_bucket[by_bucket["bucket8"] == "Soybean Oil"].set_index("period")["value"]
        uco_full = by_bucket[by_bucket["bucket8"].isin(["Yellow Grease", "White Grease"])] \
            .groupby("period")["value"].sum()
        sbo_share_full = (sbo_full / full_totals * 100).dropna()
        uco_share_full = (uco_full / full_totals * 100).dropna()

        CORE_COLORS = {"Soybean Oil": "#b0d8e8", "Corn Oil": "#f0b429", "Canola Oil": "#8b4513",
                       "Used Cooking Oil": "#5b8c3a"}
        fig = go.Figure()
        for bucket, color in CORE_COLORS.items():
            sub = core_by_bucket[core_by_bucket["core_bucket"] == bucket]
            if not sub.empty:
                fig.add_trace(go.Scatter(x=sub["period"], y=sub["value"], mode="lines",
                                          stackgroup="one", groupnorm="percent", name=bucket,
                                          line=dict(color=color), yaxis="y"))
        fig.add_trace(go.Scatter(x=sbo_share_full.index, y=sbo_share_full.values, mode="lines",
                                  name="SBO Share (of total)", line=dict(color=DM_TEXT, width=2, dash="dash"),
                                  yaxis="y2"))
        fig.add_trace(go.Scatter(x=uco_share_full.index, y=uco_share_full.values, mode="lines",
                                  name="UCO Share (of total)", line=dict(color=DM_TEXT, width=2, dash="dot"),
                                  yaxis="y2"))
        style_fig(fig, yaxis_title="Share of core feedstocks (%)")
        fig.update_layout(yaxis2=dict(title="Share of total feedstocks (%)", overlaying="y",
                                       side="right", gridcolor=DM_BORDER, range=[0, 60]))
        _snap_anchor("snap_feed_trends")
        st.plotly_chart(fig, width='stretch')
        _snap_toolbar("snap_feed_trends", "Biofuel Feedstock Demand Market Share Trends")

        with st.expander("Full feedstock breakdown (latest month)"):
            tbl = fs[fs["period"] == latest_m][["feedstock", "family", "value"]].sort_values(
                "value", ascending=False).rename(columns={"value": "Million lbs"})
            st.dataframe(tbl, width='stretch', hide_index=True)
    else:
        st.info("No feedstock data returned.")

    # ── Fuel consumed at biofuel plants ──────────────────────────────────────
    section_header("Fuel Consumed at Biofuels Plants")
    if not plant_fuel.empty:
        pf = plant_fuel.copy()
        pf["fuel"] = pf["process"].map({
            "819NG0": "Natural Gas", "819BG0": "Biogas", "819LPG": "Liquefied Petroleum Gases",
            "819PE0": "Purchased Electricity", "819CL0": "Coal", "819PS0": "Purchased Steam",
            "819PH0": "Purchased Hydrogen", "819OF0": "Other fuels including renewable fuels",
            "819NGHF": "Natural Gas for Hydrogen Feedstock",
        })
        pf = pf.dropna(subset=["fuel"])
        fig = go.Figure()
        for fuel, color in PLANT_FUEL_COLORS.items():
            sub = pf[pf["fuel"] == fuel]
            if not sub.empty:
                fig.add_trace(go.Bar(x=sub["period"], y=sub["value"] / 1000, name=fuel,
                                      marker_color=color))
        fig.update_layout(barmode="stack")
        style_fig(fig, yaxis_title="Bcf-equivalent / year")
        _snap_anchor("snap_plant_fuel")
        st.plotly_chart(fig, width='stretch')
        _snap_toolbar("snap_plant_fuel", "Fuel Consumed at Biofuels Plants")
        st.markdown("<div class='note-text'>Annual survey data; values are reported in each "
                    "fuel's native unit and shown here on a common index. Natural gas is the "
                    "dominant process fuel for U.S. biofuels production.</div>",
                    unsafe_allow_html=True)
    else:
        st.info("No biofuels plant fuel-use data returned.")

st.markdown("---")
st.markdown(
    f"<div class='note-text'>Data: U.S. Energy Information Administration, api.eia.gov/v2 "
    f"&nbsp;|&nbsp; John Stewart & Associates</div>",
    unsafe_allow_html=True,
)
