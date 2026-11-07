-- Final status of order lines per month, with cancellation and return rates.
SELECT d.year, d.month,
       COUNT(*) AS order_lines,
       SUM(f.status = 'DELIVERED') AS delivered,
       SUM(f.status = 'SHIPPED')   AS shipped,
       SUM(f.status = 'PLACED')    AS placed_only,
       SUM(f.status = 'CANCELLED') AS cancelled,
       SUM(f.status = 'RETURNED')  AS returned,
       ROUND(100.0 * SUM(f.status = 'CANCELLED') / COUNT(*), 1) AS cancel_rate_pct,
       ROUND(100.0 * SUM(f.status = 'RETURNED')  / COUNT(*), 1) AS return_rate_pct
  FROM fact_order_line f
  JOIN dim_date d USING (date_key)
 GROUP BY d.year, d.month
 ORDER BY d.year, d.month;
