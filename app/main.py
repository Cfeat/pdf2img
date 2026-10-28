"""PDF → Image web service."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.converter import ConvertOptions, convert_pdf, get_pdf_info, render_preview_jpeg

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

app = FastAPI(title="PDF2Img", description="PDF 转图片工具")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _safe_stem(name: str) -> str:
    stem = Path(name).stem.strip() or "page"
    # Keep CJK and common chars for ZIP member names; strip path separators
    stem = re.sub(r'[\\/:*?"<>|]+', "_", stem)
    return stem[:80] or "page"


def _content_disposition(filename: str) -> str:
    """RFC 5987 header safe for non-ASCII (e.g. Chinese) filenames."""
    ascii_name = filename.encode("ascii", "ignore").decode("ascii").strip() or "download.bin"
    ascii_name = ascii_name.replace('"', "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.post("/api/info")
async def pdf_info(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "请上传 PDF 文件")
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件为空")
    try:
        info = get_pdf_info(data)
    except Exception as e:
        raise HTTPException(400, f"无法解析 PDF：{e}") from e
    return {"filename": file.filename, **info}


@app.post("/api/preview")
async def preview(
    file: UploadFile = File(...),
    page: int = Form(1),
) -> Response:
    data = await file.read()
    try:
        jpeg = render_preview_jpeg(data, page_index=max(0, page - 1), dpi=96)
    except Exception as e:
        raise HTTPException(400, f"预览失败：{e}") from e
    return Response(content=jpeg, media_type="image/jpeg")


@app.post("/api/convert")
async def convert(
    file: UploadFile = File(...),
    fmt: str = Form("png"),
    dpi: int = Form(150),
    pages: str = Form(""),  # e.g. "1,2,5-8" or empty for all
    target_size_kb: float = Form(0),
    jpeg_quality: int = Form(85),
    as_zip: bool = Form(True),
) -> Response:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "请上传 PDF 文件")
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件为空")

    fmt = fmt.lower().strip()
    if fmt not in ("png", "jpg", "jpeg"):
        raise HTTPException(400, "格式仅支持 png / jpg / jpeg")

    dpi = max(36, min(600, dpi))
    jpeg_quality = max(1, min(95, jpeg_quality))
    target = target_size_kb if target_size_kb and target_size_kb > 0 else None

    page_list = _parse_pages(pages) if pages.strip() else None
    stem = _safe_stem(file.filename)

    try:
        results = convert_pdf(
            data,
            ConvertOptions(
                fmt=fmt,  # type: ignore[arg-type]
                dpi=dpi,
                pages=page_list,
                target_size_kb=target,
                jpeg_quality=jpeg_quality,
                filename_prefix=stem,
            ),
        )
    except Exception as e:
        raise HTTPException(400, f"转换失败：{e}") from e

    if not results:
        raise HTTPException(400, "没有可转换的页面")

    if len(results) == 1 and not as_zip:
        r = results[0]
        media = "image/png" if fmt == "png" else "image/jpeg"
        return Response(
            content=r.data,
            media_type=media,
            headers={
                "Content-Disposition": _content_disposition(r.filename),
                "X-Image-Width": str(r.width),
                "X-Image-Height": str(r.height),
                "X-Image-Size": str(r.size_bytes),
            },
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        meta_lines = ["page,filename,width,height,size_bytes,quality"]
        for r in results:
            # Flag UTF-8 filenames so Chinese names extract correctly
            info = zipfile.ZipInfo(r.filename)
            info.flag_bits |= 0x800
            zf.writestr(info, r.data)
            meta_lines.append(
                f"{r.page_index},{r.filename},{r.width},{r.height},{r.size_bytes},{r.quality_used or ''}"
            )
        zf.writestr("manifest.csv", "\n".join(meta_lines) + "\n")
    buf.seek(0)

    zip_name = f"{stem}_images.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(zip_name)},
    )


@app.post("/api/convert-meta")
async def convert_meta(
    file: UploadFile = File(...),
    fmt: str = Form("png"),
    dpi: int = Form(150),
    pages: str = Form(""),
    target_size_kb: float = Form(0),
    jpeg_quality: int = Form(85),
) -> dict:
    """Convert and return metadata + base64 for small batches (preview of sizes)."""
    import base64

    data = await file.read()
    fmt = fmt.lower().strip()
    if fmt not in ("png", "jpg", "jpeg"):
        raise HTTPException(400, "格式仅支持 png / jpg / jpeg")

    dpi = max(36, min(600, dpi))
    jpeg_quality = max(1, min(95, jpeg_quality))
    target = target_size_kb if target_size_kb and target_size_kb > 0 else None
    page_list = _parse_pages(pages) if pages.strip() else None
    stem = _safe_stem(file.filename or "page")

    try:
        results = convert_pdf(
            data,
            ConvertOptions(
                fmt=fmt,  # type: ignore[arg-type]
                dpi=dpi,
                pages=page_list,
                target_size_kb=target,
                jpeg_quality=jpeg_quality,
                filename_prefix=stem,
            ),
        )
    except Exception as e:
        raise HTTPException(400, f"转换失败：{e}") from e

    # Cap payload: only embed images if few and small
    embed = len(results) <= 5 and all(r.size_bytes < 2_000_000 for r in results)
    items = []
    for r in results:
        item = {
            "page": r.page_index,
            "filename": r.filename,
            "width": r.width,
            "height": r.height,
            "size_bytes": r.size_bytes,
            "size_kb": round(r.size_bytes / 1024, 2),
            "quality_used": r.quality_used,
        }
        if embed:
            mime = "image/png" if fmt == "png" else "image/jpeg"
            item["data_url"] = f"data:{mime};base64,{base64.b64encode(r.data).decode('ascii')}"
        items.append(item)

    return {
        "count": len(results),
        "total_bytes": sum(r.size_bytes for r in results),
        "embedded": embed,
        "items": items,
    }


def _parse_pages(spec: str) -> list[int]:
    """Parse '1,3,5-8' into sorted unique 1-based page numbers."""
    pages: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
            if start > end:
                start, end = end, start
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    return sorted(p for p in pages if p >= 1)
