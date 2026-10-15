"""Quick test of evaluation cases."""
import sys
sys.path.insert(0, '.')
from evaluation.run_evals import run_all_evals

results = run_all_evals()
passed = sum(1 for r in results if r.get("passed", False))
total = len(results)
print(f"\nEvaluation Results: {passed}/{total} passed")
for r in results:
    status = "PASS" if r.get("passed", False) else "FAIL"
    print(f"  {r['case_id']}: {status}")
    if not r.get("passed"):
        print(f"    Reason: {r.get('reason', 'Unknown error')}")
