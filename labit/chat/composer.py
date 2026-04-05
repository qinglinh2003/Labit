from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

from labit.chat.models import ChatAttachment
from labit.paths import RepoPaths

if TYPE_CHECKING:
    from rich.console import Console


ACCENT_COLOR = "#a0a000"


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
    slash_commands: Iterable[str] | None = None,
) -> ComposerResult:
    try:
        from prompt_toolkit.enums import EditingMode
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style
    except Exception:
        raw = input()
        return ComposerResult(text=raw)

    attachments: list[ChatAttachment] = []
    commands = tuple(sorted(set(slash_commands or ())))
    prompt_session = PromptSession()
    bindings = KeyBindings()
    style = Style.from_dict(
        {
            "frame.border": ACCENT_COLOR,
            "frame.label": f"bold {ACCENT_COLOR}",
            "prompt": f"bold {ACCENT_COLOR}",
            "placeholder": "#7a7a7a italic",
        }
    )

    @bindings.add("enter")
    def _submit(event) -> None:
        event.app.exit(result=event.app.current_buffer.text)

    @bindings.add("tab")
    def _complete_if_unique(event) -> None:
        buffer = event.app.current_buffer
        text = buffer.document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        matches = [command for command in commands if command.startswith(text)]
        if len(matches) == 1:
            buffer.delete_before_cursor(count=len(text))
            buffer.insert_text(matches[0])

    @bindings.add("c-c")
    def _cancel(event) -> None:
        raise KeyboardInterrupt

    raw = prompt_session.prompt(
        HTML(f"<prompt>{prompt_prefix}</prompt>"),
        key_bindings=bindings,
        style=style,
        placeholder=HTML("<placeholder>Ask a question or paste an image...</placeholder>"),
        show_frame=True,
        editing_mode=EditingMode.EMACS,
    )
    if attachments and not raw.strip():
        raw = "Please inspect the attached image and describe anything important."
    return ComposerResult(text=raw, attachments=attachments)
