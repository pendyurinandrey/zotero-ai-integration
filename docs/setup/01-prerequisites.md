# 1. Подготовка: Homebrew, uv, Python

Нужны Homebrew и `uv` (менеджер Python-окружений). Системный Python не используется: `zotero-mcp-server` ставится через `uv tool` в собственное окружение, поэтому версия системного Python не важна.

## Проверка

```bash
brew --version
uv --version
```

Если команды не найдены:

```bash
# Homebrew: https://brew.sh (команда установки на главной странице)
brew install uv
```

## Примечание про Python

Не полагайтесь на самый новый Python (3.14): для `torch` и `sentence-transformers` могут не быть готовых сборок. Версию Python для сервера задаём явно на шаге 4.

Проверенные версии: см. [versions.md](../versions.md).
