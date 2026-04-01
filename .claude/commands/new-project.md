When the user says /new-project, help them create a new research project.

1. Ask for:
   - Project name (e.g., "GLANCE", "SemBelief-WM")
   - One-line description
   - Research focus / what makes a paper relevant (free-text)

2. From their description, generate:
   - A list of 5-15 keywords for arXiv paper matching
   - Appropriate arxiv_categories (default: cs.AI, cs.LG, cs.CV)
   - A relevance_criteria paragraph for Claude scoring

3. Show the draft config and ask for confirmation or tweaks.

4. Write the config to configs/projects/{name}.yaml

5. Run backfill to tag existing papers:
   python scripts/daily_digest.py --backfill --project {name}

6. Switch active project: write the new project name to configs/active_project.

7. Report how many existing papers matched the new project.
