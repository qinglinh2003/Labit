When the user says /weekly-report:

1. Read all experiments/hypotheses/*.yaml files to find this week's activity (check date and status changes).
2. Read vault/sparks/ for this week's cross-analysis results.
3. Read vault/notifications.md for completed experiments.
4. Generate a concise weekly report with these sections:
   - Experiments completed this week (hypothesis ID, result, conclusion)
   - New hypotheses proposed
   - Key papers read (from vault/papers/ added this week)
   - Interesting sparks from cross-analysis
   - Plan for next week
5. Write the report to vault/reports/YYYY-WNN.md
6. Show a brief summary to the user.
