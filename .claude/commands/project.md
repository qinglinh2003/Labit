When the user says /project, manage the active project context.

## Usage
- `/project` — show current active project and its config summary
- `/project GLANCE` — switch to GLANCE
- `/project SemBelief-WM` — switch to SemBelief-WM

## Steps

1. Read the current active project from configs/active_project. If the file does not exist, respond with: "No active project. Use /new-project to create one." and stop.

2. If no argument is provided:
   - Show the current active project name.
   - Read its config from configs/projects/{name}.yaml and show a one-line description and keyword count.
   - List how many papers in vault/papers/ have this project in their relevance_to field.
   - List how many hypotheses exist under vault/projects/{name}/hypotheses/.
   - Done.

3. If an argument is provided (the target project name):
   - Check if configs/projects/{name}.yaml exists (case-insensitive match).
   - If it does NOT exist, respond with: "Project '{name}' not found. Available projects: {list}. Use /new-project to create one."
   - If it exists, write the project name to configs/active_project (just the name, one line, no trailing content).
   - Confirm the switch: "Switched to {name}: {description}"
