When the user says /commit, help them review and commit changes.

1. Run `git status` and `git diff --stat` to see all changes.

2. If there are no changes, say "Nothing to commit." and stop.

3. Group related changes into logical commits. For each proposed commit, show:
   - Files included
   - One-line summary of what changed
   - Draft commit message

4. Present the full plan and ask the user to approve, modify, or reject.

5. Only after explicit approval, execute each commit. Never add Claude Code as co-author.

6. After all commits, ask if the user wants to push.
