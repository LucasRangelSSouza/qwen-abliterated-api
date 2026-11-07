import base64
import json

import pytest

from app import mediaparams as mp
from app.media import SWAP_ENCODER, SWAP_LORA, SWAP_TEMPLATE, patch_swap_workflow, swap_result_path

VIDEO = base64.b64encode(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64).decode()
FOTO = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()


def test_normalize_swap_padroes_e_validacao():
    e, adj = mp.normalize_swap({"video": VIDEO, "image": FOTO})
    assert (e["resolution"], e["steps"], e["clip_seconds"], e["no_lip_sync"], e["segments"]) == ("480p", 4, 6.0, True, 1) and (e["width"], e["height"]) == (864, 480)
    assert "video" not in mp.public(e) and "image" not in e  # o conteudo nunca vai para o status do job
    e2, _ = mp.normalize_swap({"video": VIDEO, "image": FOTO, "resolution": "736p", "no_lip_sync": False, "steps": 6})
    assert (e2["resolution"], e2["no_lip_sync"], e2["steps"]) == ("736p", False, 6)
    with pytest.raises(mp.ParamError):
        mp.normalize_swap({"video": VIDEO, "image": FOTO, "resolution": "4k"})
    with pytest.raises(mp.ParamError):
        mp.normalize_swap({"image": FOTO})  # sem video
    with pytest.raises(mp.ParamError):
        mp.normalize_swap({"video": base64.b64encode(b"nao e video nenhum").decode(), "image": FOTO})
    with pytest.raises(mp.ParamError):
        mp.normalize_swap({"video": VIDEO, "image": base64.b64encode(b"GIF89a....").decode()})


def test_patch_do_fluxo_do_openjutsu():
    with open(SWAP_TEMPLATE, encoding="utf-8") as f:
        wf = json.load(f)
    e, _ = mp.normalize_swap({"video": VIDEO, "image": FOTO, "resolution": "576p", "seed": 5, "steps": 4})
    wf = patch_swap_workflow(wf, "swap-ab.mp4", "ref-ab.png", e)
    assert wf["2"]["inputs"]["image"] == "ref-ab.png" and wf["5"]["inputs"]["video"] == "swap-ab.mp4"
    assert wf["27"]["inputs"]["video_path"].endswith("/input/swap-ab.mp4")
    assert wf["16"]["inputs"]["lora_name"] == SWAP_LORA and wf["19"]["inputs"]["clip_name"] == SWAP_ENCODER
    assert wf["11"]["inputs"]["resolution"] == "576p" and wf["11"]["inputs"]["seed"] == 5 and wf["68"]["inputs"]["value"] is True
    assert wf["11"]["inputs"]["no_lip_sync"] == ["68", 0]  # a ligacao com o no booleano segue de pe


def test_caminho_do_resultado():
    saida = {"46": {"text": ["subject_definitions: ..."]},
             "67": {"text": ["assembled 1 generated clips with ffmpeg; hard cuts -> /workspace/ComfyUI/output/h3_chains/swap_ab/exports/videos/original/generation/swap_2026-11-07.mp4; generated audio -> /x/y.wav"]}}
    assert swap_result_path(saida) == ("h3_chains/swap_ab/exports/videos/original/generation", "swap_2026-11-07.mp4")
    assert swap_result_path({"1": {"text": ["nada"]}}) is None
