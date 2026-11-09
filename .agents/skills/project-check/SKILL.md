---
name: project-check
description: Проверка структуры и служебных инструментов репозитория перед передачей результата.
---

1. Найдите корень checkout и прочитайте AGENTS.md и README.md.
2. Из корня выполните `python scripts/workspace.py check`.
3. Выполните `python -m unittest discover -s tests -v`.
4. Проверьте git diff и git status: не включайте секреты и посторонние изменения.
5. Сообщите результаты. Для прикладного кода запустите также его собственные проверки.
