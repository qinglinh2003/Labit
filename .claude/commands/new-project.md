When the user says /new-project, help them create a new research project.

1. Ask for:
   - Project name (e.g., "GLANCE", "SemBelief-WM")
   - One-line description
   - Research focus / what makes a paper relevant (free-text)
   - GitHub repo URL (optional, e.g., https://github.com/user/repo.git)

2. From their description, generate:
   - A list of 5-15 keywords for arXiv paper matching
   - Appropriate arxiv_categories (default: cs.AI, cs.LG, cs.CV)
   - A relevance_criteria paragraph for Claude scoring

3. Show the draft config and ask for confirmation or tweaks.

4. Write the config to configs/projects/{name}.yaml (include `repo:` field if GitHub URL was provided)

5. Create project directories:
   python3 -c "from scripts.project_config import ensure_project_dirs; ensure_project_dirs('{name}')"

6. If a GitHub repo URL was provided, clone it into vault/projects/{name}/code/:
   git clone {url} vault/projects/{name}/code/

7. Run backfill to tag existing papers:
   python scripts/daily_digest.py --backfill --project {name}

8. Switch active project: write the new project name to configs/active_project.

9. Report how many existing papers matched the new project.
