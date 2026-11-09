"""Deploy via protected Actions secret and an explicitly supplied idle console."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

ACCOUNT = "kimbazirmambe"
DOMAIN = ACCOUNT + ".pythonanywhere.com"
API = "https://www.pythonanywhere.com/api/v0/user/" + ACCOUNT
ROOT = Path(__file__).resolve().parents[1]


class DeployError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PythonAnywhere:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(NoRedirect)

    def request(self, path, *, method="GET", data=None, content_type=None, missing_ok=False):
        headers = {"Authorization": "Token " + self.token}
        if content_type:
            headers["Content-Type"] = content_type
        for attempt in range(3):
            try:
                with self.opener.open(Request(API + path, data=data, method=method, headers=headers), timeout=30) as response:
                    return response.read(128000)
            except HTTPError as error:
                if missing_ok and error.code == 404:
                    return None
                # Retry only safe reads. Never duplicate a console command on an ambiguous failure.
                if method == "GET" and error.code in {429, 502, 503, 504} and attempt < 2:
                    print(f"Temporary hosting API HTTP {error.code}; retrying read {attempt + 1}/2.")
                    time.sleep(5 * (attempt + 1))
                    continue
                raise DeployError(f"PythonAnywhere HTTP {error.code}; endpoint {path.split('/')[1]}. No credential values logged.", error.code) from None
            except OSError:
                raise DeployError("PythonAnywhere connection failed or timed out.") from None

    def json(self, path):
        try:
            return json.loads(self.request(path))
        except (ValueError, TypeError):
            raise DeployError("PythonAnywhere returned an unexpected response.") from None

    def send(self, console, text):
        self.request(f"/consoles/{console}/send_input/", method="POST", data=urlencode({"input": text + "\n"}).encode(),
                     content_type="application/x-www-form-urlencoded")

    def read_file(self, path, missing_ok=False):
        return self.request("/files/path" + quote(path, safe="/"), missing_ok=missing_ok)

    def upload(self, path, content):
        boundary = "marketing-hub-" + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="content"; filename="upload"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode() + content + f"\r\n--{boundary}--\r\n".encode()
        self.request("/files/path" + quote(path, safe="/"), method="POST", data=body,
                     content_type="multipart/form-data; boundary=" + boundary)


def wait_file(api, path, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = api.read_file(path, missing_ok=True)
        if value is not None:
            return value
        time.sleep(5)
    raise DeployError("Console did not respond. Leave the supplied Bash console open and idle; no interrupt was sent.")


def runtime_paths(app):
    venv = (app.get("virtualenv_path") or "").rstrip("/")
    python = venv + "/bin/python" if venv else ""
    wsgi = app.get("wsgi_config_file") or app.get("wsgi_config_path") or f"/var/www/{DOMAIN.replace('.', '_')}_wsgi.py"
    if not python.startswith(f"/home/{ACCOUNT}/") or not str(wsgi).startswith("/var/www/"):
        raise DeployError("Existing Web app runtime paths need review; no changes made.")
    return python, str(wsgi)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--console", required=True, type=int)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--commit")
    args = parser.parse_args()
    token = os.environ.get("PA_API_TOKEN", "")
    if not token:
        print("GitHub Actions secret PA_API_TOKEN is missing. Add it in this repository, not in the chat.", file=sys.stderr)
        return 1
    try:
        api = PythonAnywhere(token)
        try:
            app = api.json(f"/webapps/{DOMAIN}/")
        except DeployError as error:
            if error.status not in {502, 503, 504}:
                raise
            apps = api.json("/webapps/")
            matches = [item for item in apps if isinstance(item, dict) and item.get("domain_name") == DOMAIN] if isinstance(apps, list) else []
            if len(matches) != 1:
                raise DeployError("Existing Web app metadata is unavailable; nothing changed.") from None
            app = matches[0]
        console = api.json(f"/consoles/{args.console}/")
        if not isinstance(console, dict) or str(console.get("id")) != str(args.console):
            raise DeployError("The specified console was not found in this account.")
        if args.probe:
            print("Web app metadata fields: " + ", ".join(sorted(app.keys())))
        python, wsgi = runtime_paths(app)
        if args.probe:
            print("PythonAnywhere authentication OK; existing Web app and supplied console accessible. No files changed.")
            return 0
        if not args.commit or len(args.commit) != 40 or any(c not in "0123456789abcdef" for c in args.commit):
            raise DeployError("A tested full commit SHA is required.")
        job_id = uuid.uuid4().hex[:12]
        directory = f"/home/{ACCOUNT}/.local/share/marketing-hub-deploy/{job_id}"
        # Harmless idle-shell handshake before uploading or running deployment code.
        ready = directory + "/ready"
        api.send(args.console, f"(umask 077; mkdir -p {shlex.quote(directory)} && chmod 700 {shlex.quote(directory)} && printf ready > {shlex.quote(ready)})")
        if wait_file(api, ready).strip() != b"ready":
            raise DeployError("Console handshake failed; no deployment started.")
        script = directory + "/deploy.py"
        spec_path = directory + "/spec.json"
        result_path = directory + "/result.json"
        spec = {"commit": args.commit, "domain": DOMAIN, "python_path": python, "wsgi_path": wsgi,
                "api_token_fingerprint": hashlib.sha256(token.encode()).hexdigest(), "job_id": job_id, "result_path": result_path}
        api.upload(script, (ROOT / "scripts/pa_remote_deploy.py").read_bytes())
        api.upload(spec_path, json.dumps(spec).encode())
        api.send(args.console, "(" + " ".join(shlex.quote(s) for s in (python, script, spec_path)) + " >/dev/null 2>&1)")
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            raw = api.read_file(result_path, missing_ok=True)
            if raw:
                try:
                    result = json.loads(raw)
                except ValueError:
                    time.sleep(5)
                    continue
                if result.get("state") == "failed":
                    raise DeployError(f"Remote preparation stopped at {result.get('stage')}: {result.get('error')}. Existing backups retained; no reload requested.")
                if result.get("state") == "prepared":
                    if result.get("commit") != args.commit:
                        raise DeployError("Unexpected prepared commit; reload refused.")
                    api.request(f"/webapps/{DOMAIN}/reload/", method="POST", data=b"")
                    print("Prepared commit: " + args.commit)
                    print("SQLite backup: " + result["backup"])
                    print("Existing Django SECRET_KEY preserved; collector key configured. Reload requested.")
                    return 0
            time.sleep(5)
        raise DeployError("Preparation timeout. Do not retry blindly; inspect the saved result in the account.")
    except DeployError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
