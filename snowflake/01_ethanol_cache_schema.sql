-- Schema + table for the EIA ethanol cache. Run once against the shared
-- JSA database before the first ETL run. Safe to re-run (IF NOT EXISTS).
--
-- One route can publish multiple processes/areas for the same product, and
-- the same (period, duoarea, product, process) can appear in more than one
-- EIA route -- e.g. weekly production shows up in both petroleum/sum/sndw
-- (national + PADD, process YOP) and petroleum/pnp/wprode (same PADDs, its
-- own series id). ROUTE is part of the key specifically so those don't
-- collide or overwrite each other.

CREATE SCHEMA IF NOT EXISTS JSA.EIA_ETHANOL_CACHE;

CREATE TABLE IF NOT EXISTS JSA.EIA_ETHANOL_CACHE.ETHANOL_WEEKLY (
    ROUTE        VARCHAR NOT NULL,   -- e.g. 'petroleum/sum/sndw'
    PERIOD       DATE NOT NULL,
    DUOAREA      VARCHAR NOT NULL,   -- NUS, R10..R50, or the move/wkly '-Z00' variants
    AREA_NAME    VARCHAR,
    PRODUCT      VARCHAR NOT NULL,   -- EPOOXE for all rows today; kept generic
    PRODUCT_NAME VARCHAR,
    PROCESS      VARCHAR NOT NULL,   -- YOP / SAE / YIR / EEX / IM0 / ...
    PROCESS_NAME VARCHAR,
    SERIES       VARCHAR,
    VALUE        FLOAT,
    UNITS        VARCHAR,
    FETCHED_AT   TIMESTAMP_NTZ NOT NULL,
    PRIMARY KEY (ROUTE, PERIOD, DUOAREA, PRODUCT, PROCESS)
);
