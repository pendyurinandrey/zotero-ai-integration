#!/bin/bash
# Поиск по смыслу с несколькими местами из одной записи (см. search_passages.py).
# Использование:
#   scripts/search-passages.sh "запрос" [--collection "Название или ключ"] [--item КЛЮЧ] [--limit N] [--per-item N]
PY="$(uv tool dir 2>/dev/null)/zotero-mcp-server/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найдено окружение zotero-mcp-server (см. docs/setup/04-mcp-server.md)" >&2
  exit 2
fi
exec "$PY" "$(dirname "$0")/search_passages.py" "$@"
