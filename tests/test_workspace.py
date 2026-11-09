import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import workspace


class GeneratorsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project with spaces"
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".local"))

    def test_generate_and_validate_all_extensions(self):
        for kind in ("agent", "skill", "plugin"):
            workspace.generate(self.root, kind, "sample-" + kind)
        workspace.check(self.root)

    def test_reject_path_traversal_and_invalid_names(self):
        for name in ("../escape", "a/b", "a\\b", "A", "a.b", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                workspace.generate(self.root, "agent", name)

    def test_duplicate_preserves_existing_files(self):
        for kind in ("agent", "skill", "plugin"):
            workspace.generate(self.root, kind, "keep-" + kind)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        for kind in ("agent", "skill", "plugin"):
            with self.assertRaises(ValueError):
                workspace.generate(self.root, kind, "keep-" + kind)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_check_detects_missing_role(self):
        (self.root / ".codex/agents/reviewer.toml").unlink()
        with self.assertRaises(OSError):
            workspace.check(self.root)


class McpTest(unittest.TestCase):
    def test_stdio_session_and_errors(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "repository_info", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "repository_info", "arguments": {"path": "../"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "run_shell"}},
        ]
        stream = "\n".join(json.dumps(r) for r in requests) + "\ninvalid\n"
        run = subprocess.run([sys.executable, str(ROOT / "scripts/repo_mcp.py")], input=stream, text=True,
                             capture_output=True, check=True, timeout=10, cwd=tempfile.gettempdir())
        replies = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(len(replies), 6)
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(replies[1]["result"]["tools"][0]["name"], "repository_info")
        data = json.loads(replies[2]["result"]["content"][0]["text"])
        self.assertEqual(data["name"], ROOT.name)
        self.assertIn("python scripts/workspace.py check", data["checks"])
        self.assertEqual([r["error"]["code"] for r in replies[3:]], [-32602, -32601, -32700])


if __name__ == "__main__":
    unittest.main()
