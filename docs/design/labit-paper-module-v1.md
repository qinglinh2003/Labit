# LABIT Paper Module V1

## Purpose

The paper module is the first `labit` module that fully depends on the agent runtime.

Its job is to:

- search for papers in the context of the active project
- avoid duplicate collection work
- store canonical paper assets in a global library
- expose a project-level working set of selected papers
- optionally generate a project-specific paper summary

## Product Direction

The paper module is:

- active-project-first
- conversation-first
- agent-assisted for search and summary
- deterministic for storage and indexing

The user should not pass project or query flags on the command line.

## Commands

V1 command surface:

```bash
labit paper search
labit paper pull
labit paper ingest
labit paper show
```

### `labit paper search`

Interactive entrypoint.

Responsibilities:

- read the active project
- inspect existing global and project paper indexes
- ask the user what they want to search for
- choose `single` or `discussion` mode
- run the agent search workflow
- show ranked candidates with duplicate status

### `labit paper pull`

Pull a paper into the system without generating a summary.

Responsibilities:

- ensure canonical global storage exists
- store raw assets
- create the project-level paper entry

### `labit paper ingest`

Pull a paper and generate a project-specific summary.

Responsibilities:

- do everything `pull` does
- run project-aware summarization
- write `summary.md` under the active project's paper directory

### `labit paper show`

Read-only view into the paper library.

Responsibilities:

- inspect global canonical paper records
- inspect project-level linked papers

## Storage Model

There are two distinct paper layers.

### 1. Global Paper Library

This is the canonical paper store.

Responsibilities:

- paper identity
- metadata
- raw downloaded assets
- cross-project relevance markers

Recommended layout:

```text
vault/papers/
  index.yaml
  by_id/
    {paper_id}/
      meta.yaml
      paper.html
      paper.pdf
```

Rules:

- one canonical directory per paper id
- raw assets live here, not in project copies

### 2. Project Paper Library

This is the project working set.

Responsibilities:

- which papers this project selected
- project-local notes or annotations
- pull vs ingest status from the project perspective

Recommended layout:

```text
vault/projects/{project}/key_papers/
  index.yaml
  {paper_id}/
    paper.yaml
    summary.md
    notes.md
```

Rules:

- project layer materializes references to global papers instead of duplicating raw assets
- `paper.yaml` should include the key canonical paths an agent needs
- `summary.md` is project-specific, not global
- `notes.md` is project-specific and optional

## Why Two Layers Exist

The global library and the project library solve different problems.

Global library:

- prevents duplicate ingestion
- supports cross-project reuse
- provides long-term memory for the runtime

Project library:

- defines the current working set
- supports project-specific interpretation
- owns project-specific summaries
- keeps local reading priorities small and deliberate

## Search Workflow

### Preconditions

Before search begins:

- there must be an active project
- global and project paper indexes must be loaded

### Search Form

`labit paper search` should collect search intent through a short conversation or form.

Suggested prompts:

- what are you trying to find
- what aspect matters most
- is this a broad exploration or a narrow lookup
- do you want `single` or `discussion`
- how many candidates do you want to review

This replaces command-line query flags.

### Search Modes

#### `single`

Use one agent backend for:

- query expansion
- search result ranking
- relevance explanation

This should be the default mode.

#### `discussion`

Use the agent runtime's `discussion` mode.

Typical role assignment:

- Claude as `scout`
- Codex as `normalizer` or second `discussant`
- Claude or `labit` as synthesizer

This should be used for:

- exploratory literature review
- ambiguous topic framing
- high-value searches

### Search Output

Each candidate should include:

- title
- authors
- year
- one-line description
- why it is relevant to the active project
- duplicate status

Duplicate status values:

- `new`
- `in_global`
- `in_project`
- `in_global_and_project`

## Duplicate Prevention

The system must check existing holdings before presenting results as new.

### Required Indexes

V1 should maintain:

- a global paper index
- a per-project key-paper index

Suggested global index fields:

- `paper_id`
- canonical title
- title aliases
- year
- global path

Suggested project index fields:

- `project`
- `paper_id`
- paper path
- status

### Match Strategy

Use this order:

1. exact paper id
2. exact normalized title
3. known external ids
4. conservative fuzzy title match

If there is uncertainty, show the match as a possible duplicate instead of auto-merging.

## Pull vs Ingest

### `pull`

Meaning:

- acquire and store the paper
- do not summarize it yet

Actions:

- resolve canonical id
- create global directory if absent
- download and save `paper.html` when available
- save fallback PDF if needed
- write `meta.yaml`
- create project `paper.yaml`

### `ingest`

Meaning:

- acquire and store the paper
- generate a project-specific summary

Actions:

- perform all `pull` actions
- run summarization against canonical assets
- write `summary.md` in `vault/projects/{project}/key_papers/{paper_id}/`
- update indexes

## Asset Policy

Preferred content format:

- `paper.html`

Fallback:

- `paper.pdf`

`meta.yaml` should record which content source was actually stored:

```yaml
content_format: html
```

or

```yaml
content_format: pdf
```

## Metadata Schema

Recommended `meta.yaml`:

```yaml
paper_id: "2401.12345"
title: "..."
authors:
  - "..."
year: 2026
venue: "arXiv"
url: "https://arxiv.org/abs/2401.12345"
html_url: "https://arxiv.org/html/2401.12345v1"
pdf_url: "https://arxiv.org/pdf/2401.12345"
source: "arxiv"
content_format: "html"
status: "to-read"
added: "2026-04-03"
relevance_to:
  - "PGOOM"
```

## Summary Schema

`summary.md` should be globally reusable, not project-specific.

Recommended structure:

```markdown
# {Paper Title}

## TL;DR

## Core Idea

## Method

## Key Evidence

## Limitations

## Relevance To Projects

## Open Questions
```

Project-specific interpretation belongs in:

```text
vault/projects/{project}/key_papers/{paper_id}/notes.md
```

## Agent Boundary

Agents may:

- expand search intent
- search the web for candidates
- rank results
- explain project relevance
- summarize canonical paper assets

Agents may not:

- directly write canonical paper files
- directly mutate indexes
- resolve duplicates by freeform judgment without structured output

Deterministic services must own:

- id resolution
- directory creation
- raw asset download
- metadata writes
- index updates
- project link creation

## Runtime Integration

The paper module sits directly on top of the agent runtime.

### Search

- build `ContextPack` from active project and indexes
- run `single` or `discussion`
- receive ranked candidates as structured artifacts
- render them to the user

### Ingest

- build a summarization task against canonical global assets
- run a single summarizer or `writer_reviewer`
- write `summary.md`

## Recommended Package Layout

```text
labit/
  papers/
    __init__.py
    models.py
    indexes.py
    services.py
    search_flow.py
    ingest_flow.py
```

## V1 Implementation Order

1. Define canonical paper and key-paper schemas.
2. Define global and project indexes.
3. Implement deterministic pull/ingest storage services.
4. Implement `paper search` on top of the agent runtime.
5. Implement `paper show`.

## Migration Note

The current repo stores papers as flat markdown files under `vault/papers/`.
That structure is too thin for canonical assets plus summaries.

V1 should treat the new `by_id/` layout as the target canonical store.
Migration of older flat notes can happen later and should not block the module design.
