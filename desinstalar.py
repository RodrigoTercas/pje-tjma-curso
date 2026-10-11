#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
# ///
"""Desfaz o que o instalar.py fez: Windows e macOS.

Remove, nesta ordem:
  1. a entrada "pje-tjma" do claude_desktop_config.json (com backup);
  2. a skill pje-login de ~/.claude/skills;
  3. a pasta do servidor, ~/mcp-pje-tjma.

Só mexe no que este pacote instalou: uma entrada "pje-tjma" que aponte para
outra pasta e uma skill pje-login de outra origem são mantidas.
O cofre (~/.pje_usuario, ~/.pje_senha, ~/.pje_totp.secret, ~/.pje_auto_login)
só é apagado com --cofre. O uv não é removido.

Uso:
  uv run desinstalar.py [--cofre]
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time

NOME = "pje-tjma"
WINDOWS = sys.platform.startswith("win")
HOME = os.path.expanduser("~")
DESTINO = os.path.join(HOME, "mcp-pje-tjma")
COFRE = (".pje_usuario", ".pje_senha", ".pje_totp.secret", ".pje_auto_login")
CARIMBO = time.strftime("%Y%m%d-%H%M%S")


def diga(marca: str, texto: str) -> None:
    print(f"{marca:<5} {texto}", flush=True)


def configs_do_claude_desktop() -> list:
    if WINDOWS:
        roaming = os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming")
        local = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
        pastas = [os.path.join(roaming, "Claude")]
        pastas += glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude"))
    elif sys.platform == "darwin":
        pastas = [os.path.join(HOME, "Library", "Application Support", "Claude")]
    else:
        pastas = [os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "Claude")]
    arquivos = [os.path.join(p, "claude_desktop_config.json") for p in pastas]
    return [a for a in arquivos if os.path.isfile(a)]


def remover_registro(arquivo: str) -> None:
    try:
        with open(arquivo, encoding="utf-8-sig") as f:
            config = json.load(f)
        servidores = config.get("mcpServers")
    except Exception as e:
        diga("AVISO", f"{arquivo} nao pode ser lido ({type(e).__name__}); nao alterei")
        return
    entrada = servidores.get(NOME) if isinstance(servidores, dict) else None
    if not isinstance(entrada, dict):
        diga("OK", f"sem servidor '{NOME}' em {arquivo}")
        return
    if DESTINO not in (entrada.get("args") or []):
        diga("AVISO", f"o servidor '{NOME}' de {arquivo} aponta para outra pasta; "
                      "nao e deste pacote e foi mantido")
        return
    shutil.copy2(arquivo, f"{arquivo}.antes-de-desinstalar-{CARIMBO}")
    del servidores[NOME]
    provisorio = arquivo + ".tmp"
    with open(provisorio, "w", encoding="utf-8", newline="\n") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(provisorio, arquivo)
    diga("OK", f"servidor '{NOME}' removido de {arquivo} (copia de seguranca ao lado)")


def remover_skill() -> None:
    pasta = os.path.join(HOME, ".claude", "skills", "pje-login")
    arquivo = os.path.join(pasta, "SKILL.md")
    if not os.path.isfile(arquivo):
        diga("OK", "skill pje-login nao estava instalada na aba Code")
        return
    with open(arquivo, "rb") as f:
        if b"pacote-pje-tjma" not in f.read():
            diga("AVISO", f"a skill pje-login em {pasta} nao e deste pacote; foi mantida")
            return
    shutil.rmtree(pasta)
    diga("OK", f"skill pje-login removida de {pasta}")


def remover_servidor() -> bool:
    if not os.path.isdir(DESTINO):
        diga("OK", f"a pasta {DESTINO} ja nao existe")
        return True
    # No Windows, renomear falha enquanto o servidor estiver rodando: melhor
    # descobrir isso antes de apagar metade dos arquivos.
    provisoria = f"{DESTINO}.removendo-{CARIMBO}"
    try:
        os.rename(DESTINO, provisoria)
    except OSError:
        diga("ERRO", f"a pasta {DESTINO} esta em uso: o servidor ainda esta rodando. "
                     "Saia do Claude Desktop por completo, abra de novo e rode este desinstalador outra vez")
        return False
    shutil.rmtree(provisoria, ignore_errors=True)
    if os.path.exists(provisoria):
        diga("AVISO", f"sobrou parte da pasta em {provisoria}; pode apagar manualmente")
    else:
        diga("OK", f"pasta do servidor removida: {DESTINO}")
    return True


def remover_cofre() -> None:
    apagados = []
    for nome in COFRE:
        caminho = os.path.join(HOME, nome)
        if os.path.isfile(caminho):
            os.remove(caminho)
            apagados.append(nome)
    diga("OK", "cofre apagado: " + (", ".join(apagados) if apagados else "nao havia arquivos"))


def main() -> int:
    p = argparse.ArgumentParser(description="Desinstala o servidor MCP do PJe do Claude Desktop.")
    p.add_argument("--cofre", action="store_true",
                   help="apaga tambem usuario, senha e chave gravados no cofre")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    diga("...", f"sistema: {sys.platform} | pasta do usuario: {HOME}")
    arquivos = configs_do_claude_desktop()
    if not arquivos:
        diga("OK", "nenhuma configuracao do Claude Desktop encontrada")
    for arquivo in arquivos:
        remover_registro(arquivo)
    remover_skill()
    removeu = remover_servidor()
    if args.cofre:
        remover_cofre()
    else:
        restam = [n for n in COFRE if os.path.isfile(os.path.join(HOME, n))]
        diga("...", "cofre mantido" + (f" ({len(restam)} arquivos)" if restam else " (estava vazio)")
             + "; para apagar, rode de novo com --cofre")
    if not removeu:
        return 1
    diga("FIM", "desinstalado. Saia do Claude Desktop por completo e abra de novo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
