# LABIT Agent Runtime V1

## Purpose

`labit` owns the workflow, state transitions, and persistence model.
Claude and Codex are replaceable agent backends inside a controlled runtime.

The runtime exists to support:

- freeform multi-agent discussion with project-aware context
- structured handoff between agents
- deterministic mutation after discussion
- auditable artifacts for every agent run

## Non-Goals

V1 is not:

- a fully autonomous lab assistant
- an always-on multi-agent chat room
- a direct wrapper around two interactive TUIs
- a system where agents mutate repo state during open-ended discussion

## Core Design Principles

- `labit` is the orchestrator, not Claude or Codex.
- Providers are implementation details. Workflows target roles and modes, not model brands.
- Discussion may be open-ended; mutation may not.
- Every run has an explicit context pack and an explicit artifact trail.
- Repo files remain the source of truth. Agent sessions are not system memory.
- Write ownership must be explicit whenever code or repo state can change.

## Two-Phase Model

Every multi-agent workflow is split into two phases.

### 1. Discussion Phase

This phase is intentionally flexible.

Agents may:

- ask follow-up questions
- challenge assumptions
- propose alternatives
- gather evidence
- refine the task framing

Agents may not:

- directly write canonical repo state
- directly update configs, vault entries, or experiment state
- silently execute destructive actions

Discussion outputs a structured synthesis artifact.

### 2. Action Phase

This phase is intentionally constrained.

It consumes the synthesis artifact and performs one of:

- deterministic CLI writes
- a single-agent implementation task with explicit write scope
- a controlled worktree-based parallel implementation task

Action outputs an execution artifact.

## Runtime Layers

### 1. Context Layer

Builds a structured `ContextPack` from repo state.

Responsibilities:

- load active project context
- collect relevant papers, reports, hypotheses, ideas, and code references
- summarize current task state
- expose known constraints and available tools

This is the only supported way to provide long-lived memory to agents.

### 2. Orchestration Layer

Owns:

- collaboration mode selection
- role-to-provider assignment
- turn ordering
- stop conditions
- artifact routing

This is the heart of the runtime.

### 3. Provider Layer

Wraps concrete backends.

V1 adapters:

- `ClaudeAdapter`
- `CodexAdapter`

The orchestrator depends on a provider interface, not on transport details.

### 4. Persistence Layer

Stores:

- run manifests
- context snapshots
- raw agent outputs
- synthesis artifacts
- execution artifacts

### 5. Mutation Layer

Owns all state-changing operations.

Examples:

- write paper metadata
- write summaries
- create project config
- apply code changes
- delete project data

Open discussion never bypasses this layer.

## Core Objects

### `ContextPack`

The full structured input for a workflow run.

Suggested shape:

```json
{
  "project": {
    "name": "PGOOM",
    "description": "...",
    "keywords": ["..."],
    "relevance_criteria": "..."
  },
  "task": {
    "kind": "paper_search",
    "goal": "Find recent VLM hallucination papers",
    "constraints": ["active-project-only"]
  },
  "memory": {
    "recent_reports": [],
    "open_hypotheses": [],
    "key_papers": [],
    "global_matches": []
  },
  "workspace": {
    "repo_root": "...",
    "allowed_write_scope": []
  }
}
```

### `TaskSpec`

The normalized request that enters the runtime.

Fields should include:

- `kind`
- `goal`
- `mode`
- `requires_mutation`
- `expected_outputs`
- `write_scope`

### `CollaborationMode`

V1 supports exactly three modes:

- `discussion`
- `writer_reviewer`
- `parallel_write`

These are workflow-level modes, not model choices.

### `AgentRole`

Roles are independent from providers.

V1 roles should stay small and concrete:

- `discussant`
- `writer`
- `reviewer`
- `scout`
- `normalizer`
- `synthesizer`

### `ProviderAssignment`

Maps roles to providers per workflow.

Example:

```yaml
paper_search:
  scout: claude
  normalizer: codex
  synthesizer: claude
```

### `RunArtifact`

Every agent call writes one artifact.

Suggested fields:

- `run_id`
- `task_kind`
- `mode`
- `role`
- `provider`
- `input_refs`
- `output`
- `status`
- `created_at`

### `SynthesisArtifact`

The boundary object between discussion and action.

Suggested fields:

- `summary`
- `claims`
- `evidence`
- `open_questions`
- `recommended_next_step`
- `mutation_plan`

### `ExecutionArtifact`

Records what actually changed or was produced.

Suggested fields:

- `performed_by`
- `action_kind`
- `write_targets`
- `outputs`
- `verification`

## Memory Model

V1 uses explicit memory, not implicit conversation state.

### Cold Memory

Repo-owned facts:

- configs
- paper metadata
- paper summaries
- reports
- hypotheses
- ideas

### Warm Memory

Project-scoped operational summary assembled on demand.

Examples:

- active project overview
- recent findings
- recent key papers
- current open problems

### Hot Memory

Run-local context assembled during a single workflow.

Examples:

- current search results
- rejected candidates
- intermediate claims
- review comments

Only cold memory is authoritative. Warm and hot memory are derived views.

## Collaboration Modes

### `discussion`

Use for:

- ideation
- paper search
- hypothesis shaping
- root cause exploration

Behavior:

- two or more agents may exchange structured turns
- the conversation is open-ended within a bounded turn budget
- the result is a synthesis artifact

This is the mode for free discussion. It is not limited to adversarial debate.

### `writer_reviewer`

Use for:

- most coding tasks
- drafting summaries or reports
- deterministic content generation with quality control

Behavior:

- one agent writes
- one agent reviews
- the writer or `labit` resolves the review

This should be the default mutation mode.

### `parallel_write`

Use for:

- high-value architecture tasks
- ambiguous or risky implementations
- tasks where solution diversity is valuable

Behavior:

- each writer gets an isolated worktree or isolated output target
- both outputs are reviewed and compared
- `labit` chooses or merges the result

This should be rare and explicit.

## Stop Conditions

Open discussion must not run indefinitely.

V1 stop conditions:

- max turn count reached
- max provider budget reached
- synthesis confidence passes threshold
- no new claims or evidence for N turns
- user intervention requested

## Provider Interface

The provider interface must hide transport details.

Suggested interface:

```python
class AgentAdapter(Protocol):
    def run(self, request: AgentRequest) -> AgentResponse: ...
```

`AgentRequest` should contain:

- `role`
- `system_instructions`
- `task_prompt`
- `context_pack`
- `expected_schema`
- `conversation_state`
- `tool_policy`

`AgentResponse` should contain:

- `content`
- `structured_output`
- `follow_up_requests`
- `usage`
- `raw_ref`

## Provider Implementations

### Claude

Recommended initial transport:

- non-interactive programmatic invocation

Possible backends:

- `claude -p`
- Claude Agent SDK
- Claude MCP server

The runtime should not depend on any single Claude transport.

### Codex

Recommended initial transport:

- `codex exec`

Possible backends:

- `codex exec`
- `codex mcp-server`

Again, orchestration depends on the adapter, not the invocation style.

## Artifact Store

Recommended run layout:

```text
.labit/runs/{run_id}/
  manifest.json
  context.json
  turns/
    001-claude-discussant.json
    002-codex-discussant.json
  synthesis.json
  execution.json
```

This store is essential for:

- debugging agent behavior
- resuming or replaying workflows
- auditing why a conclusion was reached

## Mutation Gate

No discussion run may directly mutate canonical state.

All mutations must be routed through one of:

- a deterministic service method
- a controlled writer role with explicit write scope
- a worktree-backed parallel writer flow

This is the main protection against agent drift.

## Worktree Policy

If more than one agent writes code:

- they must not share a mutable working tree
- each writer gets a distinct worktree or diff target
- merge selection is performed after review

If only one agent writes:

- the reviewer is read-only by default

## Proposed Package Layout

```text
labit/
  agents/
    __init__.py
    orchestrator.py
    context.py
    artifacts.py
    modes.py
    schemas.py
    adapters/
      __init__.py
      base.py
      claude.py
      codex.py
```

## V1 Scope

V1 should implement:

- `ContextPack`
- provider adapters for Claude and Codex
- artifact persistence
- `discussion`
- `writer_reviewer`

V1 should not yet implement:

- autonomous background agent swarms
- persistent long-running multi-agent servers
- implicit memory sync through CLI session reuse
- unrestricted discussion-to-write execution
