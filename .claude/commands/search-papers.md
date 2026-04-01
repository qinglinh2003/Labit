When the user says /search-papers followed by a query (e.g., /search-papers slot attention for POMDP exploration), do the following:

1. Read the project configs from configs/projects/*.yaml to understand active projects.

2. Use WebSearch to find 5-10 relevant academic papers matching the user's query. Search across arXiv, Semantic Scholar, and Google Scholar. Prioritize recent papers (last 1-2 years).

3. For each promising paper, use WebFetch on its Semantic Scholar page (https://api.semanticscholar.org/graph/v1/paper/ARXIV:{id}?fields=paperId,title,authors,year,venue,tldr,fieldsOfStudy,references,citations,externalIds) to get structured metadata.

4. Present the results to the user as a ranked list with title, authors, year, and a one-line summary. Ask which ones to add to the vault.

5. For each paper the user selects, create an Obsidian note in vault/papers/ following the exact frontmatter schema used by daily_digest.py:
   ```yaml
   ---
   paper_id: "{arxiv_id}"
   title: "{title}"
   authors: [...]
   venue: "{venue}"
   year: {year}
   url: "https://arxiv.org/abs/{arxiv_id}"
   tags: [{tags}]
   relevance_to: [{matching project names}]
   status: "to-read"
   added: "{today's date}"
   ---
   ```
   Fill in the "Core idea" section from the S2 TLDR. Populate relevance_to by matching the paper against each project's keywords.

6. Check vault/papers/ for existing notes — if any of the selected paper's references are already in the vault, add [[wikilinks]] under "References in my vault", and update the referenced paper's "Cited by (in my vault)" section.

7. Summarize what was added and which projects each paper is relevant to.
