#!/usr/bin/env python3
"""Собирает PDF из папки, где каждая страница книги лежит в отдельном файле SVG.

Страницы берутся в порядке числа в конце имени файла (`книга_1.svg`, `книга_2.svg`, `книга_10.svg`), а не по алфавиту.
В таких SVG текст обычно нарисован контурами (буквы это кривые), поэтому текстового слоя в результате нет: каждая
страница растеризуется в серый JPEG (по умолчанию 220 dpi, качество 45), и дальше нужен OCR (prepare-books.sh делает
это сам, либо вручную ocrmypdf и fix-ocr-layer.sh).

PNG, встроенные в SVG (иллюстрации), MuPDF иногда не читает («premature end of data in png image») и молча
выбрасывает; скрипт пересохраняет такие картинки (см. _raster.py) и сообщает, сколько не удалось.

Запускается через svg-to-pdf.sh. Исходные файлы не изменяются.

Коды выхода: 0 - успех; 2 - ошибка (нет SVG, выход уже существует).
"""
import argparse
import re
import sys
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).parent))
from _raster import fix_svg, rasterize_pages  # noqa: E402


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="папка со страницами SVG")
    ap.add_argument("output", help="куда записать PDF")
    ap.add_argument("--dpi", type=int, default=220, help="разрешение растеризации (по умолчанию 220)")
    ap.add_argument("--quality", type=int, default=45, help="качество JPEG (по умолчанию 45)")
    ap.add_argument("--color", action="store_true", help="цветные страницы вместо серых (файл заметно больше)")
    ap.add_argument("--title", help="название для метаданных")
    ap.add_argument("--author", help="автор для метаданных")
    ap.add_argument("--force", action="store_true", help="перезаписать output, если он уже есть")
    return ap.parse_args()


def page_number(p: Path):
    m = re.search(r"(\d+)(?=\.svg$)", p.name, re.I)
    return int(m.group(1)) if m else None


def svg_files(folder: Path):
    files = [p for p in folder.iterdir() if p.suffix.lower() == ".svg" and not p.name.startswith(".")]
    numbered = [(page_number(p), p) for p in files]
    if any(n is None for n, _ in numbered):
        return sorted(files, key=lambda p: p.name), None
    numbered.sort()
    return [p for _, p in numbered], [n for n, _ in numbered]


def pages_of(files, stats):
    for f in files:
        text, fixed, failed = fix_svg(f.read_text(encoding="utf-8", errors="replace"))
        stats["fixed"] += fixed
        stats["failed"] += failed
        yield fitz.open(stream=text.encode("utf-8"), filetype="svg")[0]


def main() -> int:
    args = parse_args()
    folder, dst = Path(args.folder), Path(args.output)
    if not folder.is_dir():
        print(f"Не найдена папка: {folder}", file=sys.stderr)
        return 2
    if dst.exists() and not args.force:
        print(f"Файл уже существует: {dst} (используйте --force, чтобы перезаписать)", file=sys.stderr)
        return 2
    files, nums = svg_files(folder)
    if not files:
        print("В папке нет файлов SVG.", file=sys.stderr)
        return 2
    gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in set(nums)] if nums else []
    stats = {"fixed": 0, "failed": 0}
    n = rasterize_pages(pages_of(files, stats), dst, args.dpi, args.quality, gray=not args.color,
                        progress=lambda k: print(f"  страниц: {k}", file=sys.stderr))
    doc = fitz.open(str(dst))
    meta = {"producer": "svg_to_pdf (PyMuPDF)"}
    if args.title:
        meta["title"] = args.title
    if args.author:
        meta["author"] = args.author
    doc.set_metadata(meta)
    doc.saveIncr()
    print(f"Записано: {dst}  (страниц: {n}, размер {dst.stat().st_size / 1e6:.1f} МБ, {args.dpi} dpi, "
          f"{'цвет' if args.color else 'серый'}, JPEG {args.quality})")
    if nums:
        print(f"Нумерация файлов: с {nums[0]} по {nums[-1]}" + (f", пропущены номера: {gaps[:20]}" if gaps else ", без пропусков")
              + (". Печатный номер страницы может отличаться от номера файла." if nums[0] != 1 else "."))
    else:
        print("Внимание: в именах файлов нет числа в конце, страницы взяты по алфавиту: проверьте порядок.")
    if stats["fixed"]:
        print(f"Пересохранено встроенных картинок PNG: {stats['fixed']}")
    if stats["failed"]:
        print(f"Внимание: не удалось прочитать встроенных картинок: {stats['failed']} (на страницах они пропущены).")
    print("Текстового слоя нет: запустите OCR (prepare-books.sh сделает это сам).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
