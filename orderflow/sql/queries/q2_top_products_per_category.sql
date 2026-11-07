-- Top 3 products by net revenue within each category and currency (window function).
SELECT * FROM (
  SELECT p.category, f.currency, p.product_code, p.product_name,
         ROUND(SUM(f.line_amount_minor) / 100.0, 2) AS net_revenue,
         RANK() OVER (PARTITION BY p.category, f.currency ORDER BY SUM(f.line_amount_minor) DESC) AS rnk
    FROM fact_order_line f
    JOIN dim_product p USING (product_key)
   WHERE f.is_net_revenue = 1
   GROUP BY p.category, f.currency, p.product_code, p.product_name
) WHERE rnk <= 3
ORDER BY category, currency, rnk, product_code;
