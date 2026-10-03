# Claude

Файлы для Claude Code. Всё, что относится к Claude, лежит здесь; в корне репозитория и в `.claude/` только тонкие ссылки (Claude Code ищет свои файлы в фиксированных местах, перенести их нельзя).

- `CLAUDE.md` — общие правила; в корне репозитория подключается строкой `@ai-instructions/claude/CLAUDE.md`.
- `skills/zotero-library/SKILL.md` — skill: как искать, как оформлять цитаты, как ограничивать поиск коллекцией. В `.claude/skills` ссылка на `skills/`.
- `settings.json` — разрешения (только читающие инструменты). В `.claude/settings.json` ссылка на этот файл.

Подключение сервера и установка: [docs/clients/claude-code.md](../../docs/clients/claude-code.md).
