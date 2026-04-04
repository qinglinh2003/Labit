# LABIT Context & Memory Architecture v1

## Goal

Design LABIT's context and memory system as a **research work memory system**, not a generic chat history cache.

The system should help an agent:

- understand the current research task quickly
- distinguish evidence from inference
- preserve continuity across long discussions
- reuse prior papers, reports, hypotheses, and future experiments
- keep dual-agent discussions useful without replaying every turn verbatim

## Researcher POV

A researcher does not want the agent to "remember everything."

A researcher wants the agent to reliably know:

- what the current project is trying to solve
- what the current working set is
- what evidence already exists
- what has already been tried or ruled out
- where the current uncertainty or disagreement lies

So LABIT should optimize for:

- research continuity
- evidence-aware reasoning
- task-shaped working sets

## What To Borrow

### OpenHands

Borrow:

- append-only event log
- `view` as a derived representation over raw history
- condenser-based compression

Use for:

- chat/focus session history
- dual-agent shared discussion condensation
- working-memory snapshots

Reference:

- https://docs.openhands.dev/sdk/arch/condenser

### Aider

Borrow:

- repo map / codebase map mentality
- code context compression before prompt assembly

Use for:

- investigation
- paper summarization with project code awareness
- hypothesis formation against current code reality

References:

- https://aider.chat/docs/repomap.html
- https://aider.chat/2023/10/22/repomap.html

### LangMem / LangGraph Memory

Borrow:

- typed memories
- namespaces
- retrieval over structured memory instead of a single blob

Use for:

- project-level long-term memory
- paper/hypothesis/investigation scoped memories
- semantic vs episodic vs procedural separation

Reference:

- https://langchain-ai.github.io/langmem/concepts/conceptual_guide/

### What Not To Copy

The public Python "Claude Code refactor" repos are useful as harness signals, but not as the main LABIT memory design reference.

What they are useful for:

- session flushing
- lightweight compaction awareness

What they do not provide strongly enough:

- typed long-term memory
- research artifact awareness
- event-sourced dual-agent discussion handling

Reference:

- https://github.com/ultraworkers/claw-code-parity

## Core Distinctions

### Artifact

A first-class research object:

- paper
- key paper summary
- hypothesis
- investigation report
- future experiment

### Transcript

Raw conversation messages.

Useful for audit, but not itself memory.

### Event

A typed append-only record of something that happened in a session.

Examples:

- user message
- agent reply
- paper focus bound
- idea captured
- hypothesis created
- report created

### Context

The assembled view sent to an agent for one turn.

### Memory

Distilled, reusable knowledge extracted from artifacts and events.

## Design Principles

1. `artifact-first`

Stable knowledge should point back to papers, reports, hypotheses, and future experiments.

2. `event-sourced`

Conversation state should be reconstructed from typed events, not hidden session state.

3. `distilled-not-dumped`

Do not stuff full history into prompts by default.

4. `shared-by-default`

Long-term memory is shared at the LABIT/project level, not privately hidden inside one agent session.

5. `task-shaped`

Different workflows need different context slices.

6. `inspectable`

Memories must be readable, traceable, and editable.

## LABIT-Specific Constraints

Compared with Claude Code or Codex, LABIT has extra high-value sources:

- project key papers
- project docs and reports
- dual-agent discussions
- project hypotheses
- future experiment lifecycle objects

These must become first-class context inputs and memory sources.

## Architecture

### Layer 1: Artifact Layer

Canonical research objects live here.

Examples:

- `vault/papers/by_id/{paper_id}/`
- `vault/projects/{project}/key_papers/{paper_id}/`
- `vault/projects/{project}/hypotheses/h###/`
- `vault/projects/{project}/docs/reports/`

This layer remains the durable research truth.

### Layer 2: Session Event Layer

Every interactive session gets a typed event log.

Storage:

```text
.labit/conversations/{session_id}/
  session.json
  transcript.jsonl
  events.jsonl
  working_memory.json
  context.json
```

`transcript.jsonl` remains a human/audit view.

`events.jsonl` becomes the typed source for condensation and later retrieval.

Recommended event kinds:

- `message.user`
- `message.agent`
- `message.system`
- `artifact.focus_bound`
- `artifact.idea_created`
- `artifact.note_created`
- `artifact.todo_created`
- `artifact.hypothesis_created`
- `artifact.report_created`
- `discussion.synthesis`
- future: `artifact.experiment_created`
- future: `artifact.experiment_debriefed`

### Layer 3: Working Memory Layer

Working memory is session-scoped and refreshed over time.

It is not raw history; it is a rolling distilled state.

Recommended fields:

- `current_goal`
- `active_artifacts`
- `decisions_made`
- `open_questions`
- `evidence_refs`
- `followups`
- `discussion_state`

`discussion_state` should capture:

- `consensus`
- `disagreements`
- `followups`

This is especially important for dual-agent sessions.

### Layer 4: Long-Term Memory Layer

Long-term memory is project-scoped and typed.

Storage:

```text
vault/projects/{project}/memory/
  index.yaml
  entries/
    {memory_id}.yaml
```

Memory kinds:

- `project_frame`
- `decision`
- `open_loop`
- `paper_takeaway`
- `discussion_takeaway`
- `investigation_finding`
- `experiment_outcome`
- `code_fact`

Memory types:

- `semantic`
  Stable facts and enduring project understanding.
- `episodic`
  Outcomes from concrete sessions, investigations, or experiments.
- `procedural`
  Workflow heuristics, preferred evaluation patterns, and team norms.

### Layer 5: Maps / Indexes

These are compressed lookup layers, not memories.

Important maps:

- `code map`
- `paper map`
- `report map`
- later: `experiment map`

Maps help assemble context quickly without reading everything.

In v1, the main session-time maps are:

- `Related Project Papers`
- `Related Reports`
- `Related Docs`
- `Code Map`

These are not long-term memories. They are lightweight working-set views derived from canonical project artifacts.

In retrieval, these maps should also act as query-shaping hints for long-term memory lookup, so LABIT does not rely only on the user's most recent wording.

### Layer 6: Context Assembler

The assembler builds a turn-specific context view from:

- task frame
- bound artifacts
- recent transcript window
- working memory snapshot
- retrieved long-term memories
- maps/indexes

The assembler should operate under an explicit token budget.

## Namespace Model

Long-term memory should use hierarchical namespaces.

Recommended namespaces:

- `project/{project}`
- `paper/{paper_id}`
- `hypothesis/{hypothesis_id}`
- `investigation/{investigation_id}`
- `conversation/{session_id}`

These can be stored as tuples or slash-separated paths in the implementation.

## Memory Record Schema

Minimum shape:

```yaml
memory_id: m0001
project: PGOOM
namespace: project/PGOOM
kind: discussion_takeaway
memory_type: episodic
title: Probe disagreement suggests anchor instability
summary: >
  During paper focus on OSGA, the two-agent discussion converged on the
  concern that anchor selection may be the main bottleneck.
evidence_refs:
  - paper:arxiv:2601.23041
source_event_ids:
  - evt_...
source_artifact_refs:
  - conversation:054534459a49
confidence: medium
status: active
promotion_score: 8
promotion_reasons:
  - has_consensus
  - has_followups
updated_at: 2026-04-03T23:00:00Z
```

## Promotion Policy

Long-term memory promotion should not be a blind "one event -> one record" append rule.

v1 policy:

- score candidate memories before promotion
- only promote when the score crosses a minimum threshold
- consolidate candidates that match an active record with the same `kind + namespace`
- allow newer formal artifacts to supersede weaker discussion takeaways from the same conversation
- keep retrieval focused on `active` records by default

Concrete v1 examples:

- `discussion.synthesis`
  - promote only when it has enough signal such as consensus, follow-ups, or evidence refs
- `artifact.hypothesis_created`
  - always promote as a strong `open_loop`
- `artifact.report_created`
  - promote as an `investigation_finding` when it includes enough report signal

Supersession rule:

- when a hypothesis or investigation finding is promoted from a conversation, older `discussion_takeaway` memories tied to that same conversation should leave the active set

## Dual-Agent Discussion Policy

Do not convert every multi-agent discussion into long-term memory.

Instead:

- keep the full event log
- distill working memory every session
- promote only high-signal takeaways to long-term memory

Promotion candidates:

- stable consensus
- unresolved but important disagreement
- cited evidence worth reusing
- actionable next steps

This avoids turning noisy discussion into durable clutter.

## Task-Shaped Context Views

### Chat

Default context:

- project frame
- recent turns
- working memory
- relevant long-term memories

### Paper Focus

Default context:

- focused paper metadata
- project summary for that paper
- source excerpt
- related key papers
- relevant discussion/hypothesis memories

### Hypothesis Drafting

Default context:

- current discussion window
- working memory decisions/open questions
- cited papers
- relevant code map

### Investigation

Default context:

- current issue/topic
- relevant code map
- recent discussion state
- prior investigation findings

## Token Budgeting

Context assembly should not be raw concatenation.

Suggested order of inclusion:

1. task header
2. bound artifact excerpts
3. recent turns
4. working memory
5. retrieved long-term memory
6. code/paper/report maps

If the budget is tight:

- shrink maps first
- then shrink long-term memory count
- then shrink recent window
- preserve task header and bound artifact first

## Integration With Current LABIT

Current LABIT already has:

- session storage
- transcript storage
- context bindings
- paper focus context blocks

Current gaps:

- no typed event log
- no real memory provider
- no condenser
- no context budgeter
- raw transcript still goes straight into prompts

So v1 should add the missing layers without immediately rewriting everything.

## Implementation Plan

### Phase 1

- add typed session events
- add working memory snapshot model
- add event + working memory store

### Phase 2

- add condenser interface
- implement a no-op condenser and a rolling research condenser

### Phase 3

- add context assembler and budget model
- keep current chat behavior as fallback

### Phase 4

- add typed long-term memory store
- start promoting selected session outputs into memory entries

### Phase 5

- add code/paper/report maps
- make task-specific assemblers richer

## Success Criteria

The design is successful when:

- chat/focus no longer require full raw transcript replay every turn
- dual-agent discussions preserve disagreements without bloating prompts
- papers/docs/reports/hypotheses become retrievable context sources
- the agent can explain why a memory was loaded
- the researcher can inspect and edit memory artifacts
