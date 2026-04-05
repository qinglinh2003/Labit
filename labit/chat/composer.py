from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from labit.chat.clipboard import ClipboardImageError, capture_clipboard_image
from labit.chat.models import ChatAttachment
from labit.paths import RepoPaths

if TYPE_CHECKING:
    from rich.console import Console


COMPOSER_HELP_TEXT = "Ctrl-V attaches one clipboard image. Enter sends."


@dataclass
class ComposerResult:
    text: str
    attachments: list[ChatAttachment] = field(default_factory=list)


def prompt_toolkit_available() -> bool:
    try:
        import prompt_toolkit  # noqa: F401
    except Exception:
        return False
    return True


def prompt_with_clipboard_image(
    *,
    console: Console,
    paths: RepoPaths,
    session_id: str,
    prompt_prefix: str = " › ",
    show_frame: bool = False,
) -> ComposerResult:
    try:
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style
    except Exception:
        raw = input()
        return ComposerResult(text=raw)

    attachments: list[ChatAttachment] = []
    status_message = COMPOSER_HELP_TEXT
    prompt_session = PromptSession()
    bindings = KeyBindings()
    style = Style.from_dict(
        {
            "frame.border": "#a0a000",
            "frame.label": "bold #a0a000",
            "bottom-toolbar": "fg:#d0d0d0 bg:#303030",
            "prompt": "bold #a0a000",
            "placeholder": "#7a7a7a",
        }
    )

    def _toolbar():
        suffix = ""
        if attachments:
            labels = ", ".join(attachment.label or "image" for attachment in attachments[-2:])
            if len(attachments) > 2:
                labels = f"{labels}, +{len(attachments) - 2} more"
            suffix = f" | Attached: {labels}"
        return HTML(f"<bottom-toolbar> {status_message}{suffix} </bottom-toolbar>")

    @bindings.add("c-v")
    def _paste_image(event) -> None:
        nonlocal status_message
        try:
            attachment = capture_clipboard_image(paths=paths, session_id=session_id)
        except ClipboardImageError as exc:
            status_message = f"Paste failed: {exc}"
            event.app.invalidate()
            return
        attachments.append(attachment)
        status_message = f"Attached image: {attachment.label or attachment.path.rsplit('/', 1)[-1]}"
        event.app.invalidate()

    raw = prompt_session.prompt(
        HTML(f"<prompt>{prompt_prefix}</prompt>"),
        key_bindings=bindings,
        bottom_toolbar=_toolbar,
        placeholder=HTML("<placeholder>Type a message or paste an image…</placeholder>"),
        style=style,
        show_frame=show_frame,
    )
    if attachments and not raw.strip():
        raw = "Please inspect the attached image and describe anything important."
    return ComposerResult(text=raw, attachments=attachments)
