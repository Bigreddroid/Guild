import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
from decimal import Decimal
from pathlib import Path

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from guild import vendor
from guild.agents import WebOperator, agent_modes, default_agents, public_vendor_url
from guild.kernel import Kernel, KernelError
from guild.lead import DEMO_GOAL, DEMO_JOBS, ScriptedLead
from guild.market import AgentCard
from guild.review import RuleReviewer
from guild.run import run
from guild.server import create_app

TRANSPORT = next(j for j, _ in DEMO_JOBS if j.id == "transport")
VENDOR = "https://guild.example/vendor/lisbon-transfers"
QUOTES = [{"provider": "Lisbon Transfers, private van", "price": "96", "pickup": "09:30"},
          {"provider": "Airport Shuttle Co, shared shuttle", "price": "72", "pickup": "10:15"},
          {"provider": "Taxi Lisboa, 2 cars", "price": "80", "pickup": "09:45"}]


class FakeKernelApi:
    """httpx transport that behaves like the KERNEL browsers API."""

    def __init__(self, result=QUOTES, create_status=200, execute=None):
        self.result, self.create_status, self.execute = result, create_status, execute
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        raw_path = request.url.raw_path.decode().split("?")[0]
        self.calls.append((request.method, raw_path, request.headers.get("authorization"), body))
        if request.method == "POST" and request.url.path == "/browsers":
            if self.create_status != 200:
                return httpx.Response(self.create_status, json={"message": "no"})
            return httpx.Response(200, json={"session_id": "sess/1", "cdp_ws_url": "wss://x",
                                             "browser_live_view_url": "https://live.onkernel.example/sess1"})
        if request.url.path.endswith("/playwright/execute"):
            if self.execute is not None:
                return self.execute
            return httpx.Response(200, json={"success": True, "result": self.result})
        if request.method == "DELETE":
            return httpx.Response(200)
        return httpx.Response(404)


def kernel(api):
    return Kernel("k-test", base_url="https://kernel.test", transport=httpx.MockTransport(api))


def operator(api, url=VENDOR):
    return WebOperator(AgentCard("web_operator", "Web Operator", "w@x", {"browse"}, 0.85), kernel=kernel(api),
                       vendor_url=url)


class KernelClientTests(unittest.TestCase):
    def test_live_run_creates_executes_and_deletes(self):
        api = FakeKernelApi()
        out = operator(api).work(TRANSPORT)
        self.assertEqual([(m, p) for m, p, _, _ in api.calls],
                         [("POST", "/browsers"), ("POST", "/browsers/sess%2F1/playwright/execute"),
                          ("DELETE", "/browsers/sess%2F1")])
        self.assertTrue(all(auth == "Bearer k-test" for _, _, auth, _ in api.calls))
        self.assertEqual(api.calls[0][3], {"headless": False, "stealth": False, "timeout_seconds": 60})
        code = api.calls[1][3]["code"]
        self.assertIn(f"const url = {json.dumps(VENDOR)};", code)
        self.assertEqual((out["mode"], out["items"]), ("live", QUOTES))
        self.assertEqual(out["live_view_url"], "https://live.onkernel.example/sess1")
        self.assertEqual(RuleReviewer().review(TRANSPORT, out)["verdict"], "pass")

    def test_url_is_embedded_as_a_json_literal(self):
        code = vendor.script('https://x.example/a"; process.exit(1); //')
        self.assertIn(r'const url = "https://x.example/a\"; process.exit(1); //";', code)

    def test_browser_is_deleted_even_when_the_script_fails(self):
        for execute in (httpx.Response(200, json={"success": False, "error": "timeout"}),
                        httpx.Response(500), httpx.Response(200, content=b"<html>")):
            api = FakeKernelApi(execute=execute)
            out = operator(api).work(TRANSPORT)
            self.assertEqual(out["mode"], "simulated")
            self.assertIn("KERNEL unavailable", out["note"])
            self.assertEqual(api.calls[-1][0], "DELETE")

    def test_create_failure_falls_back_without_delete(self):
        api = FakeKernelApi(create_status=401)
        out = operator(api).work(TRANSPORT)
        self.assertIn("HTTP 401", out["note"])
        self.assertEqual([m for m, *_ in api.calls], ["POST"])

    def test_bad_rows_from_the_browser_are_dropped_and_trimmed(self):
        junk = [{"provider": "X" * 500, "price": "50", "pickup": "08:00"}, {"provider": "", "price": "1", "pickup": "08:00"},
                {"provider": "A", "price": "-3", "pickup": "08:00"}, {"provider": "B", "price": "abc", "pickup": "08:00"},
                {"provider": "C", "price": "10", "pickup": "25:99"}, "nope", {"provider": "D", "price": "NaN", "pickup": "08:00"}]
        out = operator(FakeKernelApi(result=junk)).work(TRANSPORT)
        self.assertEqual([(len(i["provider"]), i["price"]) for i in out["items"]], [(100, "50")])
        self.assertEqual(RuleReviewer().review(TRANSPORT, out)["verdict"], "fail")  # 1 option, need 2
        self.assertEqual(operator(FakeKernelApi(result="not a list")).work(TRANSPORT)["items"], [])

    def test_no_public_url_or_no_key_means_simulated(self):
        api = FakeKernelApi()
        self.assertEqual(operator(api, url="").work(TRANSPORT)["mode"], "simulated")
        self.assertEqual(api.calls, [])
        saved = {k: os.environ.pop(k, None) for k in ("KERNEL_API_KEY", "GUILD_PUBLIC_URL", "RENDER_EXTERNAL_URL")}
        try:
            self.assertEqual(public_vendor_url(), "")
            self.assertIsNone(default_agents()["web_operator"].kernel)
            self.assertEqual(agent_modes()["web_operator"], "simulated")
            os.environ["RENDER_EXTERNAL_URL"] = "https://guild.onrender.com"
            self.assertEqual(public_vendor_url(), "https://guild.onrender.com/vendor/lisbon-transfers")
            os.environ["RENDER_EXTERNAL_URL"] = "http://insecure.example"
            self.assertEqual(public_vendor_url(), "")
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_full_run_with_live_web_operator_tells_the_same_story(self):
        events = []
        run(DEMO_GOAL, Decimal("3.00"), default_agents(kernel=kernel(FakeKernelApi()), vendor_url=VENDOR),
            ScriptedLead(), RuleReviewer(), events.append)
        work = next(e for e in events if e["type"] == "work" and e["agent_id"] == "web_operator")
        self.assertEqual(work["mode"], "live")
        self.assertEqual(work["live_view_url"], "https://live.onkernel.example/sess1")
        self.assertEqual(events[-1]["spent"], "2.00")


class VendorPageTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(create_app(brain=lambda: (ScriptedLead(), RuleReviewer())))

    def test_form_without_search(self):
        r = self.c.get(vendor.PATH)
        self.assertEqual(r.status_code, 200)
        self.assertIn('data-testid="search"', r.text)
        self.assertIn("Demo vendor for Guild", r.text)
        self.assertNotIn('data-testid="quote"', r.text)

    def test_six_passengers_get_three_quotes(self):
        r = self.c.get(vendor.PATH, params={"passengers": "6", "date": "2026-11-02"})
        self.assertEqual(r.text.count('data-testid="quote"'), 3)
        self.assertIn('data-amount="96"', r.text)
        self.assertEqual(vendor.quotes(6), [(q["provider"], int(q["price"]), q["pickup"]) for q in QUOTES])

    def test_bad_input_is_400_and_escaped(self):
        for params in ({"passengers": "0", "date": "2026-11-02"}, {"passengers": "13", "date": "2026-11-02"},
                       {"passengers": "x", "date": "2026-11-02"}, {"passengers": "6", "date": "<script>"}):
            r = self.c.get(vendor.PATH, params=params)
            self.assertEqual(r.status_code, 400, params)
            self.assertNotIn("<script>", r.text)


def _node_playwright() -> bool:
    if not shutil.which("node"):
        return False
    probe = "try{require('playwright')}catch{require(require('child_process').execSync('npm root -g').toString().trim()+'/playwright')}"
    return subprocess.run(["node", "-e", probe], capture_output=True).returncode == 0


@unittest.skipUnless(_node_playwright(), "needs node + playwright + a local chromium")
class RealBrowserScriptTests(unittest.TestCase):
    """Runs the exact script KERNEL receives, in a real local Chromium, on the real vendor page."""

    def test_script_reads_the_quotes_from_the_real_page(self):
        import uvicorn
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = uvicorn.Server(uvicorn.Config(create_app(brain=lambda: (ScriptedLead(), RuleReviewer())),
                                               host="127.0.0.1", port=port, log_level="warning"))
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            code = vendor.script(f"http://127.0.0.1:{port}{vendor.PATH}")
            proc = subprocess.run(["node", str(Path(__file__).with_name("kernel_script_runner.cjs"))],
                                  input=code, capture_output=True, text=True, timeout=90)
        finally:
            server.should_exit = True
            t.join(10)
        out = json.loads(proc.stdout or "{}")
        self.assertTrue(out.get("success"), out.get("error") or proc.stderr[-500:])
        self.assertEqual(out["result"], QUOTES)


if __name__ == "__main__":
    unittest.main()
