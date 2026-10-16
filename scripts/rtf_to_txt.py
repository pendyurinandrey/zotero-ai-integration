#!/usr/bin/env python3
"""Переводит RTF в обычный текст (.txt) так, чтобы его читал индексатор zotero-mcp, и сохраняет сноски.

Индексатор читает PDF, HTML и обычный текст, а RTF и DOCX не читает. Готовые конвертеры подводят: textutil
(macOS) выбрасывает сноски, pandoc не понимает кодовую страницу 1251 и портит кириллицу. Скрипт разбирает RTF
сам: кодовая страница берётся из заголовка (\\ansicpgNNNN, по умолчанию 1251), символы \\uN и \\'hh
декодируются, служебные группы (шрифты, стили, картинки, колонтитулы) пропускаются.

Сноски по умолчанию ставятся на месте, в виде «[Примечание N: текст]», чтобы они попали в тот же фрагмент
индекса, что и абзац, к которому относятся (--notes inline). --notes end собирает их в конец файла,
--notes drop отбрасывает.

Нумерации страниц в RTF нет, поэтому у текстового файла в индексе не будет номеров страниц (как у расшифровок).
К файлу в Zotero нужна карточка (книга или документ): вложения без родительской записи индексатор пропускает.

Запускается через rtf-to-txt.sh. Исходный файл не изменяется.

Коды выхода: 0 - успех; 2 - ошибка.
"""
import argparse
import re
import sys
from pathlib import Path

# Группы, содержимое которых не является текстом документа
SKIP_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "themedata", "colorschememapping", "latentstyles",
    "datastore", "listtable", "listoverridetable", "rsidtbl", "generator", "header", "footer", "headerl", "headerr",
    "headerf", "footerl", "footerr", "footerf", "xmlnstbl", "fldinst", "shpinst", "nonshppict", "revtbl", "pgptbl",
    "template", "private", "falt", "panose", "object", "objdata",
}
SPECIAL = {"emdash": "—", "endash": "–", "lquote": "‘", "rquote": "’", "ldblquote": "«", "rdblquote": "»",
           "bullet": "•", "tab": " ", "emspace": " ", "enspace": " "}
NEWLINE = {"par", "line", "sect", "page", "row"}
TOKEN = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([\\{}~_\-|*:])|([{}])|([^\\{}]+)")


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="RTF-файл")
    ap.add_argument("output", nargs="?", help="куда записать текст (по умолчанию рядом с исходным, с расширением .txt)")
    ap.add_argument("--notes", choices=("inline", "end", "drop"), default="inline",
                    help="что делать со сносками: inline (на месте, по умолчанию), end (в конце файла), drop")
    ap.add_argument("--encoding", help="кодовая страница для \\'hh (по умолчанию из заголовка, иначе cp1251)")
    ap.add_argument("--force", action="store_true", help="перезаписать output, если он уже есть")
    return ap.parse_args()


def detect_codepage(data: str) -> str:
    m = re.search(r"\\ansicpg(\d+)", data[:2000])
    return f"cp{m.group(1)}" if m else "cp1251"


def convert(data: str, codepage: str, notes_mode: str):
    """Возвращает (текст, список сносок). data это содержимое RTF, прочитанное как latin-1 (байты без потерь)."""
    body, notes = [], []
    cur = body                      # куда сейчас пишем: основной текст или текущая сноска
    frames = []                     # стек групп: (счётчик пропуска, куда писали, uc)
    skip, uc, skip_chars, star = 0, 1, 0, False
    for m in TOKEN.finditer(data):
        word, num, hexcode, sym, brace, text = m.groups()
        if brace == "{":
            frames.append((skip, cur, uc))
            star = False
        elif brace == "}":
            if frames:
                skip, prev, uc = frames.pop()
                if cur is not prev and cur is not body:      # закончилась группа сноски
                    note = " ".join("".join(cur).split())
                    if note:
                        notes.append(note)
                        if notes_mode == "inline":
                            prev.append(f" [Примечание {len(notes)}: {note}] ")
                cur = prev
        elif sym:
            if sym == "*":
                star = True
            elif sym == "~" and not skip:
                cur.append("\u00a0")
            elif sym in "\\{}" and not skip:
                cur.append(sym)
        elif word:
            if star:                                         # {\*\... } необязательное назначение: пропускаем
                skip += 1
                star = False
            if word in SKIP_DESTINATIONS:
                skip += 1
            elif word == "footnote":
                if not skip:
                    cur = []
            elif skip:
                continue
            elif word in NEWLINE:
                cur.append("\n")
            elif word in SPECIAL:
                cur.append(SPECIAL[word])
            elif word == "uc":
                uc = int(num or 1)
            elif word == "u":
                v = int(num)
                cur.append(chr(v + 65536 if v < 0 else v))
                skip_chars = uc                              # следующие uc символов это замена для старых программ
        elif hexcode:
            if skip_chars:
                skip_chars -= 1
            elif not skip:
                cur.append(bytes([int(hexcode, 16)]).decode(codepage, errors="replace"))
        elif text:
            if skip_chars:
                text = text[skip_chars:]
                skip_chars = 0
            if not skip and text.strip("\r\n"):
                cur.append(text.replace("\r", "").replace("\n", ""))
    result = "".join(body)
    if notes_mode == "end" and notes:
        result += "\n\nПримечания\n" + "\n".join(f"{i}. {n}" for i, n in enumerate(notes, 1))
    result = re.sub(r"[ \t\u00a0]+", " ", result)
    result = re.sub(r" *\n *", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result).strip() + "\n"
    return result, notes


def main() -> int:
    args = parse_args()
    src = Path(args.input)
    if not src.is_file():
        print(f"Не найден файл: {src}", file=sys.stderr)
        return 2
    dst = Path(args.output) if args.output else src.with_suffix(".txt")
    if dst.resolve() == src.resolve():
        print("Выходной файл должен отличаться от входного", file=sys.stderr)
        return 2
    if dst.exists() and not args.force:
        print(f"Файл уже существует: {dst} (используйте --force, чтобы перезаписать)", file=sys.stderr)
        return 2
    raw = src.read_bytes()
    if not raw.lstrip().startswith(b"{\\rtf"):
        print("Это не RTF (файл не начинается с {\\rtf).", file=sys.stderr)
        return 2
    data = raw.decode("latin-1")
    codepage = args.encoding or detect_codepage(data)
    try:
        text, notes = convert(data, codepage, args.notes)
    except LookupError:
        print(f"Неизвестная кодовая страница: {codepage}", file=sys.stderr)
        return 2
    dst.write_text(text, encoding="utf-8")
    words = len(re.findall(r"\w+", text))
    print(f"Записано: {dst}  (слов: {words}, символов: {len(text)}, кодовая страница {codepage}, "
          f"сносок: {len(notes)}, режим сносок: {args.notes})")
    letters = sum(c.isalpha() for c in text)
    if letters and text.count("\ufffd") / letters > 0.001:
        print("Внимание: в тексте есть символы замены (U+FFFD): проверьте кодовую страницу (--encoding).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
