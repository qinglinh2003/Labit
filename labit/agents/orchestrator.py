from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from labit.agents.adapters import ClaudeAdapter, CodexAdapter
from labit.agents.adapters.base import AgentAdapter
from labit.agents.models import (
    AgentRequest,
    AgentResponse,
    AgentRole,
    CollaborationMode,
    ContextPack,
    ExecutionArtifact,
    InputRef,
    ProviderAssignment,
    ProviderKind,
    RunArtifact,
    RunManifest,
    RunStatus,
    SynthesisArtifact,
    utc_now_iso,
)
from labit.agents.store import ArtifactStore
from labit.paths import RepoPaths


@dataclass
class ProviderRegistry:
    adapters: dict[ProviderKind, AgentAdapter]

    @classmethod
    def default(cls) -> "ProviderRegistry":
        return cls(
            adapters={
                ProviderKind.CLAUDE: ClaudeAdapter(),
                ProviderKind.CODEX: CodexAdapter(),
            }
        )

    def get(self, provider: ProviderKind) -> AgentAdapter:
        try:
            return self.adapters[provider]
        except KeyError as exc:
            raise KeyError(f"No adapter registered for provider '{provider.value}'.") from exc


class AgentRuntime:
    def __init__(
        self,
        paths: RepoPaths,
        *,
        registry: ProviderRegistry | None = None,
        store: ArtifactStore | None = None,
    ):
        self.paths = paths
        self.registry = registry or ProviderRegistry.default()
        self.store = store or ArtifactStore(paths)

    def begin_run(
        self,
        context: ContextPack,
        *,
        assignments: list[ProviderAssignment] | None = None,
    ) -> RunManifest:
        run_id = uuid4().hex[:12]
        manifest = RunManifest(
            run_id=run_id,
            task_kind=context.task.kind,
            mode=context.task.mode,
            status=RunStatus.RUNNING,
            project=context.project.name if context.project else None,
            provider_assignments=assignments or [],
        )
        run_dir = self.store.initialize_run(manifest, context)
        workspace = context.workspace.model_copy(update={"run_dir": str(run_dir)})
        updated_context = context.model_copy(update={"workspace": workspace})
        self.store.write_context(manifest.run_id, updated_context)
        return manifest

    def run_role(
        self,
        manifest: RunManifest,
        *,
        role: AgentRole,
        provider: ProviderKind,
        request: AgentRequest,
        input_refs: list[InputRef] | None = None,
    ) -> RunArtifact:
        adapter = self.registry.get(provider)
        response = adapter.run(request)
        artifact = RunArtifact(
            artifact_id=uuid4().hex[:12],
            run_id=manifest.run_id,
            task_kind=manifest.task_kind,
            mode=manifest.mode,
            role=role,
            provider=provider,
            request_prompt=request.prompt,
            request_system_prompt=request.system_prompt,
            request_output_schema=request.output_schema,
            request_cwd=request.cwd,
            request_session_id=request.session_id,
            request_timeout_seconds=request.timeout_seconds,
            request_allowed_tools=request.allowed_tools,
            request_extra_args=request.extra_args,
            input_refs=input_refs or [],
            output=response.structured_output or response.raw_output,
            raw_output=response.raw_output,
            response_session_id=response.session_id,
            command=response.command,
        )
        self.store.write_artifact(artifact)
        return artifact

    def record_synthesis(self, manifest: RunManifest, artifact: SynthesisArtifact) -> Path:
        return self.store.write_synthesis(artifact.model_copy(update={"run_id": manifest.run_id}))

    def record_execution(self, manifest: RunManifest, artifact: ExecutionArtifact) -> Path:
        return self.store.write_execution(artifact.model_copy(update={"run_id": manifest.run_id}))

    def finish_run(self, manifest: RunManifest, *, status: RunStatus = RunStatus.COMPLETED) -> RunManifest:
        updated = manifest.model_copy(update={"status": status, "finished_at": utc_now_iso()})
        self.store.write_manifest(updated)
        return updated

    def discussion_run(
        self,
        context: ContextPack,
        *,
        turns: list[tuple[AgentRole, ProviderKind, AgentRequest]],
        assignments: list[ProviderAssignment] | None = None,
    ) -> tuple[RunManifest, list[RunArtifact]]:
        if context.task.mode != CollaborationMode.DISCUSSION:
            raise ValueError("discussion_run requires a discussion task.")
        manifest = self.begin_run(context, assignments=assignments)
        artifacts: list[RunArtifact] = []
        for role, provider, request in turns:
            artifacts.append(self.run_role(manifest, role=role, provider=provider, request=request))
        return manifest, artifacts
