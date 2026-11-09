"""Check production behavior in isolated processes, without real credentials."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


class DeploymentTests(SimpleTestCase):
    def run_python(self, code, *, production=True, missing=()):
        env = {name: value for name, value in os.environ.items() if not name.startswith("DJANGO_")}
        env.update({
            "DJANGO_SETTINGS_MODULE": "marketing_hub.settings",
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONUTF8": "1",
        })
        if production:
            env.update({
                "DJANGO_ENV": "production",
                "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
                "DJANGO_ALLOWED_HOSTS": "example.pythonanywhere.com",
            })
        for name in missing:
            env.pop(name, None)
        return subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )

    def test_production_requires_secret_and_hostname(self):
        for name in ("DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS"):
            with self.subTest(name=name):
                result = self.run_python("import django; django.setup()", missing=(name,))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)

    def test_production_https_hosts_and_homepage(self):
        result = self.run_python('''
import django
from django.conf import settings
from django.test import Client

django.setup()
assert settings.DEBUG is False
assert settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE
client = Client(HTTP_HOST="example.pythonanywhere.com")
redirect = client.get("/")
assert redirect.status_code == 301
assert redirect.url == "https://example.pythonanywhere.com/"
response = client.get("/", HTTP_X_FORWARDED_PROTO="https")
assert response.status_code == 200
assert "Marketing Hub" in response.content.decode()
assert "max-age=3600" in response.headers["Strict-Transport-Security"]
assert client.get("/", HTTP_HOST="untrusted.invalid", secure=True).status_code == 400
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_development_still_opens_over_http(self):
        result = self.run_python('''
import django
from django.test import Client

django.setup()
assert Client(HTTP_HOST="localhost").get("/").status_code == 200
''', production=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_collectstatic_includes_dashboard_css(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_python(f'''
from pathlib import Path
import django
from django.conf import settings
from django.core.management import call_command

django.setup()
settings.STATIC_ROOT = {directory!r}
call_command("collectstatic", interactive=False, verbosity=0)
assert (Path(settings.STATIC_ROOT) / "dashboard/style.css").is_file()
''')
            self.assertEqual(result.returncode, 0, result.stderr)
