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
