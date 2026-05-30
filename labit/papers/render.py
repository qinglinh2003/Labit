"""Server-side PDF page rendering via PyMuPDF.

Generates WebP page images and a JSON manifest so the frontend can display
PDF pages as <img> elements — no PDF.js needed on the critical path.
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

# Default widths (px) for generated renders.
WIDTHS = [900, 1400, 1600]
THUMB_WIDTH = 240
WEBP_QUALITY = 72
# First viewport tile height in CSS px. At DPR=2 this renders to 1200 px
# tall, which keeps typical text-first arXiv pages near the 80-120KB budget.
TILE_HEIGHT_CSS = 600
DPR = 2
MANIFEST_VERSION = 3


def render_page_image(
    pdf_path: Path,
    out_path: Path,
    page_index: int,
    width_px: int,
    *,
    fmt: str = "WEBP",
    quality: int = WEBP_QUALITY,
    clip_rect: fitz.Rect | None = None,
) -> int:
    """Render a single PDF page (or clip region) to an image file.

    Returns the file size in bytes.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    page = doc[page_index]

    zoom = width_px / page.rect.width
    mat = fitz.Matrix(zoom, zoom)

    pix = page.get_pixmap(matrix=mat, alpha=False, clip=clip_rect)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()

    buf = BytesIO()
    img.save(buf, fmt, quality=quality, method=4)
    data = buf.getvalue()

    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(out_path)
    return len(data)


def generate_page1_cache(pdf_path: Path, renders_dir: Path) -> dict:
    """Generate page-1 render cache for a PDF.

    Creates:
      - p1-thumb.webp           (sidebar thumbnail)
      - p1-fit-w900.webp        (low-res fallback)
      - p1-fit-w1400.webp       (normal display)
      - p1-fit-w1600.webp       (Retina)
      - p1-tile-y0-h900-w1600.webp  (first viewport tile, DPR=2)
      - manifest.json

    Returns the manifest dict.
    """
    renders_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    page_count = len(doc)
    page = doc[0]
    page_width_pt = page.rect.width
    page_height_pt = page.rect.height
    doc.close()

    renders: dict[str, str] = {}

    # Thumbnail
    render_page_image(pdf_path, renders_dir / "p1-thumb.webp", 0, THUMB_WIDTH)
    renders["thumb"] = "p1-thumb.webp"

    # Fit-width at various sizes
    for w in WIDTHS:
        name = f"p1-fit-w{w}.webp"
        render_page_image(pdf_path, renders_dir / name, 0, w)
        renders[f"fit_w{w}"] = name

    # First viewport tile — clip to top portion only
    tile_w = 1600
    zoom = tile_w / page_width_pt
    # Tile covers TILE_HEIGHT_CSS CSS px. At this zoom, that's:
    tile_clip_height_pt = min((TILE_HEIGHT_CSS * DPR) / zoom, page_height_pt)
    clip = fitz.Rect(0, 0, page_width_pt, tile_clip_height_pt)
    tile_name = f"p1-tile-y0-h{TILE_HEIGHT_CSS}-w{tile_w}.webp"
    render_page_image(
        pdf_path, renders_dir / tile_name, 0, tile_w, clip_rect=clip
    )
    renders["first_viewport_tile"] = tile_name

    # Build manifest
    css_width = int(tile_w / DPR)
    aspect = page_height_pt / page_width_pt
    css_height = int(css_width * aspect)
    tile_aspect = tile_clip_height_pt / page_width_pt
    tile_css_height = min(int(css_width * tile_aspect), css_height)

    manifest = {
        "version": MANIFEST_VERSION,
        "page_count": page_count,
        "pages": [
            {
                "page": 1,
                "width_pt": page_width_pt,
                "height_pt": page_height_pt,
                "css_width": css_width,
                "css_height": css_height,
                "first_tile_css_height": tile_css_height,
                "thumb": renders["thumb"],
                "preview": renders["fit_w900"],
                "retina": renders["fit_w1600"],
                "first_viewport_tile": renders["first_viewport_tile"],
            }
        ],
    }

    manifest_path = renders_dir / "manifest.json"
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    tmp.replace(manifest_path)

    return manifest


def render_page(pdf_path: Path, renders_dir: Path, page_index: int, width: int = 1600) -> str:
    """Render a single page at the given width. Returns the filename."""
    name = f"p{page_index + 1}-fit-w{width}.webp"
    render_page_image(pdf_path, renders_dir / name, page_index, width)
    return name


def load_manifest(renders_dir: Path) -> dict | None:
    """Load manifest.json if it exists."""
    manifest_path = renders_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def is_manifest_current(manifest: dict | None) -> bool:
    """Return True when the cached manifest matches the current render format."""
    return manifest is not None and manifest.get("version") == MANIFEST_VERSION
