# Troubleshooting

## Zotero отвечает 403 на запросы к `<ZOTERO_API_URL>`

Не включена опция «Allow other applications on this computer to communicate with Zotero». См. [шаг 2](setup/02-zotero.md).

## Запрос к `<ZOTERO_API_URL>` не отвечает

Zotero не запущен.

## Запрос к `http://127.0.0.1:11434` не отвечает

Ollama не запущен. Запустите `brew services start ollama` или `ollama serve`.

## Claude спрашивает разрешение на каждый вызов поиска

Не принято доверие к папке (trust dialog), поэтому разрешения из `.claude/settings.json` игнорируются. Откройте Claude Code в папке репозитория и подтвердите доверие.

## `claude mcp list` не показывает `zotero` или показывает ошибку

`claude mcp add` выполняется для папки, где вы его запустили (область `local`). Выполните команду из папки репозитория. Если сервер в состоянии ошибки, проверьте `zotero-mcp version` и что Zotero запущен.
