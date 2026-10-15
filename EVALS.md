# Evaluation

Evaluation will measure whether BidBox produces evidence-grounded, appropriately cautious investigation reports across normal awards, anomalies, missing data, broken identifiers, and insufficient evidence.

The test set is synthetic and deterministic: it contains a clean award, injected price and supplier patterns, missing and malformed data, thin comparables, multiple signals, and two adversarial descriptions. The deterministic tier passed all 10 cases. The real local-model tier was attempted with `llama3.2:latest` and timed out after 300 seconds during case 003; no aggregate score is claimed.

## One failure we did not fix

The open-weights evaluation is too slow for the current execution budget because each graph pass requires multiple local-model JSON calls, with observed calls taking roughly 20-40 seconds. The next concrete experiment is to batch planner/reviewer prompts per case or use a documented hosted open-weights endpoint, then rerun the unchanged assertions at `EVAL_RUNS=3`.

<!-- RESULTS:START -->
Model: `llama3.2:latest`; date: `2026-10-15`; hardware: `Windows host; exact hardware not captured`

| Case | Pass | Agreement | Tokens | Cost |
|---|---:|---:|---:|---:|
| 001_normal_award | partial | n/a | n/a | $0.00 |
| 002_price_outlier | partial | n/a | n/a | $0.00 |
| 003-010 | not run | n/a | n/a | $0.00 |

Aggregate: unavailable because the real run timed out before aggregate scoring.
<!-- RESULTS:END -->
