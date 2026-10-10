#!/usr/bin/env python3
"""Проверяет один PDF перед добавлением в Zotero и говорит, что с ним делать.

Проверки (только чтение, файл не меняется):
  1. Есть ли текстовый слой: сколько страниц с текстом, есть ли страницы-картинки и «векторные» страницы
     (буквы нарисованы контурами: текста нет, картинок нет, рисунков много).
  2. Читаем ли текст: доля служебных слов на странице, посторонние символы (признак битой кодировки).
  3. Читает ли файл индексатор zotero-mcp так же, как обычный просмотрщик (сравнение с PyMuPDF по
     трёхсловным последовательностям, число страниц с «таблицами» из символов |).
  4. Причина искажений, если они есть: текстовый слой ocrmypdf или белые подложки под строками.
  5. Особенности: повёрнутые страницы, развороты, испорченные метаданные, сдвиг между номером страницы
     PDF и печатным номером.

Итог: вердикт и готовые команды. Коды выхода: 0 - можно импортировать как есть (замечания возможны);
1 - нужно действие до импорта; 2 - ошибка (файл не открывается, защищён паролем).

Пороги подобраны по нескольким книгам (сканы, цифровой текст, ocrmypdf), это ориентиры, а не доказанные
границы. Запускается через check-pdf.sh.
"""
import argparse
import re
import shlex
import sys
from collections import Counter
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).parent))
from _pdf_common import inspector_pages, scan_pages  # noqa: E402  (заодно отключает печать ошибок MuPDF)

STOP = set("и в на не что как это с по к из для от или а но то при его ее их он она они был была были быть также "
           "которые который которая the of and to in is that for with on as are by be this from".split())
MIN_TEXT_CHARS = 50          # страница считается текстовой, если символов больше
TEXT_SHARE_OK = 0.80         # доля текстовых страниц среди непустых, ниже которой нужен OCR
STOP_MEDIAN_MIN = 0.10       # медиана доли служебных слов; ниже - текст похож на мусор
ODD_PAGE_SHARE = 0.20        # доля страниц с посторонними символами, выше которой слой считается битым
FIDELITY_OK = 0.90          # ниже: индексатор искажает текст
FIDELITY_GOOD = 0.95        # между FIDELITY_OK и этим значением: пригоден с ограничениями
LOW_PAGES_SHARE = 0.10      # доля страниц ниже FIDELITY_OK, выше которой файл «с ограничениями»
PIPE_PAGE = 20               # столько символов | на странице - страница, вероятно, искажена
SAMPLE = 40                  # страниц для медленных проверок (повороты, развороты, подложки, OCR-слой)


def words(text: str):
    return re.findall(r"[а-яёa-z0-9]+", text.lower().replace("|", " "))


def tri(w):
    return {tuple(w[i:i + 3]) for i in range(len(w) - 2)}


def sample_pages(n: int, k: int = SAMPLE):
    if n <= k:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def odd_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 50:
        return 0.0
    odd = sum(1 for c in text if 0x0180 <= ord(c) <= 0x02AF or 0xE000 <= ord(c) <= 0xF8FF or ord(c) == 0xFFFD)
    return odd / len(letters)


def analyze_text(doc):
    """Постранично: символы, слова, служебные слова, посторонние символы, содержимое страницы."""
    rows = []
    for i, page in enumerate(doc):
        t = page.get_text()
        w = words(t)
        rows.append({
            "i": i, "chars": len(t.strip()), "words": len(w),
            "stop": (sum(1 for x in w if x in STOP) / len(w)) if len(w) >= 40 else None,
            "odd": odd_share(t),
            "images": len(page.get_images()),
            "has_text": len(t.strip()) >= MIN_TEXT_CHARS,
        })
    return rows


def classify_empty(doc, rows):
    """Для страниц без текста: картинка, векторная (много рисунков) или пустая."""
    for r in rows:
        if r["has_text"]:
            r["kind"] = "text"
        elif r["images"]:
            r["kind"] = "image"
        else:
            r["kind"] = "blank"
    # «пустые» страницы с большим числом рисунков - векторный текст; проверяем выборочно (get_drawings медленный)
    cand = [r["i"] for r in rows if r["kind"] == "blank"]
    for i in sample_pages(len(cand), 12) if cand else []:
        page_i = cand[i]
        if len(doc[page_i].get_drawings()) > 300:
            rows[page_i]["kind"] = "vector"
    vec = sum(1 for r in rows if r["kind"] == "vector")
    sampled = len(sample_pages(len(cand), 12)) if cand else 0
    if sampled and vec / sampled > 0.5:  # распространяем вывод выборки на остальные пустые страницы
        for r in rows:
            if r["kind"] == "blank":
                r["kind"] = "vector"
    return rows


def detect_layout(doc, idx):
    """Повёрнутые боком страницы, развороты, OCR-слой, белые подложки (по выборке страниц)."""
    sideways = spreads = ocr_layers = ocr_fixed = ocr_unfixed = white_boxes = checked = 0
    for i in idx:
        page = doc[i]
        checked += 1
        for x in page.get_xobjects():
            if x[1].startswith("OCR"):
                ocr_layers += 1
                st = doc.xref_stream(x[0]) or b""
                if re.search(rb"\bTm\b", st):
                    ocr_fixed += 1
                elif re.search(rb"\bTd\b", st):
                    ocr_unfixed += 1
        d = page.get_text("dict")
        dirs = Counter()
        for b in d["blocks"]:
            for ln in b.get("lines", []):
                dirs[(round(ln["dir"][0]), round(ln["dir"][1]))] += 1
        if dirs and dirs.most_common(1)[0][0] != (1, 0) and page.rotation == 0:
            sideways += 1
        w, h = page.rect.width, page.rect.height
        if w > h * 1.3:  # альбомная страница с текстом: возможно разворот (две страницы книги)
            xs = [wd[0] for wd in page.get_text("words")] + [wd[2] for wd in page.get_text("words")]
            if xs:
                mid = [x for x in xs if 0.46 * w < x < 0.54 * w]
                if len(mid) < 0.02 * len(xs):
                    spreads += 1
        # белые закрашенные прямоугольники под строками (невидимые, но мешают индексатору)
        try:
            dr = page.get_drawings()
        except Exception:
            dr = []
        wb = [x for x in dr if x.get("fill") == (1.0, 1.0, 1.0) and x["rect"].width > 0.4 * w and x["rect"].height < 25]
        if len(wb) >= 5:
            white_boxes += 1
    return {"sideways": sideways, "spreads": spreads, "ocr_layers": ocr_layers, "ocr_fixed": ocr_fixed, "ocr_unfixed": ocr_unfixed, "white_boxes": white_boxes,
            "checked": checked}


def printed_offset(doc, idx):
    """Сдвиги «номер страницы PDF - печатный номер» по числам в первых и последних строках страниц.

    Возвращает список (сдвиг, страниц с таким сдвигом, страниц с найденным числом всего) для сдвигов, которые
    подтверждены минимум 4 страницами; сдвиг может меняться по книге (вклейки, пропущенные номера)."""
    votes = Counter()
    used = 0
    for i in idx:
        lines = [ln.strip() for ln in doc[i].get_text().split("\n") if ln.strip()]
        edge = lines[:2] + lines[-2:]
        nums = [int(m) for ln in edge for m in re.findall(r"(?<![\d.,])(\d{1,4})(?![\d.,])", ln)]
        cand = {(i + 1) - n for n in nums if 0 < n < 2000 and abs((i + 1) - n) < 60}
        if cand:
            used += 1
            votes.update(cand)
    if used < 5:
        return None
    top = [(o, c, used) for o, c in votes.most_common(3) if c >= 4 and c / used >= 0.2]
    return top or None


def indexer_read(path, doc, rows):
    """Как читает файл индексатор: страницы без текста, страницы-«таблицы», сохранность слов."""
    from zotero_mcp.extract import extract_pdf
    res = extract_pdf(path)
    pages = [p if isinstance(p, str) else getattr(p, "markdown", str(p)) for p in res.pages]
    truth = [tri(words(doc[i].get_text())) for i in range(len(doc))]
    fid = [len(t & tri(words(x))) / len(t) for t, x in zip(truth, pages) if len(t) > 30]
    return {
        "needs_ocr": len(res.needs_ocr),
        "pipe_pages": sum(1 for x in pages if x.count("|") > PIPE_PAGE),
        "fidelity": (sum(fid) / len(fid)) if fid else None,
        "low_pages": sum(1 for v in fid if v < FIDELITY_OK),
        "checked": len(fid),
    }


def garbled_meta(meta: dict) -> list:
    bad = []
    for k in ("title", "author"):
        v = (meta.get(k) or "").strip()
        if not v:
            bad.append(f"{k}: пусто")
        elif re.search(r"[˜¢£¤¥¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿]", v) or re.search(r"\.(qxd|indd|doc|docx|pdf)$|^Microsoft Word|^Untitled",
                                                                       v, re.I):
            bad.append(f"{k}: «{v[:50]}» (похоже на служебное или искажённое)")
    return bad


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", help="PDF для проверки")
    ap.add_argument("--no-indexer", action="store_true", help="не запускать проверку индексатора (быстрее)")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.pdf)
    if not path.is_file():
        print(f"Не найден файл: {path}", file=sys.stderr)
        return 2
    try:
        doc = fitz.open(str(path))
    except Exception as e:  # noqa: BLE001
        print(f"Файл не открывается: {e}", file=sys.stderr)
        return 2
    if doc.needs_pass:
        print("PDF защищён паролем: снимите защиту и повторите.", file=sys.stderr)
        return 2
    n = len(doc)
    if n == 0:
        print("В файле нет страниц.", file=sys.stderr)
        return 2

    missing, damaged = scan_pages(doc)
    insp_n = None
    if not args.no_indexer:
        try:
            insp_n = inspector_pages(path)
        except Exception:  # noqa: BLE001
            insp_n = None
    structure_bad = bool(missing) or (insp_n is not None and insp_n != n)

    rows = classify_empty(doc, analyze_text(doc))
    kinds = Counter(r["kind"] for r in rows)
    content = n - kinds["blank"]                      # непустые страницы (с текстом, картинкой или рисунками)
    text_share = kinds["text"] / content if content else 0.0
    stops = sorted(r["stop"] for r in rows if r["stop"] is not None)
    stop_median = stops[len(stops) // 2] if stops else None
    odd_pages = [r["i"] + 1 for r in rows if r["odd"] > 0.2]
    idx = sample_pages(n)
    lay = detect_layout(doc, idx)
    off = printed_offset(doc, idx) if kinds["text"] else None
    meta_bad = garbled_meta(doc.metadata or {})

    print(f"Файл: {path.name}  ({path.stat().st_size / 1e6:.1f} МБ, страниц: {n}, формат {doc.metadata.get('format', '?')})")
    print(f"Страницы: с текстом {kinds['text']}, только картинка {kinds['image']}, векторные (текст контурами) "
          f"{kinds['vector']}, пустые {kinds['blank']}")
    if stop_median is not None:
        print(f"Читаемость текста: медиана доли служебных слов {stop_median:.2f} (нормально около 0,15); "
              f"страниц с посторонними символами: {len(odd_pages)}")

    actions, notes = [], []
    verdict = None
    full = path.resolve()
    here = Path(__file__).resolve().parent                    # папка scripts/: команды печатаем с полными путями,
    fix_sh = shlex.quote(str(here / "fix-ocr-layer.sh"))      # чтобы их можно было вставить из любой папки
    check_sh = shlex.quote(str(here / "check-text-layer.sh"))
    src_q = shlex.quote(str(full))
    ocr_q = shlex.quote(str(full.with_name(full.stem + "_ocr.pdf")))
    fix_q = shlex.quote(str(full.with_name(full.stem + "_ocr_fix.pdf")))
    fixed_q = shlex.quote(str(full.with_name(full.stem + "_fix.pdf")))
    rep_q = shlex.quote(str(full.with_name(full.stem + "_repaired.pdf")))
    repair_sh = shlex.quote(str(here / "repair-pdf.sh"))
    check_pdf_sh = shlex.quote(str(here / "check-pdf.sh"))

    # 0. Повреждённая структура: страницы в разных программах считаются по-разному
    if structure_bad:
        verdict = "ФАЙЛ ПОВРЕЖДЁН: структура страниц нарушена"
        parts = []
        if insp_n is not None and insp_n != n:
            parts.append(f"просмотрщик видит {n} страниц, индексатор {insp_n}: номера «стр.» в индексе и в читалке "
                         f"будут расходиться (в этой книге разница нарастает по тексту)")
        if missing:
            parts.append(f"в файле нет объектов {len(missing)} страниц (потеряны безвозвратно): {', '.join(map(str, missing[:20]))}"
                         + ("…" if len(missing) > 20 else ""))
        notes.append("; ".join(parts) + ".")
        actions.append(f"{repair_sh} {src_q} {rep_q}")
        actions.append(f"{check_pdf_sh} {rep_q}   # повторите проверку на пересобранном файле")
        notes.append("Пересборка выравнивает нумерацию и делает файл читаемым для всех программ, но потерянные страницы "
                     "она не возвращает: их можно получить только из другой копии книги. Если потерь много, "
                     "поищите другой источник.")
    # 1. Нет текстового слоя
    elif content and text_share < TEXT_SHARE_OK:
        no_text = [r["i"] + 1 for r in rows if r["kind"] in ("image", "vector")]
        kind = "векторные страницы (текст нарисован контурами)" if kinds["vector"] > kinds["image"] else "сканы"
        verdict = f"НУЖЕН OCR: текст есть только на {text_share:.0%} страниц, остальные: {kind}"
        rot = " --rotate-pages" if lay["sideways"] else ""
        actions.append(f"ocrmypdf -l rus+eng --skip-text --output-type pdf{rot} {src_q} {ocr_q}")
        actions.append(f"{fix_sh} {ocr_q} {fix_q} --check")
        if kinds["text"] and kinds["text"] > 0.05 * content:
            notes.append(f"Часть страниц ({kinds['text']}) уже с текстом: `--skip-text` оставит его как есть. Если этот "
                         "текст плохой, см. следующий пункт.")
    # 2. Текст есть, но битый
    elif (stop_median is not None and stop_median < STOP_MEDIAN_MIN) or len(odd_pages) > ODD_PAGE_SHARE * n:
        verdict = "ТЕКСТОВЫЙ СЛОЙ БИТЫЙ: текст есть, но нечитаем (кодировка или скан со старым плохим OCR)"
        actions.append(f"ocrmypdf -l rus+eng --force-ocr --output-type pdf {src_q} {ocr_q}")
        actions.append(f"{fix_sh} {ocr_q} {fix_q} --check")
        notes.append("`--force-ocr` растеризует страницы и распознаёт заново: векторный текст исчезнет, файл может "
                     "измениться по размеру. Сохраните исходник.")
    elif args.no_indexer:
        verdict = "ТЕКСТ ЕСТЬ И ЧИТАЕМ (проверка индексатора пропущена)"
    else:
        # 3. Как читает индексатор
        ix = indexer_read(str(path), doc, rows)
        print(f"Индексатор: страниц без текста по его мнению {ix['needs_ocr']}, страниц-«таблиц» {ix['pipe_pages']}, "
              f"сохранность текста {ix['fidelity']:.3f}, страниц ниже {FIDELITY_OK}: {ix['low_pages']} из {ix['checked']}"
              if ix["fidelity"] is not None else
              f"Индексатор: страниц без текста по его мнению {ix['needs_ocr']}, страниц-«таблиц» {ix['pipe_pages']}")
        if ix["needs_ocr"] > 0.5 * n:
            verdict = "ИНДЕКСАТОР НЕ ВИДИТ ТЕКСТ, хотя в просмотрщике он есть"
            notes.append("Типичная причина: PDF/A (Ghostscript переписал слой). Пересоберите из исходного скана с "
                         "`--output-type pdf`.")
            actions.append(f"ocrmypdf -l rus+eng --skip-text --output-type pdf <исходный_скан.pdf> {ocr_q}")
            actions.append(f"{fix_sh} {ocr_q} {fix_q} --check")
        elif ix["fidelity"] is not None and ix["fidelity"] < FIDELITY_OK:
            verdict = "ИНДЕКСАТОР ИСКАЖАЕТ ТЕКСТ (слова теряются или переставляются)"
            if lay["ocr_unfixed"]:
                actions.append(f"{fix_sh} {src_q} {fixed_q} --check")
                notes.append("Причина: текстовый слой ocrmypdf (найден на проверенных страницах, ещё не исправлен).")
            elif lay["ocr_fixed"]:
                notes.append("Слой ocrmypdf уже исправлен fix-ocr-layer.sh, остаточные искажения связаны с оглавлением, "
                             "таблицами или строками с большими пробелами. Запасной вариант: `.txt` с текстом к карточке "
                             "(без номеров страниц).")
            elif lay["white_boxes"] > 0.3 * lay["checked"]:
                notes.append("Причина: под каждой строкой лежит невидимая белая закрашенная полоса, и индексатор принимает "
                             "их за линейки таблицы. Скрипта очистки в репозитории пока нет: полосы удаляются из потока "
                             "содержимого страниц (операторы `1 g ... re f`) без изменения вида страницы.")
            else:
                notes.append("Причина не определена. Запасной вариант: добавить к карточке `.txt` с текстом (без номеров "
                             "страниц) и положиться на него.")
        elif ix["fidelity"] is not None and (ix["fidelity"] < FIDELITY_GOOD or ix["low_pages"] > LOW_PAGES_SHARE * ix["checked"]):
            verdict = "МОЖНО ИМПОРТИРОВАТЬ, НО С ОГРАНИЧЕНИЯМИ: часть страниц индексатор читает с искажениями"
            notes.append(f"Страниц с искажениями: {ix['low_pages']} из {ix['checked']}. Чаще всего это оглавление, таблицы, "
                         "списки литературы и строки с большими пробелами. Для проверки найдите поиском знакомую фразу "
                         "с такой страницы.")
            if lay["ocr_unfixed"]:
                actions.append(f"{fix_sh} {src_q} {fixed_q} --check")
        else:
            verdict = "МОЖНО ИМПОРТИРОВАТЬ КАК ЕСТЬ"

    # Особенности
    if damaged:
        notes.append(f"Страницы с повреждённым содержимым (ошибки чтения потока, текст на них потерян или обрезан): "
                     f"{', '.join(map(str, damaged[:20]))}" + ("…" if len(damaged) > 20 else "") + ". Исправить их нельзя: "
                     "нужна другая копия книги.")
    if lay["sideways"]:
        notes.append(f"Повёрнуты боком около {lay['sideways']} из {lay['checked']} проверенных страниц: нужен "
                     "`--rotate-pages` при OCR; страницы с низкой уверенностью поворота проверьте глазами.")
    if lay["spreads"] > 0.3 * lay["checked"]:
        notes.append("Страницы похожи на развороты (две книжные страницы на лист): номер «стр.» в ссылках будет номером "
                     "разворота, а не печатным.")
    if off:
        parts = ", ".join(f"{o:+d} ({c} стр.)" for o, c, _ in off)
        mixed = " Сдвиг меняется по книге: сверяйте номер по соседним страницам." if len(off) > 1 else ""
        notes.append(f"Сдвиг номера страницы PDF и печатного номера: {parts} из {off[0][2]} проверенных.{mixed} "
                     "Числа взяты у краёв страниц и могут быть служебными (колонтитулы скана): сверьте с книгой. В ссылках используйте формат «с. N [печатный]», как у Ждана.")
    elif kinds["text"]:
        notes.append("Печатные номера страниц по краям не определены автоматически: сверьте вручную одну-две страницы.")
    if meta_bad:
        notes.append("Метаданные файла испорчены или пусты (" + "; ".join(meta_bad) + "): внесите название, авторов и "
                     "год в карточку Zotero вручную.")
    if not doc.get_toc() and n > 100:
        notes.append("В файле нет закладок: оглавления для навигации не будет (на поиск не влияет).")

    print(f"\nВЕРДИКТ: {verdict}")
    if actions:
        print("\nЧто сделать:")
        for a in actions:
            print(f"  {a}")
    if notes:
        print("\nЗамечания:")
        for t in notes:
            print(f"  - {t}")
    print(f"\nПосле импорта: zotero-mcp update-db --fulltext, затем {check_sh} и поиск по знакомой фразе из книги.")
    return 0 if verdict.startswith(("МОЖНО", "ТЕКСТ ЕСТЬ")) else 1


if __name__ == "__main__":
    sys.exit(main())
