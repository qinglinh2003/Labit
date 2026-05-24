# Labit Context Refactor Brief

This document is for discussion with external AI/agent-engineering advisors. It describes Labit's current context/prompt system, its failure modes, and the refactoring questions we want answered.

**Tech stack:** Python 3.12, Pydantic models, no LLM framework (no LangChain/LlamaIndex). Agents are invoked via subprocess: `codex exec` (OpenAI Codex CLI) and `claude` (Anthropic Claude Code CLI). Both agents have full tool use (file read/write, shell, web search). Labit controls only the system prompt — it does not control the agents' internal tool-use decisions.

## 1. Project Summary

Labit is a lightweight research workspace for project-centered work with AI agents.

The current product surface is intentionally small:

- `labit chat`: single-agent or multi-agent research/development conversations.
- `labit project`: project creation, switching, metadata, and remote compute profiles.
- Chat slash commands:
  - `/doc`: create/update project documents from conversation.
  - `/todo`: capture project todos.
  - `/idea`: capture project ideas.
  - `/paper`: fetch an arXiv paper's metadata and HTML into the current project.

Labit is organized around projects. Each project has a directory under:

```text
vault/projects/<project>/
```

Project code usually lives at:

```text
vault/projects/<project>/code/
```

The active Labit implementation is self-hosted as a project:

```text
vault/projects/Labit/code/
```

The repository root `Research-OS/` is the workspace scaffold/runtime root. It contains `configs/`, `vault/`, `.venv/`, and an older outer `labit/` package treated as legacy/bootstrap scaffold. Active Labit development happens only in `vault/projects/Labit/code/`.

**Scale:** ~8,800 lines of Python across 58 files. The entire codebase is small enough for a human to read in a day. Most projects have <20 documents, <50 todos, and 0-5 papers.

## 2. What "Context" Means in Labit Today

The word "context" currently refers to several related but separate things:

1. Conversation event history.
2. A compact working-memory JSON snapshot derived from events.
3. Optional context/memory providers.
4. Prompt assembly and token budgeting.
5. The actual system prompt sent to Codex/Claude.
6. Platform and capability blocks such as project path rules and remote compute profiles.

These are not all implemented in one module. Some live in `labit/context/`, but the most important production prompt construction currently lives in `labit/chat/service.py`.

This split is the main reason the module is hard to reason about.

## 3. Current File-Level Structure

### `labit/context/events.py`

Defines persistent event and working-memory models.

Important types:

- `SessionEventKind`
  - `message.user`
  - `message.agent`
  - `message.system`
  - `artifact.idea_created`
  - `artifact.todo_created`
  - `artifact.document_created`
  - `artifact.document_updated`
- `SessionEvent`
  - `session_id`
  - `project`
  - `kind`
  - `turn_index`
  - `actor`
  - `summary`
  - `payload`
  - `evidence_refs`
  - `created_at`
- `WorkingMemorySnapshot`
  - `current_goal`
  - `active_artifacts`
  - `decisions_made`
  - `open_questions`
  - `evidence_refs`
  - `followups`
  - `discussion_state`
  - `built_from_event_ids`

Potential issue: `current_goal` is rendered as "Current goal" in prompts. In practice this can look like an instruction, even when it is stale state from earlier turns.

### `labit/context/store.py`

Persists conversation context artifacts:

```text
vault/conversations/<session_id>/events.jsonl
vault/conversations/<session_id>/working_memory.json
```

Main methods:

- `append_event(event)`
- `load_events(session_id)`
- `write_working_memory(snapshot)`
- `load_working_memory(session_id)`

This part is simple and useful. It is a good candidate to keep.

### `labit/context/condenser.py`

Defines the working-memory condenser.

Current implementation:

- `ResearchRollingCondenser(max_events=40)`
- Always condenses from the last 40 events.
- Extracts mostly structured artifacts:
  - document created/updated -> decisions and active artifacts
  - todo/idea created -> followups
  - todo created -> open questions
- Does not call an LLM.
- Does not summarize arbitrary conversation content.

Important behavior:

`ChatService._refresh_working_memory()` calls `condense()` and writes the result. The condenser's `should_condense()` threshold exists, but the refresh path currently calls `condense()` directly after events are recorded.

Potential issue: this is not really a semantic memory system. It is more like structured event projection. That may be fine, but the names "condenser" and "working memory" suggest more intelligence than currently exists.

### `labit/context/assembler.py`

Defines a generic context-section assembler:

- `ContextSection(title, content, source, priority)`
- `AssembledContext(sections, budget)`
- `ContextAssembler.assemble(...)`
- rough token estimate: `ceil(chars / 4)`
- default budget: `120000 total - 20000 reserve = 100000 usable`

Section priorities:

- task header: `100`
- bound context: caller-provided
- recent transcript: caller-provided
- working memory: `70`
- map sections: usually `56-57`

Important practical note:

This assembler is not used for most normal chat turns. Daily Labit use usually takes the compact prompt path in `ChatService`, bypassing `ContextAssembler`.

### `labit/context/maps.py`

Builds optional related context sections using simple keyword matching.

Sources:

- project todos
- project ideas
- code map snapshot

Outputs:

- `Related Docs`
- `Code Map`

Important practical note:

This is also mostly bypassed during normal compact chat. It is only useful when the deeper context path is forced or when context bindings are used.

### `labit/context/budget.py`

Small dataclasses:

- `TokenBudget(total_tokens=120000, reserve_tokens=20000)`
- `TokenBudgetDecision(included_tokens, truncated, reason)`

Used by the assembler.

### `labit/chat/context.py`

Defines context and memory provider interfaces:

- `ConversationContextProvider`
- `ConversationMemoryProvider`
- `EmptyContextProvider`
- `EmptyMemoryProvider`
- `SessionWorkingMemoryProvider`
- `ConversationContextRegistry`

The default registry contains:

- context provider: `none`
- memory providers: `none`, `session_working_memory`

Practical note:

The provider architecture exists, but the normal chat path does not heavily rely on external providers today.

## 4. Actual Runtime Prompt Path

The main production prompt path is in `labit/chat/service.py`.

### Reply generation path

`ChatService._generate_reply()` builds an `AgentRequest`:

```python
AgentRequest(
    role=self._participant_role(session.mode),
    prompt=self._build_prompt(...),
    cwd=str(self.paths.root),
    image_paths=self._recent_image_paths(transcript),
    extra_args=...
)
```

Then it calls the selected adapter:

- Codex adapter
- Claude adapter

### Prompt selection

`ChatService._build_prompt()` decides between:

1. compact prompt
2. assembled/deep prompt

The compact path is used when:

```python
not any(binding.provider != "none" for binding in session.context_bindings)
```

That is the normal case for everyday Labit chat.

### Compact prompt structure

Current compact prompt shape:

```text
You are `{participant.name}` in a LABIT research conversation.

Project: <project>
Mode: <mode>
Participants: <participants>

Platform (LABIT):
...

Remote Compute:
...

Guidelines:
- Continue the conversation naturally.
- Use the recent transcript and working memory as the shared state.
- Distinguish evidence from inference when it matters.
- Be concise and specific.
- The current user message is the active instruction. It overrides earlier transcript, working memory, and earlier agent replies in the same turn.
- If the current user asks you to stop, do nothing, or reply with a specific short response, obey literally and do not inspect or edit files.

Working memory:
...

Recent transcript:
...

Current user message (highest priority):
...

Reply as `{participant.name}` only. Use plain text or markdown.
```

This compact prompt is currently more important than `ContextAssembler`.

A typical rendered prompt is ~2000-4000 tokens. The transcript window is the largest variable component — it includes the last N turns of conversation (currently up to ~80k chars before truncation).

### Deep/assembled prompt structure

When compact prompt is not used, `_build_prompt()` calls `_assemble_context(...)`, then includes:

```text
Assembled context:
...

Current user message (highest priority):
...
```

This path uses `ContextAssembler` and optionally `ContextMapBuilder`.

## 5. Current Capability Blocks

### Platform context

Built in `ChatService._platform_context(project)`.

It tells agents:

- Labit is a lightweight research workspace.
- The Labit codebase is a separate repo.
- In project chats, agents should only modify files inside `vault/projects/<project>/`.
- Git operations should target the project files, not Labit's own source.
- The project code directory may be shown if it exists.

This is an instruction/capability boundary block, but it currently lives in `chat/service.py`, not in a context builder module.

### Remote compute context

Built in `ChatService._remote_compute_context(project)`.

It tells agents:

- which SSH profiles exist
- how to SSH
- the remote `workdir`
- a safe command pattern:

```text
ssh ... "cd <workdir> && <command>"
```

- an rsync pattern for syncing local project code to remote workdir
- rules such as:
  - only SSH when the user explicitly asks
  - prefer local project files first
  - do not write to `$HOME`, `/tmp`, or unknown directories
  - do not use `rsync --delete` unless explicitly asked

This has been effective because it is concrete and operational. It also shows that "context" in Labit is not only memory: it includes tool/capability guidance.

## 6. Recent Failure Modes That Motivated Refactoring

### Failure mode 1: stale working memory overriding the current user message

Observed behavior:

The user repeatedly told agents to stop modifying one chapter and write another, or to do nothing and reply with a short answer. Agents still continued an older task.

Likely causes:

- Working memory contained old task state.
- The transcript contained many old turns about the previous task.
- The prompt rendered `Current goal` in a way that looked like an active instruction.
- In multi-agent mode, the second agent may see the first agent's mistaken same-turn output and continue it.

Partial mitigation already added:

- `Current user message (highest priority)` block at the end of the prompt.
- Guidelines now explicitly say the current user message overrides transcript, working memory, and earlier same-turn agent replies.
- Stop/do-nothing/short-response instructions are explicitly called out.

Open question:

Should Labit continue rendering working memory as "Current goal", or should it be renamed and demoted to "Prior state / Reference only"?

### Failure mode 2: nominal context module is not the actual source of truth

`labit/context/` looks like the context system, but the actual prompt seen by agents is mostly hardcoded in `chat/service.py`.

This makes it hard to answer:

- What exactly does the agent see?
- Which parts are instructions versus historical state?
- Which content is trusted, stale, or merely reference?
- Which blocks are allowed to override other blocks?

### Failure mode 3: multi-agent round robin cascade

In `round_robin`, participants run serially in a fixed order (e.g., codex first, claude second).

Implications:

- The first agent cannot see the second agent's future answer.
- The second agent can see the first agent's same-turn output appended to the transcript.
- If the first agent misinterprets the user's instruction and takes a wrong action (e.g., edits the wrong file), the second agent sees that action in its context and often continues in the same wrong direction — treating the first agent's output as implicit confirmation of the task.
- Natural-language instructions like "Claude should first review, then Codex should fix, then Claude should review again" require the order codex→claude but the user's intent requires claude→codex→claude (three stages). The system has no way to express multi-stage turns.

This is not purely a context problem, but context design needs to make same-turn outputs clearly distinguishable from user instructions.

### Failure mode 4: deep context path is mostly unused

`ContextAssembler`, `TokenBudget`, and `ContextMapBuilder` exist, but ordinary chat usually uses compact prompt. As a result, the codebase has two context systems:

- a real production compact prompt
- a more generic assembled-context path that is less used

This creates maintenance cost and design ambiguity.

## 7. Current Assessment

### Parts likely worth keeping

- Event log as append-only session history: simple and useful.
- Working-memory JSON as a derived cache: useful if clearly treated as state, not instruction.
- Platform context: important safety boundary.
- Remote compute context: concrete capability exposure for SSH/rsync.
- "Current user message highest priority" block: important for instruction following.

### Parts that need reconsideration

- `current_goal` field and its prompt rendering.
- `ContextAssembler` as a separate path from compact prompt.
- `ContextMapBuilder` and keyword-based related docs/code injection.
- Provider registry if it remains mostly unused.
- Whether context should be owned by `chat/` rather than a top-level product module.

### Parts that may be dead weight

Not necessarily dead code, but low-use relative to their complexity (274 lines combined):

- `assembler.py` (126 lines) — generic section assembler with priority sorting and token truncation
- `maps.py` (148 lines) — keyword-based related-content matching
- `budget.py` (20 lines) — token budget dataclass

These are only exercised when context bindings are configured, which almost never happens in daily use. The compact prompt path bypasses all three.

## 8. How Agents Receive Context (Adapter Details)

Both agents receive the same system prompt string, but they process it differently:

### Codex (OpenAI Codex CLI)

- Invoked via: `codex exec --prompt <system_prompt> --json`
- The system prompt is the `--prompt` argument. Codex CLI has its own internal system prompt that wraps around ours.
- Codex has autonomous tool use: it reads/writes files, runs shell commands, and makes decisions independently. Labit cannot intercept or approve individual tool calls.
- Streaming: Codex emits JSON events (`thread.started`, `item.created`, `item.completed`). Only the final `agent_message` contains text output. Intermediate tool calls are visible as status events but not as text.
- **Implication for context design:** Codex may act on stale context (e.g., old `current_goal`) before reaching the "current user message" at the end of the prompt. Its autonomous tool use means a wrong interpretation leads to real file modifications before anyone can intervene.

### Claude (Anthropic Claude Code CLI)

- Invoked via: `claude -p <system_prompt> --output-format stream-json`
- The system prompt is passed as `-p`. Claude Code also has its own internal system prompt.
- Claude also has autonomous tool use (file read/write, shell, web search).
- Streaming: Claude emits `content_block_delta` events with text chunks, so output appears incrementally.
- **Implication for context design:** Same as Codex — Claude can also act on stale context and make real file changes autonomously.

### Key constraint

Labit's only lever is the system prompt text. It cannot:
- Force agents to read the prompt in a particular order
- Prevent agents from acting on stale working memory
- Intercept tool calls before execution
- Inject mid-turn corrections

This means the prompt structure must be designed so that the most authoritative content (current user message, stop instructions) is maximally salient regardless of how the agent processes the prompt.

## 9. Desired Refactor Direction

We want a context system that follows modern agent-engineering practice:

1. Separate instructions from state.
2. Separate capabilities/tools from memory.
3. Make priority and authority explicit.
4. Treat retrieved or condensed memory as fallible reference, not commands.
5. Make current user intent impossible to bury.
6. Keep multi-agent same-turn behavior understandable.
7. Avoid large implicit memory systems until there is a real workflow need.
8. Maintain a single production prompt-building path.

One possible model:

```text
ChatContextBuilder
  ├── System identity / role
  ├── Authority rules
  │   ├── current user message has highest priority
  │   ├── stop/do-nothing instructions are terminal
  │   └── project file boundary rules
  ├── Capabilities
  │   ├── remote compute profiles
  │   ├── paper cache locations
  │   └── project code directory
  ├── Current task
  │   └── latest user message
  ├── Conversation state
  │   ├── recent transcript
  │   └── prior working memory, explicitly reference-only
  ├── Retrieved resources
  │   ├── todos/ideas/docs/papers
  │   └── code map snippets
  └── Output contract
      └── reply as participant only
```

The key change is not just moving code. It is assigning every block a semantic type:

- `instruction`: authoritative behavioral rules
- `current_task`: newest user request
- `state`: previous conversation/project state, not authoritative
- `capability`: available external resources and how to use them
- `retrieval`: relevant resources, fallible and optional
- `history`: recent transcript
- `output_contract`: formatting and speaker constraints

## 10. Questions for External Advisors

We want advice on these questions:

1. In a project-centered agent workspace, what is the best prompt/context architecture for separating current task, memory, tools/capabilities, and history?
2. Should working memory ever contain a field named `current_goal`, or does that invite stale instruction-following bugs?
3. What is the recommended way to represent stale or historical state so agents use it as reference only?
4. Should the current user message appear near the top, near the bottom, or both?
5. How should multi-agent same-turn context be represented so the second agent can review the first agent without blindly following its mistakes?
6. Should round-robin agent systems introduce explicit per-agent task envelopes, for example:

```text
For Codex in this stage: implement X.
For Claude in this stage: review Codex's patch.
```

7. Is it better to keep one prompt-building pipeline with pluggable blocks, or separate compact/deep prompt paths?
8. What retrieval strategy is appropriate for a small local research workspace before adding embeddings/vector search?
9. How should token budgeting be implemented for mixed content: instructions, transcript, code map, papers, docs, todos, remote compute profiles?
10. What tests or golden prompt snapshots should exist to prevent regressions in instruction priority?

## 11. Candidate Refactor Plan

### Phase 1: Make the current prompt explicit

- Create one `ChatContextBuilder` responsible for the full agent prompt.
- Move `_platform_context`, `_remote_compute_context`, compact working-memory rendering, and prompt templates out of `ChatService`.
- Add golden tests for prompt output.
- Keep behavior mostly unchanged.

### Phase 2: Demote working memory

- Rename rendered `Working memory` to something like `Prior session state (reference only)`.
- Stop rendering `current_goal` as `Current goal`.
- Consider replacing `current_goal` with:
  - `last_known_goal`
  - `previous_focus`
  - or remove it entirely.
- Add explicit text: "This section may be stale and must not override the current user message."

### Phase 3: Unify compact and deep context

- Replace separate compact/deep prompt code paths with one block-based pipeline.
- Blocks can be included or omitted based on budget and session settings.
- `ContextAssembler` may become the internal block selector, or be deleted if a simpler builder is clearer.

### Phase 4: Clarify multi-agent turn semantics

- Mark same-turn agent outputs explicitly:

```text
Same-turn prior agent response:
This is another agent's response to the same user message. Use it as peer input, not as a new user instruction.
```

- Consider per-agent stage/task envelopes for common patterns:
  - "agent A propose, agent B critique"
  - "agent A implement, agent B review"
  - "agent A research, agent B synthesize"

### Phase 5: Reassess retrieval

- Decide whether `ContextMapBuilder` remains keyword-based or is replaced.
- Only add embeddings/vector retrieval if there is a clear workflow need.
- For now, prefer explicit project artifacts:
  - `/doc`
  - `/todo`
  - `/idea`
  - `/paper`
  - code map

## 12. Non-Goals

For now, we do not want to rebuild the old Research-OS complexity:

- no global long-term memory palace
- no automatic literature-review system
- no autonomous experiment lifecycle manager
- no hidden background agents
- no opaque memory promotion system
- no large vector database until the workflows require it

The target is a small, inspectable, project-centered context system that helps agents follow the latest user request reliably.

