from __future__ import annotations

import base64
import subprocess
import sys
import textwrap
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from labit.chat.models import AttachmentKind, ChatAttachment
from labit.paths import RepoPaths


class ClipboardImageError(RuntimeError):
    """Raised when LABIT cannot read an image from the clipboard."""


def capture_clipboard_image(*, paths: RepoPaths, session_id: str) -> ChatAttachment:
    attachments_dir = paths.conversations_dir / session_id / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)
    filename = f"clipboard-{uuid4().hex[:10]}.png"
    output_path = attachments_dir / filename

    if sys.platform != "darwin":
        raise ClipboardImageError("Clipboard image paste is currently supported only on macOS.")

    _capture_clipboard_image_macos(output_path)
    return ChatAttachment(
        kind=AttachmentKind.IMAGE,
        path=str(output_path.resolve()),
        label=filename,
        mime_type="image/png",
        source="clipboard",
    )


def _capture_clipboard_image_macos(output_path: Path) -> None:
    tiff_path = output_path.with_suffix(".tiff")
    jxa_source = textwrap.dedent(
        """
        ObjC.import('AppKit')

        function run(argv) {
            const pb = $.NSPasteboard.generalPasteboard

            const png = pb.dataForType($.NSPasteboardTypePNG)
            if (png && !png.isNil()) {
                return `PNG:${ObjC.unwrap(png.base64EncodedStringWithOptions(0))}`
            }

            const tiff = pb.dataForType($.NSPasteboardTypeTIFF)
            if (tiff && !tiff.isNil()) {
                return `TIFF:${ObjC.unwrap(tiff.base64EncodedStringWithOptions(0))}`
            }

            throw new Error('NO_IMAGE')
        }
        """
    ).strip()

    with NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(jxa_source)
        script_path = Path(handle.name)

    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", str(script_path), str(output_path), str(tiff_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except subprocess.TimeoutExpired as exc:
        output_path.unlink(missing_ok=True)
        tiff_path.unlink(missing_ok=True)
        raise ClipboardImageError("Timed out while reading an image from the clipboard.") from exc
    finally:
        script_path.unlink(missing_ok=True)

    stdout = (result.stdout or "").strip()
    if result.returncode == 0 and stdout.startswith("PNG:"):
        payload = stdout.removeprefix("PNG:")
        output_path.write_bytes(base64.b64decode(payload))
        return

    if result.returncode == 0 and stdout.startswith("TIFF:"):
        payload = stdout.removeprefix("TIFF:")
        tiff_path.write_bytes(base64.b64decode(payload))
        convert = subprocess.run(
            ["sips", "-s", "format", "png", str(tiff_path), "--out", str(output_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        tiff_path.unlink(missing_ok=True)
        if convert.returncode == 0 and output_path.exists():
            return
        output_path.unlink(missing_ok=True)
        detail = (convert.stderr or convert.stdout or "").strip()
        raise ClipboardImageError(f"Failed to convert clipboard TIFF image to PNG: {detail or 'unknown error'}")

    output_path.unlink(missing_ok=True)
    tiff_path.unlink(missing_ok=True)

    detail = (result.stderr or result.stdout or "").strip()
    if "NO_IMAGE" in detail:
        raise ClipboardImageError("Clipboard does not currently contain an image.")
    raise ClipboardImageError(f"Failed to capture clipboard image: {detail or 'unknown error'}")
