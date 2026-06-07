from __future__ import annotations

from pathlib import Path

import yaml

from labit.paths import RepoPaths
from labit.services.compute_service import ComputeService


def _paths(root: Path) -> RepoPaths:
    return RepoPaths(
        root=root,
        labit_dir=root / ".labit",
        runs_dir=root / ".labit" / "runs",
        conversations_dir=root / ".labit" / "conversations",
        context_dir=root / ".labit" / "context",
        configs_dir=root / "configs",
        project_configs_dir=root / "configs" / "projects",
        active_project_path=root / "configs" / "active_project",
        vault_dir=root / "vault",
        vault_projects_dir=root / "vault" / "projects",
    )


def _create_project(root: Path, *, workdir: str = "/workspace/Labit") -> RepoPaths:
    paths = _paths(root)
    paths.project_configs_dir.mkdir(parents=True)
    paths.vault_projects_dir.mkdir(parents=True)
    (paths.project_configs_dir / "Labit.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "Labit",
                "compute_profiles": [
                    {
                        "name": "gpu",
                        "connection": {
                            "user": "root",
                            "host": "example.com",
                            "port": 2222,
                            "identity_file": "~/.ssh/id test",
                        },
                        "workdir": workdir,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    code_dir = paths.vault_projects_dir / "Labit" / "code"
    code_dir.mkdir(parents=True)
    (code_dir / "train.py").write_text("print('train')\n", encoding="utf-8")
    return paths


def test_sync_code_rejects_empty_remote_workdir(tmp_path: Path) -> None:
    paths = _create_project(tmp_path, workdir="")

    result = ComputeService(paths).sync_code("Labit", "gpu")

    assert result.success is False
    assert result.remote_path == ""
    assert "must define a remote workdir" in result.stderr


def test_sync_code_builds_rsync_command(tmp_path: Path, monkeypatch) -> None:
    paths = _create_project(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

        class Result:
            returncode = 0
            stdout = "sent 1 file"
            stderr = ""

        return Result()

    monkeypatch.setattr("labit.services.compute_service.subprocess.run", fake_run)

    result = ComputeService(paths).sync_code("Labit", "gpu")

    assert result.success is True
    assert result.remote_path == "root@example.com:/workspace/Labit"
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[:3] == ["rsync", "-azP", "--delete"]
    assert "-e" in cmd
    remote_shell = cmd[cmd.index("-e") + 1]
    assert remote_shell.startswith("ssh -i ")
    assert remote_shell.endswith(" -p 2222")
    assert "id test" in remote_shell
    assert cmd[-2] == str(paths.vault_projects_dir / "Labit" / "code") + "/"
    assert cmd[-1] == "root@example.com:/workspace/Labit/"
