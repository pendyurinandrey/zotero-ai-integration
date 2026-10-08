#!/usr/bin/env python3
"""Исправляет текстовый слой PDF, созданный ocrmypdf, чтобы индексатор zotero-mcp читал его связно.

Проблема. ocrmypdf записывает распознанный текст по словам: каждое слово это отдельный сдвиг `dx dy Td`
относительно начала строки, а размер шрифта соответствует высоте строчных букв (заметно меньше
видимого кегля). Индексатор (библиотека pdf-inspector) отсчитывает такой сдвиг от конца предыдущего
слова, поэтому положения слов в строке «разъезжаются», а промежутки между словами выглядят для него
слишком большими. Он принимает строки за столбцы таблицы: порядок слов в тексте для поиска сбивается,
часть слов оказывается в «ячейках» вида `|слово|||слово|`. Без исправления на PDF из ocrmypdf
искажено большинство страниц.

Что делает скрипт. В невидимом слое OCR:
  * заменяет `dx dy Td` на абсолютные `1 0 0 1 x y Tm`;
  * увеличивает размер шрифта в --scale раз и во столько же раз уменьшает горизонтальный масштаб `Tz`,
    так что ширина слов не меняется.
Картинки страниц и видимое содержимое не затрагиваются. Текст при чтении через PyMuPDF прежний.

Ограничения. Лечит текстовый слой, созданный ocrmypdf с рендерером hocr (он по умолчанию). Слои других
программ, PDF/A (Ghostscript переписывает слой так, что индексатор не видит текст) и PDF без слоя
OCR скрипт не меняет. Реальные таблицы остаются таблицами. Полного исправления нет: на проверенной книге
сохранность текста для индексатора выросла с 0,69 до 0,93 (см. docs/setup/06-ocr.md).

Запускается через fix-ocr-layer.sh. Исходный файл не изменяется, результат пишется в новый файл.

Коды выхода: 0 - успех (в том числе «слоя OCR не найдено»); 2 - ошибка.
"""
import argparse
import re
import sys
from pathlib import Path

import pymupdf as fitz

NUM = rb"-?\d+(?:\.\d+)?"
TD = re.compile(rb"(" + NUM + rb")\s+(" + NUM + rb")\s+Td")
FONT = re.compile(rb"(/F\d+) ([\d.]+) Tf")
SIZED = re.compile(rb"(/F\d+ [\d.]+ Tf|[\d.]+ Tz)")
WS = b" \n\r\t"


def rewrite_positions(s: bytes) -> bytes:
    """`dx dy Td` внутри BT..ET -> `1 0 0 1 x y Tm` (x, y накапливаются от начала BT)."""
    out = bytearray()
    i, n = 0, len(s)
    x = y = 0.0
    while i < n:
        c = s[i:i + 1]
        if c == b"(":  # строка в скобках копируется как есть (внутри могут быть любые байты)
            j, depth = i + 1, 1
            while j < n and depth:
                b = s[j:j + 1]
                if b == b"\\":
                    j += 2
                    continue
                depth += (b == b"(") - (b == b")")
                j += 1
            out += s[i:j]
            i = j
            continue
        if s.startswith(b"BT", i) and (i == 0 or s[i - 1:i] in WS) and s[i + 2:i + 3] in WS:
            x = y = 0.0
            out += b"BT"
            i += 2
            continue
        m = TD.match(s, i)
        if m and (i == 0 or s[i - 1:i] in WS):
            x += float(m.group(1))
            y += float(m.group(2))
            out += b"1 0 0 1 %.2f %.2f Tm" % (x, y)
            i = m.end()
            continue
        out += c
        i += 1
    return bytes(out)


def scale_font(s: bytes, k: float) -> bytes:
    """Размер шрифта * k, горизонтальный масштаб / k (ширина слов прежняя)."""
    out = []
    for tok in SIZED.split(s):
        m = FONT.fullmatch(tok)
        if m:
            out.append(b"%s %.2f Tf" % (m.group(1), float(m.group(2)) * k))
            continue
        m = re.fullmatch(rb"([\d.]+) Tz", tok)
        if m:
            out.append(b"%.2f Tz" % (float(m.group(1)) / k))
            continue
        out.append(tok)
    return b"".join(out)


def ocr_layers(doc):
    """Номера xref слоёв OCR (форм-объекты с именем OCR-…, их создаёт ocrmypdf)."""
    found = []
    for page in doc:
        for xref, name, *_ in page.get_xobjects():
            if name.startswith("OCR"):
                found.append(xref)
    return found


def fidelity(path: str, truth_path: str):
    """Доля трёхсловных последовательностей истинного текста (PyMuPDF), найденных в тексте индексатора.

    Возвращает (средняя, число страниц ниже 0,9, число проверенных страниц)."""
    from zotero_mcp.extract import extract_pdf

    def words(t):
        return re.findall(r"[а-яёa-z0-9]+", t.lower().replace("|", " "))

    def tri(w):
        return {tuple(w[i:i + 3]) for i in range(len(w) - 2)}

    truth = [tri(words(p.get_text())) for p in fitz.open(truth_path)]
    doc = extract_pdf(path)
    pages = [p if isinstance(p, str) else getattr(p, "markdown", str(p)) for p in doc.pages]
    r = [len(t & tri(words(x))) / len(t) for t, x in zip(truth, pages) if len(t) > 30]
    return (sum(r) / len(r) if r else 0.0), sum(1 for v in r if v < 0.9), len(r)


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="PDF после ocrmypdf (с текстовым слоем OCR)")
    ap.add_argument("output", help="куда записать исправленный PDF (не должен совпадать с input)")
    ap.add_argument("--scale", type=float, default=1.25,
                    help="во сколько раз увеличить размер шрифта слоя (по умолчанию 1.25; на проверенной книге "
                         "лучшие значения лежали в диапазоне 1.25-1.3)")
    ap.add_argument("--check", action="store_true",
                    help="после исправления сравнить, как индексатор читает файл до и после (1-2 минуты на книгу)")
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
    if args.scale <= 0:
        print("--scale должен быть больше нуля", file=sys.stderr)
        return 2

    doc = fitz.open(str(src))
    layers = ocr_layers(doc)
    if not layers:
        print("Слоя OCR от ocrmypdf не найдено (нет форм-объектов OCR-…): файл не изменён, выход не создан.")
        return 0

    changed = skipped = 0
    for xref in sorted(set(layers)):
        stream = doc.xref_stream(xref)
        if stream is None or re.search(rb"\bTm\b", stream) or not TD.search(stream):
            # уже абсолютные позиции (слой переписан ранее) или нет сдвигов Td (пустая страница)
            skipped += 1
            continue
        doc.update_stream(xref, scale_font(rewrite_positions(stream), args.scale))
        changed += 1
    if not changed:
        print("Слои OCR уже исправлены или в нестандартном формате: файл не изменён, выход не создан.")
        return 0
    doc.save(str(dst), garbage=3, deflate=True)
    print(f"Исправлено слоёв OCR: {changed} (пропущено: {skipped}). Записано: {dst}")

    if args.check:
        before = fidelity(str(src), str(src))
        after = fidelity(str(dst), str(src))
        print("Сохранность текста для индексатора (доля трёхсловных последовательностей; 1,00 = без искажений):")
        print(f"  до:    {before[0]:.3f}, страниц ниже 0,9: {before[1]} из {before[2]}")
        print(f"  после: {after[0]:.3f}, страниц ниже 0,9: {after[1]} из {after[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
