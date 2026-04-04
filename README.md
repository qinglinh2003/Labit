# LABIT

**LABIT** is a local-first CLI control plane for AI-assisted ML research.

It is designed for one job: turning research work from scattered chats, paper notes, ad hoc scripts, and half-tracked experiments into a workflow with real objects and real state.

With LABIT, the core loop is explicit:

**paper -> discussion -> hypothesis -> experiment -> review -> summary**

## What It Does

LABIT gives you a small set of research-native primitives:

- **Projects**
  - Switch the active project and keep work scoped under a project overlay.
- **Papers**
  - Search, pull, ingest, summarize, and open paper-focused discussions.
- **Chat / Focus**
  - Run immersive shared sessions with one or two agents.
  - Use `single`, `round_robin`, or `parallel` modes.
- **Hypotheses**
  - Create structured hypotheses from ongoing discussions.
- **Experiments**
  - Track experiments, tasks, launch artifacts, and debrief state.
- **Memory**
  - Maintain working memory and project-level long-term memory.
- **Sync**
  - Sync configured project directories between compute and object storage.
- **Daily / Weekly Summaries**
  - Write artifact-driven closeout reports for the project.

## Why This Exists

Research workflows usually break in the same places:

- papers are read but not connected to code or experiments
- good discussion gets lost in transcripts
- hypotheses stay informal
- experiments run without a clean object model
- daily progress is hard to reconstruct later

LABIT treats those as first-class workflow problems rather than note-taking problems.

## Current Command Surface

Top-level commands:

```bash
labit project
labit paper
labit hypothesis
labit experiment
labit memory
labit sync
labit chat
labit daily-summary
labit weekly-summary
```

### Project

```bash
labit project list
labit project show
labit project switch PGOOM
```

### Papers

```bash
labit paper search
labit paper pull arxiv:2601.23041
labit paper ingest arxiv:2601.23041
labit paper show
labit paper show arxiv:2601.23041
labit paper focus open arxiv:2601.23041
```

### Chat

```bash
labit chat
labit chat --mode round_robin
labit chat --mode parallel --provider claude --second-provider codex
```

Inside an immersive session, the main slash commands are:

```text
/idea
/note
/todo
/synthesize
/investigate
/hypothesis
/launch-exp
/debrief
/review-results
/memory
/think
/long-term-memory
/think-long-term
```

### Hypotheses

```bash
labit hypothesis list
labit hypothesis show h001
```

### Experiments

```bash
labit experiment list
labit experiment show e001
```

### Memory

```bash
labit memory list
labit memory show m001
labit memory delete m001
```

### Sync

```bash
labit sync
labit sync push
labit sync pull
```

### Summaries

```bash
labit daily-summary
labit daily-summary --date 2026-04-03

labit weekly-summary
labit weekly-summary --date 2026-04-04
```

`--json` is supported on the reporting commands when you want machine-readable output while still writing files to disk.

## Typical Workflow

### 1. Start from a paper

```bash
labit paper search
labit paper ingest arxiv:2601.23041
labit paper focus open arxiv:2601.23041
```

### 2. Discuss and capture structure

Inside `labit chat` or `labit paper focus`:

```text
/investigate hidden-state steering side effects
/idea maybe steering vectors suppress compositional reasoning
/hypothesis
```

### 3. Turn the hypothesis into an experiment

```text
/launch-exp h001
/debrief
/review-results h001
```

### 4. Close the loop

```bash
labit daily-summary
labit weekly-summary
```

## Architecture in One Page

LABIT is built around explicit research objects:

- **paper**
  - canonical global paper + project-local key paper record
- **hypothesis**
  - structured research claim, linked to papers and sessions
- **experiment**
  - structured evidence plan linked to a hypothesis
- **task**
  - executable unit under an experiment
- **launch artifact**
  - frozen execution artifact for a task
- **memory**
  - distilled project memory, separate from raw transcript

The design split is intentional:

- **chat transcript** is not memory
- **memory** is not the source of truth for experiments
- **experiments** do not directly determine hypothesis resolution
- **review** is what closes hypotheses

## Repository Layout

```text
configs/
  active_project
  projects/{project}.yaml

vault/
  papers/
  projects/{project}/
    code/
    hypotheses/
    experiments/
    key_papers/
    docs/
      ideas/
      notes/
      todos/
      reports/
      daily/
      weekly/

.labit/
  conversations/
  runs/
  context/
```

## Context and Memory

LABIT now has a real context stack instead of just raw transcript replay:

- session events
- working memory
- long-term memory
- assembled context
- project maps for papers, reports, docs, and code

The default chat path stays relatively lightweight.
Heavier retrieval is opt-in through:

```text
/long-term-memory <question>
/think <question>
/think-long-term <question>
```

## Requirements

Python:

- Python `>= 3.12`

Core Python dependencies:

- `typer`
- `rich`
- `pydantic`
- `PyYAML`

External tools you will realistically want:

- `claude` and/or `codex`
- `git`
- `ssh`
- `rclone` for `labit sync`

Install locally:

```bash
pip install -e .
```

## Notes

- LABIT is local-first. It writes real files under the repo instead of hiding state in a hosted service.
- `chat` is the main research surface.
- `paper focus` is a specialized paper-scoped chat session.
- experiment execution and sync depend on project compute config; if a project has `compute.backend: none`, LABIT will refuse to submit or sync rather than guessing.

## Status

Current version:

```bash
labit --version
```

At the moment, LABIT already supports the main v1 loop:

- manage projects
- search / ingest / focus papers
- hold shared agent discussions
- create hypotheses
- scaffold and track experiments
- maintain project memory
- sync artifacts
- write daily and weekly summaries

The remaining work is mostly about refinement, not missing core objects.
