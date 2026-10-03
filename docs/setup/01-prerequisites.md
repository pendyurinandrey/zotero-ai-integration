# 1. Подготовка: Homebrew и uv

Нужны Homebrew (менеджер пакетов macOS) и `uv` (менеджер Python-окружений). Системный Python не используется: `zotero-mcp-server` ставится через `uv tool` в собственное окружение, поэтому версия системного Python не важна.

## Homebrew

Проверка:

```bash
brew --version
```

Если команда не найдена, установите Homebrew по инструкции на [brew.sh](https://brew.sh) (команда установки на главной странице).

## uv

Проверка:

```bash
uv --version
```

Если команда не найдена:

```bash
brew install uv
```

## Примечание про Python

Не полагайтесь на самый новый Python (3.14): для `torch` и `sentence-transformers` могут не быть готовых сборок. Версию Python для сервера задаём явно на шаге 4.

Проверенные версии: см. [versions.md](../versions.md).
