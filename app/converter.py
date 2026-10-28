"""PDF to image conversion with optional target file-size control."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal

import pymupdf as fitz
from PIL import Image

OutputFormat = Literal["png", "jpg", "jpeg"]


@dataclass
class PageResult:
    page_index: int
    filename: str
    data: bytes
    width: int
    height: int
    size_bytes: int
    quality_used: int | None


@dataclass
class ConvertOptions:
    fmt: OutputFormat = "png"
    dpi: int = 150
    pages: list[int] | None = None  # 1-based; None = all
    target_size_kb: float | None = None  # per-page target; None = no limit
    jpeg_quality: int = 85  # used when no target size
    filename_prefix: str = "page"


def _normalize_format(fmt: str) -> OutputFormat:
    f = fmt.lower().strip()
    if f == "jpeg":
        return "jpeg"
    if f == "jpg":
        return "jpg"
    if f == "png":
        return "png"
    raise ValueError(f"Unsupported format: {fmt}")


def _pil_format(fmt: OutputFormat) -> str:
    return "JPEG" if fmt in ("jpg", "jpeg") else "PNG"


def _ext(fmt: OutputFormat) -> str:
    return "jpg" if fmt == "jpeg" else fmt


def _encode_image(
    img: Image.Image,
    fmt: OutputFormat,
    *,
    quality: int = 85,
    png_compress: int = 6,
) -> bytes:
    buf = io.BytesIO()
    if fmt in ("jpg", "jpeg"):
        rgb = img.convert("RGB")
        rgb.save(buf, format="JPEG", quality=quality, optimize=True)
    else:
        # PNG: keep alpha if present
        if img.mode not in ("RGB", "RGBA", "L", "P"):
            img = img.convert("RGBA")
        img.save(buf, format="PNG", optimize=True, compress_level=png_compress)
    return buf.getvalue()


def _fit_jpeg_size(img: Image.Image, target_bytes: int, fmt: OutputFormat) -> tuple[bytes, int]:
    """Binary-search JPEG quality to approach target size (may undershoot/overshoot slightly)."""
    lo, hi = 5, 95
    best = _encode_image(img, fmt, quality=85)
    best_q = 85

    # If even lowest quality is too big, scale down iteratively
    working = img
    scale = 1.0
    for _ in range(8):
        data = _encode_image(working, fmt, quality=5)
        if len(data) <= target_bytes or scale < 0.25:
            break
        scale *= 0.85
        new_w = max(1, int(img.width * scale))
        new_h = max(1, int(img.height * scale))
        working = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    lo, hi = 5, 95
    best = _encode_image(working, fmt, quality=5)
    best_q = 5
    while lo <= hi:
        mid = (lo + hi) // 2
        data = _encode_image(working, fmt, quality=mid)
        if len(data) <= target_bytes:
            best, best_q = data, mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best, best_q


def _fit_png_size(img: Image.Image, target_bytes: int) -> tuple[bytes, int | None]:
    """Shrink / quantize PNG until at or under target size."""
    working = img
    # Try compress levels first
    for level in (9, 6, 3):
        data = _encode_image(working, "png", png_compress=level)
        if len(data) <= target_bytes:
            return data, None

    # Palette quantization then scale down
    scale = 1.0
    for _ in range(12):
        candidate = working
        if candidate.mode == "RGBA":
            # flatten on white for palette path if still too large after try
            bg = Image.new("RGB", candidate.size, (255, 255, 255))
            bg.paste(candidate, mask=candidate.split()[-1])
            candidate = bg
        elif candidate.mode != "RGB" and candidate.mode != "L":
            candidate = candidate.convert("RGB")

        for colors in (256, 128, 64, 32):
            try:
                quantized = candidate.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
                data = _encode_image(quantized.convert("P"), "png", png_compress=9)
            except Exception:
                data = _encode_image(candidate, "png", png_compress=9)
            if len(data) <= target_bytes:
                return data, None

        scale *= 0.85
        new_w = max(1, int(img.width * scale))
        new_h = max(1, int(img.height * scale))
        working = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    return _encode_image(working, "png", png_compress=9), None


def render_pdf_page(doc: fitz.Document, page_index: int, dpi: int) -> Image.Image:
    page = doc.load_page(page_index)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    mode = "RGB" if pix.n < 4 else "RGBA"
    img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    return img


def get_pdf_info(pdf_bytes: bytes) -> dict:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = []
        for i in range(len(doc)):
            page = doc.load_page(i)
            r = page.rect
            pages.append(
                {
                    "index": i + 1,
                    "width_pt": round(r.width, 2),
                    "height_pt": round(r.height, 2),
                }
            )
        return {"page_count": len(doc), "pages": pages}
    finally:
        doc.close()


def convert_pdf(pdf_bytes: bytes, options: ConvertOptions) -> list[PageResult]:
    fmt = _normalize_format(options.fmt)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    results: list[PageResult] = []
    try:
        total = len(doc)
        if options.pages:
            indices = [p - 1 for p in options.pages if 1 <= p <= total]
        else:
            indices = list(range(total))

        target_bytes = (
            int(options.target_size_kb * 1024) if options.target_size_kb and options.target_size_kb > 0 else None
        )
        ext = _ext(fmt)

        for i in indices:
            img = render_pdf_page(doc, i, options.dpi)
            quality_used: int | None = None

            if target_bytes is not None:
                if fmt in ("jpg", "jpeg"):
                    data, quality_used = _fit_jpeg_size(img, target_bytes, fmt)
                else:
                    data, quality_used = _fit_png_size(img, target_bytes)
            else:
                if fmt in ("jpg", "jpeg"):
                    quality_used = max(1, min(95, options.jpeg_quality))
                    data = _encode_image(img, fmt, quality=quality_used)
                else:
                    data = _encode_image(img, fmt)

            # Re-open to report final dimensions after possible resize
            with Image.open(io.BytesIO(data)) as out_img:
                w, h = out_img.size

            name = f"{options.filename_prefix}_{i + 1:03d}.{ext}"
            results.append(
                PageResult(
                    page_index=i + 1,
                    filename=name,
                    data=data,
                    width=w,
                    height=h,
                    size_bytes=len(data),
                    quality_used=quality_used,
                )
            )
        return results
    finally:
        doc.close()


def render_preview_jpeg(pdf_bytes: bytes, page_index: int = 0, dpi: int = 72) -> bytes:
    """Low-res JPEG preview for UI."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_index = max(0, min(page_index, len(doc) - 1))
        img = render_pdf_page(doc, page_index, dpi)
        return _encode_image(img, "jpg", quality=70)
    finally:
        doc.close()
