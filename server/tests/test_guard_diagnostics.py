import http.server
import threading

from server import deployment_guard as g


def _serve(status):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_probe_reports_the_reason():
    ok, bad = _serve(200), _serve(502)
    try:
        assert g._probe(f"http://127.0.0.1:{ok.server_port}/") == "ok"
        assert g._probe(f"http://127.0.0.1:{bad.server_port}/") == "HTTP 502"
    finally:
        ok.shutdown()
        bad.shutdown()
    assert "refused" in g._probe("http://127.0.0.1:9/").lower()


def test_failed_verification_names_the_failing_check(monkeypatch):
    monkeypatch.setattr(g, "_tailscale_health_url", lambda: "https://tailnet/health")
    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    monkeypatch.setattr(g, "_probe",
                        lambda url, token=None: "HTTP 502" if "tailnet" in url else "ok")
    healthy, detail = g._healthy("/repo", tries=2)
    assert not healthy
    assert detail.endswith("— tailscale: HTTP 502")
