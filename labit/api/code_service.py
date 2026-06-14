"""Code service: browse, read, write project code files + code-scoped chat."""
from __future__ import annotations

import base64
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import mimetypes

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
from labit.api.code_models import (
    CodeFileContent,
    CodeFileRecord,
    CodeTreeEntry,
)
from labit.services.project_service import ProjectService


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".rs": "rust", ".go": "go", ".java": "java",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".kt": "kotlin", ".scala": "scala",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".md": "markdown", ".markdown": "markdown",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".xml": "xml", ".html": "html", ".css": "css", ".scss": "css",
    ".sql": "sql",
    ".txt": "text",
    ".cfg": "text", ".ini": "text", ".conf": "text",
    ".dockerfile": "dockerfile",
    ".tex": "latex", ".bib": "latex",
    ".r": "r", ".R": "r",
    ".m": "matlab",
    ".lua": "lua",
    ".jl": "julia",
}

_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".whl", ".egg", ".so", ".dylib", ".dll", ".exe",
    ".pyc", ".pyo", ".class", ".o", ".obj",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".webm",
    ".db", ".sqlite", ".sqlite3",
    ".pkl", ".pickle", ".npy", ".npz", ".pt", ".pth",
    ".parquet", ".arrow", ".feather",
}

# Directories to always skip
_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox",
    "venv", ".venv", "env", ".env",
    "dist", "build", ".next", ".nuxt",
    ".eggs", "*.egg-info",
    ".history",
    "htmlcov", "coverage",
}


def _lang_from_ext(ext: str) -> str:
    return _EXT_TO_LANG.get(ext.lower(), "")


def _is_binary(ext: str) -> bool:
    return ext.lower() in _BINARY_EXTS


def _should_skip_dir(name: str) -> bool:
    if name.startswith("."):
        return True
    if name in _IGNORE_DIRS:
        return True
    if name.endswith(".egg-info"):
        return True
    return False


# ---------------------------------------------------------------------------
# file_id encoding (base64url of relative path)
# ---------------------------------------------------------------------------

def encode_file_id(rel_path: str) -> str:
    return base64.urlsafe_b64encode(rel_path.encode()).decode().rstrip("=")


def decode_file_id(file_id: str) -> str:
    try:
        padded = file_id + "=" * (-len(file_id) % 4)
        return base64.urlsafe_b64decode(padded).decode()
    except Exception as exc:
        raise ValueError(f"Invalid file_id: {exc}") from exc


# ---------------------------------------------------------------------------
# System prompt for code-scoped chat
# ---------------------------------------------------------------------------

from labit.api.shared_prompts import PROJECT_FILES_CONTEXT

CODE_SYSTEM_PROMPT = (
    "You are a software engineering assistant helping with project code.\n"
    "Your working directory is the project root.\n"
    "Use your built-in tools (read files, search, list directory) to explore the codebase as needed.\n"
    "The user may have a file open in the editor — its path is given in the prompt, but you should "
    "read it yourself if you need the content.\n"
    + PROJECT_FILES_CONTEXT +
    "\n"
    "# ARTIFACT OUTPUT FORMAT (MANDATORY)\n\n"
    "When the user asks you to modify, rewrite, fix, or create code, "
    "you MUST output the COMPLETE updated file using an artifact block.\n\n"
    "Artifact block format (use EXACTLY 5 backticks, NOT 3):\n\n"
    "`````artifact:filename.ext\n"
    "title: A descriptive title\n"
    "---\n"
    "(complete file content here)\n"
    "`````\n\n"
    "IMPORTANT rules:\n"
    "- You MUST use 5 backticks (`````) for artifact fences, not 3.\n"
    "- When modifying a file, output the COMPLETE updated version, not just the changed parts.\n"
    "- The filename should match the current file's name.\n"
    "- Do NOT use artifacts for short answers, explanations, or discussion.\n"
    "- You may include brief explanatory text before or after the artifact block.\n"
    "- The artifact content must be complete — do not truncate or summarize.\n"
)


# ---------------------------------------------------------------------------
# CodeService
# ---------------------------------------------------------------------------

class CodeService:
    def __init__(self, project_service: ProjectService):
        self.project_service = project_service

    # -- File browsing -------------------------------------------------------

    def get_tree(self, project: str, rel_dir: str = "") -> list[CodeTreeEntry]:
        """Get directory tree (one level deep, or full recursive)."""
        code_dir = self._code_dir(project)
        if not code_dir.exists():
            return []
        target = code_dir if not rel_dir else code_dir / rel_dir
        target = target.resolve()
        # Path traversal check
        try:
            resolved_rel = target.relative_to(code_dir.resolve())
        except ValueError:
            raise ValueError("Path traversal detected")
        if resolved_rel.parts and resolved_rel.parts[0].startswith("."):
            raise ValueError("Internal directories are not browsable")
        if not target.exists() or not target.is_dir():
            return []
        return self._scan_dir(code_dir, target)

    def _scan_dir(self, code_dir: Path, target: Path) -> list[CodeTreeEntry]:
        entries: list[CodeTreeEntry] = []
        try:
            items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return entries
        for item in items:
            if _should_skip_dir(item.name) and item.is_dir():
                continue
            if item.name.startswith(".") and not item.is_dir():
                # Skip hidden files except dotfiles like .gitignore
                if item.name not in (".gitignore", ".env.example", ".editorconfig"):
                    continue
            rel = str(item.relative_to(code_dir))
            if item.is_dir():
                children = self._scan_dir(code_dir, item)
                entries.append(CodeTreeEntry(
                    name=item.name, path=rel, is_dir=True, children=children,
                ))
            else:
                if _is_binary(item.suffix):
                    continue
                entries.append(CodeTreeEntry(
                    name=item.name, path=rel, is_dir=False,
                ))
        return entries

    def get_file(self, project: str, file_id: str) -> CodeFileRecord:
        code_dir = self._code_dir(project)
        path = self._resolve_file_path(code_dir, file_id)
        if not path.exists() or path.is_dir():
            raise FileNotFoundError(f"File not found: {file_id}")
        rel = str(path.relative_to(code_dir))
        lang = _lang_from_ext(path.suffix)
        stat = path.stat()
        return CodeFileRecord(
            name=path.name, path=rel,
            size_bytes=stat.st_size, language=lang,
        )

    def get_content(self, project: str, file_id: str) -> str:
        code_dir = self._code_dir(project)
        path = self._resolve_file_path(code_dir, file_id)
        if not path.exists() or path.is_dir():
            raise FileNotFoundError(f"File not found: {file_id}")
        return path.read_text(encoding="utf-8", errors="replace")

    def save_content(self, project: str, file_id: str, content: str) -> CodeFileRecord:
        code_dir = self._code_dir(project)
        path = self._resolve_file_path(code_dir, file_id)
        if not path.exists() or path.is_dir():
            raise FileNotFoundError(f"File not found: {file_id}")
        # Backup before overwrite
        self._backup(code_dir, path)
        path.write_text(content, encoding="utf-8")
        rel = str(path.relative_to(code_dir))
        lang = _lang_from_ext(path.suffix)
        stat = path.stat()
        return CodeFileRecord(
            name=path.name, path=rel,
            size_bytes=stat.st_size, language=lang,
        )

    # -- Code Chat -----------------------------------------------------------

    def create_chat(
        self, project: str, file_path: str | None = None,
        title: str = "", mode: ChatMode = ChatMode.SINGLE,
        first_agent: AgentName = "claude",
    ) -> ChatRecord:
        if file_path:
            file_id = encode_file_id(file_path)
            # Verify file exists
            self.get_file(project, file_id)
        chats_dir = self._code_chats_dir(project)
        chat_id = uuid.uuid4().hex[:12]
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        default_title = f"Chat about {file_path.split('/')[-1]}" if file_path else "Code chat"
        record = ChatRecord(
            chat_id=chat_id,
            title=title or default_title,
            file_path=file_path,
            mode=mode,
            first_agent=first_agent,
            created_at=now,
            updated_at=now,
        )
        save_chat(chats_dir, record)
        return record

    def list_chats(self, project: str, file_path: str | None = None) -> list[ChatListItem]:
        records = list_chat_records(self._code_chats_dir(project), ChatRecord)
        if file_path:
            records = [r for r in records if r.file_path == file_path]
        return [
            ChatListItem(
                chat_id=r.chat_id, title=r.title, mode=r.mode,
                first_agent=r.first_agent, updated_at=r.updated_at,
                message_count=len(r.messages),
            )
            for r in records
        ]

    def get_chat(self, project: str, chat_id: str) -> ChatRecord:
        return load_chat(self._code_chats_dir(project), chat_id, ChatRecord)

    def update_chat(
        self, project: str, chat_id: str, *,
        mode: ChatMode | None = None,
        first_agent: AgentName | None = None,
        title: str | None = None,
    ) -> ChatRecord:
        return update_chat_fields(
            self._code_chats_dir(project), chat_id, ChatRecord,
            mode=mode, first_agent=first_agent, title=title,
        )

    def delete_chat(self, project: str, chat_id: str) -> None:
        delete_chat_dir(self._code_chats_dir(project), chat_id)

    def append_message(
        self, project: str, chat_id: str,
        role: str, content: str,
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
            self._code_chats_dir(project), chat_id, ChatRecord,
            ChatMessage, role=role, content=content, agent=agent,
            extra_fields=extra or None,
        )

    # ---- Attachments ----

    def _attachments_dir(self, project: str, chat_id: str) -> Path:
        return self.code_chat_dir(project, chat_id) / "attachments"

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
        self, project: str, chat_id: str, attachment_ids: list[str],
    ) -> list[Attachment]:
        record = self.get_chat(project, chat_id)
        return _resolve_attachment_ids(
            self._attachments_dir(project, chat_id), record.messages, attachment_ids,
        )

    def build_prompt(self, project: str, chat_id: str, *, max_history: int | None = None) -> str:
        """Build prompt with file path hint + chat history (no full content injection).

        Args:
            max_history: If set, only include the last N messages from history.
                         Older messages are summarized with a count notice.
        """
        record = self.get_chat(project, chat_id)

        parts: list[str] = []

        # Only tell the agent which file is open — don't inject full content
        if record.file_path:
            parts.append(
                f"[Current open file: code/{record.file_path}]\n"
                f"Use your tools to read this file or any other files you need."
            )
        else:
            parts.append(
                "[Project-level code chat — no specific file open.]\n"
                "Use your tools to explore the codebase as needed."
            )

        # Chat history (optionally truncated)
        messages = record.messages
        if max_history is not None and len(messages) > max_history:
            omitted = len(messages) - max_history
            parts.append(f"[{omitted} earlier messages omitted for brevity]")
            messages = messages[-max_history:]

        for msg in messages:
            if msg.role == "user":
                parts.append(f"User: {msg.content}")
            else:
                label = msg.agent or "assistant"
                text = msg.content
                if msg.artifacts:
                    for art in msg.artifacts:
                        text += (
                            f"\n\n[Previous artifact: {art.filename}]\n"
                            f"{art.content}\n"
                            f"[End of artifact]"
                        )
                parts.append(f"{label}: {text}")

        prompt = "\n\n".join(parts)

        # Code modification reminder
        if record.file_path and record.messages and record.messages[-1].role == "user":
            last_user = record.messages[-1].content.lower()
            _MOD_KEYWORDS = [
                "修改", "改一下", "修", "fix", "update", "modify",
                "帮我写", "帮我改", "refactor", "重构",
                "改成", "换成", "替换", "replace",
                "add", "添加", "implement", "实现",
            ]
            if any(kw in last_user for kw in _MOD_KEYWORDS):
                filename = record.file_path.split("/")[-1]
                prompt += (
                    f"\n\n[System reminder: The user wants you to modify the code. "
                    f"Read the file first, then output the COMPLETE updated file as an artifact block using "
                    f"`````artifact:{filename} format (5 backticks). "
                    f"Do NOT output just the changed section — output the full file.]"
                )

        return prompt

    def get_project_dir(self, project: str) -> str:
        """Return the resolved project directory path (for subprocess cwd)."""
        return str(self.project_service.project_dir(project).resolve())

    def code_dir(self, project: str) -> str:
        """Return the resolved code directory path as a string."""
        return str(self._code_dir(project).resolve())

    def get_artifact(self, project: str, chat_id: str, artifact_id: str) -> Artifact | None:
        record = self.get_chat(project, chat_id)
        return find_artifact(record.messages, artifact_id)

    def apply_artifact(self, project: str, file_id: str, chat_id: str, artifact_id: str) -> CodeFileRecord:
        """Apply an artifact's content to the file."""
        record = self.get_chat(project, chat_id)
        requested_file = self.get_file(project, file_id)
        if record.file_path and requested_file.path != record.file_path:
            raise ValueError("Artifact chat does not belong to the requested file")
        art = self.get_artifact(project, chat_id, artifact_id)
        if art is None:
            raise FileNotFoundError(f"Artifact '{artifact_id}' not found.")
        return self.save_content(project, file_id, art.content)

    # -- Private -------------------------------------------------------------

    def _code_dir(self, project: str) -> Path:
        return self.project_service.project_dir(project) / "code"

    def code_chat_dir(self, project: str, chat_id: str) -> Path:
        return self._code_chats_dir(project) / chat_id

    def _code_chats_dir(self, project: str) -> Path:
        target = self._code_dir(project) / ".chats"
        # Migrate: move project-level code_chats back into code/.chats
        old_external = self.project_service.project_dir(project) / "code_chats"
        if old_external.exists() and not target.exists():
            old_external.rename(target)
        self._ensure_git_exclude(project)
        return target

    def _ensure_git_exclude(self, project: str) -> None:
        """Add .chats to code/.git/info/exclude if not already present."""
        git_dir = self._code_dir(project) / ".git"
        if not git_dir.is_dir():
            return
        info_dir = git_dir / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        exclude_file = info_dir / "exclude"
        marker = ".chats"
        if exclude_file.exists():
            content = exclude_file.read_text()
            if marker in content.splitlines():
                return
        with exclude_file.open("a") as f:
            f.write(f"\n{marker}\n")

    def _resolve_file_path(self, code_dir: Path, file_id: str) -> Path:
        rel = decode_file_id(file_id)
        path = (code_dir / rel).resolve()
        try:
            resolved_rel = path.relative_to(code_dir.resolve())
        except ValueError:
            raise ValueError("Invalid file_id: path traversal detected")
        parts = resolved_rel.parts
        if parts and parts[0].startswith("."):
            raise ValueError("Invalid file_id: internal directory")
        return path

    def _backup(self, code_dir: Path, path: Path) -> None:
        if not path.exists():
            return
        rel = str(path.relative_to(code_dir))
        file_id = encode_file_id(rel)
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        history_dir = code_dir / ".history" / file_id
        history_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, history_dir / f"{ts}_{path.name}")

