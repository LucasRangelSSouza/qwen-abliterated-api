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
    return os.environ.get("SITE_URL", "https://segundocerebro.pro").rstrip("/")


def setup_link(token: str) -> str:
    return f"{site_url()}/definir-senha?token={token}"


def api_url() -> str:
    return os.environ.get("API_URL", "https://api.segundocerebro.pro").rstrip("/")


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
    """Compra aprovada (spec 53): o e-mail so INFORMA. A pessoa entra direto no app ou no navegador com este e-mail e cria a senha na
    hora; o link continua aqui para quem preferir criar a senha pelo e-mail."""
    link = setup_link(token)
    remoto = "https://app.segundocerebro.pro"
    ola = f"Olá, {name}!" if name else "Olá!"
    depois = ("" if liberada else "Nossa equipe confirma a liberação da sua conta em seguida e avisa você.")
    paras = [ola + " Sua compra foi aprovada e a sua conta do Cérebro Pro já existe.",
             f"Para entrar é só abrir o aplicativo (ou {remoto} no navegador), digitar este e-mail ({to}) e criar a sua senha ali mesmo, na hora.",
             "Prefere criar a senha por aqui? Use o botão abaixo (vale por 7 dias)."] + ([depois] if depois else [])
    texto = "\n\n".join(paras) + f"\n\nCriar a senha pelo e-mail: {link}\nBaixar o aplicativo: {site_url()}/#baixar\n"
    corpo = render_html("Sua conta está pronta", paras, "Criar minha senha agora", link, f"Baixar o aplicativo: {site_url()}/#baixar")
    return send(to, "Sua conta do Cérebro Pro está pronta", texto, corpo)


def confirmar_link(token: str) -> str:
    return f"{api_url()}/v1/auth/confirmar-email?t={token}"


def send_bem_vindo(ctx, to: str, name: str, token: str) -> bool:
    """Primeiro acesso direto no app (spec 53): a pessoa ja entrou; o e-mail da boas-vindas e pede, sem obrigar, para confirmar o endereco."""
    link = confirmar_link(token)
    ola = f"Olá, {name}!" if name else "Olá!"
    paras = [ola + " Que bom ter você no Cérebro Pro. Sua senha foi criada e você já está dentro.",
             "Só falta uma coisinha: confirme que este e-mail é seu, para recuperar a senha e receber os avisos da sua conta.",
             "Se não foi você que criou essa conta, responda este e-mail na hora."]
    texto = "\n\n".join(paras) + f"\n\nConfirmar meu e-mail: {link}\n"
    return send(to, "Bem-vindo ao Cérebro Pro: confirme seu e-mail", texto, render_html("Bem-vindo ao Cérebro Pro", paras, "Confirmar meu e-mail", link, "Isso não bloqueia nada: você já pode usar tudo."))


def send_confirmar_email(ctx, to: str, name: str, token: str) -> bool:
    link = confirmar_link(token)
    ola = f"Olá, {name}!" if name else "Olá!"
    paras = [ola + " Aqui está o link para confirmar o seu e-mail no Cérebro Pro.", "Se não foi você que pediu, pode ignorar."]
    texto = "\n\n".join(paras) + f"\n\nConfirmar meu e-mail: {link}\n"
    return send(to, "Confirme seu e-mail do Cérebro Pro", texto, render_html("Confirme seu e-mail", paras, "Confirmar meu e-mail", link, ""))


DOWNLOADS = {  # sempre a última versão publicada
    "Windows": "https://github.com/RangelTech/second-brain/releases/latest/download/Segundo-Cerebro-Windows.exe",
    "Mac (Apple Silicon)": "https://github.com/RangelTech/second-brain/releases/latest/download/Segundo-Cerebro-macOS-arm64.dmg",
    "Mac (Intel)": "https://github.com/RangelTech/second-brain/releases/latest/download/Segundo-Cerebro-macOS-x64.dmg",
    "Linux": "https://github.com/RangelTech/second-brain/releases/latest/download/Segundo-Cerebro-Linux.AppImage",
}


def send_password_done(ctx, to: str, name: str = "", plano: str | None = None, recuperacao: bool = False) -> bool:
    """Depois de criar ou recuperar a senha (Spec 25 §11.1): ensina a BAIXAR o aplicativo e a entrar, citando o plano pelo nome
    (sem números fixos no código). Quem só redefiniu a senha e já usa o app recebe a versão curta."""
    remoto = "https://app.segundocerebro.pro"
    ola = f"Olá, {name}!" if name else "Olá!"
    if recuperacao:
        paras = [ola + " Sua senha foi redefinida.",
                 f"Entre normalmente no aplicativo ou em app.segundocerebro.pro com o e-mail {to} e a senha nova.",
                 "Se não foi você que pediu essa troca, responda este e-mail na hora."]
        texto = "\n\n".join(paras) + f"\n\nEntrar: {remoto}\n"
        return send(to, "Sua senha do Segundo Cérebro foi redefinida", texto, render_html("Senha redefinida", paras, "Entrar agora", remoto, "Qualquer dúvida, responda este e-mail."))
    plano_txt = f"Seu plano: {plano}. " if plano else ""
    links = " | ".join(f"{k}: {v}" for k, v in DOWNLOADS.items())
    paras = [ola + " Sua senha foi definida e a sua conta está pronta.",
             plano_txt + "Para usar no computador, baixe o aplicativo Segundo Cérebro, instale e abra:",
             links,
             f"Na primeira abertura toque em Entrar e use o e-mail {to} e a senha que você acabou de criar. O assistente se prepara sozinho na primeira vez (alguns minutos) e depois é só conversar.",
             f"Também dá para usar pelo navegador do computador ou do celular, sem instalar nada: abra app.segundocerebro.pro e entre com o mesmo e-mail e senha.",
             "Se esquecer a senha, use Esqueci a senha na tela de entrada, ou fale com a gente. Se não foi você que definiu essa senha, avise na hora."]
    texto = "\n\n".join(paras) + f"\n\nBaixar: {site_url()}/#baixar\nEntrar pelo navegador: {remoto}\n"
    corpo = render_html("Baixe o Segundo Cérebro e entre", paras, "Baixar o aplicativo", f"{site_url()}/#baixar", "Pelo navegador: app.segundocerebro.pro")
    return send(to, "Baixe o Segundo Cérebro e entre na sua conta", texto, corpo)


def send_vm_preparing(ctx, to: str, name: str = "") -> bool:
    """Senha definida mas a maquina virtual ainda nao esta pronta: explica o que foi comprado e que os dados de acesso chegam por e-mail."""
    site = site_url()
    ola = f"Olá, {name}!" if name else "Olá!"
    paras = [ola + " Obrigado pela sua compra do Segundo Cérebro com Máquina virtual.",
             "Você comprou um produto que vem com um workspace só seu: um espaço totalmente seu na nuvem, pronto para você fazer o que quiser. Suba o que quiser, instale o que quiser, trabalhe com o seu Segundo Cérebro sem dividir nada com ninguém.",
             "Estamos provisionando esse espaço agora. Aguarde alguns minutos: assim que ele estiver pronto, enviamos um novo e-mail com todos os seus dados de login.",
             "Enquanto isso, você não precisa fazer nada. Se tiver qualquer dúvida, é só responder este e-mail."]
    texto = "\n\n".join(paras) + f"\n\n{site}\n"
    corpo = render_html("Seu espaço exclusivo está sendo preparado", paras, "Conhecer o Segundo Cérebro", site, "Você recebe os dados de acesso por e-mail assim que a sua máquina estiver pronta.")
    return send(to, "Seu espaço exclusivo no Segundo Cérebro está sendo preparado", texto, corpo)


def send_vm_criada_dono(ctx, vm_nome: str, cliente: str, custo_cents: int, preco_cents: int, regiao: str) -> bool:
    """Aviso ao dono (spec 57 §6.6): cada VM criada na Contabo, com o custo. Destino: VM_AVISO_EMAIL."""
    para = (os.environ.get("VM_AVISO_EMAIL") or "").strip()
    if not para:
        return False
    custo, preco = f"R$ {custo_cents / 100:.2f}".replace(".", ","), f"R$ {preco_cents / 100:.2f}".replace(".", ",")
    paras = [f"Uma máquina virtual foi criada na Contabo depois do pagamento confirmado.",
             f"VM: {vm_nome}. Cliente: {cliente}. Região: {regiao}.",
             f"Custo para nós: {custo} por mês. Preço cobrado: {preco} por mês.",
             "É só informativo: a criação já aconteceu. A linha do tempo da VM fica no painel, em Máquinas virtuais."]
    texto = "\n\n".join(paras)
    corpo = render_html("Máquina virtual criada", paras, "Abrir o painel", "https://painel.rangeltech.net/admin/#/vms", "Aviso automático do llm-access.")
    return send(para, f"VM criada: {vm_nome} ({cliente}) — custo {custo}/mês", texto, corpo)


def send_vm_ready(ctx, to: str, name: str = "") -> bool:
    """A maquina virtual ficou pronta: onde entrar e com que e-mail (a senha e a que a pessoa criou)."""
    remoto = "https://app.segundocerebro.pro"
    ola = f"Olá, {name}!" if name else "Olá!"
    paras = [ola + " O seu espaço exclusivo está pronto.",
             f"Para entrar, abra app.segundocerebro.pro no navegador, digite o e-mail {to} e a senha que você criou, e toque em Entrar. Você cai direto na sua máquina virtual.",
             "Ela é só sua: suba o que quiser, instale o que quiser e peça ao Segundo Cérebro o que precisar.",
             "Se esquecer a senha, use Esqueci a senha na tela de entrada do aplicativo ou fale com a gente."]
    texto = "\n\n".join(paras) + f"\n\nEntrar: {remoto}\n"
    corpo = render_html("Seu espaço exclusivo está pronto", paras, "Entrar agora", remoto, "Qualquer dúvida, responda este e-mail.")
    return send(to, "Seu espaço exclusivo no Segundo Cérebro está pronto", texto, corpo)


def send_reset(ctx, to: str, token: str) -> bool:
    link = setup_link(token)
    texto = f"Use este link para definir uma nova senha (vale por 7 dias):\n{link}\n"
    corpo = render_html("Redefinir a senha", ["Recebemos um pedido para definir uma nova senha no Segundo Cérebro.", "Se não foi você, pode ignorar este e-mail: a sua senha atual continua valendo."],
                        "Definir nova senha", link, "Este link vale por 7 dias e só pode ser usado por você.")
    return send(to, "Redefinir a senha do Segundo Cérebro", texto, corpo)


def send_terms_receipt(ctx, to: str, versao: str, aceito_iso: str, codigo: str) -> bool:
    """Comprovante do aceite dos termos (Spec 25 §18): versao, data e hora, codigo de verificacao e links."""
    site = site_url()
    link = f"{site}/termos-de-uso/verificar/?codigo={codigo}"
    paras = [f"Você aceitou os Termos de uso do Segundo Cérebro (versão {versao}).",
             f"Data e hora do aceite (UTC): {aceito_iso}.",
             f"Código de verificação: {codigo}. Guarde este e-mail: com esse código qualquer pessoa pode conferir no nosso site que o aceite foi registrado.",
             f"Você pode ler a versão que aceitou em {site}/termos-de-uso/?versao={versao}."]
    texto = "\n\n".join(paras) + f"\n\nConferir o aceite: {link}\n"
    return send(to, "Comprovante: você aceitou os Termos de uso", texto, render_html("Comprovante do aceite", paras, "Conferir o aceite", link, "Rangel Tech - Segundo Cérebro"))
