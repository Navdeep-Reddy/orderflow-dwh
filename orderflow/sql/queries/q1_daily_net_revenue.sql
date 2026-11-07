-- Daily net revenue by currency. Net = lines not CANCELLED/RETURNED (latest status). Amounts in major units.
SELECT d.full_date AS order_date,
       f.currency,
       COUNT(*)                                   AS order_lines,
       SUM(f.quantity)                            AS units,
       ROUND(SUM(f.line_amount_minor) / 100.0, 2) AS net_revenue
  FROM fact_order_line f
  JOIN dim_date d USING (date_key)
 WHERE f.is_net_revenue = 1
 GROUP BY d.full_date, f.currency
 ORDER BY d.full_date, f.currency;
