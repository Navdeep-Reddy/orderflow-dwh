-- Top 5 customers by net revenue in each currency (current attributes shown).
SELECT * FROM (
  SELECT f.currency, c.customer_id, c.name, c.country, c.segment AS current_segment,
         COUNT(DISTINCT f.order_id) AS orders,
         ROUND(SUM(f.line_amount_minor) / 100.0, 2) AS net_revenue,
         RANK() OVER (PARTITION BY f.currency ORDER BY SUM(f.line_amount_minor) DESC) AS rnk
    FROM fact_order_line f
    JOIN dim_customer c ON c.customer_id = f.customer_id AND c.is_current = 1
   WHERE f.is_net_revenue = 1
   GROUP BY f.currency, c.customer_id, c.name, c.country, c.segment
) WHERE rnk <= 5
ORDER BY currency, rnk, customer_id;
