#!/usr/bin/env python3
"""Пересобирает повреждённый PDF так, чтобы все программы видели в нём одни и те же страницы.

Проблема. У файла с повреждённой структурой (битая таблица ссылок, в дереве страниц объекты, которых нет в
файле) разные программы восстанавливают разный список страниц. Просмотрщик (MuPDF) видит, например, 640
страниц, индексатор zotero-mcp 627, и с середины книги номера «стр.» в индексе расходятся с номерами в
читалке на 2, 7, 12 страниц. Тексты страниц при этом могут быть целыми: сбивается нумерация. Кроме того, у
части страниц бывают битые потоки содержимого (ошибки zlib), тогда текст на них потерян или обрезан.

Что делает скрипт: открывает файл как его читает PyMuPDF, переносит страницы по одной в новый документ (так
получается корректное дерево страниц) и сохраняет с очисткой и сжатием. Страницы, у которых в файле нет
объекта страницы (потеряны безвозвратно), по умолчанию заменяются пустыми страницами того же размера, чтобы
нумерация осталась такой, как в просмотрщике; --drop-missing удаляет их. Испорченные потоки скрипт не лечит,
а перечисляет такие страницы: восстановить их можно только из другой копии файла.

Исходный файл не изменяется. Запускается через repair-pdf.sh.

Коды выхода: 0 - файл записан; 2 - ошибка.
"""
import argparse
import sys
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).parent))
from _pdf_common import is_damaged, is_missing_page  # noqa: E402


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="повреждённый PDF")
    ap.add_argument("output", help="куда записать пересобранный PDF (не должен совпадать с input)")
    ap.add_argument("--drop-missing", action="store_true",
                    help="удалить потерянные страницы вместо замены пустыми (нумерация сдвинется)")
    ap.add_argument("--force", action="store_true", help="перезаписать output, если он уже есть")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    src_p, dst_p = Path(args.input), Path(args.output)
    if not src_p.is_file():
        print(f"Не найден файл: {src_p}", file=sys.stderr)
        return 2
    if src_p.resolve() == dst_p.resolve():
        print("Выходной файл должен отличаться от входного", file=sys.stderr)
        return 2
    if dst_p.exists() and not args.force:
        print(f"Файл уже существует: {dst_p} (используйте --force, чтобы перезаписать)", file=sys.stderr)
        return 2
    try:
        src = fitz.open(str(src_p))
    except Exception as e:  # noqa: BLE001
        print(f"Файл не открывается: {e}", file=sys.stderr)
        return 2
    if src.needs_pass:
        print("PDF защищён паролем: снимите защиту и повторите.", file=sys.stderr)
        return 2

    out = fitz.open()
    missing, damaged, failed = [], [], []
    fitz.TOOLS.mupdf_warnings()                     # сбросить накопленные сообщения
    for i in range(len(src)):
        page = src[i]
        miss = is_missing_page(src, page)
        try:
            page.get_text()                         # проверка читаемости потока содержимого
            if is_damaged(fitz.TOOLS.mupdf_warnings()) and not miss:
                damaged.append(i + 1)
        except Exception:                           # noqa: BLE001
            damaged.append(i + 1)
        if miss:
            missing.append(i + 1)
            if args.drop_missing:
                continue
            w, h = (src[i - 1].rect.width, src[i - 1].rect.height) if i else (src[i + 1].rect.width, src[i + 1].rect.height)
            out.new_page(width=w, height=h)
            continue
        try:
            out.insert_pdf(src, from_page=i, to_page=i)
        except Exception:                           # noqa: BLE001
            failed.append(i + 1)
            out.new_page(width=page.rect.width, height=page.rect.height)

    out.save(str(dst_p), garbage=4, deflate=True, deflate_fonts=True, deflate_images=True, clean=True)
    print(f"Записано: {dst_p}  (страниц: было {len(src)}, стало {len(out)}, "
          f"размер {src_p.stat().st_size / 1e6:.1f} -> {dst_p.stat().st_size / 1e6:.1f} МБ)")
    if missing:
        action = "удалены" if args.drop_missing else "заменены пустыми страницами"
        print(f"Потерянные страницы (в файле нет их объектов, содержимое не восстановить), {action}: "
              f"{', '.join(map(str, missing))}")
    if damaged:
        print("Страницы с ошибками чтения потока (текст может быть потерян или обрезан): "
              f"{', '.join(map(str, sorted(set(damaged))))}")
    if failed:
        print(f"Не удалось перенести, заменены пустыми: {', '.join(map(str, failed))}")
    if missing or damaged or failed:
        print("Недостающее содержимое можно получить только из другой копии книги.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
