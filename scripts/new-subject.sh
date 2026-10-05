#!/bin/bash
# Подключает папку предмета к интеграции Zotero + Claude Code.
#
# Использование:
#   scripts/new-subject.sh "<папка предмета>" --collection "Название коллекции Zotero" [--name "Название предмета"] [--dry-run] [--refresh-settings]
#   --refresh-settings  пересоздать .claude/settings.json из актуальных разрешений репозитория (свои правила держите в settings.local.json)
#
# В папке предмета создаются (существующие файлы не перезаписываются):
#   .claude/skills/zotero-library  симлинк на skill из этого репозитория (вместе со скриптами поиска)
#   .claude/settings.json          разрешения: читающие инструменты Zotero и команды скриптов (копия из репозитория) + запрет правки skill
#   .mcp.json                      подключение MCP-сервера zotero (только для этой папки)
#   CLAUDE.md                      заготовка правил предмета с названием коллекции
#   .gitignore                     записи для перечисленного выше, если папка внутри git-репозитория
# За пределами папки предмета ничего не меняется (репозиторий только читается).
set -euo pipefail

REPO="$(python3 -c 'import os,sys; print(os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[1]))))' "$0")"
DIR=""; COLLECTION=""; NAME=""; DRY=0; REFRESH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --collection) COLLECTION="${2:-}"; shift 2 ;;
    --name) NAME="${2:-}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --refresh-settings) REFRESH=1; shift ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "Неизвестный параметр: $1" >&2; exit 2 ;;
    *) DIR="$1"; shift ;;
  esac
done
[ -n "$DIR" ] && [ -n "$COLLECTION" ] || { echo "Нужны папка предмета и --collection (см. --help)" >&2; exit 2; }
[ -d "$DIR" ] || { echo "Папка не найдена: $DIR" >&2; exit 2; }
DIR="$(cd "$DIR" && pwd)"
[ -n "$NAME" ] || NAME="$(basename "$DIR")"
SKILL_SRC="$REPO/ai-instructions/claude/skills/zotero-library"
SETTINGS_SRC="$REPO/ai-instructions/claude/settings.json"
TEMPLATE="$REPO/ai-instructions/claude/templates/subject-CLAUDE.md"
for f in "$SKILL_SRC/SKILL.md" "$SETTINGS_SRC" "$TEMPLATE"; do [ -e "$f" ] || { echo "Не найден файл репозитория: $f" >&2; exit 2; }; done

MCP="$(command -v zotero-mcp 2>/dev/null || true)"
[ -n "$MCP" ] || MCP="$HOME/.local/bin/zotero-mcp"
[ -x "$MCP" ] || { echo "Не найден zotero-mcp (см. docs/setup/04-mcp-server.md)" >&2; exit 2; }

say() { local m="$*"; if [ "$DRY" = 1 ]; then m="${m/создан/будет создан}"; m="${m/добавлено/будет добавлено}"; fi; echo "  $m"; }
run() { if [ "$DRY" = 1 ]; then :; else "$@"; fi; }
echo "Предмет: $NAME"; echo "Папка:   $DIR"; echo "Коллекция Zotero: $COLLECTION"; [ "$DRY" = 1 ] && echo "(пробный запуск, ничего не создаётся)"; echo

link() {   # link <куда ведёт> <где создать>
  local target="$1" path="$2"
  if [ -L "$path" ]; then
    if [ "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$path")" = "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$target")" ]; then
      say "уже есть: ${path#$DIR/}"; else say "ПРОПУЩЕНО (симлинк уже ведёт в другое место): ${path#$DIR/}"; fi
  elif [ -e "$path" ]; then say "ПРОПУЩЕНО (файл уже есть, не трогаю): ${path#$DIR/}"
  else run mkdir -p "$(dirname "$path")"; run ln -s "$target" "$path"; say "создан симлинк: ${path#$DIR/}"; fi
}
link "$SKILL_SRC" "$DIR/.claude/skills/zotero-library"
# settings.json генерируется, а не линкуется: запреты правки skill нужны только в папке предмета,
# а в самом репозитории они мешали бы править skill
if [ -e "$DIR/.claude/settings.json" ] && [ "$REFRESH" = 0 ]; then
  say "ПРОПУЩЕНО (файл уже есть, не трогаю; --refresh-settings пересоздаст): .claude/settings.json"
else
  if [ "$DRY" = 0 ]; then
    mkdir -p "$DIR/.claude"; rm -f "$DIR/.claude/settings.json"
    SRC="$SETTINGS_SRC" python3 -c 'import json,os; d=json.load(open(os.environ["SRC"],encoding="utf-8")); d.setdefault("permissions",{})["deny"]=["Edit(/.claude/skills/zotero-library/**)","Write(/.claude/skills/zotero-library/**)"]; print(json.dumps(d,ensure_ascii=False,indent=2))' > "$DIR/.claude/settings.json"
  fi
  say "создан файл: .claude/settings.json (разрешения из репозитория и запрет правки skill)"
fi

if [ -e "$DIR/.mcp.json" ]; then say "ПРОПУЩЕНО (файл уже есть, не трогаю): .mcp.json"
else
  if [ "$DRY" = 0 ]; then
    MCP="$MCP" python3 -c 'import json,os; print(json.dumps({"mcpServers":{"zotero":{"command":os.environ["MCP"],"env":{"ZOTERO_LOCAL":"true"}}}},ensure_ascii=False,indent=2))' > "$DIR/.mcp.json"
  fi
  say "создан файл: .mcp.json (сервер $MCP)"
fi

if [ -e "$DIR/CLAUDE.md" ]; then say "ПРОПУЩЕНО (файл уже есть, не трогаю): CLAUDE.md"
else
  if [ "$DRY" = 0 ]; then
    SUBJECT="$NAME" COLL="$COLLECTION" python3 -c 'import os,sys; t=open(sys.argv[1],encoding="utf-8").read(); print(t.replace("{{SUBJECT}}",os.environ["SUBJECT"]).replace("{{COLLECTION}}",os.environ["COLL"]),end="")' "$TEMPLATE" > "$DIR/CLAUDE.md"
  fi
  say "создан файл: CLAUDE.md (заготовка с названием коллекции)"
fi

if git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  for line in ".claude/skills/zotero-library" ".claude/settings.json" ".claude/settings.local.json" ".mcp.json"; do
    if [ -f "$DIR/.gitignore" ] && grep -qxF "$line" "$DIR/.gitignore"; then :; else
      run bash -c 'echo "$1" >> "$2"' _ "$line" "$DIR/.gitignore"; say "в .gitignore добавлено: $line"; fi
  done
else say ".gitignore не нужен: папка не внутри git-репозитория"; fi

echo
echo "Дальше: откройте Claude Code в этой папке, подтвердите доверие к папке и подключение MCP-сервера zotero."
echo "Заполните разделы TODO в CLAUDE.md (источники предмета, правила ответов)."
