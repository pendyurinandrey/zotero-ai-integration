#!/usr/bin/env python3
"""Ремонт PDF, который индексатор zotero-mcp читает неправильно или в котором страницы считаются по-разному.

Скрипт сам определяет, что нужно файлу, и делает только это:

1. Пересборка страниц. Если у файла повреждена структура (битая таблица ссылок, в дереве страниц объекты,
   которых нет в файле), разные программы восстанавливают разный список страниц: просмотрщик видит, например,
   640 страниц, индексатор 627, и номера «стр.» в индексе отстают от читалки на 2, 7, 12 страниц. Страницы
   переносятся по одной в новый документ, дерево страниц получается корректным. Страницы без объекта в файле
   (потеряны безвозвратно) заменяются пустыми того же размера, чтобы нумерация осталась как в просмотрщике
   (--drop-missing удаляет их). Битые потоки не лечатся, страницы перечисляются.
2. Экранированные скобки. Индексатор обрывает разбор страницы на `\\(` или `\\)` внутри строки текста
   (просмотрщики читают такой текст правильно). На найденных файлах он видел 19% текста. Скобки внутри строк
   заменяются равнозначными восьмеричными кодами \\050 и \\051. Видимое содержимое и текст при чтении
   PyMuPDF не меняются.

3. Неполные карты ToUnicode. У части шрифтов для кодов в кодировке есть имена глифов, а в карте ToUnicode
   часть кодов пропущена (чаще всего дефис, тире, кавычки). Индексатор теряет такие знаки: «Блок-схема»
   превращается в «Блоксхема», «6-й» в «6й». Недостающие коды достраиваются по именам глифов.

Если файл ни в чём из этого не нуждается, выходной файл не создаётся. --rebuild, --escape-parens и --fix-tounicode
принудительно включают шаг, --no-escape-parens и --no-fix-tounicode отключают. Исходный файл не изменяется.
Запускается через repair-pdf.sh.

Коды выхода: 0 - успех (в том числе «ремонт не нужен»); 2 - ошибка.
"""
import argparse
import sys
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).parent))
from _pdf_common import (complete_tounicode, count_escaped_paren_pages, escape_parens, inspector_pages,  # noqa: E402
                         is_damaged, is_missing_page, scan_pages)


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="PDF для ремонта")
    ap.add_argument("output", help="куда записать результат (не должен совпадать с input)")
    ap.add_argument("--rebuild", action="store_true", help="пересобрать страницы, даже если структура в порядке")
    ap.add_argument("--escape-parens", action="store_true", help="исправить скобки в строках, даже если не найдены")
    ap.add_argument("--no-escape-parens", action="store_true", help="не исправлять скобки в строках")
    ap.add_argument("--fix-tounicode", action="store_true", help="достроить карты ToUnicode, даже если пропусков не найдено")
    ap.add_argument("--no-fix-tounicode", action="store_true", help="не достраивать карты ToUnicode")
    ap.add_argument("--drop-missing", action="store_true",
                    help="при пересборке удалить потерянные страницы вместо замены пустыми (нумерация сдвинется)")
    ap.add_argument("--force", action="store_true", help="перезаписать output, если он уже есть")
    return ap.parse_args()


def rebuild(src, drop_missing):
    out = fitz.open()
    missing, damaged, failed = [], [], []
    fitz.TOOLS.mupdf_warnings()
    for i in range(len(src)):
        page = src[i]
        miss = is_missing_page(src, page)
        try:
            page.get_text()
            if is_damaged(fitz.TOOLS.mupdf_warnings()) and not miss:
                damaged.append(i + 1)
        except Exception:  # noqa: BLE001
            damaged.append(i + 1)
        if miss:
            missing.append(i + 1)
            if drop_missing:
                continue
            ref = src[i - 1] if i else src[i + 1]
            out.new_page(width=ref.rect.width, height=ref.rect.height)
            continue
        try:
            out.insert_pdf(src, from_page=i, to_page=i)
        except Exception:  # noqa: BLE001
            failed.append(i + 1)
            out.new_page(width=page.rect.width, height=page.rect.height)
    return out, missing, damaged, failed


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
        doc = fitz.open(str(src_p))
    except Exception as e:  # noqa: BLE001
        print(f"Файл не открывается: {e}", file=sys.stderr)
        return 2
    if doc.needs_pass:
        print("PDF защищён паролем: снимите защиту и повторите.", file=sys.stderr)
        return 2

    miss_pages, _ = scan_pages(doc)
    try:
        insp = inspector_pages(src_p)
    except Exception:  # noqa: BLE001
        insp = None
    structure_bad = bool(miss_pages) or (insp is not None and insp != len(doc))
    do_rebuild = args.rebuild or structure_bad
    esc_pages = count_escaped_paren_pages(doc)
    # единичные страницы со скобками (у обычных книг их бывает пара) не мешают индексатору: автоматически чиним
    # только когда таких страниц много
    many = esc_pages >= 3 and esc_pages >= 0.05 * len(doc)
    do_escape = (args.escape_parens or many) and not args.no_escape_parens

    gap_fonts, _ = complete_tounicode(doc, dry_run=True)
    do_tounicode = (args.fix_tounicode or gap_fonts > 0) and not args.no_fix_tounicode

    if not do_rebuild and not do_escape and not do_tounicode:
        print("Ремонт не нужен: структура страниц в порядке, экранированных скобок в строках почти нет, карты "
              "ToUnicode полные. Выход не создан.")
        return 0

    done = []
    missing = damaged = failed = []
    if do_rebuild:
        doc, missing, damaged, failed = rebuild(doc, args.drop_missing)
        done.append(f"страницы пересобраны (было {len(fitz.open(str(src_p)))}, стало {len(doc)})")
    if do_escape:
        total, skipped = escape_parens(doc)
        done.append(f"заменено скобок в строках: {total}" + (f" (потоков со встроенными картинками пропущено: {skipped})" if skipped else ""))
    # clean=True нельзя: MuPDF заново записывает строки и возвращает экранированные скобки
    if do_tounicode:
        fonts, codes = complete_tounicode(doc)
        done.append(f"достроены карты ToUnicode: шрифтов {fonts}, кодов {codes}")
    doc.save(str(dst_p), garbage=4, deflate=True, deflate_fonts=True, deflate_images=True)

    print(f"Записано: {dst_p}  (размер {src_p.stat().st_size / 1e6:.1f} -> {dst_p.stat().st_size / 1e6:.1f} МБ)")
    for d in done:
        print(f"  - {d}")
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
