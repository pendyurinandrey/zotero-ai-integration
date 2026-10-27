#!/usr/bin/env python3
"""Превращает страницы PDF в серые JPEG (растр) и собирает из них новый PDF, чтобы затем сделать OCR.

Зачем. У части PDF текст нарисован контурами (векторные страницы), и `ocrmypdf --skip-text` его не распознаёт, а
`--force-ocr` растеризует страницы без сжатия: книга в 68 МБ превращается в 680 МБ. Сначала растеризация этим
скриптом (серый JPEG, по умолчанию 260 dpi и качество 55), затем обычный ocrmypdf: файл остаётся сопоставим по
размеру с исходным (68 МБ -> 186 МБ, 115 МБ -> 90 МБ на проверенных книгах).

Запускается через rasterize-pdf.sh. Исходный файл не изменяется. Закладки и ссылки не переносятся.

Коды выхода: 0 - успех; 2 - ошибка.
"""
import argparse
import sys
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).parent))
from _raster import rasterize_pages  # noqa: E402


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="PDF")
    ap.add_argument("output", help="куда записать растровый PDF (не должен совпадать с input)")
    ap.add_argument("--dpi", type=int, default=260)
    ap.add_argument("--quality", type=int, default=55)
    ap.add_argument("--color", action="store_true", help="цветные страницы вместо серых")
    ap.add_argument("--force", action="store_true", help="перезаписать output, если он уже есть")
    return ap.parse_args()


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
    try:
        doc = fitz.open(str(src))
    except Exception as e:  # noqa: BLE001
        print(f"Файл не открывается: {e}", file=sys.stderr)
        return 2
    if doc.needs_pass:
        print("PDF защищён паролем: снимите защиту и повторите.", file=sys.stderr)
        return 2
    n = rasterize_pages(doc, dst, args.dpi, args.quality, gray=not args.color,
                        progress=lambda k: print(f"  страниц: {k}", file=sys.stderr))
    print(f"Записано: {dst}  (страниц: {n}, размер {src.stat().st_size / 1e6:.1f} -> {dst.stat().st_size / 1e6:.1f} МБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
