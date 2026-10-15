# Evaluation

Evaluation will measure whether BidBox produces evidence-grounded, appropriately cautious investigation reports across normal awards, anomalies, missing data, broken identifiers, and insufficient evidence.

The test set is synthetic and deterministic: it contains a clean award, injected price and supplier patterns, missing and malformed data, thin comparables, multiple signals, and two adversarial descriptions. The deterministic tier passed all 10 cases. The latest real local-model check selected case 001 with a one-second case budget and recorded an explicit timeout; the historical full one-run artifact remains available in `evaluation/results/20261014T231018Z.json`.

## One failure we did not fix

The open-weights evaluation can exceed the case budget during MCP startup/model calls; the bounded run also exposed an exception-group warning while cancelling MCP initialization. The next concrete experiment is to isolate each case in a subprocess or add cancellation-safe MCP lifecycle cleanup before running the default two repeats.

<!-- RESULTS:START -->
Model: `llama3.2:latest`; date: `2026-10-15`; hardware: `Windows-11-10.0.26200-SP0`

| Case | Pass | Agreement | Tokens | Cost |
|---|---:|---:|---:|---:|
| 001_normal_award | timeout | false | 0 | $0.00 |

Aggregate: pass rate `0.000`, sourcing rate `0.000`, hallucinated-source rate `0.000`, run-to-run agreement `0.000`, timed out cases `1`.
<!-- RESULTS:END -->
