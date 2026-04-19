from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field

from labit.models import ComputeProfile
from labit.paths import RepoPaths


class ComputeCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ssh_ok: bool = False
    workdir_ok: bool = False
    datadir_ok: bool = False
    setup_ok: bool = False
    python_ok: bool = False
    gpu_ok: bool = False
    python_version: str = ""
    detected_gpu_count: int | None = None
    detected_gpu_type: str | None = None
    message: str = ""


class ComputeService:
    def __init__(self, paths: RepoPaths):
        self.paths = paths

    def list_compute_names(self) -> list[str]:
        if not self.paths.compute_configs_dir.exists():
            return []
        return sorted(path.stem for path in self.paths.compute_configs_dir.glob("*.yaml"))

    def resolve_compute_name(self, name: str) -> str | None:
        for candidate in self.list_compute_names():
            if candidate.lower() == name.lower():
                return candidate
        return None

    def load_compute(self, name: str) -> ComputeProfile:
        resolved = self.resolve_compute_name(name)
        if resolved is None:
            raise FileNotFoundError(
                f"Compute profile '{name}' not found. Available profiles: {', '.join(self.list_compute_names()) or '(none)'}"
            )
        raw = yaml.safe_load((self.paths.compute_configs_dir / f"{resolved}.yaml").read_text()) or {}
        return ComputeProfile.model_validate(raw)

    def save_compute(self, profile: ComputeProfile, *, force: bool = False) -> dict:
        resolved = self.resolve_compute_name(profile.name)
        if resolved and not force:
            raise FileExistsError(
                f"Compute profile '{resolved}' already exists. Re-run with '--force' to overwrite."
            )
        self.paths.compute_configs_dir.mkdir(parents=True, exist_ok=True)
        config_path = self.paths.compute_configs_dir / f"{profile.name}.yaml"
        yaml_text = yaml.safe_dump(profile.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True)
        self._atomic_write(config_path, yaml_text)
        return {"name": profile.name, "config_path": str(config_path)}

    def delete_compute(self, name: str) -> dict:
        resolved = self.resolve_compute_name(name)
        if resolved is None:
            raise FileNotFoundError(
                f"Compute profile '{name}' not found. Available profiles: {', '.join(self.list_compute_names()) or '(none)'}"
            )
        path = self.paths.compute_configs_dir / f"{resolved}.yaml"
        if path.exists():
            path.unlink()
        return {"name": resolved, "config_path": str(path)}

    def test_compute(self, name: str, *, on_step: Callable[[str], None] | None = None) -> ComputeCheckResult:
        profile = self.load_compute(name)
        ssh_command = self.build_ssh_command(profile)

        if on_step:
            on_step("Checking SSH connection")
        probe = self._ssh_run(ssh_command, "printf 'LABIT_OK'", timeout=20)
        if probe.returncode != 0 or (probe.stdout or "").strip() != "LABIT_OK":
            return ComputeCheckResult(name=profile.name, message=(probe.stderr or probe.stdout or "SSH connection failed.").strip())

        if on_step:
            on_step("Checking workdir")
        workdir_probe = self._ssh_run(
            ssh_command,
            (
                f"WORKDIR={self._shell_quote(profile.workspace.workdir)}\n"
                "if [ \"$WORKDIR\" = \"~\" ]; then WORKDIR=\"$HOME\"; fi\n"
                "case \"$WORKDIR\" in\n"
                "  \"~/\"*) WORKDIR=\"$HOME/${WORKDIR#\"~/\"}\" ;;\n"
                "esac\n"
                "[ -d \"$WORKDIR\" ] && printf 'OK'\n"
            ),
            timeout=20,
        )
        workdir_ok = workdir_probe.returncode == 0 and (workdir_probe.stdout or "").strip() == "OK"

        datadir_ok = True
        if profile.workspace.datadir:
            if on_step:
                on_step("Checking datadir")
            datadir_probe = self._ssh_run(
                ssh_command,
                (
                    f"DATADIR={self._shell_quote(profile.workspace.datadir)}\n"
                    "if [ \"$DATADIR\" = \"~\" ]; then DATADIR=\"$HOME\"; fi\n"
                    "case \"$DATADIR\" in\n"
                    "  \"~/\"*) DATADIR=\"$HOME/${DATADIR#\"~/\"}\" ;;\n"
                    "esac\n"
                    "[ -d \"$DATADIR\" ] && printf 'OK'\n"
                ),
                timeout=20,
            )
            datadir_ok = datadir_probe.returncode == 0 and (datadir_probe.stdout or "").strip() == "OK"

        setup_script = profile.setup.script.strip()
        setup_ok = True
        python_ok = False
        python_version = ""
        if setup_script:
            if on_step:
                on_step("Running setup script")
            workdir_cd = self._resolve_workdir_script(profile.workspace.workdir)
            setup_probe = self._ssh_run(ssh_command, f"set -e\n{workdir_cd}\n{setup_script}\nprintf 'LABIT_SETUP_OK'", timeout=40)
            setup_ok = setup_probe.returncode == 0 and (setup_probe.stdout or "").strip().endswith("LABIT_SETUP_OK")
            if not setup_ok:
                return ComputeCheckResult(
                    name=profile.name,
                    ssh_ok=True,
                    workdir_ok=workdir_ok,
                    datadir_ok=datadir_ok,
                    setup_ok=False,
                    message=(setup_probe.stderr or setup_probe.stdout or "Setup script failed.").strip(),
                )

        python_probe_script = "set -e\n"
        python_probe_script += f"{self._resolve_workdir_script(profile.workspace.workdir)}\n"
        if setup_script:
            python_probe_script += f"{setup_script}\n"
        python_probe_script += (
            "if command -v python >/dev/null 2>&1; then\n"
            "  python --version\n"
            "elif command -v python3 >/dev/null 2>&1; then\n"
            "  python3 --version\n"
            "else\n"
            "  exit 1\n"
            "fi\n"
        )
        if on_step:
            on_step("Checking Python")
        python_probe = self._ssh_run(ssh_command, python_probe_script, timeout=40)
        if python_probe.returncode == 0:
            python_ok = True
            python_version = (python_probe.stdout or python_probe.stderr or "").strip()

        gpu_ok = True
        detected_gpu_count: int | None = None
        detected_gpu_type: str | None = None
        if profile.hardware.gpu_count > 0:
            if on_step:
                on_step("Checking GPUs")
            gpu_script = "set -e\n"
            gpu_script += f"{self._resolve_workdir_script(profile.workspace.workdir)}\n"
            if setup_script:
                gpu_script += f"{setup_script}\n"
            gpu_script += "nvidia-smi --query-gpu=name --format=csv,noheader"
            gpu_probe = self._ssh_run(ssh_command, gpu_script, timeout=40)
            if gpu_probe.returncode != 0:
                gpu_ok = False
            else:
                gpus = [line.strip() for line in (gpu_probe.stdout or "").splitlines() if line.strip()]
                detected_gpu_count = len(gpus)
                detected_gpu_type = gpus[0] if gpus else None
                gpu_ok = detected_gpu_count >= profile.hardware.gpu_count

        ready = probe.returncode == 0 and workdir_ok and datadir_ok and setup_ok and python_ok and gpu_ok
        if ready:
            message = "Ready for LABIT experiment execution."
        else:
            parts: list[str] = []
            if not workdir_ok:
                parts.append("workdir missing or not accessible")
            if not datadir_ok:
                parts.append("datadir missing or not accessible")
            if not python_ok:
                parts.append("python unavailable after setup")
            if not gpu_ok:
                parts.append("GPU requirements not satisfied")
            message = "; ".join(parts) or "Compute check failed."

        return ComputeCheckResult(
            name=profile.name,
            ssh_ok=True,
            workdir_ok=workdir_ok,
            datadir_ok=datadir_ok,
            setup_ok=setup_ok,
            python_ok=python_ok,
            gpu_ok=gpu_ok,
            python_version=python_version,
            detected_gpu_count=detected_gpu_count,
            detected_gpu_type=detected_gpu_type,
            message=message,
        )

    def build_ssh_command(self, profile: ComputeProfile) -> list[str]:
        command = ["ssh"]
        if profile.connection.port != 22:
            command.extend(["-p", str(profile.connection.port)])
        if profile.connection.ssh_key:
            command.extend(["-i", os.path.expanduser(profile.connection.ssh_key)])
        command.append(f"{profile.connection.user}@{profile.connection.host}")
        return command

    def _ssh_run(self, ssh_command: list[str], script: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*ssh_command, "bash", "--norc", "--noprofile", "-s"],
            input=script,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    def _resolve_workdir_script(self, workdir: str) -> str:
        """Return shell snippet that cd's into the resolved workdir."""
        quoted = self._shell_quote(workdir)
        return (
            f"WORKDIR={quoted}\n"
            'if [ "$WORKDIR" = "~" ]; then WORKDIR="$HOME"; fi\n'
            'case "$WORKDIR" in\n'
            '  "~/"*) WORKDIR="$HOME/${WORKDIR#"~/"}" ;;\n'
            "esac\n"
            'cd "$WORKDIR"\n'
        )

    def _shell_quote(self, value: str) -> str:
        return shlex.quote(value)

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
