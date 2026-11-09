"""Prepare an existing PythonAnywhere checkout; never publishes or reloads it."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up SQLite, run migrations and collect static files")
    parser.add_argument("--config", type=Path, default=Path.home() / ".config/marketing-hub/environment.json")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if config.get("DJANGO_ENV") != "production":
            raise ValueError("Production configuration required")
        if not config.get("DJANGO_SECRET_KEY") or not config.get("DJANGO_ALLOWED_HOSTS"):
            raise ValueError("Secret key and allowed hosts must already be configured")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in config.items()):
            raise ValueError("Configuration must contain string values")
    except (OSError, ValueError) as error:
        # Never echo configuration contents or JSON parse excerpts.
        print(f"Configuration unavailable or invalid ({type(error).__name__}). See docs/pythonanywhere.md.", file=sys.stderr)
        return 1
    os.environ.update(config)
    os.environ["DJANGO_SETTINGS_MODULE"] = "marketing_hub.settings"
    sys.path.insert(0, str(ROOT / "src"))
    import django
    from django.conf import settings
    from django.core.management import call_command
    django.setup()
    call_command("check", deploy=True, fail_level="ERROR")
    if not args.apply:
        print("Check completed; nothing changed. Use --apply to back up and prepare files.")
        return 0
    database = Path(settings.DATABASES["default"]["NAME"])
    if database.exists():
        backups = ROOT / ".local/backups"
        backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = backups / f"sqlite-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.sqlite3"
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(backup) as target:
                source.backup(target)
        print("SQLite backup saved in .local/backups (excluded from Git).")
    call_command("migrate", interactive=False)
    call_command("collectstatic", interactive=False)
    print("Preparation completed. Reload the existing web app in PythonAnywhere's Web panel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
