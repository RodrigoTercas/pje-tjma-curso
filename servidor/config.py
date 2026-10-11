"""Configuração do MCP Server PJe/TJMA."""

import os
from dotenv import load_dotenv

load_dotenv()

# Credenciais PJe (via variáveis de ambiente)
PJE_CPF = os.getenv("PJE_CPF", "")


def _ler_arquivo_credencial(caminho: str) -> str:
    """Lê um arquivo de credencial tolerando as codificações que o Windows produz.

    O PowerShell 5.1 grava `echo x > arquivo` em UTF-16 LE com BOM, e o Bloco
    de Notas pode gravar UTF-8 com BOM. Lido como texto simples, o valor chega
    com caracteres nulos ou '\ufeff' na frente, e o Keycloak recusa a senha ou
    o código TOTP sem que nada aponte a causa.
    """
    with open(caminho, "rb") as f:
        bruto = f.read()
    if bruto.startswith((b"\xff\xfe", b"\xfe\xff")):
        texto = bruto.decode("utf-16")
    else:
        texto = bruto.decode("utf-8-sig", errors="replace")
    return texto.replace("\x00", "").strip()


def _carregar_usuario() -> str:
    """Nome de usuário do login (pode ter sufixo de perfil, ex.: CPF + '.juiz').

    O PJe permite vários perfis para o mesmo CPF; o login do gabinete costuma
    ser 'CPF.juiz'. Ordem: arquivo ~/.pje_usuario, env PJE_USUARIO, senão o CPF.
    """
    caminho = os.path.expanduser("~/.pje_usuario")
    if os.path.exists(caminho):
        try:
            v = _ler_arquivo_credencial(caminho)
            if v:
                return v
        except Exception:
            pass
    return os.getenv("PJE_USUARIO", "") or PJE_CPF


PJE_USUARIO = _carregar_usuario()


def _carregar_senha() -> str:
    """Senha do PJe. Arquivo protegido ~/.pje_senha TEM PRIORIDADE sobre a env
    (permite atualizar a senha sem editar o settings.json e sem expor no chat).
    """
    caminho = os.path.expanduser("~/.pje_senha")
    if os.path.exists(caminho):
        try:
            v = _ler_arquivo_credencial(caminho)
            if v:
                return v
        except Exception:
            pass
    return os.getenv("PJE_SENHA", "")


PJE_SENHA = _carregar_senha()


def _carregar_totp_secret() -> str:
    """Lê o segredo TOTP (base32) para gerar o código 2FA automaticamente.

    Ordem: variável de ambiente PJE_TOTP_SECRET; senão, arquivo protegido
    ~/.pje_totp.secret (deve ter permissão 600). Espaços e prefixo
    'otpauth://...secret=' são tolerados. Vazio = login automático desligado.
    """
    raw = os.getenv("PJE_TOTP_SECRET", "").strip()
    if not raw:
        caminho = os.path.expanduser("~/.pje_totp.secret")
        if os.path.exists(caminho):
            try:
                raw = _ler_arquivo_credencial(caminho)
            except Exception:
                raw = ""
    # tolera colar a URI inteira do otpauth
    if "secret=" in raw:
        import re as _re
        m = _re.search(r"secret=([A-Za-z2-7=]+)", raw)
        if m:
            raw = m.group(1)
    # normaliza: remove espaços, maiúsculas (base32 é case-insensitive)
    return raw.replace(" ", "").upper()


PJE_TOTP_SECRET = _carregar_totp_secret()

# Endpoints WSDL do TJMA 1º grau
WSDL_URL = os.getenv(
    "PJE_WSDL_URL",
    "https://pje.tjma.jus.br/pje/intercomunicacao?wsdl"
)
WSDL_URL_V223 = "https://pje.tjma.jus.br/pje/intercomunicacao/v223?wsdl"

# Timeout para requisições SOAP (segundos)
SOAP_TIMEOUT = int(os.getenv("PJE_TIMEOUT", "60"))

# Código do órgão julgador (configurar conforme sua vara)
ORGAO_JULGADOR = os.getenv("PJE_ORGAO_JULGADOR", "")

# Tipos de documento comuns no PJe
TIPOS_DOCUMENTO = {
    "despacho": 60,
    "decisao": 3,
    "sentenca": 2,
    "ato_ordinatorio": 61,
}
