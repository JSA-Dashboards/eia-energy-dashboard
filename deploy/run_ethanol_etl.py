"""
run_ethanol_etl.py -- pulls all fuel-ethanol series the dashboard uses from
the live EIA API and MERGEs them into JSA.EIA_ETHANOL_CACHE.ETHANOL_WEEKLY.

Run on a schedule from the Droplet (see run_ethanol_etl.sh); the Streamlit
app reads this cache instead of calling EIA live, so it loads fast from a
cold boot and doesn't depend on a visitor's timing relative to EIA's own
website-vs-API publish lag.

Covers only the ethanol-specific routes/facets the dashboard actually uses:
  - petroleum/sum/sndw   product=EPOOXE, duoarea=[NUS,R10..R50], process=[YOP,SAE,YIR]
  - petroleum/pnp/wprode product=EPOOXE, all duoarea
  - petroleum/move/wkly  product=EPOOXE, process=[EEX,IM0], duoarea=[NUS-Z00,R10-Z00..R50-Z00]

Natural gas, biofuel feedstocks, capacity, and non-ethanol petroleum stocks
(crude/gasoline/distillate/propane) are NOT pulled here -- those stay live.

Env vars required: EIA_API_KEY, plus the fleet-standard SNOWFLAKE_* vars
(SNOWFLAKE_PRIVATE_KEY_PATH preferred over SNOWFLAKE_PASSWORD, same as
every other Droplet job).
"""
import os
import sys
from datetime import datetime, timezone

import requests

BASE_URL = "https://api.eia.gov/v2"
API_KEY = os.environ.get("EIA_API_KEY", "").strip()

PADD_CODES = ["NUS", "R10", "R20", "R30", "R40", "R50"]
PADD_MOVE_CODES = ["NUS-Z00", "R10-Z00", "R20-Z00", "R30-Z00", "R40-Z00", "R50-Z00"]

ROUTES = [
    ("petroleum/sum/sndw", {"product": ["EPOOXE"], "duoarea": PADD_CODES,
                            "process": ["YOP", "SAE", "YIR"]}),
    ("petroleum/pnp/wprode", {"product": ["EPOOXE"]}),
    ("petroleum/move/wkly", {"product": ["EPOOXE"], "process": ["EEX", "IM0"],
                             "duoarea": PADD_MOVE_CODES}),
]


def _facet_params(facets: dict) -> list:
    out = []
    for key, vals in facets.items():
        for v in vals:
            out.append((f"facets[{key}][]", v))
    return out


def fetch_route(session: requests.Session, route: str, facets: dict) -> list[dict]:
    """Paginated fetch, mirrors app.py's eia_get() exactly."""
    params_base = [("api_key", API_KEY), ("data[0]", "value"),
                   ("sort[0][column]", "period"), ("sort[0][direction]", "desc")]
    params_base += _facet_params(facets)

    length, offset, rows = 5000, 0, []
    while True:
        params = params_base + [("offset", offset), ("length", length)]
        r = session.get(f"{BASE_URL}/{route}/data/", params=params, timeout=30)
        r.raise_for_status()
        js = r.json()
        data = js.get("response", {}).get("data", [])
        rows.extend(data)
        total = int(js.get("response", {}).get("total", len(rows)) or 0)
        offset += length
        if len(data) < length or offset >= total:
            break
    return rows


def _load_private_key():
    """RSA private key (DER bytes) for Snowflake key-pair auth; None falls
    back to SNOWFLAKE_PASSWORD. Same helper as every other Droplet job."""
    path = (os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH") or "").strip()
    pem = os.environ.get("SNOWFLAKE_PRIVATE_KEY") or ""
    if not path and not pem.strip():
        return None
    from cryptography.hazmat.primitives import serialization
    data = open(path, "rb").read() if path else pem.replace("\\n", "\n").encode()
    pwd = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PWD") or None
    key = serialization.load_pem_private_key(data, password=pwd.encode() if pwd else None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())


def sf_connect():
    import snowflake.connector
    kw = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        role=os.environ.get("SNOWFLAKE_ROLE") or None,
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE") or None,
        database=os.environ.get("SNOWFLAKE_DATABASE") or "JSA",
        schema=os.environ.get("SNOWFLAKE_SCHEMA") or "EIA_ETHANOL_CACHE",
        login_timeout=30,
    )
    pkey = _load_private_key()
    if pkey is not None:
        kw["private_key"] = pkey
    else:
        kw["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    return snowflake.connector.connect(**kw)


def merge_rows(conn, route: str, rows: list[dict]):
    if not rows:
        print(f"  {route}: 0 rows returned, nothing to merge")
        return
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    # De-dupe on the MERGE key before staging: EIA's API only sorts by period,
    # so with >5000 matching rows (this route has ~15k) pagination can return
    # the same (period, duoarea, product, process) row twice across a page
    # boundary when many rows share a period and the tie-break order isn't
    # stable between calls. Snowflake's MERGE rejects a source with duplicate
    # keys outright ("Duplicate row detected during DML action"), so last-one
    # wins here rather than trusting the API to never repeat a row.
    deduped = {}
    for r in rows:
        key = (route, r["period"], r["duoarea"], r["product"], r["process"])
        deduped[key] = r
    staged = [
        (route, r["period"], r["duoarea"], r.get("area-name"), r["product"],
         r.get("product-name"), r["process"], r.get("process-name"),
         r.get("series"), float(r["value"]) if r.get("value") not in (None, "") else None,
         r.get("units"), fetched_at)
        for r in deduped.values()
    ]
    if len(staged) < len(rows):
        print(f"  {route}: deduped {len(rows)} -> {len(staged)} rows before merge")
    cur = conn.cursor()
    cur.execute("""
        CREATE OR REPLACE TEMPORARY TABLE _stage (
            ROUTE VARCHAR, PERIOD DATE, DUOAREA VARCHAR, AREA_NAME VARCHAR,
            PRODUCT VARCHAR, PRODUCT_NAME VARCHAR, PROCESS VARCHAR, PROCESS_NAME VARCHAR,
            SERIES VARCHAR, VALUE FLOAT, UNITS VARCHAR, FETCHED_AT TIMESTAMP_NTZ
        )
    """)
    cur.executemany(
        "INSERT INTO _stage VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", staged)
    cur.execute("""
        MERGE INTO ETHANOL_WEEKLY t USING _stage s
        ON t.ROUTE = s.ROUTE AND t.PERIOD = s.PERIOD AND t.DUOAREA = s.DUOAREA
           AND t.PRODUCT = s.PRODUCT AND t.PROCESS = s.PROCESS
        WHEN MATCHED THEN UPDATE SET
            t.AREA_NAME = s.AREA_NAME, t.PRODUCT_NAME = s.PRODUCT_NAME,
            t.PROCESS_NAME = s.PROCESS_NAME, t.SERIES = s.SERIES,
            t.VALUE = s.VALUE, t.UNITS = s.UNITS, t.FETCHED_AT = s.FETCHED_AT
        WHEN NOT MATCHED THEN INSERT
            (ROUTE, PERIOD, DUOAREA, AREA_NAME, PRODUCT, PRODUCT_NAME,
             PROCESS, PROCESS_NAME, SERIES, VALUE, UNITS, FETCHED_AT)
        VALUES
            (s.ROUTE, s.PERIOD, s.DUOAREA, s.AREA_NAME, s.PRODUCT, s.PRODUCT_NAME,
             s.PROCESS, s.PROCESS_NAME, s.SERIES, s.VALUE, s.UNITS, s.FETCHED_AT)
    """)
    conn.commit()
    print(f"  {route}: merged {len(staged)} rows")


def main():
    if not API_KEY:
        print("ERROR: EIA_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    conn = sf_connect()
    try:
        for route, facets in ROUTES:
            print(f"Fetching {route} ...")
            rows = fetch_route(session, route, facets)
            merge_rows(conn, route, rows)
    finally:
        conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
