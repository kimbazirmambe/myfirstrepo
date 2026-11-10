"""GitHub runner: fetch active sources and send bounded results over HTTPS."""
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from collectors.job_runner import run_jobs
from collectors.writing import WritingError
from collectors.web import CollectionError, NoRedirect, collect_source


def hub_call(base, token, path, payload=None):
    # Never follow redirects while carrying the machine credential.
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = Request(base + path, data=data, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    for attempt in range(7):
        try:
            with build_opener(NoRedirect).open(req, timeout=30) as response:
                return json.loads(response.read(1024 * 1024))
        except HTTPError as error:
            if payload is None and error.code in {404, 502, 503, 504} and attempt < 6:
                time.sleep(5)
                continue
            raise RuntimeError(f"Marketing Hub HTTP {error.code}; check deployment and collector settings") from None
        except (OSError, ValueError):
            raise RuntimeError("Marketing Hub unavailable or returned an invalid response") from None


def canonical_url(url):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def main(check_only=False):
    base = os.environ.get("HUB_BASE_URL", "").rstrip("/")
    token = os.environ.get("COLLECTOR_TOKEN", "")
    parts = urlsplit(base)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment or len(token) < 32:
        print("Configure HTTPS HUB_BASE_URL and COLLECTOR_TOKEN (at least 32 characters).", file=sys.stderr)
        return 1
    try:
        sources = hub_call(base, token, "/collector/queue/")["sources"]
        if check_only:
            print(f"Published collector endpoint ready; eligible sources: {len(sources)}")
            return 0
        jobs = run_jobs(lambda path, payload=None: hub_call(base, token, path, payload))
        print(f"Archive batches: {jobs['batches']}; generation requests: {jobs['generations']}")
        for source in sources:
            payload = {"source_id": source["id"], "source_url": source["url"], "articles": []}
            try:
                rows = collect_source(source["url"], known_urls=source.get("known_urls", []))
                for row in rows:
                    row["url"] = canonical_url(row["url"])
                rows = [row for row in rows if row["url"] not in source.get("known_urls", [])][:3]
                payload["articles"] = rows
            except (CollectionError, WritingError) as error:
                payload["error"] = str(error)[:500]
            except Exception:
                payload["error"] = "Не удалось разобрать источник; нужен отдельный обработчик."
            result = hub_call(base, token, "/collector/results/", payload)
            print(f"Source {source['id']}: added={result['added']}, drafts={result['drafts']}, duplicates={result.get('duplicates', 0)}, filtered={result.get('filtered', 0)}, error={bool(payload.get('error'))}")
            if payload.get("error"):
                print("Collection diagnostic: " + payload["error"])
    except (RuntimeError, KeyError, TypeError):
        print("Collector failed. Check the Hub URL, deployment and matching machine secret. Secret values are never printed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(check_only="--check" in sys.argv[1:]))
