from __future__ import annotations

import os
import shutil

from labit.agents.models import ProviderKind


def provider_available(provider: str | ProviderKind) -> bool:
    kind = ProviderKind(provider) if not isinstance(provider, ProviderKind) else provider
    return shutil.which(kind.value) is not None


def resolve_provider_kind(provider: str | ProviderKind | None = None) -> ProviderKind:
    if isinstance(provider, ProviderKind):
        return provider

    selected = (provider or os.environ.get("LABIT_PROVIDER") or os.environ.get("RESEARCH_OS_LLM_PROVIDER") or "auto").lower()

    if selected == "auto":
        for candidate in (ProviderKind.CLAUDE, ProviderKind.CODEX):
            if shutil.which(candidate.value):
                return candidate
        raise FileNotFoundError("No supported local agent backend found. Install Claude Code or Codex CLI.")

    try:
        provider_kind = ProviderKind(selected)
    except ValueError as exc:
        raise ValueError("Unsupported provider. Expected one of: auto, claude, codex.") from exc

    if shutil.which(provider_kind.value) is None:
        raise FileNotFoundError(f"Provider '{provider_kind.value}' is not installed.")
    return provider_kind


def discussion_provider_kinds() -> tuple[ProviderKind, ProviderKind]:
    if provider_available(ProviderKind.CLAUDE):
        first = ProviderKind.CLAUDE
    elif provider_available(ProviderKind.CODEX):
        first = ProviderKind.CODEX
    else:
        raise FileNotFoundError("No supported local agent backend found. Install Claude Code or Codex CLI.")

    if provider_available(ProviderKind.CODEX):
        second = ProviderKind.CODEX
    else:
        second = first

    return first, second
