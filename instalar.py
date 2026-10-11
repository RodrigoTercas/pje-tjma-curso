#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
# ///
"""Instala o servidor MCP do PJe (TJMA) no Claude Desktop: Windows e macOS.

O que faz, nesta ordem:
  1. copia o servidor para ~/mcp-pje-tjma (desta pasta ou baixando o pacote);
  2. prepara o ambiente Python com o uv e roda o autoteste (não faz login);
  3. registra o servidor "pje-tjma" no claude_desktop_config.json, com backup;
  4. instala a skill pje-login em ~/.claude/skills e gera o zip para Chat/Cowork.

Não pede, não lê e não grava senha: isso é com a janela do cofre (cofre.py).
Pode ser executado de novo sem medo; cada alteração tem cópia de segurança.

Uso:
  uv run instalar.py [--origem PASTA_OU_ZIP] [--orgao ID] [--substituir]
"""
import argparse
import glob
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

VERSAO = "v2"
ORIGEM_PADRAO = f"https://github.com/RodrigoTercas/pje-tjma-curso/archive/refs/tags/{VERSAO}.zip"
# >>> integridade: SHA-256 de cada arquivo instalado; gerado por selar.py, não edite à mão.
INTEGRIDADE = {
    "servidor/server.py": "d496115611447bbd5294e5b67757b769844bee3efa96d054b3b159c5f1d70fd9",
    "servidor/pje_web_client.py": "cbe200964d656ccd725bd9d1c3009c8e8ea02475171783e03f0b183a2671fbbb",
    "servidor/config.py": "8148c836900c92362eda7e5c2af1dc47b3355fa3fd5efa65fc3e41d22000dc46",
    "servidor/models.py": "56aadc2f6ab0d05c66b856dc6d74a2e577324b0b855b6a7bff1be249a6df114b",
    "servidor/cofre.py": "9450b3504013740b4af1d355d9d4b4e582cba0784999109890e92cd0c92dff84",
    "servidor/pyproject.toml": "4bf714c2620803075deed2b4dd514cfa092fb4618d074aec8b4e62c6960d4ba5",
    "servidor/uv.lock": "f91c11d4c641596b0c3835544ae1eb82c7be4e23e35a8e42749e8d84b1382fdb",
    "servidor/.python-version": "02e735b3dfe1c32833eb550b7ff8ffa17f5f2bc3fa1e7bae61a8f5a3883ce398",
    "skill/pje-login/SKILL.md": "fc5dc497fbe27d5eb09f0586beb3d5e2bfccc5bc333875ca8c5b554151d7d8c4"
}
NOME = "pje-tjma"
CODIGO = ("server.py", "pje_web_client.py", "config.py", "models.py", "cofre.py",
          "pyproject.toml", "uv.lock", ".python-version")
WINDOWS = sys.platform.startswith("win")
HOME = os.path.expanduser("~")
DESTINO = os.path.join(HOME, "mcp-pje-tjma")
CARIMBO = time.strftime("%Y%m%d-%H%M%S")


def diga(marca: str, texto: str) -> None:
    # Só ASCII: no Windows a saída redirecionada sai em cp1252 e acento vira lixo.
    print(f"{marca:<5} {texto}", flush=True)


def falhar(texto: str) -> "NoReturn":
    diga("ERRO", texto)
    raise SystemExit(1)


def baixar(url: str) -> bytes:
    if not url.lower().startswith("https://"):
        falhar(f"origem recusada (so https ou pasta local): {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            return r.read()
    except Exception as e:
        diga("AVISO", f"download direto falhou ({type(e).__name__}); tentando com o curl do sistema")
    curl = shutil.which("curl")
    if curl:
        r = subprocess.run([curl, "-fsSL", "--max-time", "180", url], capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout
    falhar("nao consegui baixar o pacote. Baixe o zip manualmente e rode: "
           "uv run instalar.py --origem CAMINHO_DO_ZIP")


def extrair(zip_bytes: bytes, pasta: str) -> str:
    """Extrai só a árvore que contém servidor/server.py e devolve essa raiz."""
    base = os.path.abspath(pasta)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        marca = "servidor/server.py"
        raiz = next((n[:-len(marca)] for n in z.namelist() if n.endswith(marca)), None)
        if raiz is None:
            falhar("o zip nao contem servidor/server.py")
        for nome in z.namelist():
            if not nome.startswith(raiz) or nome.endswith("/"):
                continue
            alvo = os.path.abspath(os.path.join(base, nome[len(raiz):]))
            if not alvo.startswith(base + os.sep):
                continue  # entrada do zip tentando sair da pasta
            os.makedirs(os.path.dirname(alvo), exist_ok=True)
            with z.open(nome) as origem, open(alvo, "wb") as destino:
                shutil.copyfileobj(origem, destino)
    return base


def obter_pacote(origem: str) -> str:
    aqui = os.path.dirname(os.path.abspath(__file__))
    if not origem and os.path.isfile(os.path.join(aqui, "servidor", "server.py")):
        return aqui
    origem = origem or ORIGEM_PADRAO
    if os.path.isdir(origem):
        if not os.path.isfile(os.path.join(origem, "servidor", "server.py")):
            falhar(f"a pasta {origem} nao contem servidor/server.py")
        return os.path.abspath(origem)
    if os.path.isfile(origem):
        with open(origem, "rb") as f:
            dados = f.read()
    else:
        diga("...", f"baixando o pacote de {origem}")
        dados = baixar(origem)
    return extrair(dados, tempfile.mkdtemp(prefix="pje-pacote-"))


def conferir_integridade(pacote: str) -> None:
    """O pacote baixado tem de ser, byte a byte, o da versão publicada."""
    if not INTEGRIDADE:
        diga("AVISO", "este instalador nao esta selado (sem tabela de integridade); "
                      "o pacote baixado nao foi conferido")
        return
    for relativo, esperado in INTEGRIDADE.items():
        caminho = os.path.join(pacote, *relativo.split("/"))
        obtido = ""
        if os.path.isfile(caminho):
            with open(caminho, "rb") as f:
                obtido = hashlib.sha256(f.read()).hexdigest()
        if obtido != esperado:
            falhar(f"integridade: {relativo} nao confere com a versao {VERSAO} publicada. "
                   "Nada foi instalado. Avise o professor do curso")
    diga("OK", f"integridade conferida ({len(INTEGRIDADE)} arquivos, versao {VERSAO})")


def copiar_servidor(pacote: str) -> None:
    os.makedirs(DESTINO, exist_ok=True)
    for nome in CODIGO:
        origem = os.path.join(pacote, "servidor", nome)
        if not os.path.isfile(origem):
            falhar(f"pacote incompleto: falta servidor/{nome}")
        shutil.copyfile(origem, os.path.join(DESTINO, nome))
    if WINDOWS:
        # Em Windows ARM o uv prefere Python nativo, e nem toda dependencia tem
        # pacote pronto para ARM: fixa o Python x64, que la roda emulado.
        with open(os.path.join(DESTINO, ".python-version"), "w", encoding="ascii", newline="\n") as f:
            f.write("cpython-3.13-windows-x86_64-none\n")
    diga("OK", f"servidor copiado para {DESTINO}")


def achar_uv():
    exe = "uv.exe" if WINDOWS else "uv"
    candidatos = [os.environ.get("UV"), shutil.which("uv"),
                  os.path.join(HOME, ".local", "bin", exe),
                  os.path.join(HOME, ".cargo", "bin", exe)]
    if WINDOWS:
        local = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
        candidatos.append(os.path.join(local, "Microsoft", "WinGet", "Links", exe))
    else:
        candidatos += ["/opt/homebrew/bin/uv", "/usr/local/bin/uv"]
    for c in candidatos:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


def rodar(cmd: list, limite: int):
    # Certificados do sistema: rede de tribunal costuma inspecionar o TLS. As
    # duas variaveis dizem o mesmo; cada versao do uv le a que conhece.
    amb = {**os.environ, "UV_PYTHON_PREFERENCE": "only-managed",
           "UV_NATIVE_TLS": "1", "UV_SYSTEM_CERTS": "1"}
    amb.pop("VIRTUAL_ENV", None)  # o do proprio instalador confundiria o uv
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=limite, env=amb)
    except subprocess.TimeoutExpired:
        return 124, f"passou de {limite} s"
    saida = (r.stdout + b"\n" + r.stderr).decode("utf-8", "replace").strip()
    return r.returncode, saida


def preparar_ambiente(uv: str) -> None:
    diga("...", "preparando o Python e as dependencias (na primeira vez pode levar alguns minutos)")
    rc, saida = rodar([uv, "sync", "--directory", DESTINO], 1200)
    if rc != 0:
        falhar("uv sync falhou:\n" + saida[-1500:])
    diga("OK", "ambiente Python pronto")
    rc, saida = rodar([uv, "run", "--directory", DESTINO, "python", "cofre.py", "--autoteste"], 300)
    if rc != 0 or "janela (tkinter): ok" not in saida:
        falhar("autoteste do cofre falhou:\n" + saida[-1500:])
    diga("OK", "autoteste do cofre e da janela")
    rc, saida = rodar([uv, "run", "--directory", DESTINO, "python", "-c",
                       "import server; print('servidor-importa-ok')"], 300)
    if rc != 0 or "servidor-importa-ok" not in saida:
        falhar("o servidor nao carregou:\n" + saida[-1500:])
    diga("OK", "servidor carrega sem erro (nenhum login foi feito)")


def pastas_do_claude_desktop():
    if WINDOWS:
        roaming = os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming")
        local = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
        pastas = [os.path.join(roaming, "Claude")]
        # Instalacao pela Microsoft Store: o AppData e virtualizado dentro do pacote.
        pastas += glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude"))
    elif sys.platform == "darwin":
        pastas = [os.path.join(HOME, "Library", "Application Support", "Claude")]
    else:
        pastas = [os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "Claude")]
    existentes = [p for p in pastas if os.path.isdir(p)]
    return (existentes or pastas[:1]), bool(existentes)


def entrada_do_servidor(uv: str, orgao, anterior) -> dict:
    amb = dict((anterior or {}).get("env") or {})
    # PJE_SEM_COOKIES: o servidor entra so pelo cofre e nao toca no navegador.
    # No Windows nao ha alternativa; no macOS, ler o Chrome faria o sistema
    # pedir a senha do computador e permissao para controlar o navegador.
    amb.update({"PYTHONUTF8": "1", "UV_PYTHON_PREFERENCE": "only-managed",
                "PJE_SEM_COOKIES": "1"})
    if orgao is not None or "PJE_ORGAO_ALVO" not in amb:
        amb["PJE_ORGAO_ALVO"] = orgao or ""
    if WINDOWS:
        amb["USERPROFILE"] = HOME
    return {"command": uv, "args": ["--directory", DESTINO, "run", "python", "server.py"], "env": amb}


def registrar(pasta: str, uv: str, orgao, substituir: bool) -> bool:
    arquivo = os.path.join(pasta, "claude_desktop_config.json")
    os.makedirs(pasta, exist_ok=True)
    config = {}
    if os.path.exists(arquivo) and os.path.getsize(arquivo):
        try:
            with open(arquivo, encoding="utf-8-sig") as f:
                config = json.load(f)
        except Exception as e:
            diga("ERRO", f"{arquivo} tem JSON invalido ({e}); nao alterei")
            return False
        if not isinstance(config, dict) or not isinstance(config.get("mcpServers", {}), dict):
            diga("ERRO", f"{arquivo} tem formato inesperado; nao alterei")
            return False
    servidores = config.setdefault("mcpServers", {})
    anterior = servidores.get(NOME)
    if anterior is not None and not isinstance(anterior, dict):
        anterior = {}
    if anterior and not substituir and DESTINO not in (anterior.get("args") or []):
        diga("ERRO", f"{arquivo} ja tem um servidor '{NOME}' apontando para outra pasta; "
                     "mantive como estava. Para trocar, rode de novo com --substituir")
        return False
    nova = entrada_do_servidor(uv, orgao, anterior)
    if anterior == nova:
        diga("OK", f"ja estava registrado em {arquivo}")
        return True
    if os.path.exists(arquivo):
        shutil.copy2(arquivo, f"{arquivo}.antes-do-pje-{CARIMBO}")
    servidores[NOME] = nova
    provisorio = arquivo + ".tmp"
    with open(provisorio, "w", encoding="utf-8", newline="\n") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(provisorio, arquivo)
    diga("OK", f"servidor '{NOME}' registrado em {arquivo}")
    return True


def instalar_skill(pacote: str, substituir: bool) -> None:
    origem = os.path.join(pacote, "skill", "pje-login", "SKILL.md")
    if not os.path.isfile(origem):
        falhar("pacote incompleto: falta skill/pje-login/SKILL.md")
    with open(origem, "rb") as f:
        novo = f.read()

    pacote_zip = os.path.join(DESTINO, "pje-login.zip")
    with zipfile.ZipFile(pacote_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("pje-login/SKILL.md", novo)
    diga("OK", f"zip da skill para Chat e Cowork: {pacote_zip}")

    destino = os.path.join(HOME, ".claude", "skills", "pje-login", "SKILL.md")
    if os.path.isfile(destino):
        with open(destino, "rb") as f:
            atual = f.read()
        if atual == novo:
            diga("OK", "skill pje-login ja estava instalada na aba Code")
            return
        if b"pacote-pje-tjma" not in atual and not substituir:
            diga("AVISO", f"ja existe outra skill pje-login em {destino}; mantive a sua. "
                          "Para trocar, rode de novo com --substituir")
            return
        shutil.copy2(destino, f"{destino}.antes-do-pje-{CARIMBO}")
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, "wb") as f:
        f.write(novo)
    diga("OK", f"skill pje-login instalada em {destino}")


def main() -> int:
    p = argparse.ArgumentParser(description="Instala o servidor MCP do PJe no Claude Desktop.")
    p.add_argument("--origem", default="", help="pasta ou zip do pacote (padrao: esta pasta ou o repositorio)")
    p.add_argument("--orgao", default=None, help="id do orgao julgador preferido (padrao: qualquer)")
    p.add_argument("--substituir", action="store_true",
                   help="troca um servidor pje-tjma ou uma skill pje-login que ja existam")
    args = p.parse_args()
    # As mensagens de erro do uv trazem simbolos que o cp1252 do Windows nao tem.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    diga("...", f"sistema: {sys.platform} | pasta do usuario: {HOME}")
    uv = achar_uv()
    if not uv:
        falhar("nao encontrei o uv. Instale-o (https://docs.astral.sh/uv/) e rode de novo")
    diga("OK", f"uv em {uv}")

    pacote = obter_pacote(args.origem)
    if pacote == os.path.dirname(os.path.abspath(__file__)):
        diga("...", "instalando a partir desta pasta (copia local, sem conferencia de integridade)")
    else:
        conferir_integridade(pacote)
    copiar_servidor(pacote)
    preparar_ambiente(uv)

    pastas, desktop_existe = pastas_do_claude_desktop()
    if not desktop_existe:
        diga("AVISO", "nao achei a pasta do Claude Desktop; criei a configuracao no local padrao. "
                      "Se o aplicativo nao estiver instalado, instale-o e abra uma vez")
    feitos = {os.path.join(pasta, "claude_desktop_config.json"):
              registrar(pasta, uv, args.orgao, args.substituir) for pasta in pastas}
    instalar_skill(pacote, args.substituir)

    # Com duas pastas candidatas (instalacao pela loja), basta uma: a outra
    # pode ser um arquivo esquecido que o aplicativo nem le.
    instalado = any(feitos.values())
    print(json.dumps({
        "instalado": instalado,
        "pasta_do_servidor": DESTINO,
        "registrado_em": [a for a, ok in feitos.items() if ok],
        "nao_alterado": [a for a, ok in feitos.items() if not ok],
        "comando_do_cofre": [uv, "run", "--directory", DESTINO, "python", "cofre.py"],
        "zip_da_skill": os.path.join(DESTINO, "pje-login.zip"),
    }, indent=2))
    if not instalado:
        return 1
    diga("FIM", "instalado. Proximos passos: preencher o cofre e reabrir o Claude Desktop por completo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
