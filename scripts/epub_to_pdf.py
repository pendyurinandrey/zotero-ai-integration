#!/usr/bin/env python3
"""Переводит EPUB в постраничный PDF с настоящим текстом, чтобы его читал индексатор zotero-mcp.

Индексатор читает PDF, HTML и обычный текст; EPUB он сам не разбирает (при наличии у Zotero собственного кэша
берёт плоский текст оттуда, без страниц и без гарантий качества). В EPUB нет фиксированных страниц, поэтому
скрипт раскладывает книгу на страницы заданного размера (по умолчанию 6x9 дюймов, шрифт 10,5 pt) средствами
PyMuPDF, переносит оглавление в закладки PDF и записывает метаданные из файла EPUB (название, подзаголовок,
все авторы, издательство).

Номера страниц в получившемся PDF свои, а не книжные: в индексе «стр.» будет страницей этого PDF. Если важны
печатные страницы, используйте PDF-версию книги.

Известное ограничение. Движок раскладки PyMuPDF (MuPDF) не вписывает картинки в страницу: слишком высокая
картинка выходит за нижний край и обрезается, а текст местами налезает на картинку (на проверенной книге с 118
рисунками обрезано 14 картинок, на 89 страницах из 274 текст пересекается с картинкой; смена размера страницы
и шрифта этого не устраняет). Скрипт после раскладки считает такие случаи и предупреждает. Для поиска нужен
текст, а не рисунки, поэтому есть режим --text-only: картинки убираются (остаётся их подпись alt в квадратных
скобках), и вёрстка получается чистой.

Запускается через epub-to-pdf.sh. Исходный файл не изменяется.

Коды выхода: 0 - успех; 2 - ошибка (файл не открывается, не EPUB, защищён DRM, выход уже существует).
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf as fitz

fitz.TOOLS.mupdf_display_errors(False)

NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container", "o": "http://www.idpf.org/2007/opf",
      "dc": "http://purl.org/dc/elements/1.1/"}


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="EPUB-файл")
    ap.add_argument("output", help="куда записать PDF (не должен совпадать с input)")
    ap.add_argument("--width", type=float, default=432, help="ширина страницы в pt (по умолчанию 432 = 6 дюймов)")
    ap.add_argument("--height", type=float, default=648, help="высота страницы в pt (по умолчанию 648 = 9 дюймов)")
    ap.add_argument("--fontsize", type=float, default=10.5, help="размер основного шрифта в pt (по умолчанию 10,5)")
    ap.add_argument("--title", help="название для метаданных (по умолчанию из EPUB)")
    ap.add_argument("--author", help="автор(ы) для метаданных (по умолчанию из EPUB)")
    ap.add_argument("--text-only", action="store_true",
                    help="убрать картинки (останется подпись alt): без обрезанных рисунков и наложений текста")
    ap.add_argument("--force", action="store_true", help="перезаписать output, если он уже есть")
    return ap.parse_args()


def read_opf(path: Path) -> dict:
    """Название, авторы, язык, издательство и дата из пакета OPF (то, что PyMuPDF отдаёт неполно: один автор)."""
    info = {"title": "", "authors": [], "language": "", "publisher": "", "date": ""}
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("META-INF/container.xml"))
            opf_path = root.find(".//c:rootfile", NS).attrib["full-path"]
            opf = ET.fromstring(z.read(opf_path))
        titles = [(t.text or "").strip() for t in opf.findall(".//dc:title", NS) if (t.text or "").strip()]
        info["title"] = ": ".join(titles)
        info["authors"] = [(a.text or "").strip() for a in opf.findall(".//dc:creator", NS) if (a.text or "").strip()]
        for key in ("language", "publisher", "date"):
            el = opf.find(f".//dc:{key}", NS)
            info[key] = (el.text or "").strip() if el is not None else ""
    except Exception:  # noqa: BLE001
        pass  # метаданные необязательны: возьмём то, что отдаёт PyMuPDF
    return info


def strip_images(src: Path) -> bytes:
    """Копия EPUB без тегов <img> и <svg>: вместо картинки остаётся её подпись alt в квадратных скобках."""
    import io
    out = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.lower().endswith((".xhtml", ".html", ".htm")):
                html = data.decode("utf-8", errors="replace")
                html = re.sub(r"<svg\b.*?</svg>", "", html, flags=re.S)
                html = re.sub(r"<img\b[^>]*?alt=\"([^\"]*)\"[^>]*>",
                              lambda m: f"[{m.group(1)}]" if m.group(1).strip() and "/" not in m.group(1) else "", html)
                html = re.sub(r"<img\b[^>]*>", "", html)
                data = html.encode("utf-8")
            zout.writestr(info, data, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype"
                          else zipfile.ZIP_DEFLATED)
    return out.getvalue()


def layout_problems(pdf):
    """Сколько картинок вышло за край страницы и на скольких страницах текст пересекается с картинкой."""
    cut, overlapped, images = 0, 0, 0
    for page in pdf:
        boxes = [fitz.Rect(im["bbox"]) for im in page.get_image_info()]
        images += len(boxes)
        full = page.rect
        cut += sum(1 for b in boxes if b.x1 > full.width + 1 or b.x0 < -1 or b.y1 > full.height + 1 or b.y0 < -1)
        for blk in page.get_text("blocks"):
            r = fitz.Rect(blk[:4])
            if r.get_area() > 0 and any((r & b).get_area() > 0.3 * r.get_area() for b in boxes):
                overlapped += 1
                break
    return images, cut, overlapped


def fix_toc(toc):
    """PyMuPDF требует, чтобы оглавление начиналось с уровня 1 и уровни не перескакивали больше чем на 1."""
    out, prev = [], 0
    for lvl, title, page in toc:
        lvl = max(1, min(lvl, prev + 1))
        out.append([lvl, title or "(без названия)", max(1, page)])
        prev = lvl
    return out


def main() -> int:
    args = parse_args()
    src, dst = Path(args.input), Path(args.output)
    if not src.is_file():
        print(f"Не найден файл: {src}", file=sys.stderr)
        return 2
    if src.resolve() == dst.resolve():
        print("Выходной файл должен отличаться от входного", file=sys.stderr)
        return 2
    if dst.exists() and not args.force:
        print(f"Файл уже существует: {dst} (используйте --force, чтобы перезаписать)", file=sys.stderr)
        return 2
    if not zipfile.is_zipfile(src):
        print("Это не EPUB (файл не является архивом).", file=sys.stderr)
        return 2
    try:
        book = fitz.open("epub", stream=strip_images(src)) if args.text_only else fitz.open(str(src))
    except Exception as e:  # noqa: BLE001
        print(f"Файл не открывается: {e}", file=sys.stderr)
        return 2
    if book.needs_pass or not book.is_reflowable:
        print("Файл защищён (DRM) или это не EPUB с перетекающим текстом.", file=sys.stderr)
        return 2

    book.layout(width=args.width, height=args.height, fontsize=args.fontsize)
    toc = book.get_toc()
    epub_pages = len(book)
    pdf = fitz.open("pdf", book.convert_to_pdf())
    if toc:
        try:
            pdf.set_toc(fix_toc(toc))
        except Exception:  # noqa: BLE001
            toc = []  # битое оглавление не должно мешать результату
    info = read_opf(src)
    meta = book.metadata or {}
    title = args.title or info["title"] or meta.get("title") or src.stem
    author = args.author or ", ".join(info["authors"]) or meta.get("author") or ""
    pdf.set_metadata({"title": title, "author": author, "subject": info["publisher"],
                      "keywords": info["date"], "producer": "epub_to_pdf (PyMuPDF)"})
    pdf.save(str(dst), garbage=3, deflate=True)

    text = "".join(p.get_text() for p in pdf)
    letters = sum(c.isalpha() for c in text)
    print(f"Записано: {dst}  (страниц: {len(pdf)}, закладок: {len(pdf.get_toc())}, размер {dst.stat().st_size / 1e6:.1f} МБ, "
          f"страница {args.width:g}x{args.height:g} pt, шрифт {args.fontsize:g} pt)")
    print(f"Название: {title}")
    print(f"Авторы: {author or 'не указаны'}")
    images, cut, overlapped = layout_problems(pdf)
    if images and (cut or overlapped):
        print(f"Внимание: вёрстка с рисунками неидеальна: из {images} картинок {cut} обрезаны краем страницы, "
              f"на {overlapped} страницах текст пересекается с картинкой. Для поиска это не важно (текст на месте), "
              "а чистую вёрстку без рисунков даёт --text-only.")
    if not letters:
        print("Внимание: в PDF нет текста (в EPUB могут быть только картинки страниц): понадобится OCR.")
    elif len(pdf) < 0.2 * epub_pages:
        print("Внимание: страниц намного меньше, чем при раскладке EPUB; проверьте результат.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
