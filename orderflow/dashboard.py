"""One-page HTML dashboard: requirement checks + data-quality + operations + business results.

Self-contained (inline CSS, no JavaScript libraries, works offline). Everything is read from the
warehouse database and from live verification results; nothing is hard-coded.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Optional

from .reports import QUERY_DIR, run_query

STATUS_CLASS = {"PASS": "pass", "FAIL": "fail", "WARN": "warn", "MANUAL": "manual"}


def _e(v) -> str:
    return escape("" if v is None else str(v))


def _money(minor) -> str:
    return f"{(minor or 0) / 100:,.2f}"


def _bars(rows: list[tuple[str, float]], fmt=lambda v: f"{v:,.0f}") -> str:
    """Horizontal CSS bar chart. rows = [(label, value)]"""
    if not rows:
        return '<p class="muted">No data.</p>'
    top = max(v for _, v in rows) or 1
    out = ['<div class="bars">']
    for label, v in rows:
        out.append(f'<div class="bar-row"><span class="bar-label">{_e(label)}</span>'
                   f'<span class="bar-track"><span class="bar-fill" style="width:{max(v / top * 100, 1):.1f}%"></span></span>'
                   f'<span class="bar-val">{_e(fmt(v))}</span></div>')
    out.append("</div>")
    return "".join(out)


def _table(cols: list[str], rows: list[tuple], limit: Optional[int] = None) -> str:
    shown = rows[:limit] if limit else rows
    head = "".join(f"<th>{_e(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_e(v)}</td>" for v in r) + "</tr>" for r in shown)
    more = f'<p class="muted">… {len(rows) - len(shown)} more rows</p>' if limit and len(rows) > limit else ""
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>{more}'


def collect(conn) -> dict:
    """All numbers the dashboard shows, read from the warehouse."""
    one = lambda sql, p=(): conn.execute(sql, p).fetchone()[0]  # noqa: E731
    d: dict = {}
    run = conn.execute("SELECT run_id, status, started_at, duration_seconds, reconciliation_passed, failure_reason "
                       "FROM pipeline_run ORDER BY started_at DESC LIMIT 1").fetchone()
    d["latest_run"] = dict(run) if run else None
    d["loads"] = [dict(r) for r in conn.execute(
        """SELECT s.* FROM run_source s JOIN (
               SELECT source_name, MAX(run_id) AS run_id FROM run_source WHERE status='SUCCESS' GROUP BY source_name
           ) m ON m.source_name = s.source_name AND m.run_id = s.run_id WHERE s.status='SUCCESS' ORDER BY s.source_name""")]
    d["counts"] = {
        "order_lines": one("SELECT COUNT(*) FROM fact_order_line"),
        "order_events": one("SELECT COUNT(*) FROM order_line_event"),
        "customers": one("SELECT COUNT(DISTINCT customer_id) FROM dim_customer"),
        "customer_versions": one("SELECT COUNT(*) FROM dim_customer"),
        "products": one("SELECT COUNT(*) FROM dim_product"),
        "runs": one("SELECT COUNT(*) FROM pipeline_run"),
    }
    d["net_revenue"] = conn.execute(
        "SELECT currency, SUM(line_amount_minor) FROM fact_order_line WHERE is_net_revenue=1 GROUP BY currency ORDER BY currency").fetchall()
    reject_filter = " OR ".join(["(run_id=? AND source_name=?)"] * len(d["loads"])) or "0"
    params = [x for l in d["loads"] for x in (l["run_id"], l["source_name"])]
    d["rejects"] = conn.execute(
        f"SELECT source_name, reason_code, COUNT(*) FROM reject_record WHERE {reject_filter} "
        "GROUP BY source_name, reason_code ORDER BY COUNT(*) DESC, reason_code", params).fetchall()
    d["monthly"] = conn.execute(
        """SELECT d.year || '-' || printf('%02d', d.month) AS ym, f.currency, SUM(f.line_amount_minor)
             FROM fact_order_line f JOIN dim_date d USING (date_key) WHERE f.is_net_revenue=1
            GROUP BY ym, f.currency ORDER BY ym, f.currency""").fetchall()
    d["status_counts"] = conn.execute("SELECT status, COUNT(*) FROM fact_order_line GROUP BY status ORDER BY COUNT(*) DESC").fetchall()
    d["recon"] = []
    if d["latest_run"]:
        last = conn.execute("SELECT run_id FROM reconciliation_result GROUP BY run_id ORDER BY MAX(rowid) DESC LIMIT 1").fetchone()
        if last:
            d["recon"] = conn.execute("SELECT check_name, expected, actual, passed FROM reconciliation_result "
                                      "WHERE run_id=? ORDER BY check_name", (last[0],)).fetchall()
    for key, q in (("segments", "q3_revenue_by_segment_point_in_time"), ("top_customers", "q5_top_customers"),
                   ("history", "q8_pipeline_run_history")):
        d[key] = run_query(conn, QUERY_DIR / f"{q}.sql")
    return d


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2430;--muted:#667085;--line:#e4e7ec;--accent:#2f6fed;
--pass:#157f4b;--pass-bg:#e3f6ec;--fail:#b42318;--fail-bg:#fde7e5;--warn:#a15c07;--warn-bg:#fdf1d8;--man:#475467;--man-bg:#eceff3}
@media (prefers-color-scheme:dark){:root{--bg:#10151c;--card:#18202a;--ink:#e8edf3;--muted:#9aa6b5;--line:#2a3441;--accent:#6c9bff;
--pass:#5fd39a;--pass-bg:#12352a;--fail:#ff8a80;--fail-bg:#3d1b18;--warn:#f2c46b;--warn-bg:#3a2d10;--man:#b6c0cc;--man-bg:#242d39}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 48px}h1{margin:0 0 4px;font-size:26px}h2{font-size:18px;margin:0 0 12px}
.muted{color:var(--muted);font-size:13px}.grid{display:grid;gap:14px}.kpis{grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin:18px 0}
.two{grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
section{margin-top:18px}.kpi b{display:block;font-size:26px;line-height:1.2}.kpi span{color:var(--muted);font-size:13px}
.badge{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:700;letter-spacing:.3px}
.pass{color:var(--pass);background:var(--pass-bg)}.fail{color:var(--fail);background:var(--fail-bg)}
.warn{color:var(--warn);background:var(--warn-bg)}.manual{color:var(--man);background:var(--man-bg)}
.verdict{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.verdict .badge{font-size:14px;padding:4px 14px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;white-space:nowrap}.scroll{overflow-x:auto}
.bar-row{display:grid;grid-template-columns:minmax(110px,190px) 1fr 80px;gap:8px;align-items:center;margin:6px 0;font-size:13px}
.bar-track{background:var(--line);border-radius:6px;height:12px;overflow:hidden}.bar-fill{display:block;height:100%;background:var(--accent);border-radius:6px}
.bar-val{text-align:right;color:var(--muted)}.group{font-weight:700;background:var(--bg)}footer{margin-top:26px}
"""


def render(data: dict, checks: Optional[list[dict]], bench: Optional[dict], title: str = "orderflow — project dashboard") -> str:
    c, run = data["counts"], data["latest_run"]
    auto = [x for x in (checks or []) if x["status"] in ("PASS", "FAIL")]
    n_pass = sum(1 for x in auto if x["status"] == "PASS")
    n_fail = sum(1 for x in auto if x["status"] == "FAIL")
    n_warn = sum(1 for x in (checks or []) if x["status"] == "WARN")
    n_man = sum(1 for x in (checks or []) if x["status"] == "MANUAL")
    if checks is None:
        verdict = '<span class="badge manual">REQUIREMENTS NOT CHECKED</span>'
    elif n_fail:
        verdict = f'<span class="badge fail">{n_fail} CHECK(S) FAILING</span>'
    else:
        verdict = f'<span class="badge pass">ALL {n_pass} AUTOMATED CHECKS PASS</span>'

    reads = sum(l["rows_read"] for l in data["loads"])
    rej = sum(l["rows_rejected"] for l in data["loads"])
    kpis = [
        ("Last run", f'<span class="badge {STATUS_CLASS.get("PASS" if run and run["status"] == "SUCCESS" else "FAIL")}">{_e(run["status"]) if run else "none"}</span>'),
        ("Rows read / rejected", f"{reads:,} / {rej:,}"),
        ("Order lines (fact)", f"{c['order_lines']:,}"),
        ("Status events kept", f"{c['order_events']:,}"),
        ("Customers / SCD2 versions", f"{c['customers']:,} / {c['customer_versions']:,}"),
        ("Reconciliation checks", f"{sum(1 for r in data['recon'] if r[3])}/{len(data['recon'])} pass"),
    ]
    kpi_html = "".join(f'<div class="card kpi"><b>{v}</b><span>{_e(k)}</span></div>' for k, v in kpis)

    # ---- requirements table
    req_html = '<p class="muted">Run <code>python -m orderflow dashboard</code> (without --skip-verify) to include these checks.</p>'
    if checks:
        rows, group = [], None
        for x in checks:
            if x["group"] != group:
                group = x["group"]
                rows.append(f'<tr class="group"><td colspan="3">{_e(group)}</td></tr>')
            rows.append(f'<tr><td><span class="badge {STATUS_CLASS[x["status"]]}">{x["status"]}</span></td>'
                        f'<td>{_e(x["requirement"])}</td><td class="muted">{_e(x["evidence"])}</td></tr>')
        req_html = ('<div class="scroll"><table><thead><tr><th>Status</th><th>Requirement (from the brief / guidelines)</th>'
                    f'<th>Evidence observed just now</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')

    loads = _table(["source", "run_id", "file", "read", "accepted", "rejected", "new", "already present"],
                   [(l["source_name"], l["run_id"], Path(l["source_file"]).name, l["rows_read"], l["rows_accepted"],
                     l["rows_rejected"], l["rows_inserted_new"], l["rows_already_present"]) for l in data["loads"]])
    rejects = _bars([(f"{s}: {r}", n) for s, r, n in data["rejects"]]) if data["rejects"] else '<p class="muted">No rejects.</p>'
    status = _bars([(s, n) for s, n in data["status_counts"]])
    rev_by = {}
    for ym, cur, v in data["monthly"]:
        rev_by.setdefault(cur, []).append((ym, v / 100))
    monthly = "".join(f"<h3 class='muted'>{_e(cur)}</h3>" + _bars(rows, lambda v: f"{v:,.0f}") for cur, rows in sorted(rev_by.items())) \
        or '<p class="muted">No data.</p>'
    net = " · ".join(f"<b>{_e(cur)}</b> {_money(v)}" for cur, v in data["net_revenue"]) or "—"

    seg_cols, seg_rows = data["segments"]
    drift = [r for r in seg_rows if r[0] != r[1]]
    recon = _table(["check", "expected", "actual", "result"],
                   [(r[0], r[1], r[2], "PASS" if r[3] else "FAIL") for r in data["recon"]])
    top_cols, top_rows = data["top_customers"]
    hist_cols, hist_rows = data["history"]

    bench_html = '<p class="muted">Run <code>python -m orderflow benchmark --customers 100000 --order-lines 100000</code>.</p>'
    if bench:
        fr, opt = bench["first_run"], bench["optimisation_scd2_lookup_index"]
        bench_html = (f'<p><b>{bench["input_rows"]["total"]:,}</b> input rows '
                      f'({bench["input_rows"]["customers"]:,} customer events + {bench["input_rows"]["orders"]:,} order events) '
                      f'→ <b>{bench["fact_rows"]:,}</b> order lines in <b>{fr["seconds"]} s</b> ({fr["rows_per_second"]:,} rows/s).</p>'
                      f'<p>Rerun of same input: {bench["rerun_same_input_seconds"]} s · forced reload: {bench["forced_reload_seconds"]} s '
                      f'with <b>{bench["forced_reload_new_rows"]}</b> new rows (idempotent).</p>'
                      f'<p>Optimisation — composite index for SCD2 lookup: {opt["without_index_seconds"]} s → {opt["with_index_seconds"]} s '
                      f'(<b>{opt["speedup_x"]}×</b> faster).</p>')

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title><style>{CSS}</style></head><body><main>
<header><h1>{_e(title)}</h1><p class="muted">Generated {now} from the warehouse database · latest run {_e(run['run_id']) if run else '—'}</p>
<div class="verdict">{verdict}<span class="muted">{n_warn} to-do warning(s) · {n_man} manual item(s)</span></div></header>
<div class="grid kpis">{kpi_html}</div>

<section class="card"><h2>1 · Requirements check</h2>{req_html}</section>

<section class="grid two">
<div class="card"><h2>2 · Rejected records by reason (latest load)</h2>{rejects}
<p class="muted">Every reject is stored with file, line number and raw content in <code>reject_record</code>.</p></div>
<div class="card"><h2>Final status of order lines</h2>{status}</div></section>

<section class="card"><h2>3 · Latest load per source</h2>{loads}</section>

<section class="grid two">
<div class="card"><h2>4 · Net revenue by month</h2>{monthly}<p class="muted">Net = not cancelled / returned. Totals: {net}</p></div>
<div class="card"><h2>Customers whose segment changed (SCD2)</h2>
<p class="muted">Revenue by the segment at <i>order time</i> vs the segment <i>today</i>: {len(drift)} row(s) differ — history a Type-1 table would lose.</p>
{_table(seg_cols, drift or seg_rows, 8)}</div></section>

<section class="grid two">
<div class="card"><h2>Top customers by net revenue</h2>{_table(top_cols, top_rows, 8)}</div>
<div class="card"><h2>5 · Performance (benchmark)</h2>{bench_html}</div></section>

<section class="card"><h2>6 · Reconciliation (source ↔ target, warehouse invariants)</h2>{recon}</section>
<section class="card"><h2>7 · Pipeline run history</h2>{_table(hist_cols, hist_rows, 15)}</section>
<footer class="muted">Counts: {c['runs']} run(s) · {c['products']} products · see README.md and docs/DESIGN.md for how each number is produced.</footer>
</main></body></html>"""


def build_dashboard(conn, out_path: Path, checks: Optional[list[dict]], bench_path: Optional[Path] = None) -> Path:
    bench = None
    if bench_path and Path(bench_path).exists():
        bench = json.loads(Path(bench_path).read_text(encoding="utf-8"))
    html = render(collect(conn), checks, bench)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path