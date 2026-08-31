import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from app.server import HarnessRequestHandler


class AgentBackendStatusApiTests(unittest.TestCase):
    def test_manager_exposes_readonly_backend_discovery(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/agent-backends", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual("his-agent-backend-status.v1", payload["schema_version"])
        self.assertEqual("host-bridge", payload["default_backend"])
        self.assertEqual({"codex-cli", "codex-app-server", "host-bridge"}, {
            item["backend_id"] for item in payload["backends"]
        })


if __name__ == "__main__":
    unittest.main()
