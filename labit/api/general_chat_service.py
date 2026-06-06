"""General (non-paper) chat service: CRUD for project-scoped chats."""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from labit.api.chat_models import AgentName, ChatMode
from labit.api.general_chat_models import (
    Artifact,
    Attachment,
    GeneralChatListItem,
    GeneralChatMessage,
    GeneralChatRecord,
)
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def _mime_to_ext(mime_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime_type, ".bin")


def _ext_to_mime(ext: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext.lower(), "application/octet-stream")


SYSTEM_PROMPT = (
    "You are a helpful research assistant in the Labit workspace. "
    "Assist the user with their research questions, analysis, writing, "
    "and development tasks. Be concise and specific.\n\n"
    "# ARTIFACT OUTPUT FORMAT (MANDATORY)\n\n"
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


# ---------------------------------------------------------------------------
# Artifact extraction
# ---------------------------------------------------------------------------

# Match artifact fences: opening fence has N backticks (N>=3), closing fence
# must have exactly N backticks on its own line.  This avoids the bug where
# 3-backtick code blocks inside the artifact content would prematurely close
# the artifact block.
_ARTIFACT_OPEN_RE = re.compile(
    r"(`{3,})\s*artifact:\s*(.+?)\s*\n?"
    r"title:\s*([^\n]+)\n"
    r"---\s*\n"
)

_EXT_TO_LANG: dict[str, str] = {
    ".md": "markdown", ".markdown": "markdown",
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".sh": "bash", ".bash": "bash",
    ".tex": "latex", ".css": "css", ".html": "html",
    ".sql": "sql", ".rs": "rust", ".go": "go",
    ".java": "java", ".c": "c", ".cpp": "cpp",
    ".txt": "text", ".csv": "csv", ".xml": "xml",
}

_EXT_TO_MIME: dict[str, str] = {
    ".md": "text/markdown", ".markdown": "text/markdown",
    ".py": "text/x-python", ".js": "text/javascript",
    ".ts": "text/typescript", ".tsx": "text/typescript",
    ".json": "application/json", ".yaml": "text/yaml", ".yml": "text/yaml",
    ".toml": "text/toml", ".sh": "text/x-shellscript",
    ".tex": "text/x-latex", ".css": "text/css",
    ".html": "text/plain",  # serve as plain text for safety
    ".sql": "text/x-sql", ".csv": "text/csv", ".xml": "text/xml",
    ".txt": "text/plain",
}


def _sanitize_filename(name: str) -> str:
    """Remove path traversal and special characters from a filename."""
    name = name.strip().replace("\\", "/")
    name = name.split("/")[-1]  # take only the basename
    # Remove characters unsafe for filenames
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "", name)
    return name or "artifact.txt"


def extract_artifacts(text: str, agent: str = "") -> tuple[str, list[Artifact]]:
    """Extract artifact blocks from agent output.

    Returns (cleaned_text, artifacts) where cleaned_text has artifact blocks
    replaced with placeholder references.

    The parser counts the backticks in the opening fence and only closes when
    it finds a line with *exactly* that many backticks (and nothing else).
    This correctly handles artifacts that contain nested code blocks with
    fewer backticks.
    """
    artifacts: list[Artifact] = []
    result_parts: list[str] = []
    pos = 0

    while pos < len(text):
        m = _ARTIFACT_OPEN_RE.search(text, pos)
        if m is None:
            result_parts.append(text[pos:])
            break

        # Text before the artifact block
        result_parts.append(text[pos:m.start()])

        fence = m.group(1)  # the backtick sequence (e.g. ````` or ```)
        raw_filename = _sanitize_filename(m.group(2).strip())
        title = m.group(3).strip()
        content_start = m.end()

        # Find closing fence: the same backtick sequence on its own line
        # (preceded by newline) or directly after content at the very end
        # of the text (agents sometimes omit the newline before closing).
        # We try newline-preceded first, falling back to end-of-text match.
        closing_nl = re.compile(r"\n" + re.escape(fence) + r"\s*(?:\n|$)")
        close_m = closing_nl.search(text, content_start)
        if close_m is None:
            # Fallback: fence at end of text without preceding newline
            closing_end = re.compile(re.escape(fence) + r"\s*$")
            close_m = closing_end.search(text, content_start)

        if close_m is None:
            # No closing fence found — treat as plain text
            result_parts.append(text[m.start():m.end()])
            pos = m.end()
            continue

        content = text[content_start:close_m.start()]
        # Strip trailing newline from content if the closing fence was on its own line
        if close_m.group(0).startswith("\n"):
            pass  # newline is already excluded from content via close_m.start()
        # else: content may end with the last text char before the fence
        ext = Path(raw_filename).suffix.lower()
        language = _EXT_TO_LANG.get(ext, "text")
        mime_type = _EXT_TO_MIME.get(ext, "text/plain")

        art = Artifact(
            id=f"art_{uuid.uuid4().hex[:8]}",
            title=title,
            filename=raw_filename,
            language=language,
            mime_type=mime_type,
            content=content,
        )
        artifacts.append(art)
        result_parts.append(f"[Artifact: {title} ({raw_filename})]")
        pos = close_m.end()

    return "".join(result_parts), artifacts


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
    ) -> GeneralChatRecord:
        chats_dir = self._chats_dir(project)
        chats_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        chat_id = uuid.uuid4().hex[:12]
        record = GeneralChatRecord(
            chat_id=chat_id,
            title=title or f"Chat {chat_id[:6]}",
            project=project,
            mode=mode,
            first_agent=first_agent,
            created_at=now,
            updated_at=now,
        )
        self._save_chat(chats_dir, record)
        return record

    def list_chats(self, project: str) -> list[GeneralChatListItem]:
        chats_dir = self._chats_dir(project)
        if not chats_dir.exists():
            return []
        items: list[GeneralChatListItem] = []
        for path in sorted(chats_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                record = GeneralChatRecord.model_validate_json(path.read_text(encoding="utf-8"))
                items.append(
                    GeneralChatListItem(
                        chat_id=record.chat_id,
                        title=record.title,
                        mode=record.mode,
                        first_agent=record.first_agent,
                        updated_at=record.updated_at,
                        message_count=len(record.messages),
                    )
                )
            except Exception:
                continue
        return items

    def get_chat(self, project: str, chat_id: str) -> GeneralChatRecord:
        path = self._chat_path(project, chat_id)
        if not path.exists():
            raise FileNotFoundError(f"Chat '{chat_id}' not found.")
        return GeneralChatRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def update_chat(
        self,
        project: str,
        chat_id: str,
        *,
        mode: ChatMode | None = None,
        first_agent: AgentName | None = None,
        title: str | None = None,
    ) -> GeneralChatRecord:
        record = self.get_chat(project, chat_id)
        if mode is not None:
            record.mode = mode
        if first_agent is not None:
            record.first_agent = first_agent
        if title is not None:
            record.title = title
        record.updated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        self._save_chat(self._chats_dir(project), record)
        return record

    def delete_chat(self, project: str, chat_id: str) -> None:
        path = self._chat_path(project, chat_id)
        if path.exists():
            path.unlink()

    def append_message(
        self,
        project: str,
        chat_id: str,
        role: str,
        content: str,
        agent: str | None = None,
        attachments: list[Attachment] | None = None,
        artifacts: list[Artifact] | None = None,
    ) -> GeneralChatMessage:
        record = self.get_chat(project, chat_id)
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        msg = GeneralChatMessage(
            id=f"msg_{uuid.uuid4().hex[:8]}",
            role=role,
            content=content,
            agent=agent,
            attachments=attachments or [],
            artifacts=artifacts or [],
            created_at=now,
        )
        record.messages.append(msg)
        record.updated_at = now
        self._save_chat(self._chats_dir(project), record)
        return msg

    def upload_attachment(
        self,
        project: str,
        chat_id: str,
        filename: str,
        mime_type: str,
        data: bytes,
    ) -> Attachment:
        """Save an uploaded file and return its Attachment metadata."""
        att_dir = self._attachments_dir(project, chat_id)
        att_dir.mkdir(parents=True, exist_ok=True)

        att_id = uuid.uuid4().hex[:12]
        ext = _mime_to_ext(mime_type)
        dest = att_dir / f"{att_id}{ext}"
        dest.write_bytes(data)

        return Attachment(
            id=att_id,
            kind="image",
            filename=filename,
            mime_type=mime_type,
            path=str(dest),
        )

    def get_attachment_path(self, project: str, chat_id: str, att_id: str) -> Path | None:
        """Return the file path of an attachment, or None if not found."""
        att_dir = self._attachments_dir(project, chat_id)
        if not att_dir.exists():
            return None
        for f in att_dir.iterdir():
            if f.stem == att_id:
                return f
        return None

    def resolve_attachment_ids(
        self,
        project: str,
        chat_id: str,
        attachment_ids: list[str],
    ) -> list[Attachment]:
        """Look up stored attachment metadata by IDs from the chat record."""
        record = self.get_chat(project, chat_id)
        # Build index of all attachments across messages + any pending uploads
        by_id: dict[str, Attachment] = {}
        for msg in record.messages:
            for att in msg.attachments:
                by_id[att.id] = att
        # Also check files on disk for recently uploaded but not yet in a message
        att_dir = self._attachments_dir(project, chat_id)
        if att_dir.exists():
            for f in att_dir.iterdir():
                if f.stem not in by_id:
                    by_id[f.stem] = Attachment(
                        id=f.stem,
                        kind="image",
                        filename=f.name,
                        mime_type=_ext_to_mime(f.suffix),
                        path=str(f),
                    )
        return [by_id[aid] for aid in attachment_ids if aid in by_id]

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

    def build_prompt(self, project: str, chat_id: str) -> str:
        """Build prompt from chat history."""
        record = self.get_chat(project, chat_id)
        parts: list[str] = []
        for msg in record.messages:
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
        for msg in record.messages:
            for art in msg.artifacts:
                if art.id == artifact_id:
                    return art
        return None

    def _chats_dir(self, project: str) -> Path:
        project_dir = self.project_service.project_dir(project)
        return project_dir / "chats"

    def _chat_path(self, project: str, chat_id: str) -> Path:
        return self._chats_dir(project) / f"{chat_id}.json"

    def _attachments_dir(self, project: str, chat_id: str) -> Path:
        return self._chats_dir(project) / f"{chat_id}_attachments"

    def _save_chat(self, chats_dir: Path, record: GeneralChatRecord) -> None:
        chats_dir.mkdir(parents=True, exist_ok=True)
        path = chats_dir / f"{record.chat_id}.json"
        path.write_text(
            record.model_dump_json(indent=2),
            encoding="utf-8",
        )
