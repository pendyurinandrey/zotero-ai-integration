#!/usr/bin/env python3
"""Проверка библиотеки Zotero: есть ли у PDF текстовый слой и попали ли статьи в индекс.

Только читает: копирует zotero.sqlite во временную папку, файлы PDF и индекс не меняет.
Запускается через check-text-layer.sh (нужен Python из окружения zotero-mcp-server:
там есть pymupdf и chromadb).

Коды выхода: 0 - проблем нет, 1 - найдены PDF без текста / не проиндексированные статьи, 2 - ошибка.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _zotero_common import copy_db, resolve_collection  # noqa: E402

MIN_CHARS = 50          # страница считается текстовой, если в ней больше стольких символов
PARTIAL_SHARE = 0.2     # доля страниц без текста, с которой PDF считается «частично без текста»


def collection_item_ids(db, ref: str) -> tuple[set[int], str]:
    coll = resolve_collection(db, ref)
    ids = {r[0] for r in db.execute("SELECT itemID FROM collectionItems WHERE collectionID=?", (coll["collectionID"],))}
    return ids, coll["collectionName"]


def title_of(db, item_id: int) -> str:
    row = db.execute(
        "SELECT v.value FROM itemData d JOIN itemDataValues v USING(valueID) JOIN fields f USING(fieldID) "
        "WHERE d.itemID=? AND f.fieldName='title'", (item_id,)).fetchone()
    return row[0] if row else "(без названия)"


def text_pages(pdf: Path):
    import pymupdf
    with pymupdf.open(pdf) as doc:
        total = doc.page_count
        with_text = sum(len(p.get_text().strip()) > MIN_CHARS for p in doc)
    return with_text, total


def indexed_item_keys(config_dir: Path) -> set[str] | None:
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(config_dir / "chroma_db"))
        res = client.get_collection("zotero_library").get(include=["metadatas"])
    except Exception:
        return None
    return {m.get("parent_item_key") or m["item_key"] for m in res["metadatas"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collection", help="ключ или название коллекции (проверять только её статьи)")
    ap.add_argument("--zotero-dir", default=os.environ.get("ZOTERO_DATA_DIR", "~/Zotero"))
    ap.add_argument("--no-index", action="store_true", help="не сверять с индексом поиска")
    args = ap.parse_args()

    zotero_dir = Path(args.zotero_dir).expanduser()
    if not (zotero_dir / "zotero.sqlite").exists():
        print(f"Не найден {zotero_dir}/zotero.sqlite (укажите --zotero-dir)", file=sys.stderr)
        return 2
    db = sqlite3.connect(copy_db(zotero_dir))
    db.row_factory = sqlite3.Row

    scope, scope_name = collection_item_ids(db, args.collection) if args.collection else (None, None)
    trashed = {r[0] for r in db.execute("SELECT itemID FROM deletedItems")}

    # Статьи (не вложения и не заметки) и их PDF
    items = {}
    for r in db.execute(
        "SELECT i.itemID, i.key FROM items i WHERE i.itemID NOT IN (SELECT itemID FROM itemAttachments) "
        "AND i.itemID NOT IN (SELECT itemID FROM itemNotes)"):
        if r["itemID"] in trashed or (scope is not None and r["itemID"] not in scope):
            continue
        items[r["itemID"]] = {"key": r["key"], "pdfs": [], "other": 0}
    unchecked = []
    for r in db.execute(
        "SELECT a.itemID, a.parentItemID, a.linkMode, a.contentType, a.path, i.key FROM itemAttachments a "
        "JOIN items i USING(itemID)"):
        if r["itemID"] in trashed or r["parentItemID"] not in items:
            continue
        if r["contentType"] != "application/pdf":
            items[r["parentItemID"]]["other"] += 1
            continue
        path = r["path"] or ""
        if r["linkMode"] in (0, 1) and path.startswith("storage:"):
            items[r["parentItemID"]]["pdfs"].append(zotero_dir / "storage" / r["key"] / path[len("storage:"):])
        else:
            unchecked.append((title_of(db, r["parentItemID"]), path))

    no_text, partial, no_pdf, missing_files = [], [], [], []
    checked = 0
    for item_id, it in items.items():
        title = title_of(db, item_id)
        if not it["pdfs"] and not any(t == title for t, _ in unchecked):
            no_pdf.append((it["key"], title, it["other"]))
        # Статья в порядке, если хотя бы в одном её PDF достаточно текста
        results = []
        for pdf in it["pdfs"]:
            if not pdf.exists():
                missing_files.append((it["key"], title, pdf.name))
                continue
            checked += 1
            try:
                results.append((pdf, *text_pages(pdf)))
            except Exception as e:
                print(f"Не удалось прочитать {pdf.name}: {e}", file=sys.stderr)
        if not results:
            continue
        good = [r for r in results if r[1] > 0 and (r[2] - r[1]) / r[2] < PARTIAL_SHARE]
        if good:
            continue
        pdf, with_text, total = max(results, key=lambda r: r[1] / max(r[2], 1))
        entry = (it["key"], title, pdf.name, with_text, total)
        (no_text if with_text == 0 else partial).append(entry)

    scope_txt = f"коллекция «{scope_name}»" if args.collection else "вся библиотека"
    print(f"Проверено: {len(items)} статей, {checked} PDF ({scope_txt})")
    problems = 0

    if no_text:
        problems += 1
        print(f"\nPDF БЕЗ ТЕКСТОВОГО СЛОЯ ({len(no_text)}): поиск их содержимого не увидит, проиндексированы только метаданные.")
        for key, title, name, _, total in no_text:
            print(f"  - [{key}] {title[:80]} (файл {name}, 0 из {total} страниц с текстом)")
    if partial:
        problems += 1
        print(f"\nPDF С ТЕКСТОМ НЕ НА ВСЕХ СТРАНИЦАХ ({len(partial)}): часть страниц поиск не увидит.")
        for key, title, name, with_text, total in partial:
            print(f"  - [{key}] {title[:80]} (файл {name}, текст на {with_text} из {total} страниц)")
    if no_text or partial:
        print("  Что делать: docs/setup/06-ocr.md (ocrmypdf --skip-text, затем заменить вложение).")
    if no_pdf:
        print(f"\nБез PDF-вложения ({len(no_pdf)}), в поиске только по метаданным:")
        for key, title, other in no_pdf:
            print(f"  - [{key}] {title[:80]}")
    if missing_files:
        print(f"\nФайл вложения не найден на диске ({len(missing_files)}):")
        for key, title, name in missing_files:
            print(f"  - [{key}] {title[:60]} ({name})")
    if unchecked:
        print(f"\nНе проверены связанные (не импортированные) файлы: {len(unchecked)}")

    if not args.no_index:
        live = {it["key"] for it in items.values() if it["pdfs"]}
        config_dir = Path("~/.config/zotero-mcp").expanduser()
        indexed = indexed_item_keys(config_dir)
        if indexed is None:
            print("\nИндекс поиска не удалось прочитать (проверка пропущена).")
        else:
            new = sorted(live - indexed)
            if new:
                problems += 1
                print(f"\nНЕ В ИНДЕКСЕ ({len(new)}): статьи добавлены после последней индексации, поиск их не найдёт.")
                for key in new:
                    t = next(title_of(db, i) for i, it in items.items() if it["key"] == key)
                    print(f"  - [{key}] {t[:80]}")
                print("  Что делать: zotero-mcp update-db --fulltext")

    if not problems:
        print("\nПроблем не найдено.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
