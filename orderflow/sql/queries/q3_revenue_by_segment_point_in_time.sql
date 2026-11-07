-- SCD2 in action: revenue by the segment the customer had WHEN THE ORDER WAS PLACED, next to the
-- segment they have today. Rows where the two differ are exactly the history a Type-1 dimension loses.
SELECT c.segment  AS segment_at_order_time,
       cur.segment AS segment_today,
       f.currency,
       COUNT(*)    AS order_lines,
       ROUND(SUM(f.line_amount_minor) / 100.0, 2) AS net_revenue
  FROM fact_order_line f
  JOIN dim_customer c   ON c.customer_key = f.customer_key
  JOIN dim_customer cur ON cur.customer_id = f.customer_id AND cur.is_current = 1
 WHERE f.is_net_revenue = 1
 GROUP BY c.segment, cur.segment, f.currency
 ORDER BY f.currency, c.segment, cur.segment;
