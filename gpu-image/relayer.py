"""Reescreve as camadas grandes da imagem da GPU direto no registro, sem Docker e sem espaco em disco para a imagem inteira
(Spec 14 §5c, variante V2).

Medido em 30/10/2026: a imagem base (vastai/vllm) tem 23,7 GB descompactados e uma camada de 5,1 GB que o Docker descompacta
em um so nucleo; num host de 918 Mbps ela nao terminou de ser puxada em 14 min. 6,2 GB sao binarios de kernel para GPUs
Blackwell de datacenter (sm100/sm103/sm107/sm110) que a RTX 3090 (sm_86) nunca carrega.

O que faz: copia as camadas pequenas sem mexer; as grandes (> LIMIT_MB compactadas) sao lidas em fluxo, perdem os arquivos de
DROP e sao repartidas por pacote em varias camadas menores (o Docker baixa e descompacta varias em paralelo); troca a
lista de camadas e o historico na configuracao; publica no destino. As 49 camadas que nao mudam ficam byte a byte iguais.
Prova interna: todo membro de tar (nome, tipo, tamanho, modo, dono, destino do link, sha256 do conteudo) da camada original
tem que aparecer exatamente uma vez nas camadas novas, menos o que foi removido.

Uso: python relayer.py <imagem-base> <destino>   (GHCR_USER e GHCR_TOKEN no ambiente; base anonima no Docker Hub)
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import time
from collections import defaultdict

import requests

DP = "usr/local/lib/python3.12/dist-packages"
DROP = [f"{DP}/flashinfer_cubin/cubins"]          # filhos removidos; o diretorio em si fica (a importacao confere que ele existe)
LIMIT_MB = int(os.environ.get("LIMIT_MB", "700"))     # camadas compactadas maiores que isto sao reescritas
BUCKET_MB = int(os.environ.get("BUCKET_MB", "1800"))  # tamanho descompactado maximo de cada camada nova
CHUNK = 1 << 20
UP_CHUNK = 4_000_000  # o GHCR recusa PATCH acima de 4 MiB
M_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
M_OCI_INDEX = "application/vnd.oci.image.index.v1+json"
M_V2 = "application/vnd.docker.distribution.manifest.v2+json"
M_OCI = "application/vnd.oci.image.manifest.v1+json"
LAYER_MT = "application/vnd.docker.image.rootfs.diff.tar.gzip"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def split_ref(ref: str):
    host = "registry-1.docker.io"
    name, _, tag = ref.rpartition(":")
    if ref.split("/")[0] in ("ghcr.io", "docker.io", "registry-1.docker.io"):
        host, _, name = ref.partition("/")
        name, _, tag = name.rpartition(":")
        host = "registry-1.docker.io" if host == "docker.io" else host
    if "/" not in name and host == "registry-1.docker.io":
        name = "library/" + name
    return host, name, tag


class Reg:
    def __init__(self, host: str, name: str, push: bool = False):
        self.host, self.name, self.push = host, name, push
        self.s = requests.Session()
        self.token = None
        self.refresh()

    def refresh(self):
        scope = f"repository:{self.name}:" + ("push,pull" if self.push else "pull")
        if self.host == "ghcr.io":
            r = requests.get("https://ghcr.io/token", params={"service": "ghcr.io", "scope": scope}, auth=(os.environ["GHCR_USER"], os.environ["GHCR_TOKEN"]) if self.push else None, timeout=60)
        else:
            r = requests.get("https://auth.docker.io/token", params={"service": "registry.docker.io", "scope": scope}, timeout=60)
        r.raise_for_status()
        self.token = r.json()["token"]
        self.s.headers["Authorization"] = f"Bearer {self.token}"

    def url(self, path: str) -> str:
        return f"https://{self.host}/v2/{self.name}/{path}"

    def req(self, method, path, **kw):
        for i in range(5):
            try:
                r = self.s.request(method, path if path.startswith("http") else self.url(path), timeout=kw.pop("timeout", 300), **kw)
                if r.status_code == 401 and i < 4:
                    self.refresh()
                    continue
                if r.status_code in (429, 500, 502, 503, 504) and i < 4:
                    time.sleep(2 * (i + 1))
                    continue
                return r
            except requests.RequestException:
                if i == 4:
                    raise
                time.sleep(2 * (i + 1))
        raise RuntimeError("req")

    def manifest(self, ref: str) -> tuple[dict, bytes]:
        accept = ",".join([M_LIST, M_OCI_INDEX, M_V2, M_OCI])
        r = self.req("GET", f"manifests/{ref}", headers={"Accept": accept})
        r.raise_for_status()
        return r.json(), r.content

    def blob_stream(self, digest: str):
        r = self.req("GET", f"blobs/{digest}", stream=True, timeout=600)
        r.raise_for_status()
        return r

    def blob_exists(self, digest: str) -> bool:
        return self.req("HEAD", f"blobs/{digest}").status_code == 200

    def put_blob(self, digest: str, size: int, data, retries: int = 4):
        """Envio em pedacos (PATCH) e fechamento com PUT; `data` precisa ser um arquivo (reabrivel) ou bytes, para refazer em caso de queda."""
        if self.blob_exists(digest):
            return
        for attempt in range(retries):
            try:
                fh = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else open(data, "rb")
                try:
                    return self._upload(digest, size, fh)
                finally:
                    fh.close()
            except (requests.RequestException, RuntimeError) as e:
                log(f"  upload de {digest[:19]} falhou ({e!r}); tentativa {attempt + 1}/{retries}")
                self.refresh()
                time.sleep(3 * (attempt + 1))
        raise SystemExit(f"upload de {digest} falhou depois de {retries} tentativas")

    def _upload(self, digest: str, size: int, fh):
        r = self.req("POST", "blobs/uploads/")
        r.raise_for_status()
        loc = r.headers["Location"]
        off = 0
        while off < size:
            chunk = fh.read(min(UP_CHUNK, size - off))
            if not chunk:
                raise RuntimeError("fim inesperado do arquivo")
            hdr = {"Content-Type": "application/octet-stream", "Content-Length": str(len(chunk)), "Content-Range": f"{off}-{off + len(chunk) - 1}"}
            u = loc if loc.startswith("http") else f"https://{self.host}{loc}"
            r2 = self.s.patch(u, data=chunk, headers=hdr, timeout=900)
            if r2.status_code not in (202, 201, 204):
                raise RuntimeError(f"PATCH {r2.status_code} {r2.text[:150]}")
            loc = r2.headers.get("Location", loc)
            off += len(chunk)
        u = loc if loc.startswith("http") else f"https://{self.host}{loc}"
        sep = "&" if "?" in u else "?"
        r3 = self.s.put(f"{u}{sep}digest={digest}", data=b"", headers={"Content-Length": "0"}, timeout=900)
        if r3.status_code not in (201, 202):
            raise RuntimeError(f"PUT final {r3.status_code} {r3.text[:150]}")


class Hasher(io.RawIOBase):
    """Escreve num destino e calcula o sha256 do que passou (para o diff_id das camadas novas)."""

    def __init__(self, sink):
        self.sink, self.h, self.n = sink, hashlib.sha256(), 0

    def writable(self):
        return True

    def write(self, b):
        self.h.update(b)
        self.n += len(b)
        return self.sink.write(b)


def norm(name: str) -> str:
    n = name.lstrip("./")
    return n.rstrip("/")


def dropped(path: str) -> bool:
    return any(path.startswith(p + "/") for p in DROP)


def key_of(path: str) -> str:
    parts = path.split("/")
    if path.startswith(DP + "/") and len(parts) > 6:
        return "/".join(parts[:7])
    if path.startswith("usr/local/cuda-13.0/") and len(parts) > 4:
        return "/".join(parts[:5])
    return "/".join(parts[:4])


def fingerprint(ti: tarfile.TarInfo, content_sha: str | None) -> tuple:
    return (norm(ti.name), ti.type, ti.size if ti.isreg() else 0, ti.mode, ti.uid, ti.gid, ti.linkname if (ti.issym() or ti.islnk()) else "", content_sha)


def iter_members(resp):
    """Percorre o tar.gz em fluxo e entrega (TarInfo, sha256 do conteudo ou None, bytes do conteudo em pedacos)."""
    gz = gzip.GzipFile(fileobj=resp.raw)
    with tarfile.open(fileobj=gz, mode="r|") as tf:
        for ti in tf:
            yield ti, tf


def plan_layer(reg: Reg, digest: str) -> tuple[dict, int]:
    """Passada 1: tamanho por chave de pacote, para repartir em camadas de ate BUCKET_MB."""
    sizes: dict[str, int] = defaultdict(int)
    keyof: dict[str, str] = {}
    total = 0
    resp = reg.blob_stream(digest)
    for ti, _tf in iter_members(resp):
        p = norm(ti.name)
        if dropped(p) or ti.isdir():
            continue
        k = keyof.get(norm(ti.linkname), key_of(p)) if ti.islnk() else key_of(p)
        keyof[p] = k
        sizes[k] += ti.size if ti.isreg() else 0
        total += ti.size if ti.isreg() else 0
    resp.close()
    cap = BUCKET_MB * (1 << 20)
    buckets: list[int] = []
    assign: dict[str, int] = {}
    for k, sz in sorted(sizes.items(), key=lambda kv: -kv[1]):
        for i, used in enumerate(buckets):
            if used + sz <= cap:
                buckets[i] += sz
                assign[k] = i
                break
        else:
            buckets.append(sz)
            assign[k] = len(buckets) - 1
    return {"assign": assign, "keyof": keyof, "n": max(1, len(buckets))}, total


def rewrite_layer(reg: Reg, digest: str, plan: dict) -> tuple[list[dict], dict]:
    """Passada 2: grava as camadas novas em arquivos temporarios e devolve [{digest, size, diff_id, path}] e a prova."""
    n, assign, keyof = plan["n"], plan["assign"], plan["keyof"]
    outs, tars, gzs, hs, files = [], [], [], [], []
    for _ in range(n):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".tgz")
        g = gzip.GzipFile(fileobj=f, mode="wb", compresslevel=6, mtime=0)
        h = Hasher(g)
        t = tarfile.open(fileobj=h, mode="w|", format=tarfile.PAX_FORMAT)
        files.append(f), gzs.append(g), hs.append(h), tars.append(t)
    seen_orig, seen_new = defaultdict(int), defaultdict(int)
    resp = reg.blob_stream(digest)
    n_drop = 0
    for ti, tf in iter_members(resp):
        p = norm(ti.name)
        data_sha = None
        if dropped(p):
            n_drop += 1
            continue
        if ti.isdir():
            idx = 0  # todos os diretorios vao para a 1a camada, aplicada antes das outras
        else:
            idx = assign.get(keyof.get(p) or key_of(p), 0)
        if ti.isreg():
            src = tf.extractfile(ti)
            hh = hashlib.sha256()

            class Tee:
                def read(self, k=-1):
                    b = src.read(k)
                    hh.update(b)
                    return b
            tars[idx].addfile(ti, Tee())
            data_sha = hh.hexdigest()
        else:
            tars[idx].addfile(ti)
        seen_orig[fingerprint(ti, data_sha)] += 1
    resp.close()
    for t in tars:
        t.close()
    for g in gzs:
        g.close()
    for f in files:
        f.flush()
    res = []
    for i, (f, h) in enumerate(zip(files, hs)):
        size = os.path.getsize(f.name)
        sha = hashlib.sha256()
        with open(f.name, "rb") as fh:
            while (b := fh.read(CHUNK)):
                sha.update(b)
        res.append({"digest": "sha256:" + sha.hexdigest(), "size": size, "diff_id": "sha256:" + h.h.hexdigest(), "path": f.name})
        # releitura da camada nova: o conjunto de membros tem que bater com o escrito
        with tarfile.open(f.name, mode="r:gz") as tf2:
            for ti in tf2:
                sh = None
                if ti.isreg():
                    hh2 = hashlib.sha256()
                    ex = tf2.extractfile(ti)
                    while (b := ex.read(CHUNK)):
                        hh2.update(b)
                    sh = hh2.hexdigest()
                seen_new[fingerprint(ti, sh)] += 1
    return res, {"dropped": n_drop, "orig_members": sum(seen_orig.values()), "new_members": sum(seen_new.values()), "equal": seen_orig == seen_new}


def main():
    base, dest = sys.argv[1], sys.argv[2]
    bh, bn, bt = split_ref(base)
    dh, dn, dt = split_ref(dest)
    src, dst = Reg(bh, bn), Reg(dh, dn, push=True)
    top, _ = src.manifest(bt)
    if "manifests" in top:
        pick = next(m for m in top["manifests"] if m["platform"]["architecture"] == "amd64" and m["platform"]["os"] == "linux")
        man, _ = src.manifest(pick["digest"])
    else:
        man = top
    cfg_r = src.blob_stream(man["config"]["digest"])
    cfg = json.loads(cfg_r.content)
    layers = man["layers"]
    log(f"base: {len(layers)} camadas, {sum(l['size'] for l in layers) / 1e9:.2f} GB compactados")
    new_layers, new_diff, new_hist = [], [], []
    hist = [h for h in cfg["history"] if not h.get("empty_layer")]
    hi, di = 0, 0
    # historico: entradas com empty_layer ficam onde estao; as com camada seguem o indice
    out_hist = []
    li = 0
    for h in cfg["history"]:
        if h.get("empty_layer"):
            out_hist.append(h)
            continue
        L = layers[li]
        old_diff = cfg["rootfs"]["diff_ids"][li]
        li += 1
        if L["size"] <= LIMIT_MB * (1 << 20):
            if not dst.blob_exists(L["digest"]):
                tmp = tempfile.NamedTemporaryFile(delete=False)
                r = src.blob_stream(L["digest"])
                for b in r.iter_content(CHUNK):
                    tmp.write(b)
                r.close()
                tmp.close()
                dst.put_blob(L["digest"], L["size"], tmp.name)
                os.unlink(tmp.name)
            new_layers.append({"mediaType": LAYER_MT, "size": L["size"], "digest": L["digest"]})
            new_diff.append(old_diff)
            out_hist.append(h)
            continue
        log(f"reescrevendo camada {L['digest'][:19]} ({L['size'] / 1e9:.2f} GB compactados)")
        plan, total = plan_layer(src, L["digest"])
        log(f"  {total / 1e9:.2f} GB descompactados em {plan['n']} camadas novas")
        res, proof = rewrite_layer(src, L["digest"], plan)
        log(f"  prova: removidos={proof['dropped']} membros originais={proof['orig_members']} novos={proof['new_members']} iguais={proof['equal']}")
        if not proof["equal"] or proof["orig_members"] != proof["new_members"]:
            raise SystemExit("PROVA FALHOU: conjunto de arquivos das camadas novas difere do original")
        for i, o in enumerate(res):
            dst.put_blob(o["digest"], o["size"], o["path"])
            os.unlink(o["path"])
            new_layers.append({"mediaType": LAYER_MT, "size": o["size"], "digest": o["digest"]})
            new_diff.append(o["diff_id"])
            hh = dict(h)
            hh["created_by"] = f"(slim {i + 1}/{len(res)}) " + (h.get("created_by") or "")[:200]
            out_hist.append(hh)
        log("  novas camadas: " + ", ".join(f"{o['size'] / 1e9:.2f}GB" for o in res))
    cfg["rootfs"]["diff_ids"] = new_diff
    cfg["history"] = out_hist
    cfg.setdefault("config", {}).setdefault("Labels", {})["org.opencontainers.image.source"] = "https://github.com/LucasRangelSSouza/qwen-abliterated-api"
    cfg["config"]["Labels"]["qwen.slim"] = "flashinfer_cubin sm100+ removido; camadas grandes repartidas"
    cb = json.dumps(cfg, separators=(",", ":")).encode()
    cd = "sha256:" + hashlib.sha256(cb).hexdigest()
    dst.put_blob(cd, len(cb), cb)
    manifest = {"schemaVersion": 2, "mediaType": M_V2, "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "size": len(cb), "digest": cd}, "layers": new_layers}
    mb = json.dumps(manifest, separators=(",", ":")).encode()
    r = dst.req("PUT", f"manifests/{dt}", data=mb, headers={"Content-Type": M_V2})
    if r.status_code not in (200, 201):
        raise SystemExit(f"manifesto recusado: {r.status_code} {r.text[:300]}")
    tot = sum(l["size"] for l in new_layers)
    log(f"publicado {dest}: {len(new_layers)} camadas, {tot / 1e9:.2f} GB compactados (base: {sum(l['size'] for l in layers) / 1e9:.2f} GB); maior camada {max(l['size'] for l in new_layers) / 1e9:.2f} GB")
    print(json.dumps({"layers": len(new_layers), "compressed_gb": round(tot / 1e9, 2), "largest_gb": round(max(l['size'] for l in new_layers) / 1e9, 2)}))


if __name__ == "__main__":
    main()
