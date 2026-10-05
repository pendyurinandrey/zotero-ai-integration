#!/usr/bin/env python3
"""Поиск по смыслу, который возвращает НЕСКОЛЬКО мест одной записи.

Обычный поиск (инструмент zotero_semantic_search и zotero-cli) группирует результаты по записи и
оставляет в каждой одно лучшее место. Для вопросов вида «на каких страницах статьи это обсуждается»
и для длинных расшифровок лекций этого мало. Скрипт ищет прямо в индексе (тот же индекс и та же
модель эмбеддингов, что у zotero-mcp), снимает ограничение «одно место на запись» и показывает:

  * страницу и номер фрагмента (для PDF);
  * таймкоды и спикеров, если они есть в тексте расшифровки (формат «[ЧЧ:ММ:СС] Спикер: текст»);
  * для фрагмента, который начинается с середины реплики, автора и время этой реплики из предыдущего
    фрагмента.

Область поиска: вся библиотека, коллекция (--collection), конкретные записи (--item). Только читает.
Запускается через search-passages.sh.

Коды выхода: 0 - успех (в том числе «ничего не найдено»); 2 - ошибка (коллекция, Ollama, индекс).
"""
import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _zotero_common import collection_item_keys, copy_db, resolve_collection  # noqa: E402

TIME_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")
HEADER_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\] ([^:\n]{1,40}):")


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="текст запроса: лучше короткое связное предложение, русский или английский")
    ap.add_argument("--collection", help="ключ или название коллекции (по умолчанию вся библиотека)")
    ap.add_argument("--subcollections", action="store_true", help="включить статьи из подколлекций")
    ap.add_argument("--item", action="append", default=[], metavar="КЛЮЧ",
                    help="искать только в этой записи (можно повторять)")
    ap.add_argument("--limit", type=int, default=10, help="сколько мест всего показать (по умолчанию 10)")
    ap.add_argument("--per-item", type=int, default=5,
                    help="не больше стольких мест из одной записи (по умолчанию 5; 1 = как обычный поиск)")
    ap.add_argument("--min-score", type=float, default=None, help="отбросить места с сходством ниже порога")
    ap.add_argument("--max-chars", type=int, default=0, help="обрезать текст места (0 = целиком)")
    ap.add_argument("--no-merge", action="store_true", help="не склеивать соседние фрагменты одной записи")
    ap.add_argument("--zotero-dir", default=os.environ.get("ZOTERO_DATA_DIR", "~/Zotero"))
    ap.add_argument("--config", default="~/.config/zotero-mcp/config.json", help="конфиг zotero-mcp")
    return ap.parse_args()


def scope_keys(args):
    """Ключи записей, в которых искать, или None (вся библиотека). Для коллекции возвращает и её название."""
    keys, label = None, "вся библиотека"
    if args.collection:
        zotero_dir = Path(args.zotero_dir).expanduser()
        if not (zotero_dir / "zotero.sqlite").exists():
            sys.exit(f"Не найден {zotero_dir}/zotero.sqlite")
        db = sqlite3.connect(copy_db(zotero_dir))
        db.row_factory = sqlite3.Row
        coll = resolve_collection(db, args.collection)
        keys = set(collection_item_keys(db, coll, args.subcollections))
        if not keys:
            print(f"В коллекции «{coll['collectionName']}» нет статей", file=sys.stderr)
            sys.exit(2)
        label = f"коллекция «{coll['collectionName']}» ({coll['key']}), записей: {len(keys)}"
    if args.item:
        keys = set(args.item) if keys is None else keys & set(args.item)
        label = f"{label}; записи {', '.join(args.item)}" if args.collection else f"записи {', '.join(args.item)}"
        if not keys:
            print("Указанные записи не входят в выбранную коллекцию", file=sys.stderr)
            sys.exit(2)
    return keys, label


def search_index(args, keys):
    """Возвращает список мест: dict(item, title, authors, date, idx, n, start, end, page, text, score)."""
    try:
        from zotero_mcp.chroma_client import create_chroma_client
        client = create_chroma_client(os.path.expanduser(args.config))
        where = {"item_key": {"$in": sorted(keys)}} if keys is not None else None
        n = max(60, args.limit * 10)
        res = client.search([args.query], n_results=n, where=where)
    except Exception as e:  # Ollama не запущен, индекс не создан и т. п.
        print(f"Ошибка поиска: {e}\nПроверьте, что запущен Ollama и построен индекс (docs/setup/05-indexing.md).",
              file=sys.stderr)
        sys.exit(2)
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    rows = []
    for rid, doc, m, dist in zip(ids, docs, metas, dists):
        m = m or {}
        rows.append({
            "id": rid,
            "item": m.get("parent_item_key") or m.get("item_key") or rid.split("#", 1)[0],
            "title": m.get("title") or "(без названия)",
            "authors": m.get("creators") or "",
            "date": m.get("date") or "",
            "idx": m.get("chunk_index"),
            "n": m.get("n_chunks"),
            "start": m.get("char_start"),
            "end": m.get("char_end"),
            "page": m.get("page"),
            "text": (doc or "").strip(),
            "score": 1 - dist if dist is not None else 0.0,
        })
    return client, rows


def select(rows, limit, per_item, min_score):
    """Берёт лучшие места по порядку сходства, не больше per_item из одной записи и limit всего."""
    chosen, counts = [], {}
    for r in rows:
        if min_score is not None and r["score"] < min_score:
            continue
        if counts.get(r["item"], 0) >= per_item:
            continue
        counts[r["item"]] = counts.get(r["item"], 0) + 1
        chosen.append(r)
        if len(chosen) >= limit:
            break
    return chosen


def merge_adjacent(places):
    """Склеивает подряд идущие фрагменты одной записи в одно место (убирая перекрытие)."""
    by_item = {}
    for p in places:
        by_item.setdefault(p["item"], []).append(p)
    merged = []
    for item, ps in by_item.items():
        ps = sorted(ps, key=lambda x: (x["idx"] is None, x["idx"] if x["idx"] is not None else 0))
        cur = None
        for p in ps:
            if cur and p["idx"] is not None and cur["idx_last"] is not None and p["idx"] == cur["idx_last"] + 1:
                overlap = max(0, (cur["end"] or 0) - (p["start"] or 0)) if cur["end"] is not None and p["start"] is not None else 0
                cur["text"] = cur["text"] + "\n" + p["text"][overlap:].lstrip("\n")
                cur["idx_last"], cur["end"] = p["idx"], p["end"]
                cur["score"] = max(cur["score"], p["score"])
                if p["page"] is not None:
                    cur["page_last"] = p["page"]
            else:
                if cur:
                    merged.append(cur)
                cur = dict(p, idx_last=p["idx"], page_last=p["page"])
        if cur:
            merged.append(cur)
    return merged


def previous_header(client, place):
    """Для фрагмента, начинающегося с середины реплики, возвращает «[время] Спикер» из предыдущего фрагмента."""
    if place["idx"] in (None, 0):
        return None
    try:
        got = client.collection.get(ids=[f"{place['item']}#{place['idx'] - 1}"], include=["documents"])
        docs = got.get("documents") or []
        if not docs:
            return None
        found = HEADER_RE.findall(docs[0] or "")
        return f"[{found[-1][0]}] {found[-1][1]}" if found else None
    except Exception:
        return None


def format_place(client, p, number, max_chars):
    where_bits = []
    if p["page"] is not None:
        pages = f"стр. {p['page']}" if p.get("page_last") in (None, p["page"]) else f"стр. {p['page']}–{p['page_last']}"
        where_bits.append(pages)
    if p["idx"] is not None and p["n"]:
        frag = f"фрагмент {p['idx'] + 1}/{p['n']}" if p["idx_last"] == p["idx"] else f"фрагменты {p['idx'] + 1}–{p['idx_last'] + 1}/{p['n']}"
        where_bits.append(frag)
    lines = [f"### Место {number}: {', '.join(where_bits) if where_bits else 'без привязки к странице'} (сходство {p['score']:.3f})"]
    text = p["text"]
    times = TIME_RE.findall(text)
    if times:
        lines.append(f"Время в тексте: {times[0]}–{times[-1]} (таймкодов: {len(times)})")
        if not text.lstrip().startswith("["):
            head = previous_header(client, p)
            lead = text.split("[", 1)[0].strip()
            if head and lead:
                lines.append(f"Начало до первого таймкода ({len(lead)} симв.) относится к реплике {head} из предыдущего фрагмента.")
            elif lead:
                lines.append(f"Начало до первого таймкода ({len(lead)} симв.) продолжает реплику из предыдущего фрагмента (автор там).")
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    # Текст места показываем цитатой («> »): так заголовки и таймкоды внутри статьи не путаются с разметкой ответа
    lines.append("\n".join("> " + ln if ln.strip() else ">" for ln in text.split("\n")))
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if not args.query.strip():
        print("Пустой запрос", file=sys.stderr)
        return 2
    keys, label = scope_keys(args)
    client, rows = search_index(args, keys)
    chosen = select(rows, args.limit, max(1, args.per_item), args.min_score)
    places = chosen if args.no_merge else merge_adjacent(chosen)

    print(f"Запрос: {args.query}")
    print(f"Область: {label}")
    if not places:
        print("Ничего не найдено (индекс пуст или записи области не проиндексированы: scripts/check-text-layer.sh).")
        return 0
    best = {}
    for p in places:
        best[p["item"]] = max(best.get(p["item"], 0), p["score"])
    items = sorted(best, key=lambda k: -best[k])
    merged_note = "" if len(places) == len(chosen) else f", соседние фрагменты склеены в {len(places)} мест"
    print(f"Найдено фрагментов: {len(chosen)} в записях: {len(items)}{merged_note} (просмотрено кандидатов: {len(rows)})\n")
    for k, item in enumerate(items, 1):
        mine = sorted((p for p in places if p["item"] == item), key=lambda p: (p["idx"] is None, p["idx"] or 0))
        first = mine[0]
        meta = ", ".join(x for x in (first["authors"], str(first["date"]) if first["date"] else "") if x)
        print(f"## {k}. {first['title']}")
        print(f"**Item Key:** {item}" + (f" | {meta}" if meta else "") + f" | мест из этой записи: {len(mine)}\n")
        for j, p in enumerate(mine, 1):
            print(format_place(client, p, j, args.max_chars))
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
