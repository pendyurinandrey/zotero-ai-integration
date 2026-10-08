#!/bin/bash
# Запуск fix_ocr_layer.py в окружении zotero-mcp-server (там есть pymupdf и индексатор).
# Использование: scripts/fix-ocr-layer.sh вход_ocr.pdf выход_fix.pdf [--scale 1.25] [--check] [--force]
PY="$(uv tool dir 2>/dev/null)/zotero-mcp-server/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найдено окружение zotero-mcp-server (см. docs/setup/04-mcp-server.md)" >&2
  exit 2
fi
exec "$PY" -W ignore "$(dirname "$0")/fix_ocr_layer.py" "$@"
