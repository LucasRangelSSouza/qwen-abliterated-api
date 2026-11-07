"""Parametros explicitos de imagem e video: limites, defaults, tipo inequivoco do resultado e imagem inicial."""
import base64

import httpx
import pytest

from app import mediaparams as mp
from app.media import MediaError, recipe_flux_schnell, recipe_ltx_video
from tests.test_media import FakeComfy, make_media  # noqa: F401  (make_media e fixture)

PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 40).decode()


def test_imagem_defaults_limites_e_ajustes():
    e, adj = mp.normalize_image({"prompt": "logo"})
    assert (e["width"], e["height"], e["steps"], e["cfg"], e["n"], e["sampler"], e["scheduler"]) == (1024, 1024, 4, 1.0, 1, "euler", "simple") and adj == []
    e, adj = mp.normalize_image({"prompt": "x", "width": 1000, "height": 700, "steps": 99, "cfg": 50, "n": 9, "seed": 7, "sampler": "dpmpp_2m", "scheduler": "karras", "style": "anime"})
    assert (e["width"], e["height"]) == (1024, 704) and e["steps"] == 12 and e["cfg"] == 6.0 and e["n"] == 4 and e["seed"] == 7
    assert {"steps", "cfg", "n", "size"} <= set(adj)
    for ruim in ({"sampler": "inventado"}, {"style": "xyz"}, {"steps": "muitos"}, {"seed": "abc"}, {"size": "lixo"}, {"prompt": ""}):
        with pytest.raises(mp.ParamError):
            mp.normalize_image({"prompt": "x", **ruim})


def test_receita_de_imagem_leva_os_parametros_ao_grafo():
    wf = recipe_flux_schnell({"prompt": "gato", "negative_prompt": "borrado", "width": 512, "height": 768, "steps": 6, "cfg": 2.0, "seed": 5, "sampler": "heun", "scheduler": "beta", "n": 3, "style": "foto"}, "ck.safetensors")
    k = wf["5"]["inputs"]
    assert (k["steps"], k["cfg"], k["seed"], k["sampler_name"], k["scheduler"]) == (6, 2.0, 5, "heun", "beta")
    assert wf["4"]["inputs"] == {"width": 512, "height": 768, "batch_size": 3}
    assert wf["3"]["inputs"]["text"] == "borrado" and wf["2"]["inputs"]["text"].startswith("gato, photorealistic")


def test_video_defaults_limites_e_quadros():
    e, adj = mp.normalize_video({"prompt": "carro"})
    assert (e["width"], e["height"], e["seconds"], e["fps"], e["frames"], e["steps"], e["cfg"]) == (640, 384, 3.88, 25, 97, 30, 3.0) and not e["image_to_video"]
    e, adj = mp.normalize_video({"prompt": "x", "seconds": 10, "fps": 30, "width": 2000, "height": 2000, "steps": 5, "seed": 3})
    assert e["frames"] <= mp.VIDEO_MAX_FRAMES and e["width"] * e["height"] <= mp.VIDEO_MAX_PIXELS and e["steps"] == 10 and {"steps", "size"} <= set(adj)
    assert "seconds" not in adj and e["segments"] == 3 and 9.5 <= e["total_seconds"] <= 10.5  # 10 s vira 3 clipes encadeados, nao e cortado
    e15, _ = mp.normalize_video({"prompt": "x", "seconds": 15})
    assert e15["segments"] == 4 and e15["frames"] <= mp.VIDEO_MAX_FRAMES and 14.5 <= e15["total_seconds"] <= 15.7
    e99, adj99 = mp.normalize_video({"prompt": "x", "seconds": 99})
    assert "seconds" in adj99 and e99["segments"] == 15  # teto de 60 s no total
    e2, _ = mp.normalize_video({"prompt": "x", "seconds": 2, "fps": 24})
    assert e2["frames"] == 49 and e2["fps"] == 24
    with pytest.raises(mp.ParamError):
        mp.normalize_video({"prompt": "x", "format": "diagonal"})
    with pytest.raises(mp.ParamError):
        mp.normalize_video({"prompt": "x", "image": base64.b64encode(b"nao e imagem").decode()})


def test_receita_de_video_texto_e_imagem_para_video():
    wf = recipe_ltx_video({"prompt": "carro", "seconds": 2, "fps": 24, "steps": 12, "cfg": 4, "seed": 9, "sampler": "euler_ancestral", "negative_prompt": "ruim"}, "")
    assert wf["6"]["class_type"] == "EmptyLTXVLatentVideo" and wf["6"]["inputs"]["length"] == 49 and wf["5"]["inputs"]["frame_rate"] == 24.0
    assert wf["7"]["inputs"]["steps"] == 12 and wf["9"]["inputs"]["noise_seed"] == 9 and wf["9"]["inputs"]["cfg"] == 4.0 and wf["8"]["inputs"]["sampler_name"] == "euler_ancestral"
    assert wf["4"]["inputs"]["text"] == "ruim"
    wi = recipe_ltx_video({"prompt": "carro", "image": PNG_B64, "strength": 0.6}, "", "inicio.png")
    assert wi["22"]["class_type"] == "LTXVImgToVideo" and wi["22"]["inputs"]["strength"] == 0.6 and wi["20"]["inputs"]["image"] == "inicio.png"
    assert wi["5"]["inputs"]["positive"] == ["22", 0] and wi["9"]["inputs"]["latent_image"] == ["22", 2] and wi["7"]["inputs"]["latent"] == ["22", 2] and "6" not in wi


class ComfyVideo(FakeComfy):
    """Historico do video com uma previa PNG ao lado do mp4: o resultado tem que ser o mp4, nunca a previa."""
    def handler(self, req):
        path = req.url.path
        if path.startswith("/history/") and self.polls >= self.delay:
            pid = path.rsplit("/", 1)[1]
            self.polls += 1
            return httpx.Response(200, json={pid: {"status": {"status_str": "success", "completed": True},
                                                    "outputs": {"11": {"images": [{"filename": "prev_00001_.png", "subfolder": "", "type": "temp"}]},
                                                                "12": {"images": [{"filename": "midia_00001_.mp4", "subfolder": "video", "type": "output"}], "animated": [True]}}}})
        if path == "/view":
            return httpx.Response(200, content=b"\x00\x00\x00 ftypisom" if req.url.params["filename"].endswith(".mp4") else b"\x89PNGprev", headers={"content-type": "image/png"})  # content-type enganoso de proposito
        if path == "/upload/image":
            return httpx.Response(200, json={"name": "inicio.png"})
        return super().handler(req)


@pytest.mark.asyncio
async def test_video_devolve_mp4_com_tipo_inequivoco_e_nunca_a_previa(make_media):
    m, comfy = await make_media(ComfyVideo(delay=0), media_video_enabled=True)
    job = await m.submit("a", "", {"prompt": "carro", "seconds": 2, "seed": 4}, "batch", "video")
    await m.wait(job["id"], 5)
    p = await m.progress(job["id"])
    assert p["status"] == "done" and p["type"] == "video" and p["mime"] == "video/mp4" and p["ext"] == "mp4" and p["results"][0]["type"] == "video"
    assert p["params"]["seed"] == 4 and p["params"]["frames"] == 49 and "image" not in p["params"]
    assert (await m.image(job["id"])).startswith(b"\x00\x00\x00 ftyp")


@pytest.mark.asyncio
async def test_imagem_inicial_sobe_para_o_comfy_e_n_imagens_viram_resultados(make_media):
    m, comfy = await make_media(ComfyVideo(delay=0), media_video_enabled=True)
    job = await m.submit("a", "", {"prompt": "carro", "image": PNG_B64, "strength": 0.5}, "batch", "video")
    await m.wait(job["id"], 5)
    assert (await m.progress(job["id"]))["params"]["image_to_video"] is True
    assert any(w["class_type"] == "LTXVImgToVideo" for w in comfy.prompts[-1]["prompt"].values())
    with pytest.raises(MediaError):
        await m.submit("a", "", {"prompt": "x", "sampler": "inventado"})


def test_video_wan_clipes_e_cadeia():
    e, adj = mp.normalize_video_wan({"prompt": "x"})
    assert (e["width"], e["height"], e["fps"], e["segments"], e["frames"]) == (1280, 704, 24, 1, 121) and (e["frames"] - 1) % 4 == 0
    e15, _ = mp.normalize_video_wan({"prompt": "x", "seconds": 15})
    assert e15["segments"] == 3 and e15["frames"] == 121 and 14.5 <= e15["total_seconds"] <= 15.5
    # um clipe ja normalizado (seconds=5.04) nao pode ser dividido de novo ao ser reprocessado pela receita
    again, _ = mp.normalize_video_wan({"prompt": "x", "seconds": e15["seconds"]})
    assert again["segments"] == 1 and again["frames"] == 121
    e99, adj99 = mp.normalize_video_wan({"prompt": "x", "seconds": 99})
    assert "seconds" in adj99 and e99["segments"] >= 12
    from app.media import recipe_wan22
    wf = recipe_wan22({"prompt": "carro", "seconds": 5}, "")
    assert wf["7"]["class_type"] == "Wan22ImageToVideoLatent" and "start_image" not in wf["7"]["inputs"]
    wf2 = recipe_wan22({"prompt": "carro", "seconds": 5}, "", "inicio.png")
    assert wf2["6"]["inputs"]["image"] == "inicio.png" and wf2["7"]["inputs"]["start_image"] == ["6", 0]
