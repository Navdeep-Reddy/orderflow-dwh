"""Run the analytical queries in orderflow/sql/queries/ and export CSV + a Markdown report."""
from __future__ import annotations

import csv
from pathlib import Path

QUERY_DIR = Path(__file__).parent / "sql" / "queries"


def list_queries() -> list[Path]:
    return sorted(QUERY_DIR.glob("q*.sql"))


def run_query(conn, path: Path):
    cur = conn.execute(path.read_text(encoding="utf-8"))
    cols = [d[0] for d in cur.description]
    return cols, [tuple(r) for r in cur.fetchall()]


def _md_table(cols, rows, limit=12) -> str:
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows[:limit]:
        lines.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    if len(rows) > limit:
        lines.append(f"| … {len(rows) - limit} more rows (see CSV) |" + " |" * (len(cols) - 1))
    return "\n".join(lines)


def export_reports(conn, out_dir) -> Path:
    out = Path(out_dir) / "reports"
    out.mkdir(parents=True, exist_ok=True)
    md = ["# Analytical report", ""]
    for q in list_queries():
        cols, rows = run_query(conn, q)
        with open(out / f"{q.stem}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        first_comment = q.read_text(encoding="utf-8").splitlines()[0].lstrip("- ").strip()
        md += [f"## {q.stem}", "", f"_{first_comment}_", "", _md_table(cols, rows), ""]
    md_path = out / "analytical_report.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    return md_path
