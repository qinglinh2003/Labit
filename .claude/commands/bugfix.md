When the user says /bugfix followed by a short description (e.g., /bugfix MATE unimodal prompt mismatch), do the following:

1. Read configs/active_project to get the current project.

2. Create a bugfix log entry in vault/projects/{project}/docs/bugfix_log.md (append if exists, create if not). Each entry follows this format:

```markdown
## BUG-{NNN}: {short title}
**Date**: {YYYY-MM-DD}
**Status**: open | fixed | wontfix
**Severity**: critical | high | medium | low

### Symptom
What went wrong — observed behavior, unexpected metrics, error messages.

### Root cause
Why it happened — the specific code path, logic error, or config mismatch.

### Evidence
Key data points, file paths, line numbers, before/after metrics.

### Fix
What was changed — files modified, logic added/removed, with brief rationale.

### Verification
How the fix was confirmed — re-run command, expected vs actual result.
```

3. Investigate the bug:
   - Read relevant source code in the project's code/ directory
   - Check recent git history for related changes
   - Compare with expected behavior

4. Propose a fix and show it to the user for approval before applying.

5. After the user approves, apply the fix and update the bugfix log entry with the Fix and Verification sections.

6. If the fix involves code changes, remind the user to commit and push to the project repo.
