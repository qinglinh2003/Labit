from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from labit.agents.models import ContextPack, ExecutionArtifact, RunArtifact, RunManifest, SynthesisArtifact
from labit.paths import RepoPaths


class ArtifactStore:
    def __init__(self, paths: RepoPaths):
        self.paths = paths

    def run_dir(self, run_id: str) -> Path:
        return self.paths.runs_dir / run_id

    def initialize_run(self, manifest: RunManifest, context: ContextPack) -> Path:
        run_dir = self.run_dir(manifest.run_id)
        (run_dir / "turns").mkdir(parents=True, exist_ok=True)
        self.write_manifest(manifest)
        self.write_context(manifest.run_id, context)
        return run_dir

    def write_context(self, run_id: str, context: ContextPack) -> Path:
        path = self.run_dir(run_id) / "context.json"
        self._write_json(path, context.model_dump(mode="json"))
        return path

    def write_manifest(self, manifest: RunManifest) -> Path:
        path = self.run_dir(manifest.run_id) / "manifest.json"
        self._write_json(path, manifest.model_dump(mode="json"))
        return path

    def write_artifact(self, artifact: RunArtifact) -> Path:
        filename = f"{artifact.created_at.replace(':', '-')}_{artifact.role.value}_{artifact.provider.value}.json"
        path = self.run_dir(artifact.run_id) / "turns" / filename
        payload = artifact.model_dump(mode="json")
        self._write_json(path, payload)
        self._append_transcript(artifact.run_id, payload)
        return path

    def write_synthesis(self, artifact: SynthesisArtifact) -> Path:
        path = self.run_dir(artifact.run_id) / "synthesis.json"
        self._write_json(path, artifact.model_dump(mode="json"))
        return path

    def write_execution(self, artifact: ExecutionArtifact) -> Path:
        path = self.run_dir(artifact.run_id) / "execution.json"
        self._write_json(path, artifact.model_dump(mode="json"))
        return path

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=2, sort_keys=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        temp_path.replace(path)

    def _append_transcript(self, run_id: str, payload: dict) -> None:
        path = self.run_dir(run_id) / "transcript.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
            handle.write("\n")
