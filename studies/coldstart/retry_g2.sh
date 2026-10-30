#!/bin/bash
# Repete a acordada completa ate uma chegar a READY (no maximo MAX tentativas), com a guarda de saldo do wake_test.py.
MAX=${MAX:-2}
ENVJSON=$(python - <<'PYX'
import json
g=dict(l.split('=',1) for l in open(r'C:\Users\lucas.rangel\Desktop\claude\personal-skills\secrets\qwen-gpu.env',encoding='utf-8').read().splitlines() if '=' in l and not l.startswith('#'))
print(json.dumps({"GPU_IMAGE":"ghcr.io/lucasrangelssouza/qwen-vllm:v0.30.0-cu13-slim1","GPU_RUNNING_TIMEOUT_S":"900","GPU_DRIVE_REMOTE":g["GPU_DRIVE_REMOTE"],"RCLONE_CONFIG_B64":g["RCLONE_CONFIG_B64"],"GPU_EMBED_MODE":"gpu"}))
PYX
)
export VPS_KEY=/tmp/vk PYTHONIOENCODING=utf-8
for i in $(seq 1 $MAX); do
  python wake_test.py --label "g2_tentativa_$i" --env "$ENVJSON" --embed --limits --hermes --max-min 50 --out "results/g2_t$i.json" > "results/g2_t$i.log" 2>&1
  if python -c "import json,sys;d=json.load(open('results/g2_t$i.json'));sys.exit(0 if (d.get('cycle') or [{}])[0].get('ready_at') else 1)"; then echo "pronta na tentativa $i"; exit 0; fi
  sleep 120
done
echo "nenhuma tentativa chegou a READY"
