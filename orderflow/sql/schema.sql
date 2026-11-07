-- orderflow schema (SQLite). All DDL is idempotent (IF NOT EXISTS).
-- Layers:  pipeline_run/run_source/reject_record = operational metadata
--          stg_customer_event / order_line_event = append-only history
--          dim_* / fact_* = star schema

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

-- ---------------------------------------------------------------- operational metadata
CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id                TEXT PRIMARY KEY,
    started_at            TEXT NOT NULL,
    ended_at              TEXT,
    status                TEXT NOT NULL CHECK (status IN ('RUNNING','SUCCESS','PARTIAL_FAILURE','FAILED')),
    failure_reason        TEXT,
    as_of                 TEXT NOT NULL,
    duration_seconds      REAL,
    reconciliation_passed INTEGER
);

CREATE TABLE IF NOT EXISTS run_source (
    run_id               TEXT NOT NULL REFERENCES pipeline_run(run_id),
    source_name          TEXT NOT NULL,
    source_file          TEXT NOT NULL,
    file_sha256          TEXT,
    status               TEXT NOT NULL CHECK (status IN ('SUCCESS','FAILED','SKIPPED_ALREADY_LOADED')),
    rows_read            INTEGER NOT NULL DEFAULT 0,
    rows_rejected        INTEGER NOT NULL DEFAULT 0,
    rows_accepted        INTEGER NOT NULL DEFAULT 0,
    rows_inserted_new    INTEGER NOT NULL DEFAULT 0,
    rows_already_present INTEGER NOT NULL DEFAULT 0,
    expected_amount_minor INTEGER,   -- sum of accepted line amounts (orders)
    target_amount_minor  INTEGER,    -- same keys, read back from the target table
    target_rows_found    INTEGER,    -- accepted keys found in target with identical payload
    warnings             TEXT,
    failure_reason       TEXT,
    started_at           TEXT NOT NULL,
    ended_at             TEXT,
    duration_seconds     REAL,
    PRIMARY KEY (run_id, source_name, source_file)
);

-- A file (by content hash) is registered here ONLY in the same transaction that loads it.
CREATE TABLE IF NOT EXISTS ingested_file (
    source_name TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    source_file TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    loaded_at   TEXT NOT NULL,
    rows_read   INTEGER NOT NULL,
    PRIMARY KEY (source_name, file_sha256)
);

-- Quarantine: every rejected record, traceable to file + physical line + raw content.
CREATE TABLE IF NOT EXISTS reject_record (
    reject_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL REFERENCES pipeline_run(run_id),
    source_name   TEXT NOT NULL,
    source_file   TEXT NOT NULL,
    line_number   INTEGER NOT NULL,
    reason_code   TEXT NOT NULL,
    reason_detail TEXT NOT NULL,
    raw_record    TEXT NOT NULL,
    rejected_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reject_run ON reject_record(run_id, source_name);

CREATE TABLE IF NOT EXISTS reconciliation_result (
    run_id     TEXT NOT NULL,
    check_name TEXT NOT NULL,
    expected   TEXT,
    actual     TEXT,
    passed     INTEGER NOT NULL,
    detail     TEXT,
    PRIMARY KEY (run_id, check_name)
);

-- ---------------------------------------------------------------- customers
-- Append-only history of customer change events (system of record for SCD2 rebuilds).
CREATE TABLE IF NOT EXISTS stg_customer_event (
    customer_id        TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    name               TEXT NOT NULL,
    email              TEXT NOT NULL,
    country            TEXT NOT NULL,
    segment            TEXT NOT NULL,
    run_id             TEXT NOT NULL,
    source_file        TEXT NOT NULL,
    source_line_number INTEGER NOT NULL,
    ingested_at        TEXT NOT NULL,
    PRIMARY KEY (customer_id, updated_at)
);
CREATE INDEX IF NOT EXISTS ix_stg_customer_run ON stg_customer_event(run_id);

-- SCD Type 2: one row per distinct version of a customer's tracked attributes.
CREATE TABLE IF NOT EXISTS dim_customer (
    customer_key TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL,
    country      TEXT NOT NULL,
    segment      TEXT NOT NULL,
    valid_from   TEXT NOT NULL,
    valid_to     TEXT NOT NULL,          -- exclusive; '9999-12-31T23:59:59Z' for current
    is_current   INTEGER NOT NULL CHECK (is_current IN (0,1)),
    run_id       TEXT NOT NULL,
    CHECK (valid_from < valid_to)
);
CREATE INDEX IF NOT EXISTS ix_dim_customer_lookup ON dim_customer(customer_id, valid_from);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dim_customer_current ON dim_customer(customer_id) WHERE is_current = 1;

-- ---------------------------------------------------------------- products / dates
-- SCD Type 1 (latest event wins; ties impossible because load order is sorted deterministically).
CREATE TABLE IF NOT EXISTS dim_product (
    product_key   TEXT PRIMARY KEY,
    product_code  TEXT NOT NULL UNIQUE,
    product_name  TEXT NOT NULL,
    category      TEXT NOT NULL,
    last_event_ts TEXT NOT NULL,
    run_id        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_key    INTEGER PRIMARY KEY,   -- YYYYMMDD
    full_date   TEXT NOT NULL UNIQUE,  -- YYYY-MM-DD
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    day         INTEGER NOT NULL,
    iso_week    INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL,      -- 1=Mon .. 7=Sun
    is_weekend  INTEGER NOT NULL
);

-- ---------------------------------------------------------------- orders
-- Append-only event history: one row per (order line, status_ts). Never updated or deleted.
CREATE TABLE IF NOT EXISTS order_line_event (
    order_id           TEXT NOT NULL,
    line_no            INTEGER NOT NULL,
    status_ts          TEXT NOT NULL,
    customer_id        TEXT NOT NULL,
    product_code       TEXT NOT NULL,
    product_name       TEXT NOT NULL,
    category           TEXT NOT NULL,
    quantity           INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_minor   INTEGER NOT NULL CHECK (unit_price_minor >= 0),
    currency           TEXT NOT NULL,
    line_amount_minor  INTEGER NOT NULL,
    order_ts           TEXT NOT NULL,
    status             TEXT NOT NULL,
    run_id             TEXT NOT NULL,
    source_file        TEXT NOT NULL,
    source_line_number INTEGER NOT NULL,
    ingested_at        TEXT NOT NULL,
    PRIMARY KEY (order_id, line_no, status_ts)
);
CREATE INDEX IF NOT EXISTS ix_event_run ON order_line_event(run_id);

-- Current state of each order line (derived from the latest event). Grain: one order line.
CREATE TABLE IF NOT EXISTS fact_order_line (
    order_line_key    TEXT PRIMARY KEY,
    order_id          TEXT NOT NULL,
    line_no           INTEGER NOT NULL,
    customer_key      TEXT NOT NULL REFERENCES dim_customer(customer_key),
    customer_id       TEXT NOT NULL,
    product_key       TEXT NOT NULL REFERENCES dim_product(product_key),
    date_key          INTEGER NOT NULL REFERENCES dim_date(date_key),
    order_ts          TEXT NOT NULL,
    status            TEXT NOT NULL,
    status_ts         TEXT NOT NULL,
    quantity          INTEGER NOT NULL,
    unit_price_minor  INTEGER NOT NULL,
    currency          TEXT NOT NULL,
    line_amount_minor INTEGER NOT NULL,
    is_net_revenue    INTEGER NOT NULL CHECK (is_net_revenue IN (0,1)),
    run_id            TEXT NOT NULL,
    UNIQUE (order_id, line_no)
);
CREATE INDEX IF NOT EXISTS ix_fact_customer ON fact_order_line(customer_id);
CREATE INDEX IF NOT EXISTS ix_fact_product  ON fact_order_line(product_key);
CREATE INDEX IF NOT EXISTS ix_fact_date     ON fact_order_line(date_key);

INSERT INTO schema_version (version)
SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
