# Design & trade-off note

## Flow
read (schema check) → validate/normalise row by row → dedupe → RI check → **quarantine all rejects** (own transaction, always kept) →
DQ gate (reject ratio ≤ 30 %) → **one transaction**: events → dimensions → fact → file registry → source metrics → reconciliation → run report.

## Key decisions
* **Event history + derived state.** Order status changes and may arrive late, so every event is stored once (`INSERT OR IGNORE` on its natural key) and the fact is *derived* from the newest event. Late or reordered events therefore cannot regress state, and nothing is silently overwritten.
* **SCD2 by rebuild.** For customers with new events we recompute all versions from the full event history. Simpler and safer than in-place close/open logic when events arrive out of order. Surrogate keys derive from `(customer_id, valid_from)` so they are stable; facts are re-pointed to the version valid at `order_ts` before obsolete versions are deleted. Orders older than the first known version use the earliest version (documented assumption).
* **Money as integer minor units**; prices with >2 decimals are rejected rather than rounded.
* **SQLite + stdlib** to make the project runnable anywhere in seconds. Set-based SQL (upserts, window functions) is used for the fact build; Python is used for validation.
* **Reject, don't fix.** Unparseable or rule-breaking rows are quarantined with raw content and exact line; only deterministic normalisation (case, UTC, trimming) is applied.
* **DQ gate.** If >30 % of a file is rejected (e.g. a silent upstream schema drift), nothing is loaded and the source fails; rejects are still stored for diagnosis.

## Idempotency, retry, restart, backfill
* *Same input again*: sha256 in `ingested_file` → `SKIPPED_ALREADY_LOADED` (cost ≈ hashing the file).
* *Forced / overlapping input*: natural-key `INSERT OR IGNORE` + upserts ⇒ 0 new rows, identical tables (tested).
* *Crash mid-load*: single transaction ⇒ all-or-nothing; registry row is committed with the data, so a failed file is never marked loaded. **Retry = run the same command.** Quarantine rows from the failed attempt remain as audit (they are per `run_id`).
* *One source failing* leaves the other loaded (`PARTIAL_FAILURE`); the next run skips what is done and retries the rest.
* *Backfill*: feed the historical file; events are merged by timestamp; only affected customers/lines are rebuilt.

## Observability
Structured log lines (`run_id`, source, counts, seconds) to stderr and `output/pipeline.log`; `pipeline_run` / `run_source` tables; JSON run report and rejects CSV per run; persisted reconciliation results; non-zero exit codes.

## Reconciliation
Per source: `read = rejected + accepted`, `accepted = new + already_present`, every accepted key is present in the target **with identical payload**, reject rows persisted and traceable, and (orders) `Σ line_amount` source = target. Warehouse-wide: fact = distinct lines in history, no orphan keys, fact state = latest event, SCD2 one-current / contiguous / non-overlapping, `customer_key` is the version valid at `order_ts`.

## Performance
See README. Bottlenecks are the SQLite load (~55 %) and Python row validation (~40 %). Measured optimisation: composite index for the SCD2 lookup (≈180×). Further options: chunked streaming validation, `PRAGMA synchronous=NORMAL` with WAL (already WAL), multiprocessing for validation.

## Limitations
First-write-wins for conflicting re-sends across batches (detected, not resolved); late-arriving customers are not auto-replayed for already-rejected orders; single-writer SQLite; no FX; synthetic data only.
