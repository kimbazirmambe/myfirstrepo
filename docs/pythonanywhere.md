# Размещение Marketing Hub на PythonAnywhere Beginner

Все действия выполняются в браузере PythonAnywhere. Устанавливать Python или VS Code на компьютер не нужно. Эта инструкция сама ничего не публикует. Создание Web app и Reload включают доступ к сайту: выполняйте их только после решения опубликовать приложение.

Нужны бесплатный аккаунт, доступ к репозиторию и Python 3.12 в списке доступных версий. Проверьте версию до создания окружения. Если Python 3.12 недоступен, остановитесь и уточните подходящую версию окружения; не выбирайте платный тариф ради этой инструкции. Ресурсы Beginner ограничены, внешние запросы могут быть ограничены, сайт может требовать периодического продления через панель. Для текущей главной страницы внешние API не нужны.

## 1. Скачать код в браузерной консоли

После принятия PR с настройками откройте **Consoles → Bash**:

```bash
git clone https://github.com/kimbazirmambe/myfirstrepo.git
cd ~/myfirstrepo
python3.12 -m venv ~/.virtualenvs/marketing-hub
source ~/.virtualenvs/marketing-hub/bin/activate
python -m pip install -r requirements.txt
```

Если checkout уже есть, не клонируйте второй: сначала проверьте `git status`, затем при отсутствии своих изменений используйте `git pull --ff-only origin main` в существующем каталоге. Не удаляйте и не сбрасывайте свои файлы.

Если приватный репозиторий недоступен, не вводите пароль GitHub в команду и не делайте репозиторий публичным ради установки. Остановитесь и выберите безопасный способ доступа или загрузку архива через Files. Секреты не передавайте в чат.

## 2. Создать секретные настройки вне репозитория

Следующий блок создаёт файл в домашнем каталоге PythonAnywhere, а не в Git. Замените `YOUR_USERNAME.pythonanywhere.com` на точный будущий адрес сайта из панели. Для другого региона используйте домен, указанный именно в вашем аккаунте.

```bash
python - <<'PY'
import json
import os
from pathlib import Path
from django.core.management.utils import get_random_secret_key

config = Path.home() / ".config" / "marketing-hub" / "environment.json"
config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
values = {
    "DJANGO_ENV": "production",
    "DJANGO_SECRET_KEY": get_random_secret_key(),
    "DJANGO_ALLOWED_HOSTS": "YOUR_USERNAME.pythonanywhere.com",
}
# Не перезаписывает существующий ключ при повторном запуске.
fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as stream:
    json.dump(values, stream)
print("Настройки созданы; секретный ключ не выводится.")
PY
```

Файл хранит секрет только на хостинге с доступом для владельца. При запуске его содержимое загружается в переменные окружения процесса. Сам Django не читает этот JSON и `.env` автоматически. Не загружайте файл в GitHub, не копируйте в чат или скриншоты. Если файл уже существует, сохраните его: менять ключ при каждом обновлении нельзя. При необходимости исправьте только домен через **Files**, не показывая ключ другим людям.

## 3. Создать таблицы и собрать CSS

В той же Bash-консоли, из `~/myfirstrepo`, с активным виртуальным окружением:

```bash
python - <<'PY'
import json
import os
from pathlib import Path
import sys

root = Path.home() / "myfirstrepo"
config = Path.home() / ".config" / "marketing-hub" / "environment.json"
os.environ.update(json.loads(config.read_text(encoding="utf-8")))
os.environ["DJANGO_SETTINGS_MODULE"] = "marketing_hub.settings"
sys.path.insert(0, str(root / "src"))

import django
from django.core.management import call_command

django.setup()
call_command("check", deploy=True, fail_level="ERROR")
call_command("migrate", interactive=False)
call_command("collectstatic", interactive=False)
PY
```

SQLite создаётся в `~/myfirstrepo/src/db.sqlite3`. Этот файл не отправляется в GitHub. Не удаляйте его при обновлениях; когда появятся пользовательские данные, сохраняйте резервную копию средствами SQLite перед изменениями базы.

`collectstatic` складывает CSS в `~/myfirstrepo/.local/static`. Папка исключена из Git. Команда проверок может предупредить об отключённых HSTS preload и includeSubDomains: они намеренно не включены. HTTPS обязателен, HSTS действует один час только для данного адреса. Включать preload для тестового сайта не нужно.

## 4. Настроить Web app — только когда решите публиковать

Откройте **Web → Add a new web app**, выберите бесплатный адрес, **Manual configuration**, **Python 3.12**. Не выбирайте автоматический шаблон Django: приложение уже есть.

В разделе **Virtualenv** укажите абсолютный путь:

```text
/home/YOUR_USERNAME/.virtualenvs/marketing-hub
```

В **Code** укажите Source code и Working directory: `/home/YOUR_USERNAME/myfirstrepo/src`.

Откройте **WSGI configuration file** из Web-панели. Это служебный файл хостинга, не `src/marketing_hub/wsgi.py` в репозитории. Замените содержимое служебного файла следующим блоком:

```python
import json
import os
from pathlib import Path
import sys

root = Path.home() / "myfirstrepo"
config = Path.home() / ".config" / "marketing-hub" / "environment.json"
os.environ.update(json.loads(config.read_text(encoding="utf-8")))
sys.path.insert(0, str(root / "src"))
os.environ["DJANGO_SETTINGS_MODULE"] = "marketing_hub.settings"

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

В разделе **Static files** добавьте:

| URL | Directory |
| --- | --- |
| `/static/` | `/home/YOUR_USERNAME/myfirstrepo/.local/static` |

В путях замените YOUR_USERNAME на имя аккаунта. Никогда не добавляйте раздачу всего checkout, домашнего каталога или файла SQLite как статики.

Включите **Force HTTPS**, если переключатель доступен в Web-панели. В Django также включён переход с HTTP на HTTPS, защищённые cookies и учёт HTTPS-заголовка доверенного прокси PythonAnywhere. Эти настройки прокси рассчитаны на PythonAnywhere; при переезде на другую платформу их нужно проверить заново.

Нажмите **Reload** и откройте HTTPS-адрес из панели. Проверьте, что видны главная страница и оформление, а HTTP переходит на HTTPS. Не используйте `runserver` для опубликованного сайта.

## Если что-то не работает

- **400 / DisallowedHost**: проверьте точный домен в `DJANGO_ALLOWED_HOSTS`, без `https://`, слеша и порта. Не заменяйте его на `*`.
- **500**: смотрите Error log в Web-панели; проверьте путь к окружению, JSON и WSGI. Не включайте DEBUG на публичном сайте.
- **Нет оформления**: повторите `collectstatic` и проверьте сопоставление `/static/`.
- **Цикл перенаправлений HTTPS**: проверьте настройку HTTPS и прокси по документации PythonAnywhere; не отключайте защиту наугад.

Тесты можно выполнить до создания Web app, с обычными локальными настройками:

```bash
python src/manage.py test tests/web -v 2
python scripts/workspace.py check
python -m unittest discover -s tests -v
```

После обновлений: загрузите согласованные изменения, установите зависимости, повторите шаг 3 с тем же секретным файлом и нажмите Reload. Не запускайте генерацию нового ключа повторно.

Официальные справки: [Django на PythonAnywhere](https://help.pythonanywhere.com/pages/DeployExistingDjangoProject/), [статические файлы](https://help.pythonanywhere.com/pages/DjangoStaticFiles/), [проверки перед публикацией Django](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/).
