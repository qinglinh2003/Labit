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


_prompt_session_instance: object | None = None


def _get_prompt_session():
    """Return a module-level PromptSession so input history persists across calls."""
    global _prompt_session_instance
    if _prompt_session_instance is None:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory

        _prompt_session_instance = PromptSession(history=InMemoryHistory())
    return _prompt_session_instance


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
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style
    except Exception:
        raw = input()
        return ComposerResult(text=raw)

    attachments: list[ChatAttachment] = []
    commands = tuple(sorted(set(slash_commands or ())))
    prompt_session = _get_prompt_session()
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
        event.current_buffer.validate_and_handle()

    @bindings.add("s-enter")
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

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
        multiline=True,
        mouse_support=True,
    )
    if attachments and not raw.strip():
        raw = "Please inspect the attached image and describe anything important."
    return ComposerResult(text=raw, attachments=attachments)
