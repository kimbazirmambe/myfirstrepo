# myfirstrepo — рабочая среда Codex

Основа для разработки с Codex на Windows и в облаке. Прикладной стек пока не выбран; служебным инструментам нужны только Python 3.11+ и Git.

## Начало на Windows

Установите Git, Python 3.11+ и Codex по [официальной инструкции](https://developers.openai.com/codex/windows). Откройте PowerShell:

```powershell
git clone https://github.com/kimbazirmambe/myfirstrepo.git
cd myfirstrepo
python scripts/workspace.py check
python -m unittest discover -s tests -v
codex
```

Если Python доступен как `py`, используйте `py -3.11` вместо `python`. Уже скачанный проект клонировать не нужно. При запросе Codex подтвердите доверие к проверенному каталогу: проектная конфигурация может не загружаться до этого.

## Структура

| Путь | Назначение |
| --- | --- |
| `AGENTS.md` | Общие инструкции для Codex |
| `.codex/config.toml`, `.codex/agents/` | Роли исследователя, разработчика и ревьюера |
| `.agents/skills/` | Навыки проекта |
| `.agents/plugins/marketplace.json` | Каталог собственных плагинов |
| `plugins/` | Исходники плагинов |
| `integrations/` | Примеры MCP и реестр доступов |
| `scripts/` | Проверка проекта, генераторы, локальный MCP-сервер |
| `src/`, `tests/` | Будущий код и проверки |
| `docs/` | Инструкции по расширению и доступам |
| `.local/` | Игнорируемые локальные данные |

## Добавить расширение

```powershell
python scripts/workspace.py new-agent researcher
python scripts/workspace.py new-skill release-check
python scripts/workspace.py new-plugin my-tools
python scripts/workspace.py check
```

Генераторы не перезаписывают файлы. Новая роль регистрируется в конфигурации, плагин — в каталоге. Отредактируйте инструкции под задачу и начните новую сессию Codex.

Пример задания: «Изучи проект ролью explorer, реализуй изменение ролью developer и проверь ролью reviewer». Поддержка параллельных агентов зависит от клиента и политики сессии.

- [Агенты, навыки и плагины](docs/extensions.md)
- [Windows, приложения, сеть и секреты](docs/access.md)
- [Подключение MCP](integrations/README.md)

Репозиторий содержит работающий пример MCP и плагин с навыком. Аккаунты сторонних сервисов пока не подключены. Файлы проекта не выдают системные разрешения и не меняют политику облака.
