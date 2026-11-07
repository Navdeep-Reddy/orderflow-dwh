# orderflow — a small, tested, idempotent data platform

Ingests **two sources** (customer change events as JSON Lines, order-line status events as CSV),
validates and quarantines bad records, loads a **star schema** (SCD2 customer, SCD1 product, date,
order-line fact) into SQLite, exposes **analytical SQL**, and records run metrics, reconciliation
results, tests and docs. Python stdlib only — no installs, no credentials, no network.

```
 customers.jsonl ─┐                      ┌─► reject_record (quarantine: file, line, reason, raw)
                  ├─► read ─► validate ──┤
 orders.csv ──────┘   (schema   (rules,   └─► accepted ─► ONE transaction per source:
                       check)    dedupe,                    stg_customer_event ─► dim_customer (SCD2)
                                 RI check,                  order_line_event ──► dim_product (SCD1), dim_date,
                                 DQ gate)                                         fact_order_line (current state)
                                                            + ingested_file registry + run_source metrics
 then ─► reconciliation checks (persisted) ─► run_report_<run_id>.json ─► analytical SQL / CSV / Markdown
```

## Quick start (clean environment)
```bash
python3 --version                 # 3.10+ (developed/tested on 3.12, SQLite 3.45; needs SQLite >= 3.35)
make test                         # 67 tests
make run                          # loads data/sample into output/warehouse.db
make validate report              # invariants + output/reports/analytical_report.md
make bench                        # ~150k rows (see numbers below)
```
Without `make`: `python3 -m orderflow run --customers data/sample/customers.jsonl --orders data/sample/orders.csv`.
CLI: `generate | run | validate | report | benchmark` (`python3 -m orderflow <cmd> -h`).
Exit codes: `0` ok · `1` a source/run failed · `2` loaded but a reconciliation check failed.

## How to verify key behaviour (≈2 minutes)
```bash
make test                                   # 67 tests, incl. ground-truth comparison against data/sample/expected/
make run && make validate                   # sample load; all invariants [PASS]
python3 -m orderflow run --customers data/sample/customers.jsonl --orders data/sample/orders.csv --as-of 2026-11-06T00:00:00Z   # rerun -> SKIPPED_ALREADY_LOADED
python3 -m orderflow run --orders data/sample/orders_late.csv --as-of 2026-11-06T00:00:00Z                                   # late batch: history grows, state unchanged
python3 -m orderflow run --orders data/sample/edge_cases/orders_schema_change_missing_column.csv; echo $?                    # FAILED, exit 1, nothing loaded
```
Expected outputs are captured in `output/execution_evidence.txt`, `output/test_results.txt` and `output/sample_run/`.

## Layout
| Path | Purpose |
|---|---|
| `orderflow/` | pipeline: `sources` (readers) · `validate` (rules) · `load_customers` / `load_orders` · `pipeline` (orchestration) · `reconcile` · `reports` · `generate_data` · `benchmark` · `cli` |
| `orderflow/sql/schema.sql`, `sql/queries/` | DDL and 8 analytical queries |
| `data/sample/` | input sample, `expected/expected_labels.json` (answer key, **separate from inputs**), `edge_cases/` |
| `tests/` | 67 tests · `output/` committed evidence: `test_results.txt`, `execution_evidence.txt`, `sample_run/` (run report, rejects CSV, reports), `benchmark/benchmark_results.json` |
| `docs/DATA_DICTIONARY.md`, `docs/DESIGN.md` | schemas, rules, trade-offs, recovery, performance |
| `docs/ai/` | **AI disclosure**: `PROMPT_HISTORY.md`, `AI_USAGE.md` · `docs/WALKTHROUGH.md` (review-conversation guide) · `SUBMISSION_CHECKLIST.md` |

## Conventions
snake_case tables/columns · ISO-8601 UTC timestamps (`YYYY-MM-DDTHH:MM:SSZ`; naive timestamps are rejected,
offsets are normalised) · money stored as integer minor units (no floats) · deterministic IDs:
`customer_key = ck_sha1(customer_id|valid_from)`, `product_key = pk_sha1(code)`,
`order_line_key = ol_sha1(order_id|line_no)` · every row carries `run_id` and `source_file` (+ source line).
`run_id = run_<UTC timestamp>_<input fingerprint>`.

## Key assumptions
* Orders and customers arrive as files; each order row is a **status event** for an order line
  (key `order_id, line_no, status_ts`). Fact grain = one order line, showing its latest status.
* A customer exists only after its first valid change event; orders for unknown customers are rejected (`INVALID_CUSTOMER_REF`).
  Customers load before orders within a run.
* Net revenue excludes lines whose latest status is `CANCELLED`/`RETURNED`. Revenue is reported per currency (no FX).
* Same key + different payload: **first occurrence wins**, later ones are rejected (`CONFLICTING_DUPLICATE`).

## What is covered (requirements → where)
| Requirement | Evidence |
|---|---|
| malformed input, missing field, nulls | `test_orders_pipeline`, `test_customers_scd2`, `test_validate` |
| duplicate key/event (in-file and cross-batch) | `OrdersDuplicates`, `Idempotency.test_conflicting_resend…` |
| unexpected schema change | missing required column → source `FAILED`, nothing loaded; extra column → warning |
| empty batch | header-only and zero-byte files |
| partial failure + retry | `PartialFailureAndRetry` (injected crash → full rollback → plain rerun succeeds) |
| late / out-of-order data | events merged by `status_ts`; late customer history re-points facts; `orders_late.csv` |
| invalid reference key | `INVALID_CUSTOMER_REF` |
| rerun of same input | sha256 registry → `SKIPPED_ALREADY_LOADED`; `--force` inserts 0 new rows |
| domain edge cases | status before order, future timestamps, free items, qty/price boundaries, SCD2 point-in-time, product SCD1 with late older event, cancelled excluded from revenue |
| reconciliation | run-scoped (counts, payload match, amount) + 12 warehouse invariants; tests corrupt the DB to prove they fail |

## Results on the sample (`output/execution_evidence.txt`)
customers: 46 read → 35 accepted / 11 rejected · orders: 115 read → 91 / 24 · every reject matches its label
by (line, reason) · fact, status counts, net revenue per currency, revenue by segment-at-order-time and
SCD2 version count equal the independently derived expected values (`test_sample_data`).

## Performance (`make bench`, 1 vCPU sandbox)
151,162 input rows (7.9k customer events, 143k order events → 50k order lines):
first run **10.5 s ≈ 14.4k rows/s** (orders: validate 3.8 s, load 5.3 s) · rerun of same input 0.45 s ·
forced re-load 7.1 s, 0 new rows · DB 66 MB · peak RSS ≈ 217 MB.
**Optimisation measured:** composite index `dim_customer(customer_id, valid_from)` makes the SCD2
point-in-time lookup **~180× faster** (1.16 s → 0.006 s for 5,000 facts). Other next steps: stream-validate
instead of holding accepted rows in memory; `executemany` is already batched (`--batch-size`).

## Known limitations / next steps
* Memory: accepted rows of one file are held in memory (fine to a few million rows; stream/chunk beyond).
* Rejected orders whose customer arrives later are not auto-replayed (quarantine keeps the raw row; a `replay-rejects` command is the next step).
* Same event key re-sent later with a different payload is **detected** (reconciliation fails, exit 2) but not auto-resolved.
* SQLite is single-writer; a production target would be a warehouse (the SQL is standard apart from upsert syntax).
* No FX conversion; no orchestration scheduler (run the CLI from cron/Airflow); no PII handling beyond synthetic data.

## AI usage (transparent disclosure)
Built with Claude (claude.ai chat) as the only AI tool. The first full version of the code, tests and docs was AI-generated
from the brief; what I reviewed, changed and validated is recorded in [`docs/ai/AI_USAGE.md`](docs/ai/AI_USAGE.md), and every
prompt I sent is in [`docs/ai/PROMPT_HISTORY.md`](docs/ai/PROMPT_HISTORY.md). Data is fully synthetic; no secrets or third-party code.
