# Codex

Корневой `AGENTS.md` направляет Codex к [общим правилам](AGENTS.md). Для обнаружения скилов Codex использует симлинки из `.agents/skills/` на каталоги ниже.

| Файл | Назначение |
|---|---|
| [zotero-library](skills/zotero-library/SKILL.md) | Поиск, чтение и цитирование источников в Zotero |
| [prepare-books](skills/prepare-books/SKILL.md) | Подготовка файлов к импорту и индексации |
| [zotero-import](skills/zotero-import/SKILL.md) | Реквизиты и RIS для ручного импорта |
| [mcp.toml.example](mcp.toml.example) | Настройка MCP-сервера Zotero с читающими инструментами |

Порядок подключения и проверки описан в [документации для Codex](../../docs/clients/codex.md).
