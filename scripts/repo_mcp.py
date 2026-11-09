"""Minimal stdio MCP example: fixed repository metadata, no file or shell access."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}


def handle(request):
    if not isinstance(request, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in request:  # MCP notifications do not receive responses.
        return None
    response = {"jsonrpc": "2.0", "id": request["id"]}
    method = request.get("method")
    params = request.get("params", {})
    if not isinstance(params, dict):
        response["error"] = {"code": -32602, "message": "Invalid params"}
        return response
    if method == "initialize":
        version = params.get("protocolVersion")
        response["result"] = {
            "protocolVersion": version if version in PROTOCOLS else "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "myfirstrepo-tools", "version": "0.1.0"},
        }
    elif method == "ping":
        response["result"] = {}
    elif method == "tools/list":
        response["result"] = {"tools": [{
            "name": "repository_info",
            "description": "Return this checkout's name and development check commands.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        }]}
    elif method == "tools/call" and params.get("name") == "repository_info":
        if params.get("arguments", {}) != {}:
            response["error"] = {"code": -32602, "message": "This tool takes no arguments"}
        else:
            data = {"name": ROOT.name, "checks": [
                "python scripts/workspace.py check", "python -m unittest discover -s tests -v",
            ]}
            response["result"] = {"content": [{"type": "text", "text": json.dumps(data)}], "isError": False}
    else:
        response["error"] = {"code": -32601, "message": "Unknown method or tool"}
    return response


def main():
    for line in sys.stdin:
        try:
            result = handle(json.loads(line))
        except json.JSONDecodeError:
            result = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if result is not None:
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
