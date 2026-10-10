#!/bin/bash
# Запуск repair_pdf.py в окружении zotero-mcp-server (там есть pymupdf).
# Использование: scripts/repair-pdf.sh вход.pdf выход.pdf [--drop-missing] [--force]
PY="$(uv tool dir 2>/dev/null)/zotero-mcp-server/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найдено окружение zotero-mcp-server (см. docs/setup/04-mcp-server.md)" >&2
  exit 2
fi
exec "$PY" -W ignore "$(dirname "$0")/repair_pdf.py" "$@"
