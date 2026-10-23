#!/bin/bash
# Запуск epub_to_pdf.py в окружении zotero-mcp-server (там есть pymupdf).
# Использование: scripts/epub-to-pdf.sh книга.epub книга.pdf [--width 432] [--height 648] [--fontsize 10.5] [--title ...] [--author ...] [--force]
PY="$(uv tool dir 2>/dev/null)/zotero-mcp-server/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найдено окружение zotero-mcp-server (см. docs/setup/04-mcp-server.md)" >&2
  exit 2
fi
exec "$PY" -W ignore "$(dirname "$0")/epub_to_pdf.py" "$@"
