When the user says /investigate followed by a topic (e.g., /investigate how MATE CoT prompting works), do the following:

## 1. Check prior knowledge

Read the active project from configs/active_project. Then scan vault/projects/{project}/docs/reports/ for existing reports. For each report, read the title and summary lines to check if this topic (or a closely related one) has already been investigated.

- If a directly relevant report exists, show its key findings first and ask: "This was investigated before. Want me to update it or investigate further?"
- If partially relevant reports exist, reference them as context.
- If no relevant reports exist, proceed to investigate.

## 2. Investigate

Based on the user's topic, use the appropriate tools:
- **Code**: Read source files, grep for patterns, trace call chains
- **Data**: Read output files, parse JSON/JSONL, compute statistics
- **Docs**: Read papers (PDF via Read tool), fetch web pages, check README
- **Experiments**: Check remote outputs via SSH, compare metrics across runs

Be thorough but focused. Follow leads — if one finding raises a new question, investigate that too.

## 3. Write report

Create a report file at vault/projects/{project}/docs/reports/{YYYY-MM-DD}_{slug}.md with this format:

```markdown
# {Title}
**Date**: {YYYY-MM-DD}
**Project**: {project}
**Topic**: {one-line description}
**Status**: complete | partial | needs-followup

## Summary
2-3 sentence summary of the key findings. This is what future /investigate calls will scan.

## Context
Why this was investigated — what prompted the question.

## Findings
Detailed findings organized by sub-topic. Include:
- File paths and line numbers for code findings
- Exact metrics and data points
- Comparisons with expected behavior
- Relevant quotes from docs/papers

## Evidence
Key data that supports the findings — command outputs, metric tables, code snippets.

## Open questions
Anything unresolved that warrants future investigation.

## Related
Links to other reports, bugfix entries, or hypothesis IDs that connect to this investigation.
```

## 4. Present to user

After writing the report, give the user a concise summary of findings. Don't dump the whole report — highlight what matters and what's surprising.
