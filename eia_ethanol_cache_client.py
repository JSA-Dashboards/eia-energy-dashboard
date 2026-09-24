"""
eia_ethanol_cache_client.py -- read-only fuel-ethanol cache client.

A scheduled Droplet job (deploy/run_ethanol_etl.py) pulls the ethanol-specific
EIA routes on a schedule and writes them into JSA.EIA_ETHANOL_CACHE.
ETHANOL_WEEKLY. Reading that cache instead of calling EIA live means the
dashboard loads fast from a cold boot and isn't at the mercy of a visitor's
timing relative to EIA's own website-vs-API publish lag (the site can show a
new weekly report hours before api.eia.gov actually has it).

Unlike NASS, EIA's API has no per-key sharing restriction -- this cache is
purely a warmth/staleness fix, not a ToS requirement. So fetch_ethanol_cached()
falls back to a live EIA call when USE_SNOWFLAKE isn't set, which keeps local
dev working without any Snowflake setup.

Returns a DataFrame shaped exactly like app.py's eia_get(): period (datetime),
duoarea, area-name, product, product-name, process, process-name, series,
value (float), units -- so call sites need no changes beyond swapping the
fetch function.
"""
import os

import pandas as pd

try:
    import streamlit as st
except ImportError:
    st = None

_COLUMN_MAP = {
    "ROUTE": "route", "PERIOD": "period", "DUOAREA": "duoarea",
    "AREA_NAME": "area-name", "PRODUCT": "product", "PRODUCT_NAME": "product-name",
    "PROCESS": "process", "PROCESS_NAME": "process-name", "SERIES": "series",
    "VALUE": "value", "UNITS": "units", "FETCHED_AT": "fetched_at",
}


def _secret(key: str, default: str = "") -> str:
    if st is not None:
        try:
            v = st.secrets.get(key, "")
            if v:
                return str(v).strip()
        except Exception:
            pass
    return os.environ.get(key, default).strip()


def _use_sf() -> bool:
    return _secret("USE_SNOWFLAKE").lower() in ("1", "true", "yes", "on")


def _load_private_key():
    pem = _secret("SNOWFLAKE_PRIVATE_KEY")
    path = _secret("SNOWFLAKE_PRIVATE_KEY_PATH")
    if not pem and not path:
        return None
    from cryptography.hazmat.primitives import serialization
    data = open(path, "rb").read() if path else pem.replace("\\n", "\n").encode()
    pwd = _secret("SNOWFLAKE_PRIVATE_KEY_PWD") or None
    key = serialization.load_pem_private_key(data, password=pwd.encode() if pwd else None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())


def _sf_connect():
    import snowflake.connector
    kw = dict(
        account=_secret("SNOWFLAKE_ACCOUNT"),
        user=_secret("SNOWFLAKE_USER"),
        role=_secret("SNOWFLAKE_ROLE") or None,
        warehouse=_secret("SNOWFLAKE_WAREHOUSE") or None,
        database=_secret("SNOWFLAKE_DATABASE") or "JSA",
        schema=_secret("SNOWFLAKE_SCHEMA") or "EIA_ETHANOL_CACHE",
        login_timeout=30,
    )
    pkey = _load_private_key()
    if pkey is not None:
        kw["private_key"] = pkey
    else:
        kw["password"] = _secret("SNOWFLAKE_PASSWORD")
    return snowflake.connector.connect(**kw)


def fetch_ethanol_cached(route: str, facets: dict | None, live_fallback) -> pd.DataFrame:
    """`facets` matches app.py's eia_get() shape ({"duoarea": [...], "process": [...], ...}).
    `live_fallback` is a zero-arg callable that performs the equivalent live
    eia_get() call -- used when USE_SNOWFLAKE isn't set, or if the Snowflake
    read itself fails (a stale cache beats a broken dashboard)."""
    if not _use_sf():
        return live_fallback()

    where = ["ROUTE = %s"]
    params = [route]
    for key, vals in (facets or {}).items():
        if isinstance(vals, str):
            vals = [vals]
        col = key.upper()
        placeholders = ",".join(["%s"] * len(vals))
        where.append(f"{col} IN ({placeholders})")
        params.extend(vals)

    sql = f"SELECT * FROM ETHANOL_WEEKLY WHERE {' AND '.join(where)}"
    try:
        conn = _sf_connect()
        try:
            df = pd.read_sql(sql, conn, params=params)
        finally:
            conn.close()
    except Exception as e:
        if st is not None:
            st.warning(f"Ethanol cache read failed ({e}); falling back to live EIA call.")
        return live_fallback()

    if df.empty:
        return df
    df.columns = [_COLUMN_MAP.get(c, c.lower()) for c in df.columns]
    df["period"] = pd.to_datetime(df["period"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.sort_values("period").reset_index(drop=True)


def cache_freshness() -> str | None:
    """Most recent FETCHED_AT across the ethanol cache, or None. For an
    'as of ...' footer caption."""
    if not _use_sf():
        return None
    try:
        conn = _sf_connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT MAX(FETCHED_AT) FROM ETHANOL_WEEKLY")
            row = cur.fetchone()
            return str(row[0]) if row and row[0] else None
        finally:
            conn.close()
    except Exception:
        return None
