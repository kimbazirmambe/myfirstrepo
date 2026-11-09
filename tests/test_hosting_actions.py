import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from actions_credentials import collector_key, main as configure_credentials
from deploy_pythonanywhere import DeployError, NoRedirect, PythonAnywhere, runtime_paths
from pa_remote_deploy import deploy, private_json


class HostingActionsTests(unittest.TestCase):
    def test_derived_key_is_stable_and_does_not_reuse_hosting_key(self):
        key = secrets.token_urlsafe(32)
        self.assertEqual(collector_key(key), collector_key(key))
        self.assertEqual(len(collector_key(key)), 64)
        self.assertNotEqual(collector_key(key), key)
        self.assertNotEqual(collector_key(key), collector_key(key + 'x'))

    def test_actions_passes_only_collector_key_to_later_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / 'env'
            out_file = Path(directory) / 'output'
            key = secrets.token_urlsafe(32)
            with patch.dict(os.environ, {'COLLECTOR_TOKEN': '', 'PA_API_TOKEN': key,
                                         'GITHUB_ENV': str(env_file), 'GITHUB_OUTPUT': str(out_file)}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(configure_credentials(), 0)
            self.assertNotIn(key, env_file.read_text())
            self.assertIn(collector_key(key), env_file.read_text())
            self.assertIn('enabled=true', out_file.read_text())

    def test_api_errors_never_echo_key_or_response_body(self):
        key = secrets.token_urlsafe(32)
        api = PythonAnywhere(key)
        with patch.object(api.opener, 'open', side_effect=HTTPError('https://example.com', 401, key, {}, None)):
            with self.assertRaises(DeployError) as result:
                api.request('/cpu/')
            self.assertNotIn(key, str(result.exception))
            self.assertIn('401', str(result.exception))

    def test_auth_redirects_are_not_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))

    def test_runtime_paths_preserve_existing_virtualenv(self):
        python, wsgi = runtime_paths({'virtualenv_path': '/home/kimbazirmambe/.virtualenvs/marketing-hub'})
        self.assertEqual(python, '/home/kimbazirmambe/.virtualenvs/marketing-hub/bin/python')
        self.assertEqual(wsgi, '/var/www/kimbazirmambe_pythonanywhere_com_wsgi.py')
        with self.assertRaises(DeployError):
            runtime_paths({})

    def test_remote_stops_on_console_token_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / 'result.json'
            key = secrets.token_urlsafe(32)
            with patch.dict(os.environ, {'API_TOKEN': key}), patch.object(Path, 'home', return_value=Path(directory)):
                self.assertEqual(deploy({'result_path': str(result), 'api_token_fingerprint': 'wrong'}), 1)
            data = result.read_text()
            self.assertNotIn(key, data)
            self.assertEqual(json.loads(data)['error'], 'console_token_unavailable')

    def test_full_remote_preparation_preserves_database_and_django_key(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home / 'myfirstrepo'
            shutil.copytree(ROOT / 'src', root / 'src', ignore=shutil.ignore_patterns('db.sqlite3', 'db.sqlite3-*', '__pycache__'))
            (root / 'scripts').mkdir()
            shutil.copyfile(ROOT / 'scripts/update_pythonanywhere.py', root / 'scripts/update_pythonanywhere.py')
            (root / '.git').mkdir()
            config = home / '.config/marketing-hub/environment.json'
            secret = secrets.token_urlsafe(64)
            private_json(config, {'DJANGO_ENV': 'production', 'DJANGO_SECRET_KEY': secret,
                                  'DJANGO_ALLOWED_HOSTS': 'kimbazirmambe.pythonanywhere.com'})
            clean_env = {k: v for k, v in os.environ.items() if not k.startswith('DJANGO_')}
            subprocess.run([sys.executable, str(root / 'scripts/update_pythonanywhere.py'), '--config', str(config), '--apply'],
                           env=clean_env, capture_output=True, check=True, timeout=30)
            wsgi = home / 'wsgi.py'
            wsgi.write_text(
                'import os,json,sys\nfrom pathlib import Path\n'
                f'os.environ.update(json.loads(Path({str(config)!r}).read_text()))\n'
                f'sys.path.insert(0,{str(root / "src")!r})\n'
                'os.environ["DJANGO_SETTINGS_MODULE"]="marketing_hub.settings"\n'
                'from django.core.wsgi import get_wsgi_application\napplication=get_wsgi_application()\n'
            )
            insert = f"import runpy;runpy.run_path({str(wsgi)!r});from sources.models import NewsSource;NewsSource.objects.create(name='Keep',rss_url='https://example.com/feed')"
            subprocess.run([sys.executable, '-c', insert], env=clean_env, capture_output=True, check=True, timeout=10)
            key = secrets.token_urlsafe(32)
            commit = 'a' * 40
            result_path = home / 'job/result.json'
            spec = {'result_path': str(result_path), 'python_path': sys.executable, 'wsgi_path': str(wsgi),
                    'api_token_fingerprint': hashlib.sha256(key.encode()).hexdigest(),
                    'domain': 'kimbazirmambe.pythonanywhere.com', 'job_id': 'test', 'commit': commit}
            real_run = subprocess.run
            def run(command, **kwargs):
                if command[0] == 'git':
                    value = b''
                    if command[1:3] == ['rev-parse', 'HEAD']:
                        value = b'b' * 40
                    if command[1:3] == ['rev-parse', 'FETCH_HEAD']:
                        value = commit.encode()
                    return subprocess.CompletedProcess(command, 0, stdout=value)
                if '-m' in command and 'pip' in command:
                    return subprocess.CompletedProcess(command, 0)
                return real_run(command, **kwargs)
            with patch.dict(os.environ, {**clean_env, 'API_TOKEN': key}, clear=True), patch.object(Path, 'home', return_value=home), patch('pa_remote_deploy.subprocess.run', side_effect=run):
                self.assertEqual(deploy(spec), 0)
            result = json.loads(result_path.read_text())
            self.assertEqual(result['state'], 'prepared')
            self.assertTrue(Path(result['backup']).is_file())
            values = json.loads(config.read_text())
            self.assertEqual(values['DJANGO_SECRET_KEY'], secret)
            self.assertEqual(values['COLLECTOR_TOKEN'], collector_key(key))
            self.assertNotIn(secret, result_path.read_text())
            self.assertNotIn(key, result_path.read_text())
            self.assertNotIn(secret, wsgi.read_text())
            with contextlib.closing(sqlite3.connect(result['backup'])) as db:
                self.assertTrue(db.execute("SELECT name FROM sqlite_master WHERE name='newsroom_article'").fetchone())
                self.assertEqual(db.execute("SELECT name FROM sources_newssource").fetchone()[0], "Keep")
            with contextlib.closing(sqlite3.connect(root / "src/db.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT name FROM sources_newssource").fetchone()[0], "Keep")
