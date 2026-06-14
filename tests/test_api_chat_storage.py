from __future__ import annotations

from labit.api.chat_models import Artifact, Attachment, ChatMessage
from labit.api.chat_storage import format_history


def _message(
    message_id: str,
    role: str,
    content: str,
    *,
    agent: str | None = None,
    artifacts: list[Artifact] | None = None,
    attachments: list[Attachment] | None = None,
) -> ChatMessage:
    return ChatMessage(
        id=message_id,
        role=role,
        content=content,
        agent=agent,
        artifacts=artifacts or [],
        attachments=attachments or [],
        created_at="2026-06-14T00:00:00Z",
    )


def test_format_history_compresses_older_artifacts() -> None:
    older = Artifact(
        id="art-old",
        title="Old draft",
        filename="old.md",
        content="OLD ARTIFACT BODY",
    )
    latest = Artifact(
        id="art-new",
        title="New draft",
        filename="new.md",
        content="NEW ARTIFACT BODY",
    )

    parts = format_history(
        [
            _message("u1", "user", "Write a draft"),
            _message("a1", "assistant", "First draft", agent="claude", artifacts=[older]),
            _message("u2", "user", "Revise it"),
            _message("a2", "assistant", "Updated draft", agent="codex", artifacts=[latest]),
        ]
    )
    prompt = "\n\n".join(parts)

    assert "[Previous artifact: old.md - content omitted from prompt." in prompt
    assert "OLD ARTIFACT BODY" not in prompt
    assert "[Previous artifact: new.md]" in prompt
    assert "NEW ARTIFACT BODY" in prompt
    assert "[End of artifact]" in prompt


def test_format_history_preserves_attachment_labels_when_requested() -> None:
    attachment = Attachment(
        id="att-1",
        filename="figure.png",
        mime_type="image/png",
        path="/tmp/figure.png",
    )

    parts = format_history(
        [_message("u1", "user", "Look at this", attachments=[attachment])],
        include_attachments=True,
    )

    assert parts == ["User: Look at this\n[Attached images: figure.png]"]


def test_format_history_keeps_truncation_notice() -> None:
    parts = format_history(
        [
            _message("u1", "user", "Old"),
            _message("u2", "user", "Recent"),
        ],
        max_history=1,
    )

    assert parts == [
        "[1 earlier messages omitted for brevity]",
        "User: Recent",
    ]
