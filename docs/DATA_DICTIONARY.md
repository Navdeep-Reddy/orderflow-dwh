# Data dictionary

Nullability: **R** = required (blank/absent ⇒ reject `MISSING_REQUIRED_FIELD`), **O** = optional.

## Source 1 — `customers.jsonl` (one JSON object per line; a customer *change event*)
| field | type | rule |
|---|---|---|
| customer_id R | string | `^C\d{6}$` |
| name R | string | non-blank |
| email R | string | `x@y.z`; stored lower-case |
| country R | string | one of IN US GB DE SG AE FR AU (upper-cased) |
| segment R | string | RETAIL / SMB / ENTERPRISE (upper-cased) |
| updated_at R | ISO-8601 with tz | normalised to UTC; event identity = `(customer_id, updated_at)` |
Unknown extra fields are ignored with a warning.

## Source 2 — `orders.csv` (header required; one row = one status event of an order line)
Required columns: `order_id line_no customer_id product_code product_name quantity unit_price currency order_ts status status_ts`; optional: `category`.
A missing required column fails the file (`SCHEMA_ERROR`); extra columns are ignored with a warning; column order is free.
| column | rule |
|---|---|
| order_id | `^ORD-\d{8}$` |
| line_no | integer 1..999 |
| customer_id | `^C\d{6}$` **and must exist in dim_customer** (`INVALID_CUSTOMER_REF`) |
| product_code | `^P\d{4}$` |
| quantity | integer 1..10000 |
| unit_price | decimal ≥ 0, ≤ 1000000.00, max 2 decimals (0.00 allowed) |
| currency | INR / USD / EUR |
| status | PLACED / SHIPPED / DELIVERED / CANCELLED / RETURNED |
| order_ts, status_ts | ISO-8601 with tz; `status_ts ≥ order_ts`; neither later than `as_of + 24h` |
| category O | blank ⇒ `UNCATEGORISED` |
Event identity = `(order_id, line_no, status_ts)`.

## Reject reason codes
`MALFORMED_RECORD` (bad JSON / wrong column count) · `MISSING_REQUIRED_FIELD` · `INVALID_FORMAT` · `INVALID_ENUM` ·
`INVALID_TIMESTAMP` · `OUT_OF_RANGE` · `FUTURE_TIMESTAMP` · `STATUS_BEFORE_ORDER` · `INVALID_CUSTOMER_REF` ·
`DUPLICATE_EVENT` (identical repeat) · `CONFLICTING_DUPLICATE` (same key, different payload). Only the first failing rule is reported (fixed rule order).

## Target tables (SQLite, see `orderflow/sql/schema.sql`)
| table | grain / key | notes |
|---|---|---|
| `stg_customer_event` | PK `(customer_id, updated_at)` | append-only history; `run_id, source_file, source_line_number, ingested_at` |
| `dim_customer` | PK `customer_key`; unique `(customer_id, valid_from)` | **SCD2**: `valid_from` inclusive, `valid_to` exclusive (`9999-12-31T23:59:59Z` = current), `is_current`; one current row per customer (partial unique index). No-op updates do not create versions. |
| `dim_product` | PK `product_key`, unique `product_code` | **SCD1**, latest `status_ts` wins |
| `dim_date` | PK `date_key` (YYYYMMDD) | year, month, day, iso_week, day_of_week, is_weekend |
| `order_line_event` | PK `(order_id, line_no, status_ts)` | append-only history; derived `line_amount_minor = quantity × unit_price_minor` |
| `fact_order_line` | PK `order_line_key`; unique `(order_id, line_no)` | current state = event with max `status_ts`; FKs to dim_customer (version valid at `order_ts`), dim_product, dim_date; `is_net_revenue` = status ∉ {CANCELLED, RETURNED}; money in minor units |
| `pipeline_run` | PK `run_id` | status RUNNING/SUCCESS/PARTIAL_FAILURE/FAILED, start/end, duration, `failure_reason`, `reconciliation_passed` |
| `run_source` | PK `(run_id, source_name, source_file)` | sha256, rows read/accepted/rejected/inserted_new/already_present, expected vs target amount, warnings, failure reason |
| `ingested_file` | PK `(source_name, file_sha256)` | written in the same transaction as the load |
| `reject_record` | per rejected row | `run_id, source_name, source_file, line_number, reason_code, reason_detail, raw_record` |
| `reconciliation_result` | PK `(run_id, check_name)` | expected / actual / passed |

Derived fields: `line_amount_minor`, `is_net_revenue`, `date_key`, normalised UTC timestamps, SCD2 `valid_to`/`is_current`.
Referential integrity: enforced by SQLite foreign keys on the fact, plus reconciliation checks.
