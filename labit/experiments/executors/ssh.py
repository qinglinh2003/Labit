from __future__ import annotations

import subprocess

from labit.experiments.executors.base import ExperimentExecutor
from labit.experiments.models import (
    ExecutionBackend,
    LaunchArtifact,
    SubmissionErrorKind,
    SubmissionPhase,
    SubmissionReceipt,
)
from labit.paths import RepoPaths


class SSHExecutor(ExperimentExecutor):
    backend = ExecutionBackend.SSH

    def __init__(self, paths: RepoPaths):
        self.paths = paths

    def prepare(self, artifact: LaunchArtifact) -> LaunchArtifact:
        return artifact

    def submit(self, artifact: LaunchArtifact) -> SubmissionReceipt:
        if not artifact.remote_host:
            return SubmissionReceipt(
                accepted=False,
                phase=SubmissionPhase.SUBMIT,
                backend=self.backend,
                remote_host="",
                stderr_tail="Launch artifact is missing a remote host.",
                error_kind=SubmissionErrorKind.TASK_SPEC_ERROR,
            )
        if not artifact.run_sh_path:
            return SubmissionReceipt(
                accepted=False,
                phase=SubmissionPhase.SUBMIT,
                backend=self.backend,
                remote_host=artifact.remote_host,
                stderr_tail="Launch artifact is missing run.sh.",
                error_kind=SubmissionErrorKind.TASK_SPEC_ERROR,
            )

        run_sh_path = self.paths.root / artifact.run_sh_path
        if not run_sh_path.exists():
            return SubmissionReceipt(
                accepted=False,
                phase=SubmissionPhase.SUBMIT,
                backend=self.backend,
                remote_host=artifact.remote_host,
                stderr_tail=f"Local run.sh not found: {run_sh_path}",
                error_kind=SubmissionErrorKind.TASK_SPEC_ERROR,
            )

        remote_dir = self._remote_launch_dir(artifact)
        remote_log_path = f"{remote_dir}/stdout.log"
        remote_script = self._remote_submit_script(
            remote_dir=remote_dir,
            remote_log_path=remote_log_path,
            run_sh=run_sh_path.read_text(encoding="utf-8"),
        )

        try:
            result = subprocess.run(
                [*self._ssh_command(artifact), "bash", "-s"],
                input=remote_script,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return SubmissionReceipt(
                accepted=False,
                phase=SubmissionPhase.SUBMIT,
                backend=self.backend,
                remote_host=artifact.remote_host,
                stderr_tail="SSH submission timed out.",
                error_kind=SubmissionErrorKind.TRANSPORT_ERROR,
            )
        except FileNotFoundError:
            return SubmissionReceipt(
                accepted=False,
                phase=SubmissionPhase.SUBMIT,
                backend=self.backend,
                remote_host=artifact.remote_host,
                stderr_tail="ssh is not installed or not available in PATH.",
                error_kind=SubmissionErrorKind.TRANSPORT_ERROR,
            )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            return SubmissionReceipt(
                accepted=False,
                phase=SubmissionPhase.SUBMIT,
                backend=self.backend,
                remote_host=artifact.remote_host,
                ssh_exit_code=result.returncode,
                stderr_tail=(stderr or stdout or "SSH submission failed.")[-800:],
                error_kind=SubmissionErrorKind.TRANSPORT_ERROR,
            )

        payload = self._parse_submit_stdout(stdout)
        pid = payload.get("pid")
        log_path = payload.get("log_path", remote_log_path)
        return SubmissionReceipt(
            accepted=True,
            phase=SubmissionPhase.SUBMIT,
            backend=self.backend,
            remote_host=artifact.remote_host,
            remote_job_id=pid,
            pid=pid,
            log_path=log_path,
            ssh_exit_code=result.returncode,
            stderr_tail=stderr[-800:] if stderr else "",
            error_kind=None,
        )

    def poll(self, artifact: LaunchArtifact) -> dict:
        if not artifact.remote_host:
            return {
                "ok": False,
                "backend": self.backend.value,
                "message": "Launch artifact is missing a remote host.",
                "launch_id": artifact.launch_id,
            }
        pid = artifact.submission.pid if artifact.submission else None
        log_path = artifact.submission.log_path if artifact.submission else None
        if not pid:
            return {
                "ok": False,
                "backend": self.backend.value,
                "message": "Launch artifact does not have a submitted PID yet.",
                "launch_id": artifact.launch_id,
            }

        remote_script = f"""set -euo pipefail
PID={self._shell_quote(pid)}
if kill -0 "$PID" 2>/dev/null; then
  echo STATUS=running
else
  echo STATUS=stopped
fi
if [ -n {self._shell_quote(log_path or "")} ] && [ -f {self._shell_quote(log_path or "")} ]; then
  echo LOG_TAIL_BEGIN
  tail -n 20 {self._shell_quote(log_path or "")}
  echo LOG_TAIL_END
fi
"""
        try:
            result = subprocess.run(
                [*self._ssh_command(artifact), "bash", "-s"],
                input=remote_script,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except Exception as exc:
            return {
                "ok": False,
                "backend": self.backend.value,
                "message": str(exc),
                "launch_id": artifact.launch_id,
            }

        stdout = (result.stdout or "").strip()
        status = "unknown"
        if "STATUS=running" in stdout:
            status = "running"
        elif "STATUS=stopped" in stdout:
            status = "stopped"
        return {
            "ok": result.returncode == 0,
            "backend": self.backend.value,
            "launch_id": artifact.launch_id,
            "status": status,
            "stdout": stdout,
            "stderr": (result.stderr or "").strip(),
        }

    def collect(self, artifact: LaunchArtifact) -> dict:
        if not artifact.remote_host:
            return {
                "ok": False,
                "backend": self.backend.value,
                "message": "Launch artifact is missing a remote host.",
                "launch_id": artifact.launch_id,
            }
        pid = artifact.submission.pid if artifact.submission else None
        log_path = artifact.submission.log_path if artifact.submission else None
        if not pid:
            return {
                "ok": False,
                "backend": self.backend.value,
                "message": "Launch artifact does not have a submitted PID yet.",
                "launch_id": artifact.launch_id,
            }

        remote_script = self._remote_collect_script(
            pid=pid,
            log_path=log_path or "",
            workdir=artifact.frozen_spec.workdir or ".",
            output_dir=artifact.frozen_spec.output_dir or "",
        )
        try:
            result = subprocess.run(
                [*self._ssh_command(artifact), "bash", "-s"],
                input=remote_script,
                capture_output=True,
                text=True,
                check=False,
                timeout=45,
            )
        except Exception as exc:
            return {
                "ok": False,
                "backend": self.backend.value,
                "message": str(exc),
                "launch_id": artifact.launch_id,
            }

        parsed = self._parse_collect_stdout((result.stdout or "").strip())
        parsed.update(
            {
                "ok": result.returncode == 0,
                "backend": self.backend.value,
                "launch_id": artifact.launch_id,
                "stderr": (result.stderr or "").strip(),
            }
        )
        return parsed

    def cancel(self, artifact: LaunchArtifact) -> dict:
        if not artifact.remote_host or not artifact.submission or not artifact.submission.pid:
            return {
                "ok": False,
                "backend": self.backend.value,
                "message": "Launch artifact does not have enough runtime metadata to cancel.",
                "launch_id": artifact.launch_id,
            }
        try:
            result = subprocess.run(
                [*self._ssh_command(artifact), "bash", "-lc", f"kill {artifact.submission.pid}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except Exception as exc:
            return {
                "ok": False,
                "backend": self.backend.value,
                "message": str(exc),
                "launch_id": artifact.launch_id,
            }
        return {
            "ok": result.returncode == 0,
            "backend": self.backend.value,
            "message": (result.stderr or result.stdout or "").strip() or ("Cancelled" if result.returncode == 0 else "Cancel failed"),
            "launch_id": artifact.launch_id,
        }

    def _remote_launch_dir(self, artifact: LaunchArtifact) -> str:
        base = artifact.frozen_spec.workdir.rstrip("/") or "."
        return f"{base}/.labit/experiments/{artifact.experiment_id}/{artifact.task_id}/{artifact.launch_id}"

    def _ssh_command(self, artifact: LaunchArtifact) -> list[str]:
        command = ["ssh"]
        if artifact.remote_port and artifact.remote_port != 22:
            command.extend(["-p", str(artifact.remote_port)])
        if artifact.ssh_key:
            command.extend(["-i", artifact.ssh_key])
        command.append(f"{artifact.remote_user}@{artifact.remote_host}")
        return command

    def _remote_submit_script(self, *, remote_dir: str, remote_log_path: str, run_sh: str) -> str:
        marker = "__LABIT_RUN_SH__"
        return f"""set -euo pipefail
REMOTE_DIR={self._shell_quote(remote_dir)}
LOG_PATH={self._shell_quote(remote_log_path)}
case "$REMOTE_DIR" in
  "~") REMOTE_DIR="$HOME" ;;
  "~"/*) REMOTE_DIR="$HOME/${{REMOTE_DIR#~/}}" ;;
esac
case "$LOG_PATH" in
  "~") LOG_PATH="$HOME" ;;
  "~"/*) LOG_PATH="$HOME/${{LOG_PATH#~/}}" ;;
esac
mkdir -p "$REMOTE_DIR"
cat > "$REMOTE_DIR/run.sh" <<'{marker}'
{run_sh.rstrip()}
{marker}
chmod +x "$REMOTE_DIR/run.sh"
nohup bash "$REMOTE_DIR/run.sh" > "$LOG_PATH" 2>&1 < /dev/null &
PID=$!
printf 'PID=%s\\n' "$PID"
printf 'LOG_PATH=%s\\n' "$LOG_PATH"
"""

    def _remote_collect_script(self, *, pid: str, log_path: str, workdir: str, output_dir: str) -> str:
        return f"""set -euo pipefail
PID={self._shell_quote(pid)}
LOG_PATH={self._shell_quote(log_path)}
WORKDIR={self._shell_quote(workdir)}
OUTPUT_DIR={self._shell_quote(output_dir)}

resolve_path() {{
  local value="$1"
  case "$value" in
    "~") value="$HOME" ;;
    "~"/*) value="$HOME/${{value#~/}}" ;;
  esac
  printf '%s\\n' "$value"
}}

WORKDIR="$(resolve_path "$WORKDIR")"
LOG_PATH="$(resolve_path "$LOG_PATH")"
OUTPUT_DIR="$(resolve_path "$OUTPUT_DIR")"
if [ -n "$OUTPUT_DIR" ] && [ "${{OUTPUT_DIR#/}}" = "$OUTPUT_DIR" ]; then
  OUTPUT_DIR="$WORKDIR/$OUTPUT_DIR"
fi

if kill -0 "$PID" 2>/dev/null; then
  echo STATUS=running
else
  echo STATUS=stopped
fi

if [ -n "$LOG_PATH" ] && [ -f "$LOG_PATH" ]; then
  echo LOG_TAIL_BEGIN
  tail -n 20 "$LOG_PATH"
  echo LOG_TAIL_END
fi

## Check for the standard Labit experiment results file first (written by run.sh)
if [ -f "$WORKDIR/experiment_results.json" ]; then
  echo FILE_BEGIN::$WORKDIR/experiment_results.json
  cat "$WORKDIR/experiment_results.json"
  echo FILE_END::$WORKDIR/experiment_results.json
fi

if [ -n "$OUTPUT_DIR" ] && [ -d "$OUTPUT_DIR" ]; then
  echo OUTPUT_DIR=$OUTPUT_DIR
  echo OUTPUT_DIR_EXISTS=1
  for candidate in "$OUTPUT_DIR/experiment_results.json" "$OUTPUT_DIR/summary.json" "$OUTPUT_DIR/train_results.json" "$OUTPUT_DIR/results.json" "$OUTPUT_DIR/metrics.json"; do
    if [ -f "$candidate" ]; then
      echo FILE_BEGIN::$candidate
      cat "$candidate"
      echo FILE_END::$candidate
    fi
  done
  if [ -f "$OUTPUT_DIR/manifest.jsonl" ]; then
    echo MANIFEST_PATH=$OUTPUT_DIR/manifest.jsonl
    echo MANIFEST_LINE_COUNT=$(wc -l < "$OUTPUT_DIR/manifest.jsonl" | tr -d ' ')
  fi
  find "$OUTPUT_DIR" -maxdepth 2 -type f \\( -name '*.json' -o -name '*.jsonl' -o -name '*.log' -o -name '*.txt' \\) | head -n 20 | while read -r path; do
    echo ARTIFACT::$path
  done
fi
"""

    def _parse_submit_stdout(self, stdout: str) -> dict[str, str]:
        payload: dict[str, str] = {}
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            payload[key.strip().lower()] = value.strip()
        return payload

    def _parse_collect_stdout(self, stdout: str) -> dict:
        payload: dict[str, object] = {
            "status": "unknown",
            "log_tail": "",
            "output_dir": "",
            "output_dir_exists": False,
            "files": {},
            "artifact_refs": [],
            "manifest_line_count": None,
        }
        in_log_tail = False
        active_file: str | None = None
        file_lines: list[str] = []
        log_lines: list[str] = []

        for line in stdout.splitlines():
            stripped = line.rstrip("\n")
            if stripped == "LOG_TAIL_BEGIN":
                in_log_tail = True
                log_lines = []
                continue
            if stripped == "LOG_TAIL_END":
                in_log_tail = False
                payload["log_tail"] = "\n".join(log_lines).strip()
                continue
            if stripped.startswith("FILE_BEGIN::"):
                active_file = stripped.split("::", 1)[1].strip()
                file_lines = []
                continue
            if stripped.startswith("FILE_END::"):
                file_path = stripped.split("::", 1)[1].strip()
                if active_file == file_path:
                    files = dict(payload["files"])
                    files[file_path] = "\n".join(file_lines).strip()
                    payload["files"] = files
                active_file = None
                file_lines = []
                continue
            if in_log_tail:
                log_lines.append(stripped)
                continue
            if active_file is not None:
                file_lines.append(stripped)
                continue
            if stripped.startswith("STATUS="):
                payload["status"] = stripped.split("=", 1)[1].strip() or "unknown"
                continue
            if stripped.startswith("OUTPUT_DIR="):
                payload["output_dir"] = stripped.split("=", 1)[1].strip()
                continue
            if stripped.startswith("OUTPUT_DIR_EXISTS="):
                payload["output_dir_exists"] = stripped.split("=", 1)[1].strip() == "1"
                continue
            if stripped.startswith("MANIFEST_LINE_COUNT="):
                raw = stripped.split("=", 1)[1].strip()
                try:
                    payload["manifest_line_count"] = int(raw)
                except ValueError:
                    payload["manifest_line_count"] = None
                continue
            if stripped.startswith("ARTIFACT::"):
                refs = list(payload["artifact_refs"])
                refs.append(stripped.split("::", 1)[1].strip())
                payload["artifact_refs"] = refs
                continue
        return payload

    def _shell_quote(self, value: str) -> str:
        escaped = value.replace("'", "'\"'\"'")
        return f"'{escaped}'"
