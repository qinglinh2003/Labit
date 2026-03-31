When the user says /review-results:

1. Run: python scripts/hypothesis_tracker.py list
2. For any hypothesis with status "validated" or "rejected" that hasn't been discussed yet, show details.
3. Read vault/notifications.md for recent training completions.
4. Compare actual_result against expected_improvement in the hypothesis YAML.
5. Suggest next steps: refine the hypothesis, try a variant, or move on.
6. If results are good, ask if the user wants to merge the experiment branch back to dev.
