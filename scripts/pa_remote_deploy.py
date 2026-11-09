"""Runs on the owner's PythonAnywhere account. Never prints configuration values."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile


def private_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".deploy-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def deploy(spec):
    root = Path.home() / "myfirstrepo"
    result_path = Path(spec["result_path"])
    stage = "preflight"
    result = {"state": "running", "stage": stage}
    private_json(result_path, result)
    try:
        token = os.environ.get("API_TOKEN", "")
        if not token or hashlib.sha256(token.encode()).hexdigest() != spec["api_token_fingerprint"]:
            raise RuntimeError("console_token_unavailable")
        if not root.is_dir() or not (root / ".git").is_dir():
            raise RuntimeError("checkout_missing")
        # Use the exact interpreter and WSGI path supplied by the owning Web app.
        python = Path(spec["python_path"])
        wsgi = Path(spec["wsgi_path"])
        if not python.is_file() or not wsgi.is_file():
            raise RuntimeError("runtime_path_missing")
        log = result_path.with_suffix(".log")
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            def command(arguments, *, capture=False):
                run = subprocess.run(arguments, cwd=root, stdout=subprocess.PIPE if capture else stream,
                                     stderr=stream, timeout=240)
                if run.returncode:
                    raise RuntimeError("command_failed")
                return run.stdout.decode().strip() if capture else None
            if command(["git", "status", "--porcelain"], capture=True):
                raise RuntimeError("checkout_has_local_changes")
            before = command(["git", "rev-parse", "HEAD"], capture=True)
            config = Path.home() / ".config/marketing-hub/environment.json"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_dir = Path.home() / "backups/marketing-hub" / ("deploy-" + stamp + "-" + spec["job_id"])
            backup_dir.mkdir(parents=True, mode=0o700)
            shutil.copyfile(wsgi, backup_dir / "wsgi.py")
            (backup_dir / "wsgi.py").chmod(0o600)
            if config.exists():
                shutil.copyfile(config, backup_dir / "environment.json")
                (backup_dir / "environment.json").chmod(0o600)
            # Extract runtime settings on the host into a private file, never stdout.
            exported = backup_dir / "runtime.json"
            probe = (
                "import runpy,os,json; from pathlib import Path; "
                f"runpy.run_path({str(wsgi)!r}); "
                "from django.conf import settings; "
                "data={'secret':settings.SECRET_KEY,'hosts':settings.ALLOWED_HOSTS,"
                "'debug':settings.DEBUG,'database':str(settings.DATABASES['default']['NAME'])}; "
                f"fd=os.open({str(exported)!r},os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); "
                "stream=os.fdopen(fd,'w'); json.dump(data,stream); stream.close()"
            )
            command([str(python), "-c", probe])
            current = json.loads(exported.read_text())
            database = Path(current["database"]).resolve()
            if current["debug"] or not current["secret"] or spec["domain"] not in current["hosts"]:
                raise RuntimeError("existing_production_settings_invalid")
            if database != (root / "src/db.sqlite3").resolve() or not database.is_file():
                raise RuntimeError("nonstandard_database_requires_review")
            stage = "database_backup"
            backup = backup_dir / "db.sqlite3"
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(backup)) as destination:
                    source.backup(destination)
                    tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    counts_before = {table: source.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                                     for table in ("sources_newssource", "newsroom_article", "newsroom_draft", "newsroom_draftrevision") if table in tables}
            stage = "checkout_update"
            command(["git", "fetch", "origin", "main"])
            if command(["git", "rev-parse", "FETCH_HEAD"], capture=True) != spec["commit"]:
                raise RuntimeError("main_changed_since_ci")
            command(["git", "merge-base", "--is-ancestor", "HEAD", spec["commit"]])
            command(["git", "merge", "--ff-only", spec["commit"]])
            stage = "dependencies"
            command([str(python), "-m", "pip", "install", "-r", "requirements.txt"])
            values = json.loads(config.read_text()) if config.exists() else {}
            values.update(DJANGO_ENV="production", DJANGO_SECRET_KEY=current["secret"],
                          DJANGO_ALLOWED_HOSTS=",".join(current["hosts"]),
                          COLLECTOR_TOKEN=hmac.new(token.encode(), b"marketing-hub:collector:v1", hashlib.sha256).hexdigest())
            private_json(config, values)
            stage = "migrations_and_static"
            command([str(python), "scripts/update_pythonanywhere.py", "--apply", "--config", str(config)])
            # Validate WSGI before writing it; retain a private copy of the old file.
            launch = (
                "import json,os,sys\nfrom pathlib import Path\n"
                f"os.environ.update(json.loads(Path({str(config)!r}).read_text(encoding='utf-8')))\n"
                f"sys.path.insert(0,{str(root / 'src')!r})\n"
                "os.environ['DJANGO_SETTINGS_MODULE']='marketing_hub.settings'\n"
                "from django.core.wsgi import get_wsgi_application\napplication=get_wsgi_application()\n"
            )
            candidate = backup_dir / "new_wsgi.py"
            candidate.write_text(launch)
            stage = "wsgi_validation"
            command([str(python), "-c", f"import runpy;runpy.run_path({str(candidate)!r})"])
            stage = "database_preservation_check"
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as check:
                if any(check.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] < count for table, count in counts_before.items()):
                    raise RuntimeError("record_count_decreased")
            stage = "wsgi_update"
            with wsgi.open("w", encoding="utf-8") as output:
                output.write(launch)
            # Remove the redundant extracted secret; backed-up original configs remain private.
            exported.unlink()
            result = {"state": "prepared", "stage": "ready_for_reload", "commit": spec["commit"],
                      "previous_commit": before, "backup": str(backup), "wsgi_backup": str(backup_dir / "wsgi.py")}
    except Exception as error:
        allowed = {"console_token_unavailable", "checkout_missing", "runtime_path_missing", "checkout_has_local_changes",
                   "existing_production_settings_invalid", "nonstandard_database_requires_review", "main_changed_since_ci", "record_count_decreased"}
        code = str(error) if isinstance(error, RuntimeError) and str(error) in allowed else type(error).__name__
        result = {"state": "failed", "stage": stage, "error": code}
    private_json(result_path, result)
    return 0 if result["state"] == "prepared" else 1


if __name__ == "__main__":
    raise SystemExit(deploy(json.loads(Path(sys.argv[1]).read_text())))
