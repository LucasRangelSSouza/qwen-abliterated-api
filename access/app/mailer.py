"""Envio de e-mail (opcional e plugavel). Sem SMTP configurado nao envia nada e devolve False: o link de definir senha aparece no
painel (tela Assinantes) para o administrador copiar. Variaveis: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SITE_URL."""
from __future__ import annotations

import os
import smtplib
import threading
from email.message import EmailMessage


def configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def site_url() -> str:
    return os.environ.get("SITE_URL", "https://brain.rangeltech.net").rstrip("/")


def setup_link(token: str) -> str:
    return f"{site_url()}/definir-senha?token={token}"


def send(to: str, subject: str, text: str) -> bool:
    if not configured():
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["SMTP_FROM"], to, subject
    msg.set_content(text)

    def run():
        try:
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=20) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
                s.send_message(msg)
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=run, daemon=True).start()
    return True


def send_welcome(ctx, to: str, name: str, token: str) -> bool:
    return send(to, "Defina a sua senha do Segundo Cérebro",
                f"Olá{', ' + name if name else ''}!\n\nSua assinatura foi registrada. Defina a sua senha por este link (vale por 7 dias):\n{setup_link(token)}\n\n"
                "Depois, a liberação da sua conta é feita pela nossa equipe e você será avisado.\n")
