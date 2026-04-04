# LABIT Experiment Module V1

## Goal

Design experiment management as a three-layer system:

- `hypothesis` defines the research claim
- `experiment` defines the evidence plan
- `task` defines the actual compute unit

LABIT should not let the agent directly "manage GPUs."

Instead:

- the agent helps draft experiments and tasks
- LABIT freezes a launch artifact
- an executor backend submits and tracks the task
- review updates the scientific conclusion later

## Design Principles

1. `hypothesis != experiment`

Hypotheses track scientific claims and conclusions.

Experiments track evidence plans and assessments.

2. `experiment != task`

An experiment can contain prerequisite tasks and evidence-producing tasks.

3. `task != launch artifact`

Tasks describe what should be run.

Launch artifacts freeze what was actually submitted.

4. `review changes conclusions`

Launch and debrief update task and experiment state.

Only review updates hypothesis resolution.

5. `executor owns compute`

The agent may draft commands and scripts, but the executor owns:

- submission
- polling
- cancellation
- log collection

## Research OS Fit

This module should support the research loop:

```text
paper / discussion
  -> hypothesis
  -> experiment
  -> tasks
  -> launch
  -> debrief
  -> review-results
  -> hypothesis resolution
```

It should also handle compute-heavy prerequisites that are not themselves direct evidence.

Examples:

- data preparation
- feature extraction
- checkpoint conversion
- cache or manifest generation

These should live as `task` objects under an experiment, usually with `research_role=prerequisite`.

## Object Model

### Hypothesis

Already exists.

Responsibilities:

- claim
- success / failure criteria
- project-level decision state
- final supporting / contradicting experiment ids

### Experiment

One evidence plan for one hypothesis.

Responsibilities:

- bind to a hypothesis
- snapshot the hypothesis at creation time
- define executor profile and backend
- organize tasks
- hold experiment-level assessment

An experiment does not directly run anything.

### Task

One executable unit.

Responsibilities:

- define command / entrypoint / config
- declare resources
- track runtime state
- track outputs and metrics
- specify whether it is prerequisite or evidence

### Launch Artifact

A frozen execution snapshot generated from a task before submission.

Responsibilities:

- capture the exact command actually submitted
- capture env and workdir
- optionally capture generated scripts such as `run.sh` / `run.py`
- capture code snapshot info
- store submission receipt and runtime metadata

## Directory Layout

```text
vault/projects/{project}/experiments/
  index.yaml
  e001/
    experiment.yaml
    launch.md
    debrief.md
    review.md
    tasks/
      t001.yaml
      t002.yaml
      t003.yaml
      launches/
        l001/
          launch.yaml
          run.sh
          run.py
          env.json
          code_snapshot.json
          patch.diff
          receipt.yaml
```

Notes:

- v1 keeps all tasks under experiments
- no separate project-level task object is needed yet
- launch artifacts are immutable snapshots

## Task Categories

### `task_kind`

Recommended v1 values:

- `data_prep`
- `extract`
- `train`
- `eval`
- `analysis`
- `sync`
- `custom`

### `research_role`

Recommended v1 values:

- `prerequisite`
- `evidence`
- `supporting`

Only `research_role=evidence` should directly affect experiment assessment.

## Status Model

### Experiment status

- `planned`
- `approved`
- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `analyzed`

### Experiment assessment

- `pending`
- `supports`
- `contradicts`
- `inconclusive`
- `invalid`

### Task status

- `planned`
- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `skipped`

## Compute Model

V1 should be intentionally simple.

Assumption:

- the operator has access to a box or cluster with roughly `4-8 GPUs`
- LABIT does not need a full queue or scheduler yet

So v1 uses:

- one executor backend: `ssh`
- one default execution profile per project, derived from project config
- optional profile name as an abstraction boundary for later expansion

Current project config already contains the needed defaults:

- `backend`
- `host`
- `workdir`
- `runtime`

## Executor Contract

The executor interface should look like:

- `prepare(experiment, task) -> LaunchArtifact`
- `submit(launch_artifact) -> SubmissionReceipt`
- `poll(launch_artifact) -> RuntimeStatus`
- `collect(launch_artifact) -> RuntimeOutcome`
- `cancel(launch_artifact) -> CancellationReceipt`

V1 only needs an `SSHExecutor` implementation.

## Submission Receipts

Submission should return structured data, not only a return code.

Recommended receipt fields:

- `accepted`
- `phase`
- `backend`
- `remote_host`
- `remote_job_id`
- `pid`
- `assigned_gpu`
- `log_path`
- `ssh_exit_code`
- `stderr_tail`
- `error_kind`

This is important because LABIT should distinguish:

- submission accepted vs. rejected
- transport failure vs. task-spec failure
- submission-time failure vs. runtime crash

## Error Taxonomy

V1 should classify submission/runtime failures into:

- `transport_error`
  - ssh timeout
  - host unreachable
  - rsync / scp failure
- `resource_error`
  - no free GPU
  - capacity unavailable
- `task_spec_error`
  - missing path
  - invalid config
  - broken command / import error
- `runtime_error`
  - OOM
  - CUDA crash
  - assertion failure
  - training process failure

This matters because only some errors should be retried automatically.

## Launch Artifacts

The canonical truth is not a shell script alone.

The canonical truth is:

- `TaskRecord.spec`
- frozen into a `LaunchArtifact`

Each launch should materialize:

- `launch.yaml`
- `run.sh`
- optional `run.py`
- `env.json`
- `code_snapshot.json`
- optional `patch.diff`
- `receipt.yaml`

This gives LABIT a stable, inspectable execution snapshot for later debugging and reproduction.

## Runtime Strategy

V1 should support one simple execution path:

- project config defines an `ssh` compute backend
- LABIT derives one default execution profile
- executor submits tasks over SSH
- logs and runtime metadata are written back into the launch artifact

The executor is responsible for compute mechanics.

The agent is responsible for:

- proposing experiment structure
- drafting tasks
- drafting commands or helper scripts
- interpreting failures after receipts/logs come back

## Workflow Boundaries

### `/launch-exp`

Should:

1. read a hypothesis
2. draft an experiment and tasks
3. freeze launch artifacts
4. submit ready tasks
5. write submission receipts

Should not:

- directly change hypothesis resolution
- directly treat a running task as scientific evidence

### `/debrief`

Should:

1. inspect active launches
2. poll runtime state
3. collect logs, metrics, and artifacts
4. update task / experiment runtime state
5. write `debrief.md`

Should not:

- directly mark the hypothesis validated or rejected

### `/review-results`

Should:

1. aggregate evidence tasks across experiments
2. summarize support / contradiction / inconclusive evidence
3. update experiment assessment
4. update hypothesis state and resolution after confirmation

Only this step is allowed to change the scientific conclusion.

## Legacy Compatibility

The current repository still contains legacy flat hypothesis YAML files under:

- `vault/projects/{project}/hypotheses/h###.yaml`

V1 should remain compatible with those fields when drafting experiments, especially:

- `branch`
- `config`
- `gpu`
- `baseline_metric`
- `expected_improvement`
- `wandb_run_id`

LABIT should map those values into experiment/task specs rather than continue storing runtime state on the hypothesis itself.

## CLI Shape

V1 should expose:

- `labit experiment list`
- `labit experiment show <experiment_id>`

Session-native workflows should later expose:

- `/launch-exp <hypothesis_id>`
- `/debrief`
- `/review-results`

The CLI and the session commands should share the same underlying experiment objects and executor layer.

## Non-Goals For V1

Do not build these yet:

- a full queueing system
- generalized DAG scheduling
- multiple executor backends
- large-scale sweep orchestration
- automatic branch merge / cleanup

V1 only needs enough structure to make experiment execution inspectable, reproducible, and compatible with hypothesis review.
- `pid`
- `assigned_gpu`
- `log_path`
- `ssh_exit_code`
- `stderr_tail`
- `error_kind`
- `created_at`

### Error kinds

At minimum:

- `transport_error`
- `resource_error`
- `task_spec_error`
- `runtime_error`
- `unknown`

This lets LABIT decide whether a retry is safe.

## Launch Workflow

### `/launch-exp h012`

1. Resolve active project and hypothesis
2. Draft experiment and tasks
3. Show summary to the user
4. Confirm
5. Create `ExperimentRecord`
6. Create `TaskRecord`s
7. Materialize launch artifacts for ready tasks
8. Submit via executor
9. Write submission receipts

Launch updates:

- experiment status
- task status
- launch artifacts

Launch does **not** update hypothesis resolution.

## Debrief Workflow

### `/debrief`

1. Find active experiments and running launches
2. Poll executor for task states
3. Read logs and output files
4. Update task runtime and results
5. Write `debrief.md`
6. Mark experiment `completed` only when all required tasks are terminal

Debrief does **not** directly validate or reject the hypothesis.

## Review Workflow

### `/review-results`

1. Collect experiments under a hypothesis
2. Consider only `evidence` tasks for the main verdict
3. Summarize metrics and artifacts
4. Produce an experiment-level assessment
5. Ask the user to confirm
6. Update hypothesis state / resolution / result summary

This is the only layer allowed to change hypothesis conclusions.

## Why Freeze Scripts

LABIT should store generated execution artifacts such as `run.sh` and optional `run.py`.

Reasons:

- reproducibility
- auditability
- branch/config drift protection
- easier failure debugging

The task spec is the logical plan.

The launch artifact is the actual frozen execution snapshot.

## V1 Scope

In scope:

- experiment records
- task records
- launch artifact records
- SSH executor skeleton
- experiment list/show CLI
- object model compatible with existing flat hypothesis YAMLs

Out of scope:

- full queue system
- multi-backend scheduling
- DAG scheduler
- automatic experiment sweeps
- hypothesis auto-resolution at launch or debrief time

## Implementation Order

1. `ExperimentRecord`, `TaskRecord`, `LaunchArtifact`
2. Experiment store and service
3. `SSHExecutor` skeleton
4. `labit experiment list/show`
5. session-driven `/launch-exp`
6. `/debrief`
7. `/review-results`
