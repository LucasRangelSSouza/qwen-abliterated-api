#!/usr/bin/env python3
"""Embedding quality + speed bench: our /v1/embeddings vs Vertex text-multilingual-embedding-002.

Quality needs labelled text (JSONL, one object per line: {"id":..., "text":..., "label":...}). Three metrics,
all computed on the same corpus for every backend so they are comparable:
  knn_acc   leave-one-out kNN (k=5, cosine) label accuracy: does the embedding keep same-label texts close?
  p_at_k    precision@10 of "same label" among the 10 nearest neighbours
  overlap   mean top-10 neighbour overlap against the --reference backend (how much it agrees with gecko)
Speed is measured separately (texts/s and tokens/s) over a batch-size x concurrency grid.

  python tests/embed_bench.py quality --data data.jsonl --out reports/embed/q.json \
      --backend ours=https://qwen.example.com --backend file_gecko=data_tmp/pncp.gecko.npy --reference file_gecko
  python tests/embed_bench.py speed --data data.jsonl --backend ours=https://qwen.example.com

Env: VLLM_API_KEY for "ours"; gcloud ADC (gcloud auth print-access-token) for "vertex".
"""
import argparse, base64, json, os, subprocess, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

VERTEX_MODEL = "text-multilingual-embedding-002"
DEFAULT_TASK = "Given a Portuguese text, retrieve semantically similar texts"


def post(url, payload, headers, timeout=300, tries=6):
    """POST JSON, retrying 429/5xx and connection errors with backoff (the GPU box is reached through an edge proxy)."""
    body = json.dumps(payload).encode()
    for i in range(tries):
        req = urllib.request.Request(url, body, {"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or i == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if i == tries - 1:
                raise
        time.sleep(min(2 ** i, 20))


class Ours:
    """OpenAI-compatible /v1/embeddings (vLLM pooling). Qwen3-Embedding wants an instruction on the query side."""

    def __init__(self, base, model, dim, instruct):
        self.base, self.model, self.dim, self.instruct = base.rstrip("/"), model, dim, instruct
        self.key = os.environ["VLLM_API_KEY"]

    def embed(self, texts, role="doc"):
        if role == "query" and self.instruct:
            texts = [f"Instruct: {self.instruct}\nQuery:{t}" for t in texts]
        # base64 floats: ~4x smaller than JSON text, which dominates throughput over a WAN link
        body = {"model": self.model, "input": texts, "encoding_format": "base64"}
        if self.dim:
            body["dimensions"] = self.dim
        d = post(f"{self.base}/v1/embeddings", body, {"Authorization": f"Bearer {self.key}"})
        vecs = [np.frombuffer(base64.b64decode(x["embedding"]), dtype=np.float32) for x in d["data"]]
        return vecs, d.get("usage", {}).get("total_tokens", 0)


class Vertex:
    def __init__(self, project, location="us-central1"):
        self.url = (f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}"
                    f"/publishers/google/models/{VERTEX_MODEL}:predict")
        self.token, self.t0 = None, 0

    def _auth(self):
        if time.time() - self.t0 > 1800:
            self.token = subprocess.check_output("gcloud auth print-access-token", shell=True, text=True).strip()
            self.t0 = time.time()
        return {"Authorization": f"Bearer {self.token}"}

    def embed(self, texts, role="doc"):
        task = "RETRIEVAL_QUERY" if role == "query" else "RETRIEVAL_DOCUMENT"
        out, toks = [], 0
        for i in range(0, len(texts), 50):  # Vertex caps instances per request
            chunk = texts[i:i + 50]
            d = post(self.url, {"instances": [{"content": t[:8000], "task_type": task} for t in chunk]}, self._auth())
            for p in d["predictions"]:
                out.append(p["embeddings"]["values"])
                toks += int(p["embeddings"]["statistics"]["token_count"])
        return out, toks


class Precomputed:
    """Vectors already on disk (.npy, same row order as --data), e.g. the production gecko vectors from embed_dataset.py."""

    def __init__(self, path):
        self.v = np.load(path)


def make_backend(spec, a):
    name, target = spec.split("=", 1)
    if name.startswith("file"):
        return name, Precomputed(target)
    if name.startswith("vertex"):
        return name, Vertex(target)
    return name, Ours(target, a.model, a.dim, a.instruct)


def load(path, limit):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    return rows[:limit] if limit else rows


def embed_all(be, texts, role, batch, workers):
    batches = [texts[i:i + batch] for i in range(0, len(texts), batch)]
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda b: be.embed(b, role), batches))
    vecs = np.array([v for r, _ in res for v in r], dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs, sum(t for _, t in res)


def neighbours(v, k):
    s = v @ v.T
    np.fill_diagonal(s, -1)
    return np.argsort(-s, axis=1)[:, :k]


def quality(a):
    rows = load(a.data, a.limit)
    texts = [r["text"] for r in rows]
    labels = np.array([r["label"] for r in rows])
    nn, res = {}, {}
    for spec in a.backend:
        name, be = make_backend(spec, a)
        # symmetric task (text<->text): the same role for every text, so the instruction is applied consistently
        if isinstance(be, Precomputed):
            v = be.v / np.linalg.norm(be.v, axis=1, keepdims=True)
        else:
            v, _ = embed_all(be, texts, "query" if a.symmetric_query else "doc", a.batch, a.workers)
        nn[name] = neighbours(v, 10)
        top5 = labels[nn[name][:, :5]]
        def vote(r):
            u, c = np.unique(r, return_counts=True)
            return u[np.argmax(c)]
        knn = np.mean([vote(r) == l for r, l in zip(top5, labels)])
        res[name] = {"dim": int(v.shape[1]), "knn_acc": float(knn),
                     "p_at_10": float(np.mean(labels[nn[name]] == labels[:, None]))}
        print(name, res[name], flush=True)
    if a.reference in nn:
        for name in nn:
            if name != a.reference:
                ov = np.mean([len(set(x) & set(y)) / 10 for x, y in zip(nn[name], nn[a.reference])])
                res[name]["overlap_vs_" + a.reference] = float(ov)
                print(name, "overlap vs", a.reference, round(float(ov), 4))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump({"n": len(rows), "results": res}, open(a.out, "w"), indent=2)


def speed(a):
    texts = [r["text"] for r in load(a.data, a.limit)]
    out = {}
    for spec in a.backend:
        name, be = make_backend(spec, a)
        be.embed(texts[:4])  # warm-up
        for batch in (1, 16, 64):
            for workers in (1, 4, 16):
                sub = texts[:max(64, batch * workers * 6)]  # enough requests to saturate, without hours at batch=1
                t0 = time.time()
                _, toks = embed_all(be, sub, "doc", batch, workers)
                dt = time.time() - t0
                out[f"{name} batch={batch} conc={workers}"] = {"texts_s": round(len(sub) / dt, 1),
                                                                 "tokens_s": round(toks / dt), "s": round(dt, 1)}
                print(name, batch, workers, out[f"{name} batch={batch} conc={workers}"], flush=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w"), indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["quality", "speed"])
    ap.add_argument("--data", required=True)
    ap.add_argument("--backend", action="append", required=True, help="ours=https://host | vertex=<gcp-project> | file_<x>=<vectors.npy>")
    ap.add_argument("--reference", default="file_gecko")
    ap.add_argument("--model", default="qwen-embedding")
    ap.add_argument("--dim", type=int, default=768, help="Matryoshka dimensions for ours; 768 matches gecko-002")
    ap.add_argument("--instruct", default=DEFAULT_TASK)
    ap.add_argument("--symmetric-query", action="store_true", help="apply the query instruction to every text")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="reports/embed/result.json")
    a = ap.parse_args()
    (quality if a.mode == "quality" else speed)(a)


if __name__ == "__main__":
    sys.exit(main())
