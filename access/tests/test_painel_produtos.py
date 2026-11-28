"""Painel único (spec 57 §6.2): registro dos produtos e marcação de contas de QA."""
from app import painel_produtos as pp


def test_registro_tem_os_12_produtos_e_links():
    reg = {p["id"]: p for p in pp.registro()}
    for pid in ("viralizai", "cortai", "rendai", "perfilai", "animaai", "postaai", "atendeai", "astralai", "shapeai", "casaai", "arenabruta", "boateazul"):
        assert pid in reg and reg[pid]["tipo"] == "produto" and reg[pid]["schema"] == pid
        assert reg[pid]["links"]["produto"].startswith("https://") and reg[pid]["links"]["github"].startswith("https://github.com/")
    assert reg["insta-ai"]["tipo"] == "variante" and reg["insta-ai"]["base"] == "perfilai"


def test_qa_marcado_e_cliente_nao():
    for e in ("demo@rangeltech.net", "contato@rangeltech.net", "qa-cortai@rangeltech.net", "rtqa1@x.com", "a@fldubai.com"):
        assert pp.QA.match(e)
    for e in ("cliente@gmail.com", "lucas.rangel@outlook.com", "adriano@cerebro.pro"):
        assert not pp.QA.match(e)


def test_schema_invalido_recusado():
    assert pp._schema_valido("cortai") == "cortai"
    assert pp._schema_valido("x; drop table") is None and pp._schema_valido(None) is None


def test_qa_da_variante_usa_o_da_base(monkeypatch):
    import json
    monkeypatch.setenv("PAINEL_QA", json.dumps({"perfilai": {"email": "demo@rangeltech.net", "senha": "x"}}))
    reg = {p["id"]: p for p in pp.registro()}
    assert pp._qa_de(reg["perfilai"])["email"] == "demo@rangeltech.net"
    assert pp._qa_de(reg["insta-ai"])["senha"] == "x"
    assert pp._qa_de(reg["cortai"]) is None


def test_saude_sem_url_avisa(monkeypatch):
    from app import painel_saude
    monkeypatch.delenv("PAINEL_SAUDE_URL", raising=False)
    try:
        painel_saude._amostras(24)
        assert False
    except RuntimeError as e:
        assert "PAINEL_SAUDE_URL" in str(e)


def test_infra_importa_e_usa_proxy_so_leitura():
    from app import painel_infra
    assert painel_infra.PROXY.endswith("rt-docker-leitura:2375")


def test_caixai_le_do_proprio_banco(monkeypatch):
    """Caixaí tem banco próprio: o painel usa o mesmo painel_leitura trocando só o nome do banco (visão painel.usuarios)."""
    reg = {p["id"]: p for p in pp.registro()}
    assert reg["caixai"]["banco"] == "caixai" and reg["caixai"]["schema"] == "painel"
    vistos = []

    class Falso:
        def __init__(self, url, **kw):
            vistos.append(url)

    import psycopg
    monkeypatch.setattr(psycopg, "connect", Falso)
    monkeypatch.setenv("PRODUTOS_LEITURA_URL", "postgresql://painel_leitura:x@postgres:5432/produtos")
    pp._conexao("caixai")
    pp._conexao(None)
    pp._conexao("x; drop")
    assert vistos == ["postgresql://painel_leitura:x@postgres:5432/caixai"] + ["postgresql://painel_leitura:x@postgres:5432/produtos"] * 2
