#!/bin/bash
# Поиск по смыслу только в одной коллекции Zotero (см. search_in_collection.py).
# Использование: scripts/search-in-collection.sh "запрос" --collection КЛЮЧ_ИЛИ_НАЗВАНИЕ [--limit N] [--subcollections]
PY="$(uv tool dir 2>/dev/null)/zotero-mcp-server/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найдено окружение zotero-mcp-server (см. docs/setup/04-mcp-server.md)" >&2
  exit 2
fi
exec "$PY" "$(dirname "$0")/search_in_collection.py" "$@"
