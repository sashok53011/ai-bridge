"""Рендер страниц PDF в картинки (PNG).

Чистая функция «байты → список байтов»: никакой сети, файлов, времени.
PyMuPDF (pymupdf) — единственная зависимость: без неё рендерер недоступен
и приставка честно отказывает в PDF (JPG/PNG всё равно принимаются).
"""

from __future__ import annotations

from io import BytesIO

MAX_SIDE = 1024
DEFAULT_DPI = 200


def render_pdf(data: bytes, *, dpi: int = DEFAULT_DPI, max_side: int = MAX_SIDE) -> list[bytes]:
    """Каждая страница PDF → PNG (обрезка по длинной стороне).

    Возвращает список PNG-байтов, по одной странице. Пустой/битый PDF
    даёт пустой список (вызывающий покажет ошибку).
    """
    if not data:
        return []
    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError:
        raise RuntimeError("PDF не поддержан на этом сервере (нет pymupdf)")

    out: list[bytes] = []
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        for page in doc:
            # матрица масштаба: zoom = dpi / 72 (72 — базовый dpi PDF).
            zoom = dpi / 72.0
            matrix = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            if pix.width > max_side or pix.height > max_side:
                scale = max_side / max(pix.width, pix.height)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom * scale, zoom * scale),
                                     alpha=False)
            out.append(pix.tobytes("png"))
    finally:
        doc.close()
    return out