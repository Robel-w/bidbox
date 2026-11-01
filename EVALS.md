# Evaluation

Evaluation will measure whether BidBox produces evidence-grounded, appropriately cautious investigation reports across normal awards, anomalies, missing data, broken identifiers, and insufficient evidence.

The test set is synthetic and deterministic: it contains a clean award, injected price and supplier patterns, missing and malformed data, thin comparables, multiple signals, and two adversarial descriptions. The deterministic tier passed all 10 cases. The latest real local-model suite ran all 10 cases with three repeats requested and recorded an explicit timeout for every case.

## One failure we did not fix

The real three-repeat suite timed out all cases because the configured local OpenAI-compatible endpoint did not complete the planner/reviewer calls within 240 seconds. The next concrete experiment is to start the documented Ollama model explicitly or use a reachable hosted open-weights endpoint, then resume the committed result file without changing assertions.

<!-- RESULTS:START -->
Model: `llama3.2:latest`; date: `2026-11-01`; hardware: `Windows-11-10.0.26200-SP0`

| Case | Pass | Agreement | Tokens | Cost |
|---|---:|---:|---:|---:|
| 001-010 | timeout | false | 0 | $0.00 |

Aggregate: pass rate `0.000`, sourcing rate `0.000`, hallucinated-source rate `0.000`, run-to-run agreement `0.000`, timed out cases `10`.
<!-- RESULTS:END -->
