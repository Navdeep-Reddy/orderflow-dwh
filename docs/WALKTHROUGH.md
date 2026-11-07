# Walkthrough guide (for the review conversation)

**30-second pitch.** Two file sources → validate/quarantine → one transaction per source → event history + star schema →
reconciliation + run metrics. Reruns are safe by construction (content hash + natural-key upserts).

**Be ready to explain**
1. *Why order rows are events and the fact is derived* — late/out-of-order data can't regress state; history is never overwritten (`load_orders.rebuild_fact`).
2. *SCD2 by rebuild* — recompute versions from the event table for affected customers; keys stable via `(customer_id, valid_from)`; facts re-pointed *before* orphan versions are deleted (`load_customers.rebuild_dim_customer`).
3. *Transaction boundaries* — rejects commit first (always kept); load + `ingested_file` + metrics commit together; a crash leaves nothing half-loaded (`pipeline._run_source`).
4. *Idempotency layers* — sha256 skip → `INSERT OR IGNORE` on natural keys → upserts → reconciliation proves no duplicates.
5. *Data-quality gate* — why 30 %, and why rejects are stored even when the load is blocked.
6. *Reconciliation* — which checks are run-scoped vs warehouse-wide; the cross-batch conflicting re-send case (detected, exit 2, not auto-resolved).
7. *Money as integer minor units*, UTC normalisation, rejecting naive timestamps.
8. *Limitations* — in-memory accepted rows, no auto-replay of rejected orders, first-write-wins, SQLite single writer, no FX.

**Demo script (3 min).** `make test` → `make run` (show rejects by reason) → rerun (skipped) → `--force` (0 new) →
`run --orders data/sample/orders_late.csv` (history grows, state unchanged) → `run --orders data/sample/edge_cases/orders_schema_change_missing_column.csv` (FAILED, exit 1) →
`make validate report` → open `output/sample_run/reports/analytical_report.md`.

**Questions to practise.** What if `status_ts` ties? (PK makes ties impossible.) What if the same customer event arrives with different data later? (first write wins, flagged.) How would this scale to 100M rows? (chunked streaming validation, warehouse target, partitioned loads.) What would you change first with another day? (replay of rejects once customers arrive; resolve conflicting re-sends by `source_priority`.)
