from __future__ import annotations

import json

from labit.agents.models import AgentRequest, AgentRole, ProviderKind
from labit.agents.orchestrator import ProviderRegistry
from labit.agents.providers import resolve_provider_kind
from labit.chat.models import ChatMessage, ChatSession, ContextSnapshot
from labit.experiments.models import ExperimentTaskPlan, LaunchExpSession
from labit.paths import RepoPaths


class ExperimentPlanner:
    def __init__(self, paths: RepoPaths, *, registry: ProviderRegistry | None = None):
        self.paths = paths
        self.registry = registry or ProviderRegistry.default()

    def draft_task_breakdown(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        hypothesis_title: str,
        hypothesis_claim: str,
        experiment_plan_md: str,
        code_tree: str,
        provider: str | ProviderKind | None = None,
    ) -> list[ExperimentTaskPlan]:
        provider_kind = resolve_provider_kind(provider)
        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_breakdown_prompt(
                session=session,
                transcript=transcript,
                context_snapshot=context_snapshot,
                hypothesis_title=hypothesis_title,
                hypothesis_claim=hypothesis_claim,
                experiment_plan_md=experiment_plan_md,
                code_tree=code_tree,
            ),
            cwd=str(self.paths.root),
            output_schema=self._breakdown_schema(),
            extra_args=self._extra_args(provider_kind),
        )
        response = self.registry.get(provider_kind).run(request)
        payload = response.structured_output
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Experiment planner returned an invalid payload.")

        tasks = []
        for item in payload.get("tasks", []):
            tasks.append(ExperimentTaskPlan.model_validate(item))
        return tasks

    def revise_task_breakdown(
        self,
        *,
        current_tasks: list[ExperimentTaskPlan],
        user_instruction: str,
        hypothesis_title: str,
        hypothesis_claim: str,
        interaction_log: str = "",
        provider: str | ProviderKind | None = None,
    ) -> list[ExperimentTaskPlan]:
        provider_kind = resolve_provider_kind(provider)
        tasks_json = json.dumps([t.model_dump() for t in current_tasks], indent=2)
        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_revise_breakdown_prompt(
                current_tasks_json=tasks_json,
                user_instruction=user_instruction,
                hypothesis_title=hypothesis_title,
                hypothesis_claim=hypothesis_claim,
                interaction_log=interaction_log,
            ),
            cwd=str(self.paths.root),
            output_schema=self._breakdown_schema(),
            extra_args=self._extra_args(provider_kind),
        )
        response = self.registry.get(provider_kind).run(request)
        payload = response.structured_output
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Experiment planner returned an invalid payload.")

        tasks = []
        for item in payload.get("tasks", []):
            tasks.append(ExperimentTaskPlan.model_validate(item))
        return tasks

    def plan_task_detail(
        self,
        *,
        task: ExperimentTaskPlan,
        all_tasks: list[ExperimentTaskPlan],
        hypothesis_title: str,
        hypothesis_claim: str,
        code_tree: str,
        user_instruction: str = "",
        interaction_log: str = "",
        provider: str | ProviderKind | None = None,
    ) -> ExperimentTaskPlan:
        provider_kind = resolve_provider_kind(provider)
        all_tasks_summary = "\n".join(
            f"  - {t.id}: {t.name} (depends_on: {t.depends_on or 'none'})"
            + (f" [APPROVED]" if t.approved else "")
            for t in all_tasks
        )
        approved_details = ""
        for t in all_tasks:
            if t.approved and t.id != task.id:
                approved_details += f"\n### {t.id}: {t.name}\n"
                approved_details += f"- Goal: {t.goal}\n"
                approved_details += f"- Entry hint: {t.entry_hint}\n"
                approved_details += f"- Inputs: {t.inputs}\n"
                approved_details += f"- Outputs: {t.outputs}\n"
                approved_details += f"- Checkpoint: {t.checkpoint}\n"

        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_task_detail_prompt(
                task=task,
                all_tasks_summary=all_tasks_summary,
                approved_details=approved_details,
                hypothesis_title=hypothesis_title,
                hypothesis_claim=hypothesis_claim,
                code_tree=code_tree,
                user_instruction=user_instruction,
                interaction_log=interaction_log,
            ),
            cwd=str(self.paths.root),
            output_schema=self._task_detail_schema(),
            extra_args=self._extra_args(provider_kind),
        )
        response = self.registry.get(provider_kind).run(request)
        payload = response.structured_output
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Experiment planner returned an invalid payload.")

        return ExperimentTaskPlan.model_validate(payload)

    def generate_run_sh(
        self,
        *,
        session: LaunchExpSession,
        hypothesis_title: str,
        hypothesis_claim: str,
        code_tree: str,
        workdir: str = "",
        setup_script_summary: str = "",
        user_instruction: str = "",
        provider: str | ProviderKind | None = None,
    ) -> dict[str, str]:
        """Generate run.sh and optional config.yaml.

        Returns dict with keys 'run_sh', 'config_yaml' (may be empty), 'summary'.
        """
        provider_kind = resolve_provider_kind(provider)
        tasks_json = json.dumps([t.model_dump() for t in session.task_plans], indent=2)
        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_generate_script_prompt(
                tasks_json=tasks_json,
                hypothesis_title=hypothesis_title,
                hypothesis_claim=hypothesis_claim,
                code_tree=code_tree,
                workdir=workdir,
                setup_script_summary=setup_script_summary,
                user_instruction=user_instruction,
            ),
            cwd=str(self.paths.root),
            output_schema=self._script_schema(),
            extra_args=self._extra_args(provider_kind),
        )
        response = self.registry.get(provider_kind).run(request)
        payload = response.structured_output
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Experiment planner returned an invalid payload.")

        return {
            "run_sh": payload.get("run_sh", ""),
            "config_yaml": payload.get("config_yaml", ""),
            "summary": payload.get("summary", ""),
        }

    def revise_run_sh(
        self,
        *,
        current_run_sh: str,
        current_config_yaml: str,
        tasks_json: str,
        user_instruction: str,
        code_tree: str,
        workdir: str = "",
        setup_script_summary: str = "",
        provider: str | ProviderKind | None = None,
    ) -> dict[str, str]:
        provider_kind = resolve_provider_kind(provider)
        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_revise_script_prompt(
                current_run_sh=current_run_sh,
                current_config_yaml=current_config_yaml,
                tasks_json=tasks_json,
                user_instruction=user_instruction,
                code_tree=code_tree,
                workdir=workdir,
                setup_script_summary=setup_script_summary,
            ),
            cwd=str(self.paths.root),
            output_schema=self._script_schema(),
            extra_args=self._extra_args(provider_kind),
        )
        response = self.registry.get(provider_kind).run(request)
        payload = response.structured_output
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Experiment planner returned an invalid payload.")

        return {
            "run_sh": payload.get("run_sh", ""),
            "config_yaml": payload.get("config_yaml", ""),
            "summary": payload.get("summary", ""),
        }

    # ── Prompt builders ──

    def _build_breakdown_prompt(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        hypothesis_title: str,
        hypothesis_claim: str,
        experiment_plan_md: str,
        code_tree: str,
    ) -> str:
        transcript_text = self._format_transcript(transcript)
        context_text = self._format_context(context_snapshot)

        return f"""You are an experiment planner for LABIT. Your job is to break down a hypothesis into a list of experiment tasks.

Return JSON only. Do not add markdown fences or commentary.

Given the hypothesis, its experiment plan, and the project code structure, propose a list of tasks that together form a complete experiment. Each task should be a logical step (e.g., data preprocessing, training, evaluation).

Requirements:
- Each task needs: `id` (t001, t002, ...), `name` (short), `goal` (one sentence), `depends_on` (list of task ids).
- Tasks must be in topological order (no circular dependencies).
- Keep the number of tasks minimal — typically 2-5. Don't over-decompose.
- `entry_hint`, `inputs`, `outputs`, `checkpoint`, `failure_modes` can be left empty at this stage — they will be filled in during detailed planning.

Hypothesis:
- Title: {hypothesis_title}
- Claim: {hypothesis_claim}

Experiment plan:
{self._clip(experiment_plan_md, 4000)}

Project code structure:
{self._clip(code_tree, 3000)}

Session context:
{context_text}

Recent transcript:
{transcript_text}
"""

    def _build_revise_breakdown_prompt(
        self,
        *,
        current_tasks_json: str,
        user_instruction: str,
        hypothesis_title: str,
        hypothesis_claim: str,
        interaction_log: str = "",
    ) -> str:
        return f"""You are revising an experiment task breakdown for LABIT.

Return JSON only. Do not add markdown fences or commentary.

The user has reviewed the current task list and wants changes. Apply their feedback precisely. Do not change tasks the user did not mention unless the change logically follows.

Rules:
- Maintain topological order (no circular dependencies).
- Keep task ids stable if the task is unchanged (don't renumber).
- If the user adds a task, assign the next available id.
- If the user removes a task, also update `depends_on` in other tasks.

Hypothesis:
- Title: {hypothesis_title}
- Claim: {hypothesis_claim}

Prior iteration log:
{self._clip(interaction_log, 2000)}

Current task list:
{current_tasks_json}

User's revision instruction:
{user_instruction}
"""

    def _build_task_detail_prompt(
        self,
        *,
        task: ExperimentTaskPlan,
        all_tasks_summary: str,
        approved_details: str,
        hypothesis_title: str,
        hypothesis_claim: str,
        code_tree: str,
        user_instruction: str = "",
        interaction_log: str = "",
    ) -> str:
        task_json = task.model_dump_json(indent=2)
        user_text = user_instruction.strip() or "(no specific instruction — fill in the detail fields)"

        return f"""You are planning the details of a single experiment task for LABIT.

Return JSON only. Do not add markdown fences or commentary.

Fill in all detail fields for this task. Be specific and actionable.

Fields to fill:
- `id`: keep unchanged ({task.id})
- `name`: keep unchanged or refine slightly
- `goal`: one clear sentence about what this task achieves
- `depends_on`: keep unchanged unless user says otherwise
- `entry_hint`: what code/script/module from the project code will be called (e.g. "training/train.py", "scripts/preprocess.sh")
- `inputs`: what data/files this task needs
- `outputs`: what this task produces
- `checkpoint`: a file or condition that indicates this task completed successfully
- `failure_modes`: what could go wrong and how to detect it

Hypothesis:
- Title: {hypothesis_title}
- Claim: {hypothesis_claim}

All tasks in this experiment:
{all_tasks_summary}

Already approved tasks (for reference):
{approved_details or "(none yet)"}

Project code structure:
{self._clip(code_tree, 3000)}

User instruction:
{user_text}

Prior iteration log:
{self._clip(interaction_log, 1500)}

Current task to plan:
{task_json}
"""

    def _build_generate_script_prompt(
        self,
        *,
        tasks_json: str,
        hypothesis_title: str,
        hypothesis_claim: str,
        code_tree: str,
        workdir: str = "",
        setup_script_summary: str = "",
        user_instruction: str = "",
    ) -> str:
        user_text = user_instruction.strip() or "(generate based on the approved task plans)"

        runtime_ctx = self._runtime_context_block(workdir, setup_script_summary)

        return f"""You are generating a run.sh script for a LABIT experiment.

Return JSON only. Do not add markdown fences or commentary.

IMPORTANT — Runtime environment:
{runtime_ctx}

Generate the BODY of a bash script that executes all the experiment tasks in order.
Your script will be embedded inside a wrapper that already handles the preamble above.
Do NOT include:
- `#!/usr/bin/env bash` or any shebang
- `set -euo pipefail` (already in wrapper)
- Virtual environment activation (already done by wrapper)
- `cd` to the project directory (already done by wrapper — `$PWD` is the workdir)
- `dirname "$0"` for path resolution (it will resolve to a temp launch dir, NOT your code dir)

DO use `$PWD` if you need to reference the project working directory.

The script should:
1. Execute tasks in topological order
2. Before each task, check that its dependency checkpoints exist
3. Print clear status messages for each task (e.g., "[t001] Running data preprocessing...")
4. Skip tasks whose checkpoint already exists (with a message)
5. Call code from the project's code directory — do NOT inline large implementations in the script
6. At the very end, write a `$PWD/experiment_results.json` summarizing the run

IMPORTANT — Standard results file:
Your script MUST end with a block that writes `$PWD/experiment_results.json`.
This file is the contract between the experiment and Labit's review system.
Format:
```json
{{
  "status": "completed",
  "metrics": {{"auroc": 0.72, "accuracy": 0.85}},
  "conclusion": "one-sentence takeaway from the results",
  "artifacts": ["relative/path/to/key/output1.json", "relative/path/to/output2.pt"]
}}
```
- `status`: "completed" if all tasks succeeded, "failed" if any critical task failed
- `metrics`: key numeric results (keep it flat, names should match the hypothesis criteria)
- `conclusion`: one sentence summarizing whether the hypothesis is supported or not
- `artifacts`: list of key output file paths (relative to $PWD)
If the script fails midway, use a trap to still write the file with `"status": "failed"` and `"error": "<message>"`.

Also generate a config.yaml if the experiment needs one (leave empty string if not needed).

Hypothesis:
- Title: {hypothesis_title}
- Claim: {hypothesis_claim}

Approved task plans:
{tasks_json}

Project code structure:
{self._clip(code_tree, 3000)}

User instruction:
{user_text}

Return:
- `run_sh`: bash script body (NO shebang, NO set -euo pipefail, NO venv activation, NO cd to workdir)
- `config_yaml`: configuration file content (empty string if not needed)
- `summary`: one sentence describing the script
"""

    def _build_revise_script_prompt(
        self,
        *,
        current_run_sh: str,
        current_config_yaml: str,
        tasks_json: str,
        user_instruction: str,
        code_tree: str,
        workdir: str = "",
        setup_script_summary: str = "",
    ) -> str:
        runtime_ctx = self._runtime_context_block(workdir, setup_script_summary)

        return f"""You are revising a run.sh script for a LABIT experiment.

Return JSON only. Do not add markdown fences or commentary.

IMPORTANT — Runtime environment:
{runtime_ctx}

Remember: your script is ONLY the body. Do NOT add shebang, `set -euo pipefail`, venv activation,
or `cd` to workdir. These are handled by the wrapper. Use `$PWD` for the project directory, never `dirname "$0"`.

The user wants changes to the current script. Apply their feedback precisely.

IMPORTANT: The script MUST still write `$PWD/experiment_results.json` at the end (see original generation rules).
If the current script already does this, preserve it. If not, add it.

Approved task plans:
{tasks_json}

Project code structure:
{self._clip(code_tree, 2000)}

Current run.sh body:
{current_run_sh}

Current config.yaml:
{current_config_yaml or "(none)"}

User's revision instruction:
{user_instruction}

Return:
- `run_sh`: updated bash script body (NO shebang, NO preamble)
- `config_yaml`: updated config (empty string if not needed)
- `summary`: one sentence describing the change
"""

    # ── Schemas ──

    def _breakdown_schema(self) -> dict:
        task_props = {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "goal": {"type": "string"},
            "depends_on": {"type": "array", "items": {"type": "string"}},
            "entry_hint": {"type": "string"},
            "inputs": {"type": "string"},
            "outputs": {"type": "string"},
            "checkpoint": {"type": "string"},
            "failure_modes": {"type": "string"},
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": task_props,
                        "required": ["id", "name", "goal", "depends_on"],
                    },
                },
            },
            "required": ["tasks"],
        }

    def _task_detail_schema(self) -> dict:
        props = {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "goal": {"type": "string"},
            "depends_on": {"type": "array", "items": {"type": "string"}},
            "entry_hint": {"type": "string"},
            "inputs": {"type": "string"},
            "outputs": {"type": "string"},
            "checkpoint": {"type": "string"},
            "failure_modes": {"type": "string"},
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
            "required": list(props.keys()),
        }

    def _script_schema(self) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "run_sh": {"type": "string"},
                "config_yaml": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["run_sh", "config_yaml", "summary"],
        }

    # ── Helpers ──

    def _format_transcript(self, transcript: list[ChatMessage]) -> str:
        if not transcript:
            return "(empty)"
        lines: list[str] = []
        for message in transcript[-16:]:
            provider = f" ({message.provider.value})" if message.provider else ""
            lines.append(f"[turn {message.turn_index}] {message.speaker}{provider}: {message.content}")
        return self._clip("\n".join(lines), 6000)

    def _format_context(self, snapshot: ContextSnapshot) -> str:
        parts: list[str] = []
        total_budget = 4000
        for block in snapshot.blocks[:4]:
            content = self._clip(block.content, min(1200, total_budget))
            parts.append(f"## {block.title}\n{content}")
            total_budget -= len(content)
            if total_budget <= 0:
                break
        return "\n\n".join(parts) if parts else "(none)"

    def _clip(self, text: str, max_chars: int) -> str:
        text = text.strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1] + "\u2026"

    def _runtime_context_block(self, workdir: str, setup_script_summary: str) -> str:
        lines = []
        if workdir:
            lines.append(f"- Working directory on remote: {workdir}")
            lines.append(f"  At runtime, the wrapper will `cd` to this directory before your script runs.")
            lines.append(f"  So `$PWD` == {workdir}. Use `$PWD` for paths, NEVER `dirname \"$0\"`.")
        if setup_script_summary:
            lines.append(f"- The wrapper already runs this setup BEFORE your script:")
            for sline in setup_script_summary.strip().splitlines():
                lines.append(f"    {sline}")
        if not lines:
            lines.append("- No runtime context available. Write a self-contained script body.")
        return "\n".join(lines)

    def _extra_args(self, provider: ProviderKind) -> list[str]:
        if provider == ProviderKind.CLAUDE:
            return ["--effort", "low"]
        if provider == ProviderKind.CODEX:
            return ["-c", 'model_reasoning_effort="low"']
        return []
