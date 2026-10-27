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

4. Белые подложки под строками. У некоторых PDF под каждой строкой лежит невидимая белая закрашенная полоса,
   индексатор принимает стопку полос за линейки таблицы и перемешивает текст страницы. Полосы удаляются из
   потока содержимого (вид страницы не меняется).
5. Колонтитулы и штампы (только по запросу, --strip-running-heads). Тексты у верхнего и нижнего краёв, которые
   повторяются на большинстве страниц и содержат не меньше 3 букв (штамп электронной библиотеки, название книги), удаляются:
   в индексе они шум, одинаковый на каждой странице. Номера страниц не трогаются.

6. Знак минус U+2212 в картах ToUnicode. Если шрифты отдают U+2212 вместо дефиса, индексатор на страницах с большим
   числом таких знаков (листинги кода, строки «----» в выводе консоли) считает, что текста нет, и выдаёт пустую
   страницу. Целевой символ меняется на обычный дефис-минус. Шаг включается автоматически, только если индексатор
   действительно не видит текст на заметной доле страниц (--fix-minus включает принудительно).

7. Один огромный текстовый блок на странице. У части PDF (старые сканы с готовым слоем OCR) весь текст страницы
   лежит в одном объекте BT...ET с десятками перемещений, и индексатор такую страницу не читает (из 3000 символов
   остаётся колонтитул). Блок разбивается на отдельные, вид страницы не меняется. Шаг включается только на тех
   страницах, где индексатор действительно теряет текст (--split-text включает принудительно).

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
from _pdf_common import (complete_tounicode, count_hidden_text_pages, reveal_hidden_text, count_escaped_paren_pages, count_white_box_pages, escape_parens,  # noqa: E402
                         find_running_text, inspector_pages, is_damaged, is_missing_page, long_text_object_pages,
                         remap_minus, fix_winansi_cyrillic, scan_pages, split_text_objects, strip_running_text, strip_white_boxes)


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="PDF для ремонта")
    ap.add_argument("output", help="куда записать результат (не должен совпадать с input)")
    ap.add_argument("--rebuild", action="store_true", help="пересобрать страницы, даже если структура в порядке")
    ap.add_argument("--escape-parens", action="store_true", help="исправить скобки в строках, даже если не найдены")
    ap.add_argument("--no-escape-parens", action="store_true", help="не исправлять скобки в строках")
    ap.add_argument("--fix-tounicode", action="store_true", help="достроить карты ToUnicode, даже если пропусков не найдено")
    ap.add_argument("--no-fix-tounicode", action="store_true", help="не достраивать карты ToUnicode")
    ap.add_argument("--fix-minus", action="store_true", help="заменить U+2212 в картах ToUnicode на дефис")
    ap.add_argument("--no-fix-minus", action="store_true", help="не заменять U+2212 в картах ToUnicode")
    ap.add_argument("--reveal-text", action="store_true", help="сделать невидимый текст поверх скана читаемым для индексатора")
    ap.add_argument("--no-reveal-text", action="store_true", help="не трогать невидимый текст поверх скана")
    ap.add_argument("--no-fix-cp1251", action="store_true",
                    help="не добавлять карты ToUnicode для шрифтов WinAnsi с текстом в кодировке cp1251")
    ap.add_argument("--split-text", action="store_true", help="разбить длинные текстовые блоки на всех подходящих страницах")
    ap.add_argument("--no-split-text", action="store_true", help="не разбивать текстовые блоки")
    ap.add_argument("--strip-white-boxes", action="store_true", help="удалить белые подложки, даже если не найдены")
    ap.add_argument("--no-strip-white-boxes", action="store_true", help="не удалять белые подложки")
    ap.add_argument("--strip-running-heads", action="store_true",
                    help="удалить повторяющиеся колонтитулы и штампы у краёв страниц (по умолчанию не удаляются)")
    ap.add_argument("--title", help="записать название в метаданные файла")
    ap.add_argument("--author", help="записать автора в метаданные файла")
    ap.add_argument("--drop-missing", action="store_true",
                    help="при пересборке удалить потерянные страницы вместо замены пустыми (нумерация сдвинется)")
    ap.add_argument("--force", action="store_true", help="перезаписать output, если он уже есть")
    return ap.parse_args()


def restore_internal_links(src, out):
    """Постраничный перенос теряет ссылки на другие страницы (цели не попали в перенесённый кусок): ставим их заново."""
    for i in range(min(len(src), len(out))):
        try:
            have = {(round(l["from"].x0), round(l["from"].y0)) for l in out[i].get_links()}
            for link in src[i].get_links():
                # ссылки по имени (kind 4, у O'Reilly и многих других) в новом файле без таблицы имён не работают:
                # заменяем их прямой ссылкой на страницу
                if link.get("kind") in (fitz.LINK_GOTO, fitz.LINK_NAMED) and link.get("page", -1) >= 0 \
                        and (round(link["from"].x0), round(link["from"].y0)) not in have:
                    out[i].insert_link({"kind": fitz.LINK_GOTO, "from": link["from"], "page": link["page"],
                                        "to": link.get("to", fitz.Point(0, 0)), "zoom": link.get("zoom", 0)})
        except Exception:  # noqa: BLE001
            continue


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
    if not drop_missing:                       # номера страниц не сдвинулись: возвращаем внутренние ссылки и закладки
        restore_internal_links(src, out)
        try:
            out.set_toc(src.get_toc())
        except Exception:                      # noqa: BLE001
            pass                               # битые закладки не должны мешать ремонту
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

    minus_fonts = remap_minus(doc, dry_run=True)
    do_minus = False
    if minus_fonts and not args.no_fix_minus:
        if args.fix_minus:
            do_minus = True
        else:                                      # автоматически только если индексатор слеп на заметной доле страниц
            from zotero_mcp.extract import extract_pdf
            try:
                blind = len(extract_pdf(str(src_p)).needs_ocr)
            except Exception:  # noqa: BLE001
                blind = 0
            do_minus = blind >= max(5, 0.05 * len(doc))

    split_pages = []
    if not args.no_split_text:
        cand = long_text_object_pages(doc)
        if cand and args.split_text:
            split_pages = cand
        elif cand:                                 # проверяем, где индексатор правда теряет текст
            from zotero_mcp.extract import extract_pdf
            try:
                res = extract_pdf(str(src_p))
                pages_md = [p if isinstance(p, str) else getattr(p, "markdown", str(p)) for p in res.pages]
                split_pages = [i for i in cand if i < len(pages_md)
                               and len(pages_md[i]) < 0.3 * len(doc[i].get_text()) and len(doc[i].get_text()) > 300]
            except Exception:  # noqa: BLE001
                split_pages = []
    do_split = bool(split_pages)

    white_pages = count_white_box_pages(doc, range(0, len(doc), max(1, len(doc) // 40)))
    sampled = len(range(0, len(doc), max(1, len(doc) // 40)))
    do_white = (args.strip_white_boxes or white_pages > 0.3 * sampled) and not args.no_strip_white_boxes
    running = find_running_text(doc) if args.strip_running_heads else {}
    do_cp1251 = fix_winansi_cyrillic(doc, dry_run=True) > 0 and not args.no_fix_cp1251
    do_reveal = (args.reveal_text or count_hidden_text_pages(doc) > 0.3) and not args.no_reveal_text
    do_meta = bool(args.title or args.author)

    if not (do_rebuild or do_escape or do_tounicode or do_cp1251 or do_reveal or do_minus or do_split or do_white or running or do_meta):
        print("Ремонт не нужен: структура страниц в порядке, экранированных скобок в строках почти нет, карты "
              "ToUnicode полные. Выход не создан.")
        return 0

    done = []
    missing = damaged = failed = []
    if do_rebuild:
        doc, missing, damaged, failed = rebuild(doc, args.drop_missing)
        done.append(f"страницы пересобраны (было {len(fitz.open(str(src_p)))}, стало {len(doc)})")
    if do_white:                      # до всего остального: MuPDF при редактировании переписывает потоки
        done.append(f"удалено белых подложек: {strip_white_boxes(doc)}")
    if running:
        done.append(f"удалено колонтитулов и штампов: {strip_running_text(doc, set(running))} "
                    f"(повторяющихся текстов: {len(running)})")
    if do_meta:
        meta = dict(doc.metadata or {})
        if args.title:
            meta["title"] = args.title
        if args.author:
            meta["author"] = args.author
        doc.set_metadata(meta)
        done.append("обновлены метаданные файла")
    if do_escape:
        total, skipped = escape_parens(doc)
        done.append(f"заменено скобок в строках: {total}" + (f" (потоков со встроенными картинками пропущено: {skipped})" if skipped else ""))
    # clean=True нельзя: MuPDF заново записывает строки и возвращает экранированные скобки
    if do_split:
        done.append(f"текстовые блоки разбиты на {len(split_pages)} страницах (вставлено разрывов: {split_text_objects(doc, split_pages)})")
    if do_minus:
        done.append(f"знак минус в картах ToUnicode заменён на дефис: шрифтов {remap_minus(doc)}")
    if do_reveal:
        done.append(f"невидимый текст поверх скана сделан обычным и спрятан под картинкой: страниц {reveal_hidden_text(doc)}")
    if do_cp1251:
        done.append(f"добавлены карты ToUnicode (cp1251) для шрифтов с объявленной кодировкой WinAnsi: {fix_winansi_cyrillic(doc)}")
    if do_tounicode:
        fonts, codes = complete_tounicode(doc)
        done.append(f"достроены карты ToUnicode: шрифтов {fonts}, кодов {codes}")
    doc.save(str(dst_p), garbage=4, deflate=True, deflate_fonts=True, deflate_images=True,
             encryption=fitz.PDF_ENCRYPT_NONE)   # шифрование с пустым паролем снимается: иначе результат не откроется индексатором

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
