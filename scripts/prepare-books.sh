#!/bin/bash
# Подготовка папки с книгами к загрузке в Zotero (PDF, DjVu, EPUB, RTF, страницы SVG): проверка, ремонт, OCR, перенос в fixed.
# Использование: scripts/prepare-books.sh "<папка>" [--out fixed] [--only строка] [--dry-run] [--copy] [--lang rus+eng] [--force]
# Зависимостей, кроме Python 3 и инструментов, которые вызывают соседние скрипты (ocrmypdf, djvulibre, окружение zotero-mcp-server), нет.
exec python3 "$(dirname "$0")/prepare_books.py" "$@"
