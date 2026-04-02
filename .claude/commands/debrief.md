When the user says /debrief, check all running GPU tasks and close the loop on completed ones.

1. Read configs/active_project to get the current project.

2. Read vault/projects/{project}/gpu_tasks.yaml. If it doesn't exist, say "No GPU tasks tracked. Tasks are registered when you use /launch-exp." and stop.

3. Read the project's compute config from configs/projects/{project}.yaml to get SSH host.

4. For each task in gpu_tasks.yaml, SSH to the remote host and check:
   - Is the process still running? (ps aux | grep the command)
   - GPU memory usage for that GPU index (nvidia-smi)
   - If the process is still running, get the last progress line from the log file

5. For each task, report one of:

   **RUNNING**: Show GPU index, hypothesis ID, progress (e.g., "3400/5500, avg_score=0.82"), estimated time remaining.

   **COMPLETED**: 
   - SSH and read the result file from output_dir:
     - result_type=probe → read train_results.json for accuracy, f1, auroc
     - result_type=eval → read summary.json for aggregate_score/accuracy
     - result_type=extract → count samples in manifest.jsonl
   - Update the hypothesis YAML: set actual_result, set status to validated/rejected by comparing against expected_improvement
   - Remove this task entry from gpu_tasks.yaml
   - Show: hypothesis ID, result metrics, validated/rejected

   **FAILED**: If process is gone but no result file exists:
   - Check the log file tail for error messages
   - Report the error
   - Remove this task entry from gpu_tasks.yaml

6. Show a summary table of all GPUs.

7. If any tasks were completed, remind user to /commit.

## gpu_tasks.yaml format

```yaml
host: pgoom
tasks:
  - gpu: 0
    hypothesis: h005
    description: "qwen25vl_7b visual encoder probe"
    output_dir: "outputs/probing/probes/qwen25vl_7b/mate_visual_encoder_linear"
    result_type: probe
    log_file: "/tmp/probe_qwen_visual.log"
    started: "2026-04-01T22:00:00"
```

result_type values: probe, eval, extract, custom
