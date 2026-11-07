# Analytical report

## q1_daily_net_revenue

_Daily net revenue by currency. Net = lines not CANCELLED/RETURNED (latest status). Amounts in major units._

| order_date | currency | order_lines | units | net_revenue |
|---|---|---|---|---|
| 2026-01-18 | EUR | 2 | 4 | 142.62 |
| 2026-02-16 | EUR | 1 | 1 | 49.86 |
| 2026-02-25 | INR | 2 | 8 | 2138.51 |
| 2026-02-26 | USD | 2 | 5 | 300.98 |
| 2026-03-10 | EUR | 1 | 1 | 326.42 |
| 2026-03-26 | USD | 2 | 7 | 850.2 |
| 2026-04-03 | USD | 1 | 1 | 47.54 |
| 2026-04-16 | EUR | 1 | 1 | 49.86 |
| 2026-04-30 | USD | 1 | 1 | 404.2 |
| 2026-05-01 | EUR | 2 | 4 | 703.91 |
| 2026-05-02 | USD | 1 | 1 | 8.36 |
| 2026-05-10 | INR | 2 | 4 | 413.37 |
| … 5 more rows (see CSV) | | | | |

## q2_top_products_per_category

_Top 3 products by net revenue within each category and currency (window function)._

| category | currency | product_code | product_name | net_revenue | rnk |
|---|---|---|---|---|---|
| Books | EUR | P0010 | Product 10 | 0.0 | 1 |
| Books | INR | P0010 | Product 10 | 1083.0 | 1 |
| Books | INR | P0004 | Product 4 | 437.37 | 2 |
| Electronics | EUR | P0012 | Product 12 | 142.62 | 1 |
| Electronics | INR | P0006 | Product 6 | 1212.6 | 1 |
| Electronics | INR | P0012 | Product 12 | 142.62 | 2 |
| Electronics | USD | P0006 | Product 6 | 1212.6 | 1 |
| Electronics | USD | P0012 | Product 12 | 47.54 | 2 |
| Fashion | EUR | P0003 | Product 3 | 695.55 | 1 |
| Fashion | EUR | P0009 | Product 9 | 326.42 | 2 |
| Fashion | INR | P0009 | Product 9 | 1305.68 | 1 |
| Fashion | INR | P0003 | Product 3 | 1159.25 | 2 |
| … 11 more rows (see CSV) | | | | | |

## q3_revenue_by_segment_point_in_time

_SCD2 in action: revenue by the segment the customer had WHEN THE ORDER WAS PLACED, next to the_

| segment_at_order_time | segment_today | currency | order_lines | net_revenue |
|---|---|---|---|---|
| ENTERPRISE | ENTERPRISE | EUR | 4 | 1080.19 |
| RETAIL | RETAIL | EUR | 3 | 192.48 |
| ENTERPRISE | ENTERPRISE | INR | 7 | 1033.18 |
| RETAIL | RETAIL | INR | 7 | 4923.16 |
| ENTERPRISE | ENTERPRISE | USD | 3 | 802.09 |
| RETAIL | RETAIL | USD | 4 | 713.54 |
| SMB | SMB | USD | 2 | 850.2 |

## q4_status_funnel_by_month

_Final status of order lines per month, with cancellation and return rates._

| year | month | order_lines | delivered | shipped | placed_only | cancelled | returned | cancel_rate_pct | return_rate_pct |
|---|---|---|---|---|---|---|---|---|---|
| 2026 | 1 | 4 | 1 | 1 | 0 | 1 | 1 | 25.0 | 25.0 |
| 2026 | 2 | 7 | 3 | 1 | 1 | 1 | 1 | 14.3 | 14.3 |
| 2026 | 3 | 4 | 3 | 0 | 0 | 1 | 0 | 25.0 | 0.0 |
| 2026 | 4 | 4 | 2 | 1 | 0 | 0 | 1 | 0.0 | 25.0 |
| 2026 | 5 | 21 | 14 | 1 | 2 | 3 | 1 | 14.3 | 4.8 |

## q5_top_customers

_Top 5 customers by net revenue in each currency (current attributes shown)._

| currency | customer_id | name | country | current_segment | orders | net_revenue | rnk |
|---|---|---|---|---|---|---|---|
| EUR | C000014 | Customer 14 | SG | ENTERPRISE | 1 | 703.91 | 1 |
| EUR | C000009 | Customer 9 | IN | ENTERPRISE | 1 | 326.42 | 2 |
| EUR | C000019 | Customer 19 | AU | RETAIL | 1 | 142.62 | 3 |
| EUR | C000013 | Customer 13 | AE | RETAIL | 1 | 49.86 | 4 |
| EUR | C000025 | Customer 25 | IN | ENTERPRISE | 1 | 49.86 | 4 |
| INR | C000001 | Customer 1 | US | RETAIL | 1 | 2635.07 | 1 |
| INR | C000004 | Customer 4 | US | RETAIL | 1 | 2138.51 | 2 |
| INR | C000005 | Customer 5 | AE | ENTERPRISE | 1 | 437.37 | 3 |
| INR | C000009 | Customer 9 | IN | ENTERPRISE | 1 | 413.37 | 4 |
| INR | C000013 | Customer 13 | AE | RETAIL | 1 | 149.58 | 5 |
| USD | C000024 | Customer 24 | US | SMB | 1 | 850.2 | 1 |
| USD | C000003 | Customer 3 | US | ENTERPRISE | 1 | 754.55 | 2 |
| … 3 more rows (see CSV) | | | | | | | |

## q6_delivery_time_by_category

_Uses the preserved event HISTORY: hours from PLACED to DELIVERED per product category._

| category | delivered_lines | avg_hours_to_deliver | min_hours | max_hours |
|---|---|---|---|---|
| Books | 4 | 170.2 | 105.0 | 223.0 |
| Electronics | 4 | 180.2 | 125.0 | 222.0 |
| Fashion | 4 | 129.5 | 87.0 | 194.0 |
| Grocery | 5 | 188.8 | 146.0 | 234.0 |
| Home | 5 | 140.8 | 115.0 | 193.0 |
| Toys | 5 | 156.6 | 108.0 | 210.0 |

## q7_data_quality_rejects

_Data-quality view: rejected records by run, source and reason (quarantine summary)._

| run_id | source_name | reason_code | rejected_rows |
|---|---|---|---|
| run_20261106T160358495668Z_3ff3c7 | customers | INVALID_ENUM | 3 |
| run_20261106T160358495668Z_3ff3c7 | customers | INVALID_FORMAT | 2 |
| run_20261106T160358495668Z_3ff3c7 | customers | MISSING_REQUIRED_FIELD | 2 |
| run_20261106T160358495668Z_3ff3c7 | customers | CONFLICTING_DUPLICATE | 1 |
| run_20261106T160358495668Z_3ff3c7 | customers | DUPLICATE_EVENT | 1 |
| run_20261106T160358495668Z_3ff3c7 | customers | INVALID_TIMESTAMP | 1 |
| run_20261106T160358495668Z_3ff3c7 | customers | MALFORMED_RECORD | 1 |
| run_20261106T160358495668Z_3ff3c7 | orders | INVALID_FORMAT | 4 |
| run_20261106T160358495668Z_3ff3c7 | orders | OUT_OF_RANGE | 4 |
| run_20261106T160358495668Z_3ff3c7 | orders | DUPLICATE_EVENT | 3 |
| run_20261106T160358495668Z_3ff3c7 | orders | INVALID_ENUM | 3 |
| run_20261106T160358495668Z_3ff3c7 | orders | MALFORMED_RECORD | 3 |
| … 24 more rows (see CSV) | | | |

## q8_pipeline_run_history

_Operational view: every source load with counts, status and failure reason._

| run_id | run_status | source_name | source_status | rows_read | rows_accepted | rows_rejected | rows_inserted_new | rows_already_present | duration_seconds | failure_reason |
|---|---|---|---|---|---|---|---|---|---|---|
| run_20261106T160358495668Z_3ff3c7 | SUCCESS | customers | SUCCESS | 46 | 35 | 11 | 35 | 0 | 0.003 |  |
| run_20261106T160358610694Z_3ff3c7 | SUCCESS | customers | SKIPPED_ALREADY_LOADED | 0 | 0 | 0 | 0 | 0 | 0.0 |  |
| run_20261106T160358708544Z_3ff3c7 | SUCCESS | customers | SUCCESS | 46 | 35 | 11 | 0 | 35 | 0.003 |  |
| run_20261106T160358495668Z_3ff3c7 | SUCCESS | orders | SUCCESS | 115 | 91 | 24 | 91 | 0 | 0.006 |  |
| run_20261106T160358610694Z_3ff3c7 | SUCCESS | orders | SKIPPED_ALREADY_LOADED | 0 | 0 | 0 | 0 | 0 | 0.0 |  |
| run_20261106T160358708544Z_3ff3c7 | SUCCESS | orders | SUCCESS | 115 | 91 | 24 | 0 | 91 | 0.007 |  |
| run_20261106T160358821481Z_b09b2b | SUCCESS | orders | SUCCESS | 17 | 17 | 0 | 17 | 0 | 0.003 |  |
| run_20261106T160358922678Z_64083f | SUCCESS | orders | SUCCESS | 0 | 0 | 0 | 0 | 0 | 0.002 |  |
| run_20261106T160359023883Z_8a7e18 | FAILED | orders | FAILED | 0 | 0 | 0 | 0 | 0 | 0.001 | SCHEMA_ERROR: missing required column(s) ['unit_price']; got header ['order_id', 'line_no', 'customer_id', 'product_code', 'product_name', 'category', 'quantity', 'price', 'currency', 'order_ts', 'status', 'status_ts'] |
| run_20261106T160359123337Z_8c4e11 | SUCCESS | orders | SUCCESS | 1 | 1 | 0 | 1 | 0 | 0.002 |  |
