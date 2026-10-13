"""PDF processing with PDFium (pypdfium2): image extraction, text near images,
page rendering and manual rectangular crops.

Uncertain layouts are flagged rather than guessed:
* pages with no text layer (probably scanned) cannot provide names or IDs;
* images covering most of a page are treated as scans and must be cropped manually;
* very small images (logos, icons) are ignored.
OCR is not included; scanned pages are handled by manual cropping and manual assignment.
"""
from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

from ..errors import HubError

MIN_IMAGE_PX = 48
FULL_PAGE_FRACTION = 0.6


def open_pdf(path: Path, max_pages: int):
    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        raise HubError(f"The PDF could not be opened ({exc}). It may be damaged or password protected.") from None
    if len(pdf) > max_pages:
        n = len(pdf)
        pdf.close()
        raise HubError(f"The PDF has {n} pages, above the limit of {max_pages} set in Settings.")
    return pdf


def analyse(path: Path, max_pages: int) -> tuple[list[dict], list[dict]]:
    """Return (pages, images). Each image dict has bytes, ext, page_no, bbox (PDF units), context_text, flags."""
    pdf = open_pdf(path, max_pages)
    pages, images = [], []
    try:
        for pno in range(len(pdf)):
            page = pdf[pno]
            w, h = page.get_size()
            tp = page.get_textpage()
            text = (tp.get_text_range() or "").strip()
            flags = []
            objs = list(page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=4))
            usable = 0
            if not text:
                flags.append("No text layer on this page (probably scanned). Names and RHC IDs cannot be read automatically.")
            for obj in objs:
                try:
                    l, b, r, t = obj.get_bounds()
                    pw, ph = obj.get_px_size()
                except Exception:
                    continue
                area = max(0.0, (r - l) * (t - b)) / (w * h)
                if pw < MIN_IMAGE_PX or ph < MIN_IMAGE_PX:
                    continue
                if area >= FULL_PAGE_FRACTION:
                    flags.append("A single image covers most of the page (scanned layout). Use manual crop to select each photo.")
                    continue
                data, ext = _extract(obj)
                if data is None:
                    flags.append("An image on this page uses an unsupported format; use manual crop instead.")
                    continue
                ctx_text = ""
                if text:
                    pad_x = max(20.0, (r - l) * 0.25)
                    below = tp.get_text_bounded(l - pad_x, max(0, b - 70), r + pad_x, b) or ""
                    above = tp.get_text_bounded(l - pad_x, t, r + pad_x, min(h, t + 30)) or ""
                    right = tp.get_text_bounded(r, b, min(w, r + 200), t) or ""
                    ctx_text = " ".join(s.strip() for s in (below, above, right) if s.strip())
                img_flags = ["Extracted from PDF"]
                if not ctx_text:
                    img_flags.append("No text found next to this image")
                images.append({
                    "bytes": data, "ext": ext, "page_no": pno + 1,
                    "bbox": [round(l / w, 4), round(1 - t / h, 4), round(r / w, 4), round(1 - b / h, 4)],
                    "context_text": ctx_text[:500], "flags": img_flags,
                })
                usable += 1
            if len(objs) > 40:
                flags.append("Many images on this page; check that each extracted image is a headshot.")
            if not objs and not text:
                flags.append("Blank page or unsupported content.")
            elif not objs:
                flags.append("No embedded images found on this page.")
            pages.append({"page_no": pno + 1, "width": w, "height": h, "text_excerpt": text[:1500],
                          "image_count": usable, "flags": sorted(set(flags))})
            tp.close()
            page.close()
    finally:
        pdf.close()
    return pages, images


def _extract(obj) -> tuple[bytes | None, str]:
    buf = io.BytesIO()
    try:
        obj.extract(buf, fb_format="png")
        data = buf.getvalue()
        if data[:3] == b"\xff\xd8\xff":
            return data, ".jpg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return data, ".png"
    except Exception:
        pass
    try:
        pil = obj.get_bitmap(render=True).to_pil().convert("RGB")
        out = io.BytesIO()
        pil.save(out, "PNG")
        return out.getvalue(), ".png"
    except Exception:
        return None, ""


def render_page(path: Path, page_no: int, scale: float, max_pages: int) -> Image.Image:
    pdf = open_pdf(path, max_pages)
    try:
        if page_no < 1 or page_no > len(pdf):
            raise HubError("That page does not exist.")
        page = pdf[page_no - 1]
        img = page.render(scale=scale).to_pil().convert("RGB")
        page.close()
        return img
    finally:
        pdf.close()


def crop_region(path: Path, page_no: int, rect: list[float], max_pages: int) -> tuple[bytes, str]:
    """Crop a rectangle given as fractions of the page (x0, y0, x1, y1 from the top left). Returns (png, nearby text)."""
    x0, y0, x1, y1 = [float(v) for v in rect]
    x0, x1 = sorted((max(0.0, x0), min(1.0, x1)))
    y0, y1 = sorted((max(0.0, y0), min(1.0, y1)))
    if (x1 - x0) < 0.01 or (y1 - y0) < 0.01:
        raise HubError("The selected area is too small.")
    scale = 300 / 72
    img = render_page(path, page_no, scale, max_pages)
    W, H = img.size
    crop = img.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
    out = io.BytesIO()
    crop.save(out, "PNG")
    text = ""
    pdf = open_pdf(path, max_pages)
    try:
        page = pdf[page_no - 1]
        w, h = page.get_size()
        tp = page.get_textpage()
        text = tp.get_text_bounded(x0 * w - 20, max(0, (1 - y1) * h - 70), x1 * w + 20, (1 - y0) * h) or ""
        tp.close()
        page.close()
    finally:
        pdf.close()
    return out.getvalue(), text.strip()[:500]
