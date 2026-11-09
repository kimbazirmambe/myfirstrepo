from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


class HostingUpdateTests(SimpleTestCase):
    def test_preparation_preserves_database_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            shutil.copytree(ROOT / "src", root / "src", ignore=shutil.ignore_patterns("db.sqlite3", "db.sqlite3-*", "__pycache__"))
            (root / "scripts").mkdir()
            shutil.copyfile(ROOT / "scripts/update_pythonanywhere.py", root / "scripts/update_pythonanywhere.py")
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": secrets.token_urlsafe(64), "DJANGO_ALLOWED_HOSTS": "example.pythonanywhere.com"}))
            command = [sys.executable, str(root / "scripts/update_pythonanywhere.py"), "--config", str(config), "--apply"]
            env = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_")}
            subprocess.run(command, env=env, capture_output=True, check=True, timeout=30)
            database = root / "src/db.sqlite3"
            with closing(sqlite3.connect(database)) as db:
                db.execute("INSERT INTO sources_newssource (name,rss_url,is_active,created_at) VALUES ('Keep','https://example.com/feed',1,'2026-11-09')")
                db.commit()
            subprocess.run(command, env=env, capture_output=True, check=True, timeout=30)
            backups = list((root / ".local/backups").glob("*.sqlite3"))
            self.assertEqual(len(backups), 1)
            for path in (database, backups[0]):
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute("SELECT name FROM sources_newssource").fetchone()[0], "Keep")
            self.assertTrue((root / ".local/static/dashboard/style.css").is_file())
            self.assertTrue((root / ".local/static/admin/css/base.css").is_file())

    def test_invalid_config_fails_without_printing_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            marker = secrets.token_urlsafe(24)
            path.write_text('{"secret":"' + marker)
            run = subprocess.run([sys.executable, str(ROOT / "scripts/update_pythonanywhere.py"), "--config", str(path)], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(run.returncode, 0)
            self.assertNotIn(marker, run.stdout + run.stderr)
