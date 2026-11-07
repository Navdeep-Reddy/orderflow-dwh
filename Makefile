PY ?= python3
.PHONY: sample run test validate report bench clean
sample:   ## regenerate the small reproducible sample + expected labels
	$(PY) -m orderflow generate --out data/sample --edge-cases
run:      ## load the sample into output/warehouse.db
	$(PY) -m orderflow run --customers data/sample/customers.jsonl --orders data/sample/orders.csv --as-of 2026-11-06T00:00:00Z
validate: ## warehouse invariants
	$(PY) -m orderflow validate
report:   ## analytical queries -> output/reports
	$(PY) -m orderflow report
test:
	$(PY) -m unittest discover -s tests -t . -v
bench:    ## ~150k rows
	$(PY) -m orderflow benchmark --customers 5000 --order-lines 50000
clean:
	rm -rf output/warehouse.db* output/run_report_* output/rejects_* output/reports output/pipeline.log
