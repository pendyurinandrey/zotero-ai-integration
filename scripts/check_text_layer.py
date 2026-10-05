#!/usr/bin/env python3
"""Проверка библиотеки Zotero: есть ли у PDF текстовый слой и попали ли статьи в индекс.

Только читает: копирует zotero.sqlite во временную папку, файлы PDF и индекс не меняет.
Запускается через check-text-layer.sh (нужен Python из окружения zotero-mcp-server:
там есть pymupdf и chromadb).

Коды выхода: 0 - проблем нет, 1 - найдены PDF без текста / не проиндексированные статьи, 2 - ошибка.
"""
import argparse
import hashlib
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


TEXTUAL_TYPES = ("text/",)
HTMLISH = ("application/xhtml+xml", "application/epub+zip")


def is_textual(content_type: str | None) -> bool:
    ct = content_type or ""
    return ct.startswith(TEXTUAL_TYPES) or ct in HTMLISH


def standalone_indexing_enabled(config_dir: Path) -> bool:
    """Включена ли в zotero-mcp опция semantic_search.index_standalone_attachments."""
    try:
        import json
        cfg = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        return cfg.get("semantic_search", {}).get("index_standalone_attachments") is True
    except Exception:
        return False


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
        items[r["itemID"]] = {"key": r["key"], "pdfs": [], "textual": 0, "other": 0}
    unchecked = []
    for r in db.execute(
        "SELECT a.itemID, a.parentItemID, a.linkMode, a.contentType, a.path, i.key FROM itemAttachments a "
        "JOIN items i USING(itemID)"):
        if r["itemID"] in trashed or r["parentItemID"] not in items:
            continue
        if r["contentType"] != "application/pdf":
            items[r["parentItemID"]]["textual" if is_textual(r["contentType"]) else "other"] += 1
            continue
        path = r["path"] or ""
        if r["linkMode"] in (0, 1) and path.startswith("storage:"):
            items[r["parentItemID"]]["pdfs"].append(zotero_dir / "storage" / r["key"] / path[len("storage:"):])
        else:
            unchecked.append((title_of(db, r["parentItemID"]), path))

    # Вложения без родительской записи (Zotero не смог создать запись по метаданным, файл просто положили
    # в коллекцию и т. п.): по умолчанию индексатор берёт только статьи и книги и такие файлы пропускает.
    orphans = []
    for r in db.execute(
        "SELECT a.itemID, a.linkMode, a.contentType, a.path, i.key FROM itemAttachments a JOIN items i USING(itemID) "
        "WHERE a.parentItemID IS NULL"):
        ct = r["contentType"]
        if r["itemID"] in trashed or (scope is not None and r["itemID"] not in scope):
            continue
        if ct != "application/pdf" and not is_textual(ct):
            continue
        path = r["path"] or ""
        file = (zotero_dir / "storage" / r["key"] / path[len("storage:"):]
                if r["linkMode"] in (0, 1) and path.startswith("storage:") else None)
        digest = hashlib.md5(file.read_bytes()).hexdigest() if file and file.exists() else None
        orphans.append({"key": r["key"], "title": title_of(db, r["itemID"]), "name": file.name if file else path,
                        "digest": digest, "pdf": ct == "application/pdf", "file": file})

    no_text, partial, no_pdf, missing_files = [], [], [], []
    checked = 0
    for item_id, it in items.items():
        title = title_of(db, item_id)
        if not it["pdfs"] and not it["textual"] and not any(t == title for t, _ in unchecked):
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

    # Текстовый слой у PDF без родительской записи проверяется так же, как у остальных
    for o in orphans:
        if not o["pdf"] or not o["file"] or not o["file"].exists():
            continue
        checked += 1
        try:
            with_text, total = text_pages(o["file"])
        except Exception as e:
            print(f"Не удалось прочитать {o['name']}: {e}", file=sys.stderr)
            continue
        if total and (with_text == 0 or (total - with_text) / total >= PARTIAL_SHARE):
            (no_text if with_text == 0 else partial).append((o["key"], o["title"], o["name"], with_text, total))

    config_dir = Path("~/.config/zotero-mcp").expanduser()
    indexed = None if args.no_index else indexed_item_keys(config_dir)
    standalone_on = standalone_indexing_enabled(config_dir)

    scope_txt = f"коллекция «{scope_name}»" if args.collection else "вся библиотека"
    print(f"Проверено: {len(items)} записей, {checked} PDF, отдельных вложений: {len(orphans)} ({scope_txt})")
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
    # Не попавшие в индекс: опция выключена -> все; опция включена -> те, которых нет в индексе (если индекс читается)
    if standalone_on:
        orphan_bad = [o for o in orphans if indexed is not None and o["key"] not in indexed]
    else:
        orphan_bad = list(orphans)
    if orphan_bad:
        problems += 1
        why = ("такие файлы без записи-карточки индексатор пропускает" if not standalone_on
               else "опция index_standalone_attachments включена, но индекс не обновлён")
        print(f"\nОТДЕЛЬНЫЕ ВЛОЖЕНИЯ БЕЗ РОДИТЕЛЬСКОЙ ЗАПИСИ, НЕ В ИНДЕКСЕ ({len(orphan_bad)}): {why}. "
              "Поиск их содержимого не увидит.")
        for o in orphan_bad:
            same = [x["key"] for x in orphans if x is not o and o["digest"] and x["digest"] == o["digest"]]
            dup = f"; точная копия [{', '.join(same)}]" if same else ""
            label = o["title"] if o["title"] != "(без названия)" else o["name"]
            print(f"  - [{o['key']}] {label[:80]} (файл {o['name']}{dup})")
        if standalone_on:
            print("  Что делать: zotero-mcp update-db --fulltext")
        else:
            print("  Что делать: создайте для файла запись-карточку (тип «Документ», «Книга» или «Статья») и перетащите в неё файл;"
                  " повторные копии удалите; затем zotero-mcp update-db --fulltext (docs/setup/06-ocr.md).")
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
        live = {it["key"] for it in items.values() if it["pdfs"] or it["textual"]}
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
