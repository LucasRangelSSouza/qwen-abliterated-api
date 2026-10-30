import http.server
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "remote" / "watchdog.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or shutil.which("curl") is None, reason="precisa de bash e curl")

METRICS = ('vllm:num_requests_running{model_name="x"} %d\nvllm:num_requests_waiting{model_name="x"} 0\n'
           'vllm:request_success_total{model_name="x",finished_reason="stop"} %d\n')


class _H(http.server.BaseHTTPRequestHandler):
    state = {"running": 0, "total": 0}

    def do_GET(self):
        body = (METRICS % (self.state["running"], self.state["total"])).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def metrics_server():
    _H.state.update(running=0, total=0)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/metrics", _H.state
    srv.shutdown()


def run(url, idle_s, timeout, **extra):
    env = dict(os.environ, WATCHDOG_POLL_S="0.2", WATCHDOG_IDLE_S=str(idle_s), WATCHDOG_METRICS_URLS=url, WATCHDOG_DRYRUN="1", **extra)
    return subprocess.Popen([BASH, str(SCRIPT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def test_para_quando_ocioso(metrics_server):
    url, _ = metrics_server
    p = run(url, idle_s=1, timeout=8)
    out, _ = p.communicate(timeout=10)
    assert "WOULD_STOP" in out and p.returncode == 0


def test_nao_para_com_requisicao_rodando(metrics_server):
    url, st = metrics_server
    st["running"] = 2
    p = run(url, idle_s=1, timeout=3)
    time.sleep(2.5)
    assert p.poll() is None  # continua vivo: ha trabalho
    p.kill()


def test_contador_de_concluidas_mudando_conta_como_atividade(metrics_server):
    url, st = metrics_server
    p = run(url, idle_s=1, timeout=3)
    t0 = time.time()
    while time.time() - t0 < 2.5:
        st["total"] += 1  # pedidos curtos que terminam entre duas leituras
        time.sleep(0.15)
    assert p.poll() is None
    p.kill()


def test_script_valido_para_bash():
    assert subprocess.run([BASH, "-n", str(SCRIPT)]).returncode == 0
    assert subprocess.run([BASH, "-n", str(SCRIPT.parent / "bootstrap.sh")]).returncode == 0
