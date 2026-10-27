#!/bin/bash
# Запуск svg_to_pdf.py в окружении zotero-mcp-server (там есть pymupdf).
# Использование: scripts/svg-to-pdf.sh папка_со_страницами_svg выход.pdf [--dpi 220] [--quality 45] [--color] [--title ...] [--author ...] [--force]
PY="$(uv tool dir 2>/dev/null)/zotero-mcp-server/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найдено окружение zotero-mcp-server (см. docs/setup/04-mcp-server.md)" >&2
  exit 2
fi
exec "$PY" -W ignore "$(dirname "$0")/svg_to_pdf.py" "$@"
