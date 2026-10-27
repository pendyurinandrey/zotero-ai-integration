"""Общие функции для сборки PDF из картинок страниц: растеризация страниц и починка PNG внутри SVG.

Нужен pymupdf (окружение zotero-mcp-server). Используется svg_to_pdf.py, rasterize_pdf.py и prepare_books.py.
"""
import base64
import re
import struct
import zlib

import pymupdf as fitz

fitz.TOOLS.mupdf_display_errors(False)

PNG_URI = re.compile(r'(data:image/png;base64,)([^"\']*)(["\'])')


def _png_chunks(raw: bytes):
    i = 8
    while i + 8 <= len(raw):
        n, t = struct.unpack(">I4s", raw[i:i + 8])
        yield t, raw[i + 8:i + 8 + n], raw[i:i + 12 + n]
        i += 12 + n


def _decode_png(raw: bytes):
    """Разбирает PNG без внешних библиотек (8 бит, без чересстрочности: серый, RGB, с альфой или без).
    Возвращает pymupdf.Pixmap или None."""
    width = height = depth = ctype = interlace = None
    idat = b""
    for t, data, _ in _png_chunks(raw):
        if t == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", data)
        elif t == b"IDAT":
            idat += data
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
    if not width or depth != 8 or interlace or channels is None:
        return None
    stride = width * channels
    data = zlib.decompressobj().decompress(idat)
    if len(data) < (stride + 1) * height:
        return None
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        f = data[pos]
        line = bytearray(data[pos + 1:pos + 1 + stride])
        pos += stride + 1
        if f == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out += line
        prev = line
    if channels == 2:                       # серый с альфой: альфу отбрасываем
        out = bytearray(out[0::2])
        channels = 1
    elif channels == 4:
        keep = bytearray()
        for i in range(0, len(out), 4):
            keep += out[i:i + 3]
        out, channels = keep, 3
    cs = fitz.csGRAY if channels == 1 else fitz.csRGB
    return fitz.Pixmap(cs, width, height, bytes(out), False)


def repair_png(raw: bytes) -> bytes:
    """Возвращает PNG, который MuPDF читает. Часть PNG, встроенных в SVG, MuPDF отвергает («premature end of data»),
    хотя данные целы. Порядок: как есть, без блока iCCP, затем разбор своим декодером и сохранение заново."""
    try:
        fitz.Pixmap(raw)
        return raw
    except Exception:  # noqa: BLE001
        pass
    stripped = raw[:8] + b"".join(c for t, _, c in _png_chunks(raw) if t != b"iCCP")
    try:
        fitz.Pixmap(stripped)
        return stripped
    except Exception:  # noqa: BLE001
        pass
    pm = _decode_png(raw)
    return pm.tobytes("png") if pm is not None else raw


def fix_svg(text: str):
    """Чинит встроенные PNG в тексте SVG. Возвращает (новый текст, сколько картинок пересохранено, сколько не удалось)."""
    stats = {"fixed": 0, "failed": 0}

    def sub(m):
        raw = base64.b64decode(re.sub(r"\s", "", m.group(2)))
        new = repair_png(raw)
        try:
            fitz.Pixmap(new)
        except Exception:  # noqa: BLE001
            stats["failed"] += 1
            return m.group(0)
        if new is not raw:
            stats["fixed"] += 1
        return m.group(1) + base64.b64encode(new).decode() + m.group(3)

    if "data:image/png" not in text:
        return text, 0, 0
    return PNG_URI.sub(sub, text), stats["fixed"], stats["failed"]


def rasterize_page(page, dpi: int = 260, quality: int = 55, gray: bool = True) -> bytes:
    cs = fitz.csGRAY if gray else fitz.csRGB
    return page.get_pixmap(dpi=dpi, colorspace=cs).tobytes("jpeg", jpg_quality=quality)


def rasterize_pages(pages, dst, dpi: int = 260, quality: int = 55, gray: bool = True, progress=None):
    """pages: итератор страниц (pymupdf.Page). Собирает PDF из серых JPEG и сохраняет в dst. Возвращает число страниц."""
    out = fitz.open()
    n = 0
    for p in pages:
        img = rasterize_page(p, dpi, quality, gray)
        q = out.new_page(width=p.rect.width, height=p.rect.height)
        q.insert_image(q.rect, stream=img)
        n += 1
        if progress and n % 50 == 0:
            progress(n)
    out.save(str(dst), garbage=3, deflate=True)
    return n
