"""Общие функции для скриптов: копия базы Zotero и поиск коллекции по названию или ключу."""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def copy_db(zotero_dir: Path) -> Path:
    """Копирует zotero.sqlite (с -wal и -shm) во временную папку, чтобы не мешать запущенному Zotero."""
    tmp = Path(tempfile.mkdtemp(prefix="zotero-check-"))
    for suffix in ("", "-wal", "-shm"):
        src = zotero_dir / f"zotero.sqlite{suffix}"
        if src.exists():
            shutil.copy2(src, tmp / src.name)
    return tmp / "zotero.sqlite"


def live_collections(db):
    return db.execute(
        "SELECT collectionID, key, collectionName, parentCollectionID FROM collections "
        "WHERE collectionID NOT IN (SELECT collectionID FROM deletedCollections)").fetchall()


def find_collections(db, ref: str):
    """Ищет коллекцию по ключу, затем по точному названию, затем по части названия (без учёта регистра)."""
    rows = live_collections(db)
    by_key = [r for r in rows if r["key"] == ref]
    if by_key:
        return by_key
    exact = [r for r in rows if r["collectionName"].casefold() == ref.casefold()]
    return exact or [r for r in rows if ref.casefold() in r["collectionName"].casefold()]


def resolve_collection(db, ref: str):
    """Возвращает одну коллекцию или завершает программу с понятным сообщением (код 2)."""
    found = find_collections(db, ref)
    if not found:
        names = ", ".join(r["collectionName"] for r in live_collections(db))
        print(f"Коллекция «{ref}» не найдена. Есть: {names}", file=sys.stderr)
        sys.exit(2)
    if len(found) > 1:
        print(f"Название «{ref}» подходит нескольким коллекциям, укажите ключ:", file=sys.stderr)
        for r in found:
            print(f"  {r['key']}  {r['collectionName']}", file=sys.stderr)
        sys.exit(2)
    return found[0]


def collection_ids(db, root_id: int, with_sub: bool = False) -> set[int]:
    """Идентификаторы коллекции и (по желанию) всех её подколлекций."""
    ids, frontier = {root_id}, [root_id]
    while with_sub and frontier:
        cur = frontier.pop()
        for (cid,) in db.execute("SELECT collectionID FROM collections WHERE parentCollectionID=?", (cur,)):
            if cid not in ids:
                ids.add(cid)
                frontier.append(cid)
    return ids


def collection_item_keys(db, coll, with_sub: bool = False) -> list[str]:
    """Ключи статей и книг коллекции (без вложений, заметок и записей из корзины)."""
    ids = collection_ids(db, coll["collectionID"], with_sub)
    marks = ",".join("?" * len(ids))
    return sorted({r[0] for r in db.execute(
        f"SELECT i.key FROM collectionItems ci JOIN items i USING(itemID) WHERE ci.collectionID IN ({marks}) "
        "AND i.itemID NOT IN (SELECT itemID FROM deletedItems) "
        "AND i.itemID NOT IN (SELECT itemID FROM itemAttachments) AND i.itemID NOT IN (SELECT itemID FROM itemNotes)",
        tuple(ids))})
