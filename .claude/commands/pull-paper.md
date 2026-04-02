When the user says /pull-paper followed by an arXiv ID or paper title (e.g., /pull-paper 2603.05465 or /pull-paper HALP), do the following:

1. Read configs/active_project to get the current project.

2. Resolve the paper:
   - If an arXiv ID is given (e.g., 2603.05465), use it directly.
   - If a title/name is given, search vault/papers/ for a matching note to get the arXiv ID.
   - If not found in vault, use WebSearch to find the arXiv ID.

3. Create vault/projects/{project}/docs/key_papers/ if it doesn't exist.

4. Create a folder: vault/projects/{project}/docs/key_papers/{arxiv_id}/

5. Download the raw HTML:
   curl -sL https://arxiv.org/html/{arxiv_id}v1 -o vault/projects/{project}/docs/key_papers/{arxiv_id}/paper.html
   If HTML is not available, download PDF instead:
   curl -sL https://arxiv.org/pdf/{arxiv_id} -o vault/projects/{project}/docs/key_papers/{arxiv_id}/paper.pdf

6. Extract content via WebFetch and save as markdown:
   WebFetch https://arxiv.org/html/{arxiv_id} → extract full paper content
   Save as vault/projects/{project}/docs/key_papers/{arxiv_id}/paper.md with structure:
   ```markdown
   # {Paper Title}
   **arXiv**: {arxiv_id}
   **Authors**: {authors}
   **Year**: {year}
   
   ## Abstract
   {abstract}
   
   ## 1. Introduction
   {content}
   
   ... (all sections with full text)
   
   ## Key Tables
   {all tables in markdown format with exact numbers}
   
   ## Relevance to {project}
   {how this paper relates to the current project}
   ```

7. Confirm to the user: paper saved, folder path, files (paper.md + paper.html/pdf).
