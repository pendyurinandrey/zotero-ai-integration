#!/bin/bash
# Запуск check_text_layer.py в окружении zotero-mcp-server (там есть pymupdf и chromadb).
# Использование: scripts/check-text-layer.sh [--collection КЛЮЧ] [--no-index]
PY="$(uv tool dir 2>/dev/null)/zotero-mcp-server/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найдено окружение zotero-mcp-server (см. docs/setup/04-mcp-server.md)" >&2
  exit 2
fi
exec "$PY" "$(dirname "$0")/check_text_layer.py" "$@"
