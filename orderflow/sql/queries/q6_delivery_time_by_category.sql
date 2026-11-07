-- Uses the preserved event HISTORY: hours from PLACED to DELIVERED per product category.
SELECT p.category,
       COUNT(*) AS delivered_lines,
       ROUND(AVG((julianday(d.status_ts) - julianday(pl.status_ts)) * 24), 1) AS avg_hours_to_deliver,
       ROUND(MIN((julianday(d.status_ts) - julianday(pl.status_ts)) * 24), 1) AS min_hours,
       ROUND(MAX((julianday(d.status_ts) - julianday(pl.status_ts)) * 24), 1) AS max_hours
  FROM order_line_event d
  JOIN order_line_event pl ON pl.order_id = d.order_id AND pl.line_no = d.line_no AND pl.status = 'PLACED'
  JOIN dim_product p ON p.product_code = d.product_code
 WHERE d.status = 'DELIVERED'
 GROUP BY p.category
 ORDER BY p.category;
