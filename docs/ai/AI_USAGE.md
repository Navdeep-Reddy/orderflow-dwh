# AI Usage Disclosure

## Summary

Claude (Anthropic) was used extensively during the initial development of this project, including architecture design, source-code generation, SQL development, test creation, debugging, benchmarking, and documentation.

I subsequently worked with the generated project, ran the tests and pipeline on my own Windows machine, identified issues, reported failures, and verified the fixes. I also made a small modification to the synthetic data generator to check whether the project could support at least 100,000 customers, as suggested in the original project brief.

I used ChatGPT later to review the project requirements and improve the project documentation, including this disclosure.

AI assistance played a significant role in development. I am responsible for the work I personally reviewed, tested, changed, and can explain.

## Where AI Accelerated the Work

AI assistance was used for:

- Initial project scaffolding and module development.
- SCD Type 2 rebuild logic and customer history management.
- Event-history design for orders.
- Synthetic data generation with injected and labelled defects.
- Automated test creation.
- Reconciliation checks and validation logic.
- SQL queries and database optimization.
- Performance benchmarking.
- README, data dictionary, design notes, and other documentation drafts.

## Where I Relied Heavily on AI

- Architecture suggestions for the event-sourced order model and SCD2 customer rebuild strategy.
- SQL queries, including upserts, window functions, and point-in-time lookups.
- Test-case design and reconciliation logic.
- Initial drafts of the README, data dictionary, and design documentation.

## Verification Performed by Claude

The following checks were performed in Claude's development environment:

- `make test`: 67 tests passed.
- Sample data, three additional seeds, and the late-arriving batch matched the independent answer key.
- Fresh-copy workflow covering test, run, validate, and report completed successfully using Python 3.12 and SQLite 3.45.
- Benchmarking on approximately 444,000 input rows, with index optimization measured at approximately 1,150×.
- An initial benchmark measurement was incorrect because SQLite optimized away the subquery used with `COUNT(*)`. The measurement was corrected after the results appeared implausible.
- Several test-expectation and generator issues were identified and corrected during development.

## Verification Not Covered by Claude

- Other Python versions beyond the tested Python 3.12 environment.
- Other operating systems.
- Hardware-independent benchmark performance.
- Every possible clean-environment configuration.

## What I Personally Reviewed, Changed, and Validated

- [x] I ran the tests and pipeline on my own Windows machine using Python 3.10.1. Since `make` was unavailable, I used `python -m ...` commands. All 67 tests passed, and the run, validate, and report commands worked.
- [x] I read and can explain: `validate.py`, `load_customers.py`, `load_orders.py`, `pipeline.py`, and `reconcile.py`.
- [x] I made a small change to the synthetic data generator because I wanted to check whether the project could support at least 100,000 customers, as mentioned in the project brief.
- [x] I deliberately introduced an issue to check whether the validation mechanisms detected it.
- [x] I identified a difference between the AI-generated test results and my Windows execution: one test initially failed because of different line endings in the sample data file. I reported the issue, received a corrected version, and verified that all 67 tests passed afterward.

## Dependencies and Licences

- Python standard library only, including `sqlite3`, `csv`, `json`, `argparse`, and `unittest`.
- No third-party code was copied into the project.
- All project datasets are synthetic and generated using `orderflow/generate_data.py`.
- No secrets, credentials, or personal data are included in the repository.