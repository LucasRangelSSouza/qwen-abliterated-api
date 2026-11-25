"""Abas "Postgres" e "MinIO" do painel (spec 57 §6.10): leem as amostras do coletor de 5 min (plataforma.saude_amostras,
banco `produtos`) pelo usuário somente leitura `painel_saude`. Se o coletor parar, a aba avisa "sem dados há X min"."""
from __future__ import annotations

import json
import os
import time

from fastapi import Request

from app.admin import api, require

LIMITES = {"disco_pct": (85, 92), "pg_conexoes_pct": (70, 85), "pg_cache_pct": (97, 93), "pg_tx_longa_s": (600, 1800),
           "minio_5xx_pct": (1, 5), "backup_horas": (26, 50)}


def _amostras(horas: float) -> list[tuple[float, dict]]:
    import psycopg
    url = os.environ.get("PAINEL_SAUDE_URL", "")
    if not url:
        raise RuntimeError("PAINEL_SAUDE_URL não configurada")
    with psycopg.connect(url, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute("select extract(epoch from ts), dados from plataforma.saude_amostras where ts > now() - make_interval(secs => %s) order by ts",
                    (horas * 3600,))
        return [(float(t), d if isinstance(d, dict) else json.loads(d)) for t, d in cur.fetchall()]


@api.get("/painel/saude")
def painel_saude(request: Request, horas: float = 24):
    require(request)
    try:
        am = _amostras(max(1.0, min(horas, 24 * 7)))
    except Exception as e:  # noqa: BLE001
        return {"erro": f"não consegui ler as amostras: {type(e).__name__}"}
    if not am:
        return {"erro": "sem amostras ainda"}
    t, ult = am[-1]
    idade = time.time() - t
    serie = [{"t": int(ts), "disco_pct": d.get("disco", {}).get("pct"), "pg_conexoes": d.get("postgres", {}).get("conexoes"),
              "pg_tps": d.get("postgres", {}).get("tps"), "pg_cache": d.get("postgres", {}).get("cache_pct"),
              "minio_req_s": d.get("minio", {}).get("req_s"), "minio_5xx_pct": d.get("minio", {}).get("erros_5xx_pct")} for ts, d in am]
    # projeção de dias até o disco encher pelo crescimento da janela
    proj = None
    if len(am) > 2:
        (t0, d0), (t1, d1) = am[0], am[-1]
        dg = (d1["disco"]["usado_gb"] - d0["disco"]["usado_gb"]) / max(1e-6, (t1 - t0) / 86400)
        proj = round(d1["disco"]["livre_gb"] / dg, 1) if dg > 0.01 else None
    return {"ultima": ult, "idade_s": int(idade), "sem_dados": idade > 15 * 60, "serie": serie, "limites": LIMITES,
            "dias_ate_encher": proj, "amostras": len(am)}
