#!/usr/bin/env python3
"""Готовит папку с книгами и статьями к загрузке в Zotero и индексу zotero-mcp.

Для каждого файла в папке (без вложенных, кроме папок со страницами SVG):
  * PDF, который можно загружать как есть, переносится в подпапку `fixed` (или копируется с --copy);
  * PDF с дефектами исправляется (repair-pdf.sh, fix-ocr-layer.sh), результат пишется в `fixed`;
  * скан или PDF с текстом из контуров распознаётся (OCR: ocrmypdf; векторные страницы сначала растеризуются);
  * DjVu -> PDF -> OCR (djvu-to-pdf.sh); EPUB -> PDF (epub-to-pdf.sh --text-only); RTF -> .txt (rtf-to-txt.sh);
  * папка со страницами SVG -> PDF -> OCR (svg-to-pdf.sh);
  * .txt, .md, .html лежат в `fixed` как есть (индексатор читает их сам).
Исходные файлы (кроме PDF, которые можно загружать как есть и которые переносятся) не изменяются.

Решение по каждому PDF принимает check-pdf.sh (его вердикт), после каждого шага результат проверяется заново.
В конце печатается сводная таблица: что сделано, сохранность текста для индексатора до и после, замечания.
Таблицы и метаданные (название, автор) автоматически не исправляются: см. замечания в таблице.

Запускается через prepare-books.sh. Долгая операция (OCR книги занимает минуты): запускайте в фоне.

Коды выхода: 0 - все файлы обработаны; 1 - часть файлов не удалось подготовить; 2 - ошибка запуска.
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SH = {n: str(HERE / f"{n}.sh") for n in ("check-pdf", "repair-pdf", "fix-ocr-layer", "svg-to-pdf", "djvu-to-pdf",
                                          "epub-to-pdf", "rtf-to-txt", "rasterize-pdf")}
AS_IS_EXT = {".txt", ".md", ".html", ".htm"}
SKIP_SUFFIX = ("_ocr", "_fix", "_repaired", "_ocr_fix")


def log(msg=""):
    print(msg, flush=True)


def run(cmd, check_ok=False):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    return r


# --- Разбор вердикта check-pdf ---------------------------------------------------------------------------------
def run_check(path: Path) -> dict:
    r = run([SH["check-pdf"], path])
    t = r.stdout
    info = {"raw": t, "verdict": "", "code": "unknown", "fid": None, "low": None, "checked": None, "pages": None,
            "text": 0, "image": 0, "vector": 0, "needs_ocr": None, "sideways": 0, "notes": []}
    m = re.search(r"ВЕРДИКТ: (.*)", t)
    if not m:
        info["verdict"] = (r.stderr.strip() or "check-pdf не дал вердикта")[:200]
        info["code"] = "error"
        return info
    v = info["verdict"] = m.group(1).strip()
    if v.startswith("МОЖНО ИМПОРТИРОВАТЬ КАК ЕСТЬ") or v.startswith("ТЕКСТ ЕСТЬ И ЧИТАЕМ"):
        info["code"] = "ok"
    elif "кэша Zotero" in v:
        info["code"] = "blind"
    elif v.startswith("МОЖНО ИМПОРТИРОВАТЬ, НО С ОГРАНИЧЕНИЯМИ"):
        info["code"] = "limited"
    elif v.startswith("ИНДЕКСАТОР ИСКАЖАЕТ"):
        info["code"] = "distorted"
    elif v.startswith("ИНДЕКСАТОР НЕ ВИДИТ"):
        info["code"] = "blind"
    elif v.startswith("НУЖЕН OCR"):
        info["code"] = "ocr_vector" if "векторные" in v else "ocr"
    elif v.startswith("ТЕКСТОВЫЙ СЛОЙ БИТЫЙ"):
        info["code"] = "garbled"
    elif v.startswith("ФАЙЛ ПОВРЕЖДЁН"):
        info["code"] = "damaged"
    for key, pat in (("pages", r"страниц: (\d+)"),):
        mm = re.search(pat, t)
        info[key] = int(mm.group(1)) if mm else None
    mm = re.search(r"с текстом (\d+), только картинка (\d+), векторные \(текст контурами\) (\d+), пустые (\d+)", t)
    if mm:
        info["text"], info["image"], info["vector"] = int(mm.group(1)), int(mm.group(2)), int(mm.group(3))
    mm = re.search(r"сохранность текста ([\d.]+), страниц ниже [\d.]+: (\d+) из (\d+)", t)
    if mm:
        info["fid"], info["low"], info["checked"] = float(mm.group(1)), int(mm.group(2)), int(mm.group(3))
    mm = re.search(r"страниц без текста по его мнению (\d+)", t)
    info["needs_ocr"] = int(mm.group(1)) if mm else None
    mm = re.search(r"Повёрнуты боком около (\d+)", t)
    info["sideways"] = int(mm.group(1)) if mm else 0
    for ln in t.splitlines():
        if ln.startswith("  - "):
            info["notes"].append(ln[4:].strip())
    return info


def fid_s(info):
    return "-" if info is None or info["fid"] is None else f"{info['fid']:.2f}"


# --- Шаги -----------------------------------------------------------------------------------------------------
class Step:
    """Результат шага: путь нового файла (или None, если шаг ничего не изменил) и пояснение."""

    def __init__(self, path, label):
        self.path, self.label = path, label


def do_repair(src: Path, dst: Path):
    r = run([SH["repair-pdf"], src, dst, "--force"])
    if not dst.exists():
        return None
    done = [ln.strip()[2:] for ln in r.stdout.splitlines() if ln.strip().startswith("- ")]
    return Step(dst, "repair-pdf (" + "; ".join(done)[:200] + ")")


def do_fix_ocr(src: Path, dst: Path):
    r = run([SH["fix-ocr-layer"], src, dst, "--check", "--force"])
    if not dst.exists():
        return None
    return Step(dst, "fix-ocr-layer")


def do_rasterize(src: Path, dst: Path):
    r = run([SH["rasterize-pdf"], src, dst, "--force"])
    return Step(dst, "растеризация страниц (серый JPEG)") if dst.exists() else None


def do_ocr(src: Path, dst: Path, lang: str, rotate: bool, force: bool = False, oversample: bool = False):
    cmd = ["ocrmypdf", "-l", lang, "--force-ocr" if force else "--skip-text", "--output-type", "pdf"]
    if rotate:
        cmd += ["--rotate-pages", "--rotate-pages-threshold", "6"]
    if oversample:
        cmd += ["--oversample", "300"]
    r = run(cmd + [src, dst])
    if not dst.exists():
        err = (r.stderr or r.stdout).strip().splitlines()
        return None, (err[-1] if err else "ocrmypdf завершился без файла")
    return Step(dst, "OCR (ocrmypdf" + (", повороты страниц" if rotate else "") + ")"), ""


def ocr_pipeline(src: Path, work: Path, tag: str, lang: str, rotate: bool, oversample: bool, steps: list):
    """Скан или растр -> OCR -> исправление слоя для индексатора. Возвращает путь результата или None."""
    ocr_out = work / f"{tag}_ocr.pdf"
    st, err = do_ocr(src, ocr_out, lang, rotate, oversample=oversample)
    if st is None:
        steps.append(f"OCR не удался: {err}")
        return None
    steps.append(st.label)
    fixed = work / f"{tag}_fix.pdf"
    fx = do_fix_ocr(ocr_out, fixed)
    if fx:
        steps.append(fx.label)
        return fx.path
    return ocr_out


def improve_pdf(src: Path, work: Path, tag: str, lang: str, steps: list, notes: list, first: dict = None):
    """Приводит PDF в годный вид по вердикту check-pdf. Возвращает (путь результата, итоговая проверка).
    Путь равен src, если менять нечего."""
    cur = src
    info = first or run_check(cur)
    seen = set()
    for it in range(5):
        code = info["code"]
        if code in ("ok", "error") or code in seen and code != "damaged":
            break
        seen.add(code)
        nxt = None
        out = work / f"{tag}_s{it}.pdf"
        if code == "damaged":
            st = do_repair(cur, out)
            nxt = st.path if st else None
            if st:
                steps.append(st.label)
        elif code in ("limited", "distorted"):
            for fn in (do_fix_ocr, do_repair):
                st = fn(cur, out)
                if st:
                    nxt = st.path
                    steps.append(st.label)
                    break
        elif code == "blind":
            st = do_repair(cur, out)
            if st:
                nxt = st.path
                steps.append(st.label)
            else:
                notes.append("индексатор не видит слой, ремонт не помог: страницы распознаны заново")
                rast = work / f"{tag}_r{it}.pdf"
                if do_rasterize(cur, rast):
                    steps.append("растеризация страниц (серый JPEG)")
                    nxt = ocr_pipeline(rast, work, f"{tag}_b{it}", lang, bool(info["sideways"]), False, steps)
        elif code in ("ocr", "ocr_vector", "garbled"):
            base = cur
            if code in ("ocr_vector", "garbled"):
                rast = work / f"{tag}_r{it}.pdf"
                if do_rasterize(cur, rast):
                    steps.append("растеризация страниц (серый JPEG)")
                    base = rast
            nxt = ocr_pipeline(base, work, f"{tag}_o{it}", lang, bool(info["sideways"]), False, steps)
        if not nxt:
            break
        new = run_check(nxt)
        # результат шага принимаем, если он не хуже: вердикт лучше или сохранность не ниже
        if rank(new) >= rank(info) or code in ("ocr", "ocr_vector", "garbled", "damaged", "blind"):
            cur, info = nxt, new
        else:
            notes.append("шаг не улучшил результат и отброшен")
            break
    return cur, info


def rank(info):
    order = {"ok": 5, "limited": 4, "distorted": 3, "blind": 2, "damaged": 2, "garbled": 1, "ocr": 1, "ocr_vector": 1,
             "unknown": 0, "error": 0}
    return (order.get(info["code"], 0), info["fid"] or 0)


# --- Обработка элементов --------------------------------------------------------------------------------------
def discover(folder: Path, out_name: str, only: str):
    items = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        if p.name.startswith(".") or p.name == out_name:
            continue
        if only and only.lower() not in p.name.lower():
            continue
        if p.is_dir():
            if sum(1 for f in p.iterdir() if f.suffix.lower() == ".svg") >= 3:
                items.append(("svg", p))
            continue
        ext = p.suffix.lower()
        if ext == ".pdf" and not p.stem.endswith(SKIP_SUFFIX):
            items.append(("pdf", p))
        elif ext == ".djvu" or ext == ".djv":
            items.append(("djvu", p))
        elif ext == ".epub":
            items.append(("epub", p))
        elif ext == ".rtf":
            items.append(("rtf", p))
        elif ext in AS_IS_EXT:
            items.append(("text", p))
        else:
            items.append(("unknown", p))
    return items


def place(src: Path, out_dir: Path, copy: bool, force: bool):
    dst = out_dir / src.name
    if dst.exists() and not force:
        return None
    out_dir.mkdir(exist_ok=True)
    (shutil.copy2 if copy else shutil.move)(str(src), str(dst))
    return dst


def process(kind, path: Path, out_dir: Path, work: Path, args):
    """Возвращает словарь результата строки сводки."""
    res = {"name": path.name, "kind": kind, "steps": [], "notes": [], "before": None, "after": None, "status": "?",
           "out": None}
    steps, notes = res["steps"], res["notes"]
    tag = re.sub(r"\W+", "_", path.stem)[:30] or "book"
    dst_pdf = out_dir / (path.stem + ".pdf")
    if kind in ("pdf", "djvu", "svg", "epub") and dst_pdf.exists() and not args.force:
        res["status"] = "пропущено: в fixed уже есть такой файл (--force перезапишет)"
        return res
    if kind == "unknown":
        res["status"] = "пропущено: неизвестный формат"
        return res
    if kind == "text":
        dst = place(path, out_dir, args.copy, args.force)
        res["status"] = "как есть" if dst else "пропущено: в fixed уже есть такой файл"
        steps.append("перенесён как есть" if not args.copy else "скопирован как есть")
        return res
    if kind == "rtf":
        dst = out_dir / (path.stem + ".txt")
        out_dir.mkdir(exist_ok=True)
        r = run([SH["rtf-to-txt"], path, dst] + (["--force"] if args.force else []))
        res["status"] = "RTF -> .txt" if dst.exists() else "ошибка: " + (r.stderr.strip() or r.stdout.strip())[:150]
        steps.append("rtf-to-txt")
        notes.append("в Zotero нужна карточка (книга/документ) с этим .txt")
        if "Внимание" in r.stdout:
            notes.append(r.stdout.strip().splitlines()[-1][:150])
        return res
    if kind == "epub":
        tmp = work / f"{tag}.pdf"
        r = run([SH["epub-to-pdf"], path, tmp, "--text-only", "--force"])
        if not tmp.exists():
            res["status"] = "ошибка: " + (r.stderr.strip() or r.stdout.strip())[:150]
            return res
        steps.append("epub-to-pdf --text-only")
        notes.append("номера страниц в PDF свои, не книжные")
        res["after"] = run_check(tmp)
        out_dir.mkdir(exist_ok=True)
        shutil.copy2(tmp, dst_pdf)
        res["out"] = dst_pdf.name
        res["status"] = status_of(res["after"])
        return res
    if kind in ("djvu", "svg"):
        raw = work / f"{tag}_raw.pdf"
        tool = "djvu-to-pdf" if kind == "djvu" else "svg-to-pdf"
        r = run([SH[tool], path, raw, "--force"])
        if not raw.exists():
            res["status"] = "ошибка: " + (r.stderr.strip() or r.stdout.strip())[:200]
            return res
        steps.append(tool)
        for ln in r.stdout.splitlines():
            if ln.startswith("Внимание") or ln.startswith("Нумерация"):
                notes.append(ln[:200])
        # растр -> OCR (для DjVu и SVG растр всегда без текста)
        result = ocr_pipeline(raw, work, tag, args.lang, True, kind == "djvu", steps)
        if not result:
            res["status"] = "ошибка OCR"
            return res
        cur, info = improve_pdf(result, work, tag + "_i", args.lang, steps, notes)
        res["after"] = info
        out_dir.mkdir(exist_ok=True)
        shutil.copy2(cur, dst_pdf)
        res["out"] = dst_pdf.name
        res["status"] = status_of(info)
        return res
    # pdf
    before = run_check(path)
    res["before"] = before
    if before["code"] == "error":
        res["status"] = "ошибка: " + before["verdict"]
        return res
    if before["code"] == "ok":
        dst = place(path, out_dir, args.copy, args.force)
        res["after"] = before
        res["status"] = "как есть" if dst else "пропущено: в fixed уже есть такой файл"
        steps.append("перенесён как есть" if not args.copy else "скопирован как есть")
        res["out"] = path.name
        return res
    cur, info = improve_pdf(path, work, tag, args.lang, steps, notes, first=before)
    if cur == path:
        # изменить нечего: «с ограничениями» тоже годится к загрузке как есть
        dst = place(path, out_dir, args.copy, args.force)
        res["after"] = info
        res["out"] = path.name
        res["status"] = status_of(info, unchanged=True) if dst else "пропущено: в fixed уже есть такой файл"
        steps.append("исправить не удалось, перенесён как есть" if not args.copy else "исправить не удалось, скопирован как есть")
        return res
    out_dir.mkdir(exist_ok=True)
    shutil.copy2(cur, dst_pdf)
    res["after"] = info
    res["out"] = dst_pdf.name
    res["status"] = status_of(info)
    return res


def status_of(info, unchanged=False):
    return {"ok": "готово", "limited": "готово, с оговорками", "distorted": "ТРЕБУЕТ ВНИМАНИЯ: индексатор искажает текст",
            "blind": "ТРЕБУЕТ ВНИМАНИЯ: индексатор не видит текст", "damaged": "ТРЕБУЕТ ВНИМАНИЯ: файл повреждён",
            "garbled": "ТРЕБУЕТ ВНИМАНИЯ: слой битый", "ocr": "ТРЕБУЕТ ВНИМАНИЯ: нет текста",
            "ocr_vector": "ТРЕБУЕТ ВНИМАНИЯ: нет текста"}.get(info["code"], "проверьте вручную") + (" (не менялся)" if unchanged else "")


# --- Основной поток -------------------------------------------------------------------------------------------
def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="папка с файлами")
    ap.add_argument("--out", default="fixed", help="подпапка для результата (по умолчанию fixed)")
    ap.add_argument("--only", default="", help="обработать только файлы, в названии которых есть эта строка")
    ap.add_argument("--dry-run", action="store_true", help="только проверить файлы и показать план, ничего не менять")
    ap.add_argument("--copy", action="store_true", help="файлы, годные как есть, копировать, а не переносить")
    ap.add_argument("--lang", default="rus+eng", help="языки OCR (по умолчанию rus+eng)")
    ap.add_argument("--force", action="store_true", help="перезаписывать файлы в подпапке результата")
    return ap.parse_args()


PLAN = {"ok": "как есть (перенос)", "limited": "исправление (repair-pdf / fix-ocr-layer), иначе как есть",
        "distorted": "исправление (repair-pdf / fix-ocr-layer)", "blind": "исправление или повторный OCR",
        "damaged": "пересборка страниц (repair-pdf)", "ocr": "OCR", "ocr_vector": "растеризация + OCR",
        "garbled": "растеризация + OCR заново", "error": "ошибка проверки"}


def main() -> int:
    args = parse_args()
    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        print(f"Не найдена папка: {folder}", file=sys.stderr)
        return 2
    missing = [t for t in ("ocrmypdf",) if not shutil.which(t)]
    if missing and not args.dry_run:
        print("Не найден ocrmypdf: brew install ocrmypdf tesseract-lang (docs/setup/06-ocr.md)", file=sys.stderr)
        return 2
    out_dir = folder / args.out
    items = discover(folder, args.out, args.only)
    if not items:
        print("В папке нет файлов, которые можно подготовить.")
        return 0
    log(f"Папка: {folder}\nРезультат: {out_dir}\nНайдено элементов: {len(items)}\n")
    t0 = time.time()
    results = []
    work = Path(tempfile.mkdtemp(prefix="prepare-books-"))
    try:
        for n, (kind, p) in enumerate(items, 1):
            log(f"[{n}/{len(items)}] {p.name}  ({kind})")
            if args.dry_run:
                if kind == "pdf":
                    info = run_check(p)
                    log(f"    {info['verdict'][:90]}\n    план: {PLAN.get(info['code'], '?')}")
                else:
                    log("    план: " + {"djvu": "DjVu -> PDF -> OCR", "svg": "SVG -> PDF -> OCR", "epub": "EPUB -> PDF",
                                      "rtf": "RTF -> .txt", "text": "как есть", "unknown": "пропуск"}[kind])
                continue
            t1 = time.time()
            try:
                res = process(kind, p, out_dir, work, args)
            except Exception as e:  # noqa: BLE001
                res = {"name": p.name, "kind": kind, "steps": [], "notes": [], "before": None, "after": None,
                       "status": f"ошибка: {e}", "out": None}
            res["sec"] = time.time() - t1
            results.append(res)
            log(f"    -> {res['status']}  ({res['sec']:.0f} с)")
            for s in res["steps"]:
                log(f"       · {s}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if args.dry_run:
        return 0
    log(f"\n{'=' * 100}\nСводка ({time.time() - t0:.0f} с)\n")
    bad = 0
    for r in results:
        b, a = r["before"], r["after"]
        log(f"* {r['name']}")
        log(f"    результат: {r['status']}" + (f"  [{args.out}/{r['out']}]" if r["out"] else ""))
        if a:
            fid = f"сохранность текста {fid_s(b)} -> {fid_s(a)}" if b else f"сохранность текста {fid_s(a)}"
            low = f", страниц ниже 0,9: {a['low']} из {a['checked']}" if a["low"] is not None else ""
            log(f"    {fid}{low}; страниц: {a['pages']}")
            for note in a["notes"]:
                if note.startswith(("Метаданные", "Сдвиг", "Страницы похожи", "Повёрнуты", "В файле нет закладок")):
                    log(f"    - {note[:230]}")
        for note in r["notes"]:
            log(f"    - {note}")
        if "ТРЕБУЕТ" in r["status"] or r["status"].startswith(("ошибка", "пропущено")):
            bad += "ТРЕБУЕТ" in r["status"] or r["status"].startswith("ошибка")
    log("\nДальше: добавьте файлы из подпапки в Zotero, затем `zotero-mcp update-db --fulltext` и scripts/check-text-layer.sh.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
