# 4. MCP-сервер zotero-mcp

Единая точка входа в Zotero для нейросетей: [54yyyu/zotero-mcp](https://github.com/54yyyu/zotero-mcp) (лицензия MIT). Он читает библиотеку Zotero, строит индекс для поиска по смыслу (через Ollama и `bge-m3`) и отдаёт инструменты нейросети по протоколу MCP.

## Установка

Ставим через `uv tool`, в собственное окружение, с явно заданным Python (не 3.14):

```bash
uv tool install --python 3.13 "zotero-mcp-server[semantic,pdf]"
```

Скачивается около 1,2 ГБ (вместе с `torch`). Исполняемые файлы кладутся в `~/.local/bin`. Если команда `zotero-mcp` не находится, добавьте эту папку в `PATH`:

```bash
uv tool update-shell
```

(команда правит конфиг вашего шелла; после неё откройте новый терминал).

## Проверка

```bash
zotero-mcp version
```

Должно вывести версию (мы проверяли на 0.13.1).

## Конфигурация поиска по смыслу

Файл `~/.config/zotero-mcp/config.json`:

```json
{
  "semantic_search": {
    "embedding_model": "ollama",
    "embedding_config": {
      "model_name": "bge-m3",
      "timeout": 600,
      "request_batch_size": 16
    },
    "include_fulltext": true,
    "extraction": {
      "pdf_max_pages": 1000
    },
    "chunking": {
      "enabled": true,
      "chunk_size": 1500,
      "overlap": 200,
      "max_chunks_per_item": 800
    }
  }
}
```

Зачем каждая настройка:

- `embedding_model: ollama` и `model_name: bge-m3` — многоязычные эмбеддинги через локальный Ollama (адрес по умолчанию `http://localhost:11434`).
- `chunking.enabled: true` — **обязательно**. Без него каждая статья превращается в один вектор, и поиск находит статью, но не место в ней, а длинные тексты обрезаются. С разбиением на фрагменты поиск возвращает конкретные отрывки для цитат.
- `chunk_size` и `overlap` — размер фрагмента и перекрытие, в символах.
- `max_chunks_per_item: 800` — по умолчанию 20 фрагментов на документ (около 30 тысяч символов, то есть примерно 10 страниц), для книг этого мало.
- `pdf_max_pages: 1000` — по умолчанию читается только часть страниц PDF, для книг нужно больше.

Папку конфига стоит закрыть от других пользователей:

```bash
chmod 700 ~/.config/zotero-mcp
```

Проверка, что конфиг подхвачен:

```bash
zotero-mcp db-status
```

В выводе должно быть `Embedding model: ollama` и `Passage chunking: enabled`.

## Как сервер подключается к Zotero

Режим `ZOTERO_LOCAL=true` (используется в конфиге MCP-клиента) читает библиотеку локально. Для записи в Zotero 10 нужна отдельная авторизация (`zotero-mcp authorize-local`). Если вам нужен только поиск, не выполняйте её.

TODO: уточнить на практике, зависит ли чтение от адреса локального API из шага 2, или сервер читает `zotero.sqlite` напрямую.

## Подключение к нейросети

Зависит от клиента, см. [docs/clients/](../clients/claude-code.md).

Проверенные версии: см. [versions.md](../versions.md).
