# Zotero + ИИ

Инструкции по подключению библиотеки Zotero к нейросетям через MCP-сервер: вы задаёте вопрос, нейросеть ищет по смыслу в полных текстах ваших статей и отвечает со ссылками и цитатами.

> Статус: решение работает с Claude Code на macOS (проверено на одной машине, см. [docs/versions.md](docs/versions.md)). **Ещё не проверено:** установка с нуля на чистом Mac, работа на реальных объёмах (сотни PDF, книги), распознавание русских сканов на настоящих файлах. Подробности и открытые вопросы — в соответствующих шагах.

Платформа: **macOS**. Версии, на которых всё проверено, указаны в [docs/versions.md](docs/versions.md).

## Что будет установлено

| Компонент | Как ставится | Шаг | Размер |
|---|---|---|---|
| Homebrew | по инструкции на [brew.sh](https://brew.sh) | [1](docs/setup/01-prerequisites.md) | — |
| `uv` | `brew install uv` | [1](docs/setup/01-prerequisites.md) | — |
| Zotero 10 | с [zotero.org](https://www.zotero.org/download/) | [2](docs/setup/02-zotero.md) | — |
| Ollama | `brew install ollama` | [3](docs/setup/03-ollama-and-model.md) | — |
| Модель `bge-m3` | `ollama pull bge-m3` | [3](docs/setup/03-ollama-and-model.md) | 1,2 ГБ |
| `zotero-mcp-server` (форк, с `torch` и др.) | `uv tool install ...` | [4](docs/setup/04-mcp-server.md) | 1,2 ГБ |
| `ocrmypdf` + Tesseract, `tesseract-lang` (если есть сканы) | `brew install ocrmypdf tesseract-lang` | [6](docs/setup/06-ocr.md) | 0,7 ГБ (языки) и более |
| Claude Code | приложение Claude, вкладка Code | [клиент](docs/clients/claude-code.md) | — |

Всего порядка 3 ГБ и более на диске, плюс индекс поиска, который строится на шаге 5 из вашей библиотеки (20 МБ на 14 статей). Нужен интернет для скачивания; сами вопросы по библиотеке обрабатываются локально, кроме обращения к модели Claude (см. [архитектуру](docs/architecture.md#что-работает-локально-а-что-уходит-в-облако)).

### Все команды установки подряд (краткая версия)

Это сводка команд из шагов 1–4. Подряд на чистой машине они ещё **не прогонялись**; подробности, проверки и объяснения — в самих шагах.

```bash
# Homebrew должен быть уже установлен (шаг 1)
brew install uv ollama ocrmypdf tesseract-lang
brew services start ollama
ollama pull bge-m3
uv tool install --python 3.13 "zotero-mcp-server[semantic,pdf] @ git+https://github.com/pendyurinandrey/zotero-mcp.git@v0.13.1-snippet-width"
```

Дальше: создать `~/.config/zotero-mcp/config.json` ([шаг 4](docs/setup/04-mcp-server.md)), построить индекс ([шаг 5](docs/setup/05-indexing.md)) и подключить Claude Code ([клиент](docs/clients/claude-code.md)).

## С чего начать

0. **Склонируйте этот репозиторий** (при необходимости попросите доступ у автора) и откройте в нём терминал. Работать с библиотекой через ИИ вы будете из этой же папки: в ней лежат инструкции для нейросети и скрипты.

   ```bash
   git clone https://github.com/pendyurinandrey/zotero-ai-integration.git
   cd zotero-ai-integration
   ```

1. **Прочитайте [архитектуру](docs/architecture.md)** (5 минут): как устроено решение и что где работает.
2. **Общая часть (делается всегда, один раз на компьютер)** — по порядку:
   1. [Подготовка: Homebrew и uv](docs/setup/01-prerequisites.md)
   2. [Zotero](docs/setup/02-zotero.md)
   3. [Ollama и модель bge-m3](docs/setup/03-ollama-and-model.md)
   4. [MCP-сервер zotero-mcp](docs/setup/04-mcp-server.md)
   5. [Индексация библиотеки](docs/setup/05-indexing.md)
   6. [OCR для сканов](docs/setup/06-ocr.md) — по необходимости
   7. [Расшифровки лекций и семинаров](docs/transcripts.md) — если ведёте такую коллекцию
   8. [Папки предметов вне репозитория](docs/subjects.md) — если ведёте несколько предметов
3. **Подключите нейросеть** — выберите свою:
   - Claude Code: [настройка](docs/clients/claude-code.md)
4. Если что-то не работает: [troubleshooting](docs/troubleshooting.md).

## Для нейросетей

В [ai-instructions/](ai-instructions/README.md) лежат файлы, которые читает сама нейросеть (как пользоваться библиотекой). Для каждой нейросети своя папка. Как установить эти файлы, описано на странице клиента в `docs/clients/`.

## Структура репозитория

```
docs/             инструкции для человека
  setup/          общая часть
  clients/        подключение конкретной нейросети (по файлу на нейросеть)
ai-instructions/  инструкции для нейросети (по папке на нейросеть)
scripts/          вспомогательные скрипты (проверка текстового слоя PDF и индекса, проверка PDF перед импортом, поиск, исправление слоя OCR)
```

## Как добавить новую нейросеть

1. Создать `docs/clients/<имя>.md` — как подключить её к MCP-серверу.
2. Создать `ai-instructions/<имя>/` с файлами, нужными этой нейросети.
3. Добавить ссылку в раздел «Подключите нейросеть» выше.

Общую часть и остальные нейросети трогать не нужно.
