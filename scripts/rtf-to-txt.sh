#!/bin/bash
# Запуск rtf_to_txt.py. Зависимостей, кроме Python 3, нет.
# Использование: scripts/rtf-to-txt.sh вход.rtf [выход.txt] [--notes inline|end|drop] [--encoding cp1251] [--force]
exec python3 "$(dirname "$0")/rtf_to_txt.py" "$@"
