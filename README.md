# LABIT

LABIT is a lightweight local workspace for project-scoped AI research conversations and document drafting.

The current lite surface is intentionally small:

```bash
labit          # open the dashboard shell
labit chat     # start a project-scoped multi-agent conversation
labit project  # manage local projects
labit setup    # show workspace status
```

Inside `labit chat`, the retained slash commands are:

```text
/doc
/todo
/idea
/paper
```

## Workspace Layout

LABIT currently runs inside a self-hosting Research-OS workspace:

```text
Research-OS/                         # workspace scaffold and runtime data
  configs/                           # workspace config
  vault/                             # project vault
    projects/
      Labit/
        code/                        # active LABIT implementation
```

The active source tree for LABIT development is:

```text
vault/projects/Labit/code/
```

The root workspace may also contain an outer `labit/` package as part of the bootstrap/scaffold layout. Treat that outer package as legacy scaffold code, not as the active implementation. New LABIT features, fixes, tests, commits, and pushes should target `vault/projects/Labit/code/`.

Useful checks:

```bash
which labit
pip show labit
python -c "import labit; print(labit.__file__)"
```

The `labit` command should be installed from `vault/projects/Labit/code`. If a direct Python import from the workspace root resolves to the outer scaffold package, do not use that as the development source.

## Project Workflow

Create or switch to a project:

```bash
labit project new
labit project list
labit project switch <name>
labit project show
```

When `labit project new` is given a repository URL or local git path, LABIT automatically clones it into the project's `code/` directory.

Attach optional SSH compute profiles to a project:

```bash
labit project compute add gpu --host example.com --user alice --workdir /work/project
labit project compute list
labit project compute test gpu
```

Compute profiles only describe how to reach a remote machine. They are exposed to chat agents as project context, but LABIT does not run an experiment executor or sync stack.

Start a conversation:

```bash
labit chat
labit chat --mode round_robin
```

Use `/doc` when a discussion should become a Markdown document. In doc mode, normal text discusses the document; explicit `/edit <instruction>` applies changes to the file.

Use `/todo` and `/idea` for lightweight capture from the current session.

## Dashboard

Running `labit` opens a Streamlit dashboard shell. The shell is intentionally empty until the GUI surface is redesigned.
