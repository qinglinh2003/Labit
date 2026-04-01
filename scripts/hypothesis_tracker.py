#!/usr/bin/env python3
"""
hypothesis_tracker.py — Manage research hypotheses as YAML + git branches.

Usage:
    python hypothesis_tracker.py new
    python hypothesis_tracker.py list
    python hypothesis_tracker.py status h003
    python hypothesis_tracker.py update h003 --status validated --result "coverage=0.62"
    python hypothesis_tracker.py launch h003

Requires:
    pip install pyyaml
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from project_config import (
    list_project_names, ensure_project_dirs, project_dir,
    find_hypothesis, find_task, PROJECTS_DIR,
)

HYPOTHESES_DIR = Path(__file__).parent.parent / "experiments" / "hypotheses"
TASKS_DIR = Path(__file__).parent.parent / "experiments" / "tasks"

SKYPILOT_TEMPLATE = """resources:
  accelerators: {gpu}
  cloud: runpod
  use_spot: true

file_mounts:
  /data:
    source: r2://my-research/datasets/
    mode: COPY

setup: |
  cd ~/project && pip install -r requirements.txt

run: |
  cd ~/project && python train.py \\
    --config configs/{config} \\
    --wandb_project {project} \\
    --wandb_tags {id} \\
    --output_dir /data/results/{id}/
  curl -sf -X POST http://$WEBHOOK_URL/callback \\
    -H "Content-Type: application/json" \\
    -d '{{"id":"{id}","status":"done"}}' || true

autostop:
  idle_minutes: 10
"""


def next_id() -> str:
    """Generate next hypothesis ID, scanning all locations."""
    all_files = list(HYPOTHESES_DIR.glob("h*.yaml"))
    if PROJECTS_DIR.exists():
        all_files.extend(PROJECTS_DIR.glob("*/hypotheses/h*.yaml"))
    if not all_files:
        return "h001"
    nums = []
    for f in all_files:
        try:
            nums.append(int(f.stem[1:]))
        except ValueError:
            pass
    return f"h{max(nums) + 1:03d}" if nums else "h001"


def create_hypothesis():
    """Interactive hypothesis creation."""
    hid = next_id()
    print(f"Creating hypothesis: {hid}\n")

    title = input("Short title (e.g., slot-byol): ").strip()
    source = input("Source paper arXiv ID (e.g., 2405.12345): ").strip()
    hypothesis = input("Hypothesis statement:\n> ").strip()
    project = input("Project (GLANCE / SemBelief-WM / other): ").strip()
    baseline = input("Baseline metric (e.g., coverage@100k=0.45): ").strip()
    expected = input("Expected improvement (e.g., >0.55): ").strip()
    gpu = input("GPU type (RTX4090:1 / A100:80GB:1) [RTX4090:1]: ").strip() or "RTX4090:1"
    config = input("Config file name (e.g., h003_slot_byol.yaml): ").strip() or f"{hid}_{title}.yaml"

    data = {
        "id": hid,
        "title": title,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "source_paper": source,
        "hypothesis": hypothesis,
        "project": project,
        "branch": f"exp/{hid}-{title}",
        "status": "proposed",
        "baseline_metric": baseline,
        "expected_improvement": expected,
        "actual_result": None,
        "wandb_run_id": None,
        "gpu": gpu,
        "config": config,
        "notes": "",
    }

    # Write hypothesis YAML — under project overlay if project is recognized
    known_projects = list_project_names()
    if project and project in known_projects:
        ensure_project_dirs(project)
        hyp_dir = project_dir(project) / "hypotheses"
    else:
        hyp_dir = HYPOTHESES_DIR
    hyp_dir.mkdir(parents=True, exist_ok=True)
    hyp_path = hyp_dir / f"{hid}.yaml"
    hyp_path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
    print(f"\nHypothesis saved: {hyp_path}")

    # Generate SkyPilot task YAML — same project overlay as hypothesis
    task_dir = hyp_dir.parent / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_content = SKYPILOT_TEMPLATE.format(
        gpu=gpu, config=config, project=project, id=hid
    )
    task_path = task_dir / f"{hid}.yaml"
    task_path.write_text(task_content)
    print(f"SkyPilot task:      {task_path}")

    # Create git branch
    try:
        subprocess.run(["git", "checkout", "-b", data["branch"]], check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-"], check=True, capture_output=True)
        print(f"Git branch created: {data['branch']}")
    except subprocess.CalledProcessError:
        print(f"Git branch skipped (create manually: git checkout -b {data['branch']})")

    print(f"\nDone! To start: python hypothesis_tracker.py launch {hid}")


def list_hypotheses():
    """List all hypotheses with status across all locations."""
    files = list(HYPOTHESES_DIR.glob("h*.yaml"))
    if PROJECTS_DIR.exists():
        files.extend(PROJECTS_DIR.glob("*/hypotheses/h*.yaml"))
    files = sorted(files, key=lambda f: f.stem)
    if not files:
        print("No hypotheses yet. Create one with: python hypothesis_tracker.py new")
        return

    print(f"{'ID':<6} {'Status':<12} {'Title':<25} {'Result':<20} {'Date'}")
    print("-" * 80)
    for f in files:
        data = yaml.safe_load(f.read_text())
        result = data.get("actual_result") or "-"
        print(f"{data['id']:<6} {data['status']:<12} {data.get('title',''):<25} {str(result):<20} {data['date']}")


def show_status(hid: str):
    """Show detailed hypothesis status."""
    path = find_hypothesis(hid, HYPOTHESES_DIR)
    if not path:
        print(f"Hypothesis {hid} not found.")
        return
    data = yaml.safe_load(path.read_text())
    print(yaml.dump(data, default_flow_style=False, allow_unicode=True))


def update_hypothesis(hid: str, status: str = None, result: str = None, wandb_run: str = None):
    """Update hypothesis fields."""
    path = find_hypothesis(hid, HYPOTHESES_DIR)
    if not path:
        print(f"Hypothesis {hid} not found.")
        return

    data = yaml.safe_load(path.read_text())
    if status:
        data["status"] = status
    if result:
        data["actual_result"] = result
    if wandb_run:
        data["wandb_run_id"] = wandb_run

    path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
    print(f"Updated {hid}: status={data['status']}, result={data.get('actual_result')}")


def launch_experiment(hid: str):
    """Launch experiment via SkyPilot."""
    task_path = find_task(hid, TASKS_DIR)
    hyp_path = find_hypothesis(hid, HYPOTHESES_DIR)

    if not task_path:
        print(f"Task file not found for {hid}.")
        return

    # Update status
    if hyp_path:
        data = yaml.safe_load(hyp_path.read_text())
        data["status"] = "in-progress"
        hyp_path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))

    print(f"Launching: sky launch {task_path} --cluster {hid}")
    try:
        subprocess.run(["sky", "launch", str(task_path), "--cluster", hid, "-y"], check=True)
    except FileNotFoundError:
        print("SkyPilot not installed. Install with: pip install 'skypilot[runpod]'")
    except subprocess.CalledProcessError as e:
        print(f"Launch failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Manage research hypotheses")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("new", help="Create a new hypothesis")
    sub.add_parser("list", help="List all hypotheses")

    p_status = sub.add_parser("status", help="Show hypothesis details")
    p_status.add_argument("id", help="Hypothesis ID (e.g., h003)")

    p_update = sub.add_parser("update", help="Update hypothesis")
    p_update.add_argument("id", help="Hypothesis ID")
    p_update.add_argument("--status", choices=["proposed", "in-progress", "validated", "rejected"])
    p_update.add_argument("--result", help="Actual result string")
    p_update.add_argument("--wandb-run", help="W&B run ID")

    p_launch = sub.add_parser("launch", help="Launch experiment via SkyPilot")
    p_launch.add_argument("id", help="Hypothesis ID")

    args = parser.parse_args()

    if args.command == "new":
        create_hypothesis()
    elif args.command == "list":
        list_hypotheses()
    elif args.command == "status":
        show_status(args.id)
    elif args.command == "update":
        update_hypothesis(args.id, args.status, args.result, args.wandb_run)
    elif args.command == "launch":
        launch_experiment(args.id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
