#!/bin/bash
# Запуск rasterize_pdf.py в окружении zotero-mcp-server (там есть pymupdf).
# Использование: scripts/rasterize-pdf.sh вход.pdf выход.pdf [--dpi 260] [--quality 55] [--color] [--force]
PY="$(uv tool dir 2>/dev/null)/zotero-mcp-server/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найдено окружение zotero-mcp-server (см. docs/setup/04-mcp-server.md)" >&2
  exit 2
fi
exec "$PY" -W ignore "$(dirname "$0")/rasterize_pdf.py" "$@"
