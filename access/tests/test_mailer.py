"""E-mail de boas-vindas e de redefinir senha: multipart (texto + HTML), logo do site e texto da liberacao."""
from app import mailer


class _Inline:
    def __init__(self, target):
        self.target = target

    def start(self):
        self.target()


class _SMTP:
    sent: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        _SMTP.sent.append(msg)


def _prepara(monkeypatch):
    _SMTP.sent = []
    for k, v in {"SMTP_HOST": "smtp.teste", "SMTP_FROM": "Segundo Cérebro <contato@teste>", "SMTP_USER": "u", "SMTP_PASSWORD": "p", "SITE_URL": "https://brain.teste"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(mailer.smtplib, "SMTP", _SMTP)
    monkeypatch.setattr(mailer.threading, "Thread", lambda target, daemon=True: _Inline(target))


def _partes(msg):
    return {p.get_content_type(): p.get_content() for p in msg.iter_parts()}


def test_boas_vindas_tem_texto_e_html_com_logo_e_conta_liberada(monkeypatch):
    _prepara(monkeypatch)
    assert mailer.send_welcome(None, "ana@teste.com", "Ana", "tok123") is True
    msg = _SMTP.sent[0]
    assert msg.get_content_type() == "multipart/alternative"
    partes = _partes(msg)
    link = "https://brain.teste/definir-senha?token=tok123"
    assert link in partes["text/plain"] and "já está liberada" in partes["text/plain"] and "equipe" not in partes["text/plain"]
    assert link in partes["text/html"] and 'src="https://brain.teste/assets/img/logo.png"' in partes["text/html"] and "#cc6a47" in partes["text/html"]
    assert "Olá, Ana!" in partes["text/html"] and "equipe" not in partes["text/html"]


def test_cadastro_manual_nao_promete_liberacao(monkeypatch):
    _prepara(monkeypatch)
    mailer.send_welcome(None, "b@teste.com", "", "t", liberada=False)
    partes = _partes(_SMTP.sent[0])
    assert "já está liberada" not in partes["text/plain"] and "equipe" in partes["text/plain"]


def test_nome_com_html_e_escapado(monkeypatch):
    _prepara(monkeypatch)
    mailer.send_welcome(None, "c@teste.com", "<b>x</b>", "t")
    assert "<b>x</b>" not in _partes(_SMTP.sent[0])["text/html"]


def test_redefinir_senha_multipart(monkeypatch):
    _prepara(monkeypatch)
    assert mailer.send_reset(None, "d@teste.com", "tk9") is True
    partes = _partes(_SMTP.sent[0])
    assert "definir-senha?token=tk9" in partes["text/plain"] and "definir-senha?token=tk9" in partes["text/html"]


def test_sem_smtp_nao_envia(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert mailer.send_welcome(None, "e@teste.com", "", "t") is False
