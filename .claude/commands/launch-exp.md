When the user says /launch-exp followed by a hypothesis ID (e.g., /launch-exp h003):

1. Read the active project from configs/active_project.
2. Find the hypothesis YAML (search vault/projects/{project}/hypotheses/).
3. Read the project's compute config from configs/projects/{project}.yaml.
4. Show a summary: hypothesis statement, GPU type, compute host, what will be run.
5. Confirm with the user before launching.

6. After confirmation, launch the experiment on the remote host via SSH:
   - For SSH backend: ssh {host} "source conda_init && conda activate env && cd workdir && {command}"
   - Use nohup and redirect output to a log file
   - Use CUDA_VISIBLE_DEVICES to select a free GPU

7. Register the task in vault/projects/{project}/gpu_tasks.yaml:
   - Append a new task entry with: gpu index, hypothesis ID, description, output_dir, result_type, log_file path, start time
   - Create the file if it doesn't exist (with host field from compute config)

8. Update hypothesis status to "in-progress".

9. Confirm launch and remind user to use /debrief to check progress and collect results.
