#!/usr/bin/env python3
"""Переводит DjVu в PDF из страниц-картинок (чёрно-белых), чтобы затем сделать OCR.

Порядок: копия файла под простым именем (djvulibre не открывает пути с кириллицей и пробелами), `ddjvu -mode=black`
в многостраничный TIFF (1 бит, компактно) на родном разрешении файла, `tiff2pdf`. Текстового слоя в
результате нет: нужен OCR (prepare-books.sh делает это сам).

Ограничение: режим `black` оставляет от цветных страниц только чёрную маску. Текст и штриховые рисунки сохраняются,
фотографии и цветные иллюстрации выходят грубыми. Для поиска это не важно; если иллюстрации нужны, используйте
`ddjvu -format=pdf` (файл получается очень большим) или исходный PDF книги.

Нужен djvulibre (`brew install djvulibre`) и tiff2pdf (libtiff). Запускается через djvu-to-pdf.sh.
Исходный файл не изменяется.

Коды выхода: 0 - успех; 2 - ошибка.
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="файл DjVu")
    ap.add_argument("output", help="куда записать PDF")
    ap.add_argument("--dpi", type=int, help="разрешение результата (по умолчанию родное разрешение файла: при другом значении страницы пересчитываются в серые и файл вырастает в десятки раз)")
    ap.add_argument("--force", action="store_true", help="перезаписать output, если он уже есть")
    return ap.parse_args()


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main() -> int:
    args = parse_args()
    src, dst = Path(args.input), Path(args.output)
    for tool in ("ddjvu", "djvudump", "tiff2pdf"):
        if not shutil.which(tool):
            print(f"Не найдена программа {tool}: установите djvulibre и libtiff (brew install djvulibre libtiff).", file=sys.stderr)
            return 2
    if not src.is_file():
        print(f"Не найден файл: {src}", file=sys.stderr)
        return 2
    if dst.exists() and not args.force:
        print(f"Файл уже существует: {dst} (используйте --force, чтобы перезаписать)", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="djvu-") as tmp:
        book = Path(tmp) / "book.djvu"
        shutil.copy2(src, book)
        dump = run(["djvudump", str(book)])
        if dump.returncode != 0:
            print(f"Файл не открывается как DjVu: {dump.stderr.strip()[:200]}", file=sys.stderr)
            return 2
        dpis = [int(m) for m in re.findall(r"(\d+) dpi", dump.stdout)]
        pages = len(re.findall(r"^\s*FORM:DJVU", dump.stdout, re.M)) or None
        dpi = args.dpi or max(dpis[:50], default=300)
        color = len(re.findall(r"BG44|FG44", dump.stdout))
        tif = Path(tmp) / "book.tif"
        r = run(["ddjvu", "-format=tiff", "-mode=black", f"-scale={dpi}", str(book), str(tif)])
        if not tif.exists() or tif.stat().st_size == 0:
            print(f"ddjvu не создал файл: {r.stderr.strip()[:300]}", file=sys.stderr)
            return 2
        out = Path(tmp) / "book.pdf"
        r = run(["tiff2pdf", "-o", str(out), str(tif)])
        if not out.exists() or out.stat().st_size == 0:
            print(f"tiff2pdf не создал файл: {r.stderr.strip()[:300]}", file=sys.stderr)
            return 2
        shutil.copy2(out, dst)
    print(f"Записано: {dst}  (страниц: {pages or '?'}, {dpi} dpi, чёрно-белые, размер {dst.stat().st_size / 1e6:.1f} МБ)")
    if color:
        print("Внимание: в файле есть цветные слои (фото, иллюстрации): в режиме black они сохранены только как чёрная маска.")
    print("Текстового слоя нет: запустите OCR (prepare-books.sh сделает это сам).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
