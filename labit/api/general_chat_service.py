"""General (non-paper) chat service: CRUD for project-scoped chats."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from labit.api.attachment_utils import (
    get_attachment_path as _get_attachment_path,
    resolve_attachment_ids as _resolve_attachment_ids,
    upload_attachment as _upload_attachment,
)
from labit.api.chat_models import (
    AgentName,
    Artifact,
    Attachment,
    ChatListItem,
    ChatMessage,
    ChatMode,
    ChatRecord,
)
from labit.api.chat_storage import (
    append_message as _append_message,
    delete_chat_dir,
    find_artifact,
    list_chat_records,
    load_chat,
    save_chat,
    update_chat_fields,
)
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


from labit.api.shared_prompts import PROJECT_FILES_CONTEXT

SYSTEM_PROMPT = (
    "You are a helpful research assistant in the Labit workspace. "
    "Assist the user with their research questions, analysis, writing, "
    "and development tasks. Be concise and specific.\n"
    + PROJECT_FILES_CONTEXT +
    "\n# ARTIFACT OUTPUT FORMAT (MANDATORY)\n\n"
    "When the user asks you to write, draft, create, or generate a standalone "
    "document, report, proposal, code file, script, configuration, or any "
    "content that is meant to be saved, downloaded, or used as a file, you "
    "MUST wrap the content in an artifact block. This is NOT optional.\n\n"
    "Artifact block format (use EXACTLY 5 backticks, NOT 3):\n\n"
    "`````artifact:filename.ext\n"
    "title: A descriptive title\n"
    "---\n"
    "(your full content here)\n"
    "`````\n\n"
    "Example — if the user says 'write me a research proposal':\n\n"
    "Here is the research proposal.\n\n"
    "`````artifact:research_proposal.md\n"
    "title: Research Proposal - Topic Name\n"
    "---\n"
    "# Research Proposal\n\n"
    "## 1. Introduction\n"
    "...\n"
    "`````\n\n"
    "IMPORTANT: You MUST use 5 backticks (`````) for artifact fences, not 3.\n"
    "This prevents confusion with normal code blocks (```) inside the artifact.\n\n"
    "Rules:\n"
    "- ALWAYS use artifact blocks for complete documents, reports, proposals, "
    "scripts, configs, plans, etc. Never output a full document as plain text.\n"
    "- Do NOT use artifacts for short answers, explanations, or conversational "
    "code snippets.\n"
    "- The filename MUST include an extension (.md, .py, .json, .tex, etc.).\n"
    "- You may output multiple artifacts in one response.\n"
    "- You may include brief explanatory text before or after artifact blocks.\n"
    "- The artifact content must be complete — do not truncate or summarize."
)


class GeneralChatService:
    def __init__(self, paths: RepoPaths, project_service: ProjectService):
        self.paths = paths
        self.project_service = project_service

    def create_chat(
        self,
        project: str,
        title: str = "",
        mode: ChatMode = ChatMode.SINGLE,
        first_agent: AgentName = "claude",
    ) -> ChatRecord:
        chats_dir = self._chats_dir(project)
        chats_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        chat_id = uuid.uuid4().hex[:12]
        record = ChatRecord(
            chat_id=chat_id,
            title=title or f"Chat {chat_id[:6]}",
            project=project,
            mode=mode,
            first_agent=first_agent,
            created_at=now,
            updated_at=now,
        )
        save_chat(chats_dir, record)
        return record

    def list_chats(self, project: str) -> list[ChatListItem]:
        records = list_chat_records(self._chats_dir(project), ChatRecord)
        return [
            ChatListItem(
                chat_id=r.chat_id, title=r.title, mode=r.mode,
                first_agent=r.first_agent, updated_at=r.updated_at,
                message_count=len(r.messages),
            )
            for r in records
        ]

    def get_chat(self, project: str, chat_id: str) -> ChatRecord:
        return load_chat(self._chats_dir(project), chat_id, ChatRecord)

    def update_chat(
        self,
        project: str,
        chat_id: str,
        *,
        mode: ChatMode | None = None,
        first_agent: AgentName | None = None,
        title: str | None = None,
    ) -> ChatRecord:
        return update_chat_fields(
            self._chats_dir(project), chat_id, ChatRecord,
            mode=mode, first_agent=first_agent, title=title,
        )

    def delete_chat(self, project: str, chat_id: str) -> None:
        delete_chat_dir(self._chats_dir(project), chat_id)

    def append_message(
        self,
        project: str,
        chat_id: str,
        role: str,
        content: str,
        agent: str | None = None,
        attachments: list[Attachment] | None = None,
        artifacts: list[Artifact] | None = None,
    ) -> ChatMessage:
        extra: dict = {}
        if attachments:
            extra["attachments"] = attachments
        if artifacts:
            extra["artifacts"] = artifacts
        return _append_message(
            self._chats_dir(project), chat_id, ChatRecord,
            ChatMessage, role=role, content=content, agent=agent,
            extra_fields=extra or None,
        )

    def upload_attachment(
        self,
        project: str,
        chat_id: str,
        filename: str,
        mime_type: str,
        data: bytes,
    ) -> Attachment:
        return _upload_attachment(
            self._attachments_dir(project, chat_id), filename, mime_type, data,
        )

    def get_attachment_path(self, project: str, chat_id: str, att_id: str) -> Path | None:
        return _get_attachment_path(self._attachments_dir(project, chat_id), att_id)

    def resolve_attachment_ids(
        self,
        project: str,
        chat_id: str,
        attachment_ids: list[str],
    ) -> list[Attachment]:
        record = self.get_chat(project, chat_id)
        return _resolve_attachment_ids(
            self._attachments_dir(project, chat_id), record.messages, attachment_ids,
        )

    def recent_image_paths(self, project: str, chat_id: str, max_images: int = 4) -> list[str]:
        """Collect image paths from most recent user messages."""
        record = self.get_chat(project, chat_id)
        paths: list[str] = []
        for msg in reversed(record.messages):
            if msg.role != "user":
                continue
            for att in msg.attachments:
                if att.kind == "image" and att.path not in paths:
                    paths.append(att.path)
                    if len(paths) >= max_images:
                        return paths
            if paths:
                break  # only grab from the most recent user message with images
        return paths

    def build_prompt(self, project: str, chat_id: str, *, max_history: int | None = None) -> str:
        """Build prompt from chat history.

        Args:
            max_history: If set, only include the last N messages from history.
        """
        record = self.get_chat(project, chat_id)
        parts: list[str] = []

        messages = record.messages
        if max_history is not None and len(messages) > max_history:
            omitted = len(messages) - max_history
            parts.append(f"[{omitted} earlier messages omitted for brevity]")
            messages = messages[-max_history:]

        for msg in messages:
            if msg.role == "user":
                text = msg.content
                if msg.attachments:
                    labels = ", ".join(a.filename for a in msg.attachments)
                    text += f"\n[Attached images: {labels}]"
                parts.append(f"User: {text}")
            else:
                label = msg.agent or "assistant"
                text = msg.content
                # Inject artifact context for previous artifacts
                if msg.artifacts:
                    for art in msg.artifacts:
                        text += (
                            f"\n\n[Previous artifact: {art.filename}]\n"
                            f"{art.content}\n"
                            f"[End of artifact]"
                        )
                parts.append(f"{label}: {text}")

        prompt = "\n\n".join(parts)

        # If the last user message looks like a document/writing request,
        # append an artifact format reminder to reinforce the instruction.
        if record.messages and record.messages[-1].role == "user":
            last_user = record.messages[-1].content.lower()
            _DOC_KEYWORDS = [
                "帮我写", "帮我生成", "写一个", "写一份", "生成一个", "生成一份",
                "draft a", "draft me", "write a", "write me",
                "generate a", "create a file", "create a script",
                "proposal", "report", "文档", "document", "方案",
                "template",
            ]
            if any(kw in last_user for kw in _DOC_KEYWORDS):
                prompt += (
                    "\n\n[System reminder: The user is asking you to produce a "
                    "document or file. You MUST use the `````artifact:filename.ext "
                    "format (5 backticks) as specified in your system instructions. "
                    "Do NOT output the document as plain text.]"
                )

        return prompt

    def get_artifact(
        self, project: str, chat_id: str, artifact_id: str,
    ) -> Artifact | None:
        """Find an artifact by ID across all messages in a chat."""
        record = self.get_chat(project, chat_id)
        return find_artifact(record.messages, artifact_id)

    def get_project_dir(self, project: str) -> str:
        """Return the resolved project directory path (for subprocess cwd)."""
        return str(self.project_service.project_dir(project).resolve())

    def chat_dir(self, project: str, chat_id: str) -> Path:
        """Return the per-chat directory: chats/{chat_id}/."""
        return self._chats_dir(project) / chat_id

    def _chats_dir(self, project: str) -> Path:
        project_dir = self.project_service.project_dir(project)
        return project_dir / "chats"

    def _attachments_dir(self, project: str, chat_id: str) -> Path:
        # New format: chats/{chat_id}/attachments/
        new_dir = self._chats_dir(project) / chat_id / "attachments"
        if new_dir.exists():
            return new_dir
        # Fallback to old format
        old_dir = self._chats_dir(project) / f"{chat_id}_attachments"
        if old_dir.exists():
            return old_dir
        # Default to new format
        return new_dir
