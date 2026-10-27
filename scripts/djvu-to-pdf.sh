#!/bin/bash
# Запуск djvu_to_pdf.py. Зависимостей, кроме Python 3 и djvulibre/libtiff, нет.
# Использование: scripts/djvu-to-pdf.sh книга.djvu выход.pdf [--dpi 300] [--force]
exec python3 "$(dirname "$0")/djvu_to_pdf.py" "$@"
