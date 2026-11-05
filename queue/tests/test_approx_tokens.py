"""Estimativa de tokens do chat: imagem em base64 tem custo fixo, nao conta caracteres."""
from app.main import IMAGE_TOKENS, approx_chat_tokens


def test_imagem_base64_nao_conta_como_texto():
    img = "data:image/png;base64," + "A" * 1_500_000
    msgs = [{"role": "user", "content": [{"type": "text", "text": "descreva"}, {"type": "image_url", "image_url": {"url": img}}]}]
    assert approx_chat_tokens(msgs) < IMAGE_TOKENS + 100


def test_texto_continua_por_caracteres():
    assert approx_chat_tokens([{"role": "user", "content": "a" * 3000}]) >= 1000


def test_varias_imagens_somam_custo_fixo():
    part = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "B" * 500_000}}
    msgs = [{"role": "user", "content": [part, part, part]}]
    assert 3 * IMAGE_TOKENS <= approx_chat_tokens(msgs) < 3 * IMAGE_TOKENS + 100


def test_conteudo_estranho_nao_quebra():
    assert approx_chat_tokens(None) == 0
    assert approx_chat_tokens([None, {"role": "tool", "content": None}]) >= 0
