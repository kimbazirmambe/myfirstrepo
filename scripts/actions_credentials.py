"""Prepare narrowly scoped collector credentials inside a trusted Actions job."""
import hashlib
import hmac
import os
from pathlib import Path

CONTEXT = b"marketing-hub:collector:v1"


def collector_key(api_token):
    return hmac.new(api_token.encode(), CONTEXT, hashlib.sha256).hexdigest()


def main():
    parent = os.environ.get("PA_API_TOKEN", "")
    token = collector_key(parent) if parent else os.environ.get("COLLECTOR_TOKEN", "")
    enabled = len(token) >= 32
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as stream:
            stream.write(f"enabled={'true' if enabled else 'false'}\n")
    if enabled:
        # Runner workflow command: masks the derived value before any later step.
        print("::add-mask::" + token)
        with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as stream:
            stream.write("COLLECTOR_TOKEN=" + token + "\n")
    print("Collector credentials ready." if enabled else "Collector disabled: no protected credential is configured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
