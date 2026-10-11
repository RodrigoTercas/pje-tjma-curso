#!/usr/bin/env python3
"""Sela o pacote para publicação.

Grava em instalar.py a versão e o SHA-256 de cada arquivo que ele instala e,
nos COMANDO-*.md, a versão e o SHA-256 do próprio instalar.py. Assim o texto
colado no chat confere o instalador, e o instalador confere o resto.

Rodar sempre por último, antes do commit:
    python3 selar.py v1
Depois de selar, qualquer alteração nos arquivos exige selar de novo.
"""
import hashlib
import json
import pathlib
import re
import sys

AQUI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import instalar  # noqa: E402  (só para reaproveitar a lista de arquivos)


def sha(caminho: pathlib.Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"v\d+(\.\d+)*", sys.argv[1]):
        sys.exit("uso: python3 selar.py v1   (a versão é a tag que será publicada)")
    versao = sys.argv[1]

    arquivos = [f"servidor/{nome}" for nome in instalar.CODIGO] + ["skill/pje-login/SKILL.md"]
    tabela = json.dumps({a: sha(AQUI / a) for a in arquivos}, indent=4)

    alvo = AQUI / "instalar.py"
    texto = alvo.read_text(encoding="utf-8")
    texto, n1 = re.subn(r"(?s)\nINTEGRIDADE = \{.*?\}\n", lambda _: f"\nINTEGRIDADE = {tabela}\n", texto, count=1)
    texto, n2 = re.subn(r'\nVERSAO = "[^"]*"\n', f'\nVERSAO = "{versao}"\n', texto, count=1)
    if (n1, n2) != (1, 1):
        sys.exit("instalar.py não tem os marcadores VERSAO e INTEGRIDADE esperados")
    alvo.write_bytes(texto.encode("utf-8"))
    selo = sha(alvo)

    for nome in ("COMANDO-WINDOWS.md", "COMANDO-MAC.md"):
        doc = AQUI / nome
        t = doc.read_text(encoding="utf-8")
        t, a = re.subn(r"pje-tjma-curso/[^/\s]+/instalar\.py", f"pje-tjma-curso/{versao}/instalar.py", t)
        t, b = re.subn(r"\b[0-9a-fA-F]{64}\b", selo, t)
        if not (a and b):
            sys.exit(f"{nome}: não achei o endereço do instalador ou o SHA-256 para atualizar")
        doc.write_bytes(t.encode("utf-8"))

    print(f"selado como {versao}: {len(arquivos)} arquivos na tabela de integridade")
    print(f"SHA-256 de instalar.py: {selo}")
    print(f"publique com a tag {versao}; depois disso não altere mais nada sem selar de novo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
