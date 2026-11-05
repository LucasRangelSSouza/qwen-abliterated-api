"""Envio de e-mail (opcional e plugavel). Sem SMTP configurado nao envia nada e devolve False: o link de definir senha aparece no
painel (tela Assinantes) para o administrador copiar. Variaveis: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SITE_URL."""
from __future__ import annotations

import html
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


def logo_url() -> str:
    return f"{site_url()}/assets/img/logo.png"


def send(to: str, subject: str, text: str, html_body: str | None = None) -> bool:
    if not configured():
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["SMTP_FROM"], to, subject
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")  # multipart/alternative: quem nao le HTML fica com o texto

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


def render_html(title: str, paragraphs: list[str], button_label: str, link: str, note: str) -> str:
    """Layout de e-mail: so tabelas e estilos inline (clientes de e-mail ignoram CSS externo). Cores e logo do site."""
    e = html.escape
    body = "".join(f'<p style="margin:0 0 16px;font-size:16px;line-height:1.6;color:#1d1b18">{e(p)}</p>' for p in paragraphs)
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title></head>
<body style="margin:0;padding:0;background:#faf6f0;font-family:'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#faf6f0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#fffdf9;border:1px solid #e6dfd3;border-radius:18px">
<tr><td style="padding:28px 32px 8px"><img src="{e(logo_url())}" alt="" width="36" height="36" style="vertical-align:middle;border:0">
<span style="font-family:Georgia,'Palatino Linotype',serif;font-size:20px;color:#1d1b18;vertical-align:middle;margin-left:10px">Segundo Cérebro</span></td></tr>
<tr><td style="padding:16px 32px 8px"><h1 style="margin:0 0 16px;font-family:Georgia,'Palatino Linotype',serif;font-size:26px;line-height:1.25;font-weight:normal;color:#1d1b18">{e(title)}</h1>{body}</td></tr>
<tr><td style="padding:8px 32px 8px"><a href="{e(link)}" style="display:inline-block;background:#cc6a47;color:#ffffff;text-decoration:none;font-weight:600;font-size:16px;padding:14px 28px;border-radius:12px">{e(button_label)}</a></td></tr>
<tr><td style="padding:20px 32px 28px"><p style="margin:0 0 6px;font-size:13px;line-height:1.5;color:#6b655c">{e(note)}</p>
<p style="margin:0;font-size:13px;line-height:1.5;color:#6b655c;word-break:break-all">Se o botão não abrir, copie este endereço: <a href="{e(link)}" style="color:#cc6a47">{e(link)}</a></p></td></tr>
</table>
<p style="margin:16px 0 0;font-size:12px;color:#6b655c">Segundo Cérebro · Rangel Tech</p>
</td></tr></table></body></html>"""


def send_welcome(ctx, to: str, name: str, token: str, liberada: bool = True) -> bool:
    link = setup_link(token)
    ola = f"Olá, {name}!" if name else "Olá!"
    depois = ("Sua conta já está liberada: assim que você definir a senha, é só abrir o aplicativo e entrar com este e-mail."
              if liberada else "Depois de definir a senha, nossa equipe confirma a liberação da sua conta e avisa você.")
    texto = f"{ola}\n\nSua assinatura foi registrada. Defina a sua senha por este link (vale por 7 dias):\n{link}\n\n{depois}\n"
    corpo = render_html("Defina a sua senha", [ola, "Sua assinatura foi registrada. Falta só criar a sua senha de acesso.", depois],
                        "Definir minha senha", link, "Este link vale por 7 dias e só pode ser usado por você.")
    return send(to, "Defina a sua senha do Segundo Cérebro", texto, corpo)


def send_reset(ctx, to: str, token: str) -> bool:
    link = setup_link(token)
    texto = f"Use este link para definir uma nova senha (vale por 7 dias):\n{link}\n"
    corpo = render_html("Redefinir a senha", ["Recebemos um pedido para definir uma nova senha no Segundo Cérebro.", "Se não foi você, pode ignorar este e-mail: a sua senha atual continua valendo."],
                        "Definir nova senha", link, "Este link vale por 7 dias e só pode ser usado por você.")
    return send(to, "Redefinir a senha do Segundo Cérebro", texto, corpo)
