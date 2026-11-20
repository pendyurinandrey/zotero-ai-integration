#!/bin/bash
# Файл RIS для импорта папки в Zotero: подсказки по реквизитам (extract) и сборка RIS с путями к файлам (build).
# Запуск make_ris.py в окружении zotero-mcp-server (там есть pymupdf).
# Использование: scripts/make-ris.sh extract "<папка>" [--skeleton реквизиты.json]
#                scripts/make-ris.sh build реквизиты.json выход.ris [--dir "<папка>"] [--force]
PY="$(uv tool dir 2>/dev/null)/zotero-mcp-server/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найдено окружение zotero-mcp-server (см. docs/setup/04-mcp-server.md)" >&2
  exit 2
fi
exec "$PY" -W ignore "$(dirname "$0")/make_ris.py" "$@"
