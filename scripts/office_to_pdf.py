#!/usr/bin/env python3
"""Переводит документ Word (.doc, .docx, .docm) или OpenDocument (.odt) в PDF с настоящим текстом, чтобы его читал индексатор.

Индексатор zotero-mcp читает PDF, HTML и обычный текст, а Word-файлы нет (при наличии у Zotero собственного кэша
берёт плоский текст оттуда, без страниц). Конвертация делается через LibreOffice в «безголовом» режиме:
`soffice --headless --convert-to pdf`. Сохраняются страницы, таблицы, сноски, закладки заголовков, название и автор из
свойств документа. На проверенных файлах (три .doc и три .docx, в том числе книга в 358 страниц) конвертация занимает
2-6 секунд на файл, сохранность текста для индексатора 0,92-1,00 (таблицы читаются хуже: индексатор склеивает ячейки).

Что учесть:
- Разбивка на страницы определяется LibreOffice, а не Word. При отсутствии шрифтов (например, Times New Roman вместо
  аналога) страницы могут разойтись с тем, что видно в Word: «стр.» в ссылках будет номером страницы этого PDF.
- Запускается отдельный экземпляр с временным профилем: он не мешает открытому LibreOffice и не зависит от него.
- Исправления и комментарии экспортируются в том виде, в котором документ показывается по умолчанию.
- Защищённые паролем или повреждённые файлы LibreOffice не открывает: скрипт сообщает об ошибке.

Нужен LibreOffice (`brew install --cask libreoffice`). Запускается через office-to-pdf.sh. Исходный файл не изменяется.

Коды выхода: 0 - успех; 2 - ошибка (нет LibreOffice, файл не открывается, выход уже существует).
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OFFICE_EXT = (".doc", ".docx", ".docm", ".odt")
MAC_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"


def find_soffice():
    return shutil.which("soffice") or (MAC_SOFFICE if Path(MAC_SOFFICE).exists() else None)


def check_signature(src: Path):
    """LibreOffice открывает любой мусор как простой текст и создаёт «PDF» из него, поэтому формат проверяем сами.
    Возвращает None, если файл похож на документ, иначе текст ошибки."""
    head = src.read_bytes()[:2048]
    ext = src.suffix.lower()
    ole = head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")        # старый .doc и зашифрованные файлы Office
    if ext in (".docx", ".docm", ".odt"):
        if head.startswith(b"PK") or ole:        # OLE под расширением .docx: старый .doc с чужим расширением или файл с паролем
            return None
        return f"Файл {ext} не является документом (нет сигнатуры ZIP): он повреждён или это другой формат."
    if ole or head.lstrip().startswith((b"{\\rtf", b"<html", b"<HTML", b"<?xml", b"<!DOCTYPE")) or head.startswith(b"PK"):
        return None                                                  # .doc бывает RTF, HTML или даже docx
    return "Файл .doc не похож на документ Word (нет сигнатуры OLE/RTF/HTML): он повреждён или это простой текст."


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="файл .doc, .docx, .docm или .odt")
    ap.add_argument("output", help="куда записать PDF (не должен совпадать с input)")
    ap.add_argument("--timeout", type=int, default=600, help="сколько секунд ждать конвертацию (по умолчанию 600)")
    ap.add_argument("--force", action="store_true", help="перезаписать output, если он уже есть")
    return ap.parse_args()


def convert(src: Path, dst: Path, timeout: int):
    """Возвращает (None, сообщение об ошибке) или (dst, '')."""
    soffice = find_soffice()
    if not soffice:
        return None, "Не найден LibreOffice (soffice): brew install --cask libreoffice"
    with tempfile.TemporaryDirectory(prefix="office-") as tmp:
        profile, outdir = Path(tmp) / "profile", Path(tmp) / "out"
        outdir.mkdir()
        # копия под ASCII-именем: пути с кириллицей и пробелами проходят, но так нагляднее и без сюрпризов с «~$»
        work = Path(tmp) / ("input" + src.suffix.lower())
        shutil.copy2(src, work)
        try:
            r = subprocess.run([soffice, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
                                "--convert-to", "pdf", "--outdir", str(outdir), str(work)],
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, f"LibreOffice не уложился в {timeout} с: файл слишком тяжёлый или повреждён"
        out = outdir / "input.pdf"
        if not out.exists() or out.stat().st_size == 0:
            err = (r.stderr or r.stdout).strip().splitlines()
            return None, "LibreOffice не создал PDF (файл защищён паролем или повреждён?): " + (err[-1][:200] if err else "без сообщения")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, dst)
    return dst, ""


def main() -> int:
    args = parse_args()
    src, dst = Path(args.input), Path(args.output)
    if not src.is_file():
        print(f"Не найден файл: {src}", file=sys.stderr)
        return 2
    if src.suffix.lower() not in OFFICE_EXT:
        print(f"Формат {src.suffix or '(без расширения)'} не поддерживается (нужны {', '.join(OFFICE_EXT)}).", file=sys.stderr)
        return 2
    if src.name.startswith("~$"):
        print("Это служебный файл блокировки Word (~$…), а не документ.", file=sys.stderr)
        return 2
    bad = check_signature(src)
    if bad:
        print(bad, file=sys.stderr)
        return 2
    if src.resolve() == dst.resolve():
        print("Выходной файл должен отличаться от входного", file=sys.stderr)
        return 2
    if dst.exists() and not args.force:
        print(f"Файл уже существует: {dst} (используйте --force, чтобы перезаписать)", file=sys.stderr)
        return 2
    out, err = convert(src, dst, args.timeout)
    if not out:
        print(err, file=sys.stderr)
        return 2
    try:
        import pymupdf as fitz
        fitz.TOOLS.mupdf_display_errors(False)
        doc = fitz.open(str(dst))
        pages, meta = len(doc), doc.metadata or {}
        letters = sum(c.isalpha() for p in doc for c in p.get_text())
    except Exception:  # noqa: BLE001
        pages, meta, letters = None, {}, None
    print(f"Записано: {dst}  (страниц: {pages if pages is not None else '?'}, размер {dst.stat().st_size / 1e6:.1f} МБ)")
    print(f"Название: {meta.get('title') or 'не указано'}; автор: {meta.get('author') or 'не указан'}")
    if letters == 0:
        print("Внимание: в PDF нет текста (в документе только картинки, например вставленный скан): понадобится OCR.")
    print("Страницы размечены LibreOffice и могут отличаться от Word: «стр.» в ссылках это номер страницы PDF.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
