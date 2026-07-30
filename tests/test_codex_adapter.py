from pathlib import Path

from labit.agents.adapters import codex as codex_adapter


def test_codex_executable_prefers_standalone_installer_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    standalone_codex = home / ".local" / "bin" / "codex"
    standalone_codex.parent.mkdir(parents=True)
    standalone_codex.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(codex_adapter.shutil, "which", lambda name: None)

    assert codex_adapter._codex_executable() == str(standalone_codex)
