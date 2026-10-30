#!/bin/bash
# Запуск office_to_pdf.py в окружении zotero-mcp-server (там есть pymupdf, нужен для проверки результата).
# Использование: scripts/office-to-pdf.sh документ.docx выход.pdf [--timeout 600] [--force]
PY="$(uv tool dir 2>/dev/null)/zotero-mcp-server/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найдено окружение zotero-mcp-server (см. docs/setup/04-mcp-server.md)" >&2
  exit 2
fi
exec "$PY" -W ignore "$(dirname "$0")/office_to_pdf.py" "$@"
