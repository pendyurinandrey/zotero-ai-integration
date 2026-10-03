# Troubleshooting

## Локальный API Zotero отвечает 403

Опция «Allow other applications on this computer to communicate with Zotero» выключена. Для поиска и чтения это нормально, она нужна только для записи в Zotero. См. [шаг 2](setup/02-zotero.md).

## Запись в Zotero не работает

Проверьте, что опция из предыдущего пункта включена и что Zotero слушает порт 23119 (порт по умолчанию; сервер жёстко использует его для записи). Затем выполните `zotero-mcp authorize-local`.

## Запрос к `http://127.0.0.1:11434` не отвечает

Ollama не запущен. Запустите `brew services start ollama` или `ollama serve`.

## Claude спрашивает разрешение на каждый вызов поиска

Не принято доверие к папке (trust dialog), поэтому разрешения из `.claude/settings.json` игнорируются. Откройте Claude Code в папке репозитория и подтвердите доверие.

## `claude mcp list` не показывает `zotero` или показывает ошибку

`claude mcp add` выполняется для папки, где вы его запустили (область `local`). Выполните команду из папки репозитория. Если сервер в состоянии ошибки, проверьте `zotero-mcp version` и что Zotero запущен.

## Поиск по коллекции через MCP даёт ошибку `filters ... Input should be a valid string`

Это известное ограничение: инструмент `zotero_semantic_search` принимает фильтр только как словарь со строковыми значениями, а для ограничения набором статей нужен вложенный фильтр `{"$in": [...]}`. Claude Code не умеет передать его через MCP. Поэтому поиск внутри коллекции выполняется скриптом `scripts/search-in-collection.sh` (он вызывает `zotero-cli`, где фильтр передаётся аргументом командной строки). Подробнее: [docs/clients/claude-code.md](clients/claude-code.md).
