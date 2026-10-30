"""G1 do estudo de cold start (Spec 14 §5c/§5d): quanto leva para a Vast puxar cada imagem da GPU.

A puxada da imagem acontece no host antes de o contêiner existir, entao qualquer GPU barata serve para medir o registro.
Para cada imagem usa hosts DIFERENTES (um host guarda a imagem em cache e a segunda puxada nao mede nada), cria a instancia,
cronometra ate `running`, destroi na hora e registra tempo, regiao, banda do host e preco. Respeita o saldo: nao roda passo
que deixe o saldo abaixo de RESERVE. Sem segredo no arquivo: a chave vem de VAST_API_KEY.

  VAST_API_KEY=... python probe_images.py --images IMG1 IMG2 --hosts 3 --out results/g1.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "queue"))
from app.gpu.vast import RealVast  # noqa: E402

RESERVE = float(os.environ.get("PROBE_RESERVE_USD", "0.80"))
PROBE_TIMEOUT_S = float(os.environ.get("PROBE_TIMEOUT_S", "840"))
NL = chr(10)


async def cheap_offers(v: RealVast) -> list[dict]:
    q = {"verified": {"in": [True, False]}, "external": {"in": [True, False]}, "rentable": {"eq": True}, "type": "on-demand",
         "limit": 300, "order": [["dph_total", "asc"]], "num_gpus": {"eq": 1}, "cuda_max_good": {"gte": 13.0},
         "inet_down": {"gte": 400}, "reliability2": {"gte": 0.95}, "disk_space": {"gte": 40}}
    r = await v._req("POST", "/v0/bundles/", json=q)  # noqa: SLF001
    offers = list(v._body(r).get("offers", []))  # noqa: SLF001
    ok = []
    for o in offers:
        geo = str(o.get("geolocation") or "")
        if geo.endswith(", CN") or float(o.get("dph_total") or 9) > 0.18:
            continue
        if float(o.get("internet_down_cost_per_tb") if o.get("internet_down_cost_per_tb") is not None else 99) > 6:
            continue
        ok.append(o)
    return ok


async def probe(v: RealVast, offer: dict, image: str) -> dict:
    t0 = time.time()
    rec = {"image": image, "machine_id": offer["machine_id"], "geo": offer.get("geolocation"), "inet_down_mbps": offer.get("inet_down"),
           "dph": offer.get("dph_total"), "cost_per_tb": offer.get("internet_down_cost_per_tb"), "events": []}
    res = await v.create_instance(int(offer["id"]), image, 40, f"coldprobe-{int(t0)}", "#!/bin/bash" + NL + "sleep 3600" + NL, {})
    if not res.ok:
        rec.update({"result": "criar_falhou", "detail": f"{res.error} {res.msg}"[:200]})
        return rec
    iid, last = res.instance_id, None
    try:
        while time.time() - t0 < PROBE_TIMEOUT_S:
            i = await v.get_instance(iid)
            msg = (i.status_msg or "")[:140] if i else "sumiu"
            if msg != last:
                rec["events"].append({"t": round(time.time() - t0, 1), "status": getattr(i, "actual_status", None), "msg": msg})
                last = msg
            if i and i.running:
                rec.update({"result": "running", "pull_s": round(time.time() - t0, 1)})
                return rec
            if i and any(x in msg.lower() for x in ("error", "failed", "denied", "unauthorized")):
                rec.update({"result": "erro", "detail": msg, "pull_s": round(time.time() - t0, 1)})
                return rec
            await asyncio.sleep(5)
        rec.update({"result": "timeout", "pull_s": round(time.time() - t0, 1)})
        return rec
    finally:
        await v.destroy(iid)
        rec["destroyed"] = True


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--hosts", type=int, default=3, help="hosts por imagem")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    v = RealVast(os.environ["VAST_API_KEY"], "https://console.vast.ai/api", "RTX 3090", 1)
    credit0 = await v.credit()
    print(f"saldo inicial US${credit0:.3f}", flush=True)
    offers = await cheap_offers(v)
    print(f"{len(offers)} ofertas candidatas", flush=True)
    used: set = set()
    results = []
    for k in range(a.hosts):
        for img in a.images:  # alterna as imagens: cada rodada usa hosts novos e de preco parecido
            pick = next((o for o in offers if o["machine_id"] not in used), None)
            if pick is None:
                print("sem mais hosts", flush=True)
                break
            credit = await v.credit()
            if credit - 0.12 < RESERVE:
                print(f"parando: saldo {credit:.3f} - 0,12 < reserva {RESERVE}", flush=True)
                break
            used.add(pick["machine_id"])
            print(f"sonda {img} no host {pick['machine_id']} ({pick.get('geolocation')}, {pick.get('inet_down')} Mbps)", flush=True)
            r = await probe(v, pick, img)
            results.append(r)
            print(f"  -> {r.get('result')} {r.get('pull_s')}s {r.get('detail', '')}", flush=True)
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps({"credit_start": credit0, "results": results}, indent=2), encoding="utf-8")
    credit1 = await v.credit()
    print(f"saldo final US${credit1:.3f} (gasto US${credit0 - credit1:.3f})", flush=True)
    left = [i.id for i in await v.list_instances() if (i.raw.get("label") or "").startswith("coldprobe")]
    for iid in left:
        await v.destroy(iid)
    Path(a.out).write_text(json.dumps({"credit_start": credit0, "credit_end": credit1, "results": results}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
