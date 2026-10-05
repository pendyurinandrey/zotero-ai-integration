#!/usr/bin/env python3
"""Поиск по смыслу только в статьях одной коллекции Zotero.

Зачем: MCP-инструмент zotero_semantic_search принимает фильтр только как словарь со строковыми
значениями, а ограничить поиск набором статей можно только вложенным фильтром {"$in": [...]}.
Claude Code не может его передать через MCP, поэтому поиск запускается через zotero-cli, где
фильтр передаётся обычным аргументом командной строки.

Скрипт находит коллекцию (по ключу или по названию), собирает ключи её статей и запускает
`zotero-cli search --mode semantic --filters {"item_key": {"$in": [...]}}`.
Только читает. Запускается через search-in-collection.sh.

Коды выхода: 0 - успех; 2 - коллекция не найдена или неоднозначна, или ошибка.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _zotero_common import collection_item_keys, copy_db, resolve_collection  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="текст запроса (русский или английский)")
    ap.add_argument("--collection", required=True, help="ключ или название коллекции")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--subcollections", action="store_true", help="включить статьи из подколлекций")
    ap.add_argument("--zotero-dir", default=os.environ.get("ZOTERO_DATA_DIR", "~/Zotero"))
    args = ap.parse_args()

    zotero_dir = Path(args.zotero_dir).expanduser()
    if not (zotero_dir / "zotero.sqlite").exists():
        print(f"Не найден {zotero_dir}/zotero.sqlite", file=sys.stderr)
        return 2
    db = sqlite3.connect(copy_db(zotero_dir))
    db.row_factory = sqlite3.Row

    coll = resolve_collection(db, args.collection)

    keys = collection_item_keys(db, coll, args.subcollections)
    if not keys:
        print(f"В коллекции «{coll['collectionName']}» нет статей", file=sys.stderr)
        return 2

    cli = Path(sys.executable).parent / "zotero-cli"
    flt = json.dumps({"item_key": {"$in": keys}})
    print(f"Коллекция: {coll['collectionName']} ({coll['key']}), статей: {len(keys)}"
          f"{', с подколлекциями' if args.subcollections else ''}\n")
    sys.stdout.flush()
    return subprocess.call([str(cli), "search", args.query, "--mode", "semantic", "--limit", str(args.limit),
                            "--detail", "full", "--filters", flt])


if __name__ == "__main__":
    sys.exit(main())
