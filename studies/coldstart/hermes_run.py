"""Roda uma run curta do Hermes pelo kernel-harness de producao (Spec 14): confere o vigia da fila e o timeout. Local: usa o runner de QA do repo do harness."""
import base64, os, subprocess, sys
SP = os.path.dirname(os.path.abspath(__file__))
runner = open(os.environ.get("K4K5_RUNNER", "C:/Users/lucas.rangel/Desktop/new_ragente/repos/kernel-harness/qa/k4_k5_runner.py"), "rb").read()
drv = open(os.path.join(SP, "hermes_drv.py"), "rb").read()
msg = sys.argv[1] if len(sys.argv) > 1 else "Responda somente: HERMES QWEN OK"
b = lambda x: base64.b64encode(x).decode()
script = f'''set -u
echo {b(runner)} | base64 -d | sudo -n docker exec -i kernel-harness sh -c 'cat > /tmp/k4k5.py'
echo {b(drv)} | base64 -d | sudo -n docker exec -i kernel-harness sh -c 'cat > /tmp/hdrv.py'
sudo -n docker exec -e HARNESS_QA_MODEL_ALIAS=qwen-abliterated -e HARNESS_QA_BASE_URL=http://127.0.0.1:8080 -e HARNESS_QA_MESSAGE={subprocess.list2cmdline([msg])} kernel-harness sh -c 'export HARNESS_QA_SERVICE_TOKEN="${{HARNESS_SERVICE_TOKENS%%,*}}"; export HARNESS_QA_RUN_TOKEN_SECRET="$HARNESS_RUN_TOKEN_SECRET"; cd /tmp && python /tmp/hdrv.py' 2>&1
echo "[fim do script remoto rc=$?]"
'''
r = subprocess.run(["ssh","-i",os.environ.get("VPS_KEY", "/tmp/vk"),"-o","IdentitiesOnly=yes","-o","UserKnownHostsFile=/dev/null","-o","StrictHostKeyChecking=no","-o","BatchMode=yes","-o","ServerAliveInterval=20","-o","ServerAliveCountMax=80","deploy@66.94.101.153","bash -s"], input=script.encode(), capture_output=True, timeout=1700)
print(r.stdout.decode(errors="replace")[-4500:]); print(r.stderr.decode(errors="replace")[-400:])
