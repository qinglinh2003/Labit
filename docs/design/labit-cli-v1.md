# LABIT CLI V1

## Purpose

`labit` is the deterministic control plane for the repository.

The repository state lives in files. Agents may help users decide what to do,
but agents should not directly own project state transitions.

## Scope

V1 only covers project state management:

- inspect current project context
- list and show project configs
- switch the active project
- create a project through an interactive form
- edit an existing project through a prefilled interactive form
- delete a project and all local project files
- turn strict project input plus a semantic brief into a draft spec
- validate a project spec
- create a project config and overlay directories
- optionally clone project code from the declared repo
- provide a thin init command that orchestrates validated project bootstrap

## Command Shape

```bash
labit project new
labit project current
labit project list
labit project show MBRL-VLM
labit project edit MBRL-VLM
labit project switch MBRL-VLM
labit project delete MBRL-VLM
labit project draft --seed seed.yaml --brief brief.yaml --output project.yaml
labit project validate --spec project.yaml
labit project create --spec project.yaml --dry-run
labit project create --spec project.yaml --set-active
labit project clone-code MBRL-VLM
labit project init --spec project.yaml --set-active
```

## Agent Boundary

Agents may:

- ask users for missing information
- call `labit`
- explain the command output

Agents may not:

- free-write `configs/active_project`
- free-write `configs/projects/*.yaml`
- improvise directory layouts
- mix validation and side effects in a single prompt-only flow

## Design Rules

- `project` commands are deterministic and testable.
- strict system fields and semantic research fields are modeled separately.
- semantic research fields may be left blank at project creation time and refined later.
- `project new` is the primary user-facing project creation flow.
- `project edit` reuses the same schema through a prefilled form.
- `project delete` removes both config and local project overlay after confirmation.
- `create` defaults to local file changes only.
- Networked side effects such as `git clone` remain explicit, even when `init` orchestrates them.
- All write operations are atomic.
- Every command supports machine-readable output via `--json`.

## Input Layers

- `ProjectSeed`: strict fields the user should provide exactly.
- `SemanticBrief`: freeform research description the agent may rewrite.
- `ProjectDraft`: semantic fields derived from the brief.
- `ProjectSpec`: the final merged object that the write-path commands accept.

## Why Typer

- fits the current Python-first repository
- lightweight migration path from standalone scripts
- clear nested command structure
- good typing and test ergonomics with `CliRunner`
