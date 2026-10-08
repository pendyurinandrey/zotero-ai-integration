#!/bin/bash
# Запуск check_pdf.py в окружении zotero-mcp-server (там есть pymupdf и индексатор).
# Использование: scripts/check-pdf.sh файл.pdf [--no-indexer]
PY="$(uv tool dir 2>/dev/null)/zotero-mcp-server/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найдено окружение zotero-mcp-server (см. docs/setup/04-mcp-server.md)" >&2
  exit 2
fi
exec "$PY" -W ignore "$(dirname "$0")/check_pdf.py" "$@"
