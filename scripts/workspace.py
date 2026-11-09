"""Dependency-free project checks and extension generators (Python 3.11+)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


def skill_text(name: str) -> str:
    return (
        f"---\nname: {name}\n"
        f"description: Навык {name}; уточните задачи и условия применения.\n---\n\n"
        "1. Прочитайте AGENTS.md и уточните ожидаемый результат.\n"
        "2. Выполните задачу в рамках предоставленных прав.\n"
        "3. Проверьте результат и сообщите ограничения.\n"
    )


def generate(root: Path, kind: str, name: str) -> None:
    if not NAME.fullmatch(name):
        raise ValueError("Имя: строчная латинская буква, затем буквы, цифры или дефис; до 64 символов.")
    files: dict[Path, str] = {}
    update: tuple[Path, str] | None = None
    if kind == "agent":
        config_path = root / ".codex/config.toml"
        content = config_path.read_text(encoding="utf-8")
        config = tomllib.loads(content)
        if name in config.get("agents", {}):
            raise ValueError(f"Роль {name} уже зарегистрирована.")
        files[root / f".codex/agents/{name}.toml"] = (
            'developer_instructions = """\n'
            f"Ты выполняешь роль {name}. Уточни назначение этой роли перед использованием.\n"
            'Следуй AGENTS.md, проверь результат и сообщи ограничения.\n"""\n'
        )
        update = (config_path, content + (
            f'\n[agents.{name}]\ndescription = "Роль {name}: уточните назначение."\n'
            f'config_file = "agents/{name}.toml"\n'
        ))
    elif kind == "skill":
        directory = root / f".agents/skills/{name}"
        if directory.exists():
            raise ValueError(f"Каталог уже существует: {directory}")
        files[directory / "SKILL.md"] = skill_text(name)
    elif kind == "plugin":
        directory = root / f"plugins/{name}"
        if directory.exists():
            raise ValueError(f"Каталог уже существует: {directory}")
        catalog_path = root / ".agents/plugins/marketplace.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        if any(p["name"] == name for p in catalog["plugins"]):
            raise ValueError(f"Плагин {name} уже зарегистрирован.")
        manifest = {"name": name, "version": "0.1.0", "skills": "./skills"}
        files[directory / ".codex-plugin/plugin.json"] = json.dumps(manifest, indent=2) + "\n"
        files[directory / f"skills/{name}/SKILL.md"] = skill_text(name)
        catalog["plugins"].append({"name": name, "source": f"./plugins/{name}"})
        update = (catalog_path, json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    else:
        raise ValueError(f"Неизвестный тип: {kind}")
    for path in files:
        if path.exists():
            raise ValueError(f"Файл уже существует: {path}")
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    if update:
        update[0].write_text(update[1], encoding="utf-8", newline="\n")


def inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Путь выходит за пределы проекта: {relative}")
    return path


def check(root: Path) -> None:
    for required in ["AGENTS.md", "README.md", "scripts/repo_mcp.py"]:
        if not (root / required).is_file():
            raise ValueError(f"Отсутствует {required}")
    for path in sorted(root.rglob("*.toml")):
        if not any(p in {".git", ".local", ".venv", "node_modules"} for p in path.relative_to(root).parts):
            tomllib.loads(path.read_text(encoding="utf-8"))
    config_path = root / ".codex/config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    for name, role in config.get("agents", {}).items():
        if not isinstance(role, dict):
            continue
        if not role.get("description"):
            raise ValueError(f"Нет описания роли {name}")
        path = inside(config_path.parent, role["config_file"])
        tomllib.loads(path.read_text(encoding="utf-8"))
    catalog = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    names: set[str] = set()
    for plugin in catalog["plugins"]:
        if plugin["name"] in names:
            raise ValueError(f"Дублирующийся плагин {plugin['name']}")
        names.add(plugin["name"])
        directory = inside(root, plugin["source"])
        manifest = json.loads((directory / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        if manifest["name"] != plugin["name"]:
            raise ValueError("Имя плагина не совпадает с каталогом")
        skills = inside(directory, manifest["skills"])
        if not list(skills.glob("*/SKILL.md")):
            raise ValueError(f"Нет навыков у плагина {plugin['name']}")
    paths = list((root / ".agents/skills").glob("*/SKILL.md"))
    paths += list((root / "plugins").glob("*/skills/*/SKILL.md"))
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ValueError(f"Нет заголовка навыка: {path}")
        header = text.split("---", 2)[1]
        if not re.search(r"^name: \S+", header, re.M) or not re.search(r"^description: \S+", header, re.M):
            raise ValueError(f"Нет имени или описания: {path}")
    print(f"OK: конфигурация, {len(names)} плагин(ов), {len(paths)} навык(ов).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "new-agent", "new-skill", "new-plugin"])
    parser.add_argument("name", nargs="?")
    args = parser.parse_args()
    try:
        if args.command == "check":
            check(ROOT)
        else:
            if args.name is None:
                parser.error("Для создания нужно имя.")
            generate(ROOT, args.command.removeprefix("new-"), args.name)
            print(f"Создано: {args.command} {args.name}. Отредактируйте инструкции и выполните check.")
    except (ValueError, OSError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
