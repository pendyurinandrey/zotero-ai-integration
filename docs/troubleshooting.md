# Troubleshooting

## Zotero отвечает 403 на запросы к `<ZOTERO_API_URL>`

Не включена опция «Allow other applications on this computer to communicate with Zotero». См. [шаг 2](setup/02-zotero.md).

## Запрос к `<ZOTERO_API_URL>` не отвечает

Zotero не запущен.

## Запрос к `http://127.0.0.1:11434` не отвечает

Ollama не запущен. Запустите `brew services start ollama` или `ollama serve`.
