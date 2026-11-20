#!/usr/bin/env python3
"""Готовит файл RIS для импорта папки с PDF в Zotero одним действием (записи с реквизитами и прикреплёнными файлами).

Реквизиты (авторы, название, журнал, год, страницы) надёжно определяются только чтением титульной страницы, поэтому
работа разделена на два шага:

1. `extract <папка>` печатает по каждому файлу подсказки: метаданные файла, начало текста, строки с выходными данными
   (журнал, том, номер, издательство, «Для цитирования», «In:», «Edited by»), колонтитулы первой, второй и последней
   страницы (по ним видны номера страниц). С `--skeleton файл.json` записывает заготовку реквизитов: по записи на файл.
2. `build <реквизиты.json> <выход.ris>` проверяет заполненные реквизиты (тип, название, авторы, год, что каждый файл
   папки описан ровно один раз) и записывает RIS с абсолютными путями к файлам (поле L1).

В Zotero: «Файл → Импортировать… → Из файла», отметить «Поместить в новую коллекцию» и «Копировать файлы в папку
хранилища Zotero». Zotero создаст карточки с прикреплёнными файлами.

Формат реквизитов (JSON, список объектов):
  file        имя файла в папке (обязательно)
  type        journalArticle, bookSection, book, conferencePaper, webpage, presentation, thesis, report,
              magazineArticle, newspaperArticle, document (обязательно)
  title       название (обязательно)
  authors     ["Фамилия, Имя" ...]; editors - то же для редакторов (глава: редакторы книги; сборник: редакторы)
  container   журнал; книга (для главы); сайт; конференция (для доклада); у книги и отчёта это серия, а не сборник
  edition, place, publisher, year, date (ГГГГ-ММ-ДД), volume, issue, pages ("224-246"), url, language (ru, en)
  note        заметка к записи (например, «файл: авторская рукопись, номера страниц свои»)
DOI и ISBN намеренно не поддерживаются.

Имена файлов сравниваются без учёта нормализации Unicode (на macOS «й» и «ё» в именах бывают разложены), в RIS пишется
имя в том виде, в каком оно лежит на диске.

Запускается через make-ris.sh. Файлы папки не изменяются.
Коды выхода: 0 - успех; 1 - реквизиты не прошли проверку (RIS не записан); 2 - ошибка (нет папки, битый JSON).
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

ATTACH_EXT = (".pdf", ".txt", ".html", ".htm", ".epub", ".md")

RIS_TYPE = {
    "journalArticle": "JOUR", "bookSection": "CHAP", "book": "BOOK", "conferencePaper": "CPAPER",
    "webpage": "ELEC", "presentation": "SLIDE", "thesis": "THES", "report": "RPRT",
    "magazineArticle": "MGZN", "newspaperArticle": "NEWS", "document": "GEN",
}
FIELDS = {"file", "type", "title", "authors", "editors", "container", "edition", "place", "publisher", "year", "date",
          "volume", "issue", "pages", "url", "language", "note"}
NEEDS_CONTAINER = {"journalArticle", "bookSection", "webpage", "magazineArticle", "newspaperArticle"}

CITE_RE = re.compile(
    r"(?i)(doi|vol\.|volume|no\.|issue|pp\.|journal|quarterly|review|proceedings|in:|eds?\.|edited|press|publish|"
    r"copyright|©|\(c\)|isbn|issn|для цитир|библиографическ|ссылка на|журнал|вестник|т\.\s?\d|№|с\.\s?\d|"
    r"изд|издательство|под ред|ред\.|материалы|сборник|конференц|учебн|пособие|москва|м\.:|спб)")
YEAR_RE = re.compile(r"\b(19|20)\d\d\b")   # только для первой страницы: дальше годы чаще из ссылок в тексте
REF_RE = re.compile(r"\(\s*[^()]*\b(19|20)\d\d[a-z]?\s*[,;)]|et al\.|e\.g\.")  # ссылки внутри текста


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def folder_files(folder: Path):
    return sorted(f for f in os.listdir(folder)
                  if not f.startswith((".", "~$")) and f.lower().endswith(ATTACH_EXT) and (folder / f).is_file())


def short(s: str, n: int) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s).strip()
    return s if len(s) <= n else s[:n] + " …"


# ---------- extract ----------

def pdf_hints(path: Path, chars: int):
    import pymupdf as fitz
    doc = fitz.open(path)
    n = len(doc)
    meta = {k: v for k, v in (doc.metadata or {}).items() if k in ("title", "author", "subject") and v and v.strip()}
    out = [f"страниц: {n}"]
    for k, v in meta.items():
        out.append(f"{k} (метаданные файла): {short(v, 200)}")
    first = "".join(doc[i].get_text() for i in range(min(2, n)))
    out.append("начало текста:\n" + short(first, chars))
    lines, seen = [], set()
    for i in sorted({0, 1, 2, n - 1} & set(range(n))):
        cap = 10 if i == 0 else 4   # на остальных страницах совпадения чаще из списка литературы
        for ln in doc[i].get_text().splitlines():
            ln = ln.strip()
            hit = CITE_RE.search(ln) or (i == 0 and YEAR_RE.search(ln))
            if 6 <= len(ln) <= 220 and hit and not REF_RE.search(ln) and ln not in seen and cap > 0:
                seen.add(ln)
                cap -= 1
                lines.append(f"  [с. {i + 1}] {ln}")
    if lines:
        out.append("строки с выходными данными (с. 1-3 и последняя):\n" + "\n".join(lines[:25]))
    for i in sorted({0, 1, n - 1} & set(range(n))):
        p = doc[i]
        h = p.rect.height
        edge = [short(b[4].replace("\n", " "), 180) for b in p.get_text("blocks")
                if (b[1] < h * 0.08 or b[3] > h * 0.92) and b[4].strip()]
        if edge:
            out.append(f"колонтитулы с. {i + 1}: " + " | ".join(edge))
    return out


def text_hints(path: Path, chars: int):
    t = path.read_text(encoding="utf-8", errors="replace")
    return ["начало текста:\n" + short(t, chars)]


def cmd_extract(a) -> int:
    folder = Path(a.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"Нет папки: {folder}", file=sys.stderr)
        return 2
    files = folder_files(folder)
    if not files:
        print(f"В папке нет файлов для импорта ({', '.join(ATTACH_EXT)}): {folder}", file=sys.stderr)
        return 2
    print(f"Папка: {folder}\nФайлов: {len(files)}\n")
    for k, f in enumerate(files, 1):
        print(f"{'#' * 12} [{k}/{len(files)}] {f}")
        p = folder / f
        try:
            hints = pdf_hints(p, a.chars) if f.lower().endswith(".pdf") else (
                text_hints(p, a.chars) if f.lower().endswith((".txt", ".md")) else ["(подсказок нет: откройте файл)"])
        except Exception as e:  # битый файл не должен останавливать остальные
            hints = [f"не удалось прочитать: {e}"]
        print("\n".join(hints))
        print()
    if a.skeleton:
        sk = Path(a.skeleton).expanduser()
        if sk.exists() and not a.force:
            print(f"Заготовка не записана: {sk} уже есть (--force, чтобы перезаписать)", file=sys.stderr)
            return 2
        blank = {"type": "", "title": "", "authors": [], "container": "", "year": "", "pages": "", "language": ""}
        sk.write_text(json.dumps([{"file": nfc(f), **blank} for f in files], ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
        print(f"Заготовка реквизитов: {sk}")
    return 0


# ---------- build ----------

def check(entries, folder: Path):
    errors, warns = [], []
    on_disk = {nfc(f): f for f in folder_files(folder)}
    seen = set()
    for i, e in enumerate(entries, 1):
        tag = f"запись {i} ({e.get('file') or 'без file'})"
        if not isinstance(e, dict):
            errors.append(f"запись {i}: ожидался объект")
            continue
        extra = set(e) - FIELDS
        if extra:
            errors.append(f"{tag}: неизвестные поля {sorted(extra)} (допустимы: {', '.join(sorted(FIELDS))})")
        f = nfc(e.get("file", ""))
        if f not in on_disk:
            errors.append(f"{tag}: файла нет в папке")
        elif f in seen:
            errors.append(f"{tag}: файл описан повторно")
        seen.add(f)
        t = e.get("type", "")
        if t not in RIS_TYPE:
            errors.append(f"{tag}: type «{t}» не из списка {', '.join(RIS_TYPE)}")
        if not str(e.get("title", "")).strip():
            errors.append(f"{tag}: нет названия")
        for k in ("authors", "editors"):
            if k in e and not isinstance(e[k], list):
                errors.append(f"{tag}: {k} должен быть списком")
        if not e.get("authors") and not e.get("editors"):
            warns.append(f"{tag}: нет ни авторов, ни редакторов")
        if not e.get("year") and not e.get("date"):
            warns.append(f"{tag}: нет года")
        if t in NEEDS_CONTAINER and not e.get("container"):
            warns.append(f"{tag}: нет container (журнал, книга или сайт)")
        if e.get("date") and not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", str(e["date"])):
            errors.append(f"{tag}: date в формате ГГГГ-ММ-ДД")
        if e.get("pages") and not re.fullmatch(r"\s*[\w.]+\s*([-–—]\s*[\w.]+)?\s*", str(e["pages"])):
            errors.append(f"{tag}: pages в виде «224-246» или «e44748»")
    missing = sorted(set(on_disk) - seen)
    for m in missing:
        errors.append(f"файл папки не описан: {m}")
    return errors, warns, on_disk


def to_ris(e, path: Path):
    out = [f"TY  - {RIS_TYPE[e['type']]}"]
    out += [f"AU  - {a}" for a in e.get("authors", [])]
    out.append(f"TI  - {e['title'].strip()}")
    simple = [("container", "T2"), ("edition", "ET"), ("place", "CY"), ("publisher", "PB"), ("year", "PY"),
              ("volume", "VL"), ("issue", "IS")]
    # ED, а не A2: переводчик RIS в Zotero читает A2 у книг и отчётов как «редактор серии», ED у всех типов как «редактор»
    out += [f"ED  - {a}" for a in e.get("editors", [])]
    for k, tag in simple:
        if str(e.get(k, "")).strip():
            out.append(f"{tag}  - {str(e[k]).strip()}")
    if e.get("date"):
        d = str(e["date"]).replace("-", "/")
        out.append(f"DA  - {d}")
        if not e.get("year"):
            out.append(f"PY  - {d[:4]}")
    if str(e.get("pages", "")).strip():
        parts = re.split(r"\s*[-–—]\s*", str(e["pages"]).strip(), maxsplit=1)
        out.append(f"SP  - {parts[0]}")
        if len(parts) > 1:
            out.append(f"EP  - {parts[1]}")
    for k, tag in (("url", "UR"), ("language", "LA"), ("note", "N1")):
        if str(e.get(k, "")).strip():
            out.append(f"{tag}  - {str(e[k]).strip()}")
    out.append(f"L1  - {path}")
    out.append("ER  - ")
    return "\n".join(out) + "\n"


def cmd_build(a) -> int:
    spec = Path(a.spec).expanduser()
    try:
        entries = json.loads(spec.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Не прочитать реквизиты {spec}: {e}", file=sys.stderr)
        return 2
    if not isinstance(entries, list):
        print("Реквизиты: ожидался список записей", file=sys.stderr)
        return 2
    folder = Path(a.dir).expanduser().resolve() if a.dir else spec.resolve().parent / "fixed"
    if not folder.is_dir():
        print(f"Нет папки с файлами: {folder} (укажите --dir)", file=sys.stderr)
        return 2
    errors, warns, on_disk = check(entries, folder)
    for w in warns:
        print(f"предупреждение: {w}")
    if errors:
        for er in errors:
            print(f"ошибка: {er}")
        print(f"RIS не записан: ошибок {len(errors)}")
        return 1
    out = Path(a.output).expanduser()
    if out.exists() and not a.force:
        print(f"{out} уже есть (--force, чтобы перезаписать)", file=sys.stderr)
        return 2
    out.write_text("\n".join(to_ris(e, folder / on_disk[nfc(e["file"])]) for e in entries), encoding="utf-8")
    print(f"Записано: {out}  (записей: {len(entries)}, файлов в папке: {len(on_disk)})")
    print("Импорт: Zotero → Файл → Импортировать… → Из файла; «Поместить в новую коллекцию», "
          "«Копировать файлы в папку хранилища Zotero».")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("extract", help="подсказки по файлам папки и заготовка реквизитов")
    x.add_argument("folder")
    x.add_argument("--skeleton", help="записать заготовку реквизитов (JSON)")
    x.add_argument("--chars", type=int, default=800, help="сколько символов начала текста показывать")
    x.add_argument("--force", action="store_true")
    b = sub.add_parser("build", help="проверить реквизиты и записать RIS")
    b.add_argument("spec", help="реквизиты (JSON)")
    b.add_argument("output", help="куда записать .ris")
    b.add_argument("--dir", help="папка с файлами (по умолчанию fixed рядом с файлом реквизитов)")
    b.add_argument("--force", action="store_true")
    a = ap.parse_args()
    return cmd_extract(a) if a.cmd == "extract" else cmd_build(a)


if __name__ == "__main__":
    sys.exit(main())
