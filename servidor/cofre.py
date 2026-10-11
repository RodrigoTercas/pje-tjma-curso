#!/usr/bin/env python3
"""Cofre local do PJe: janela com campos velados para gravar as credenciais.

Grava os arquivos que o servidor MCP lê no momento do login:
  ~/.pje_usuario       usuário do PJe (CPF, com sufixo de perfil se houver)
  ~/.pje_senha         senha
  ~/.pje_totp.secret   chave do autenticador (base32)
  ~/.pje_auto_login    arquivo vazio que liga o login automático

O que é digitado não aparece na tela, na saída do processo nem em argumentos
de linha de comando. A saída padrão traz apenas um resumo em JSON, sem valores.

Uso:
  python cofre.py              abre a janela
  python cofre.py --estado     informa o que já está gravado (sem valores)
  python cofre.py --autoteste  confere validação e gravação numa pasta temporária
"""
import base64
import json
import os
import re
import subprocess
import sys
import time

ARQUIVOS = {
    "usuario": ".pje_usuario",
    "senha": ".pje_senha",
    "totp": ".pje_totp.secret",
}
FLAG = ".pje_auto_login"
ROTULOS = {"usuario": "Usuário", "senha": "Senha", "totp": "Chave do autenticador"}


def normalizar_usuario(bruto: str) -> str:
    v = bruto.strip()
    if re.fullmatch(r"\d{3}\.\d{3}\.\d{3}-\d{2}", v):
        v = re.sub(r"\D", "", v)
    return v


def normalizar_totp(bruto: str) -> str:
    """Aceita base32 (com ou sem espaços) ou a URI otpauth:// inteira."""
    m = re.search(r"secret=([A-Za-z2-7=]+)", bruto)
    seg = re.sub(r"[\s﻿-]", "", m.group(1) if m else bruto).upper().rstrip("=")
    if not re.fullmatch(r"[A-Z2-7]{16,}", seg):
        raise ValueError(
            "A chave do autenticador deve ter só letras de A a Z e números de 2 a 7. "
            "É o texto que o PJe mostra junto do QR code (ou o endereço otpauth:// "
            "inteiro lido do QR), não o código de 6 dígitos."
        )
    try:
        base64.b32decode(seg + "=" * (-len(seg) % 8))
    except Exception:
        raise ValueError("A chave do autenticador está incompleta ou tem caractere trocado.")
    return seg


def _restringir_no_windows(caminho: str) -> bool:
    """Deixa o arquivo acessível só ao usuário atual (equivalente ao modo 600)."""
    sem_janela = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # SID em vez de nome: não tem espaço, acento nem tradução do Windows.
        saida = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
                               timeout=15, creationflags=sem_janela).stdout
        sid = saida.decode("ascii", "ignore").strip().rsplit(",", 1)[-1].strip('"')
        if not sid.startswith("S-1-"):
            return False
        # Concede primeiro e só depois corta a herança: se o segundo passo
        # falhar, o arquivo continua legível pelo dono.
        for args in (["/grant:r", f"*{sid}:(F)"], ["/inheritance:r"]):
            r = subprocess.run(["icacls", caminho, *args], capture_output=True,
                               timeout=15, creationflags=sem_janela)
            if r.returncode != 0:
                return False
        return True
    except Exception:
        return False


def gravar_protegido(caminho: str, valor: str) -> None:
    # ponytail: texto puro protegido só pela permissão do usuário no sistema;
    # se o risco for disco roubado ou backup, trocar por DPAPI/Keychain (keyring).
    fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(valor)
    try:
        os.chmod(caminho, 0o600)
    except OSError:
        pass
    if sys.platform.startswith("win"):
        _restringir_no_windows(caminho)


def gravar_cofre(base: str, usuario: str, senha: str, totp: str, auto_login: bool) -> dict:
    """Grava só os campos preenchidos; campo vazio mantém o que já existe."""
    valores = {}
    if usuario.strip():
        valores["usuario"] = normalizar_usuario(usuario)
    if senha:
        if senha != senha.strip():
            # O servidor apara as pontas ao ler: gravar assim enviaria outra senha.
            raise ValueError("A senha não pode começar nem terminar com espaço. Digite de novo.")
        valores["senha"] = senha
    if totp.strip():
        valores["totp"] = normalizar_totp(totp)  # valida antes de gravar qualquer coisa
    for chave, valor in valores.items():
        gravar_protegido(os.path.join(base, ARQUIVOS[chave]), valor)
    flag = os.path.join(base, FLAG)
    if auto_login:
        open(flag, "a").close()
    elif os.path.exists(flag):
        os.remove(flag)
    return {"gravados": sorted(valores), "auto_login": auto_login}


def liberar_trava() -> None:
    """Remove a trava de credencial recusada, que fica ao lado deste script.

    O servidor já a ignora quando o cofre é regravado depois dela, mas compara
    datas de arquivo: com o relógio do computador corrigido para trás, a trava
    ficaria "no futuro" e nunca cairia. Chamar só quando o usuário gravou algo.
    """
    try:
        os.remove(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pje_cred_cooldown"))
    except OSError:
        pass


def estado(base: str) -> dict:
    itens = {}
    for chave, nome in ARQUIVOS.items():
        caminho = os.path.join(base, nome)
        if os.path.exists(caminho):
            st = os.stat(caminho)
            # Sem o tamanho: o comprimento da senha também não deve ir para o chat.
            itens[chave] = {
                "gravado": st.st_size > 0,
                "em": time.strftime("%d/%m/%Y %H:%M", time.localtime(st.st_mtime)),
            }
        else:
            itens[chave] = {"gravado": False}
    faltam = [ROTULOS[c] for c, i in itens.items() if not i["gravado"]]
    return {
        "pasta": base,
        "itens": itens,
        "auto_login": os.path.exists(os.path.join(base, FLAG)),
        "completo": not faltam,
        "faltam": faltam,
    }


def codigo_atual(base: str):
    """(código de 6 dígitos, segundos de validade) da chave gravada, ou None."""
    caminho = os.path.join(base, ARQUIVOS["totp"])
    try:
        import pyotp
        with open(caminho, encoding="utf-8-sig") as f:
            seg = normalizar_totp(f.read())
        return pyotp.TOTP(seg).now(), 30 - int(time.time()) % 30
    except Exception:
        return None


def janela(base: str) -> dict:
    import tkinter as tk
    from tkinter import ttk

    resultado = {"gravados": [], "auto_login": os.path.exists(os.path.join(base, FLAG))}
    raiz = tk.Tk()
    raiz.title("Cofre do PJe")
    raiz.resizable(False, False)
    quadro = ttk.Frame(raiz, padding=18)
    quadro.grid()

    ttk.Label(quadro, text="Cofre do PJe", font=("", 15, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(
        quadro, wraplength=430, justify="left",
        text=("O que você digitar fica só neste computador. Nada é enviado ao "
              "chat. Campo em branco mantém o que já está gravado. Clique em "
              "cada campo antes de digitar."),
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 12))

    situacao = estado(base)["itens"]
    campos = {}
    for linha, (chave, rotulo) in enumerate((
        ("usuario", "Usuário (como no login do PJe)"),
        ("senha", "Senha do PJe"),
        ("totp", "Chave do autenticador"),
    )):
        ttk.Label(quadro, text=rotulo).grid(row=2 + linha * 2, column=0, sticky="w", padx=(0, 10))
        entrada = ttk.Entry(quadro, width=38, show="•")
        entrada.grid(row=2 + linha * 2, column=1, sticky="we")
        item = situacao[chave]
        nota = f"já gravado em {item['em']}" if item["gravado"] else "ainda não gravado"
        ttk.Label(quadro, text=nota, foreground="#666").grid(
            row=3 + linha * 2, column=1, sticky="w", pady=(0, 8))
        campos[chave] = entrada

    mostrar = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        quadro, variable=mostrar, text="Mostrar o usuário enquanto digito",
        command=lambda: campos["usuario"].config(show="" if mostrar.get() else "•"),
    ).grid(row=8, column=0, columnspan=2, sticky="w")
    auto = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        quadro, variable=auto,
        text="Ativar login automático (o servidor entra sozinho quando a sessão cair)",
    ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(2, 10))

    aviso = ttk.Label(quadro, text="", wraplength=430, justify="left")
    aviso.grid(row=10, column=0, columnspan=2, sticky="w")
    codigo = ttk.Label(quadro, text="", font=("", 13, "bold"))
    codigo.grid(row=11, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def atualizar_codigo():
        atual = codigo_atual(base)
        if atual:
            numero, resto = atual
            codigo.config(text=f"Código agora: {numero[:3]} {numero[3:]}   (vale por {resto} s)")
        raiz.after(1000, atualizar_codigo)

    def salvar():
        digitado = [campos["senha"].get(), campos["totp"].get()]
        try:
            r = gravar_cofre(base, campos["usuario"].get(), digitado[0], digitado[1], auto.get())
        except ValueError as e:
            aviso.config(text=str(e), foreground="#b00020")
            return
        except OSError as e:
            aviso.config(text=f"Não consegui gravar: {e}", foreground="#b00020")
            return
        resultado["gravados"] = sorted(set(resultado["gravados"]) | set(r["gravados"]))
        resultado["auto_login"] = r["auto_login"]
        for entrada in campos.values():
            entrada.delete(0, "end")
        limpou = False
        try:
            # Segredo colado continuaria na área de transferência, a um Ctrl+V do chat.
            if raiz.clipboard_get() in [v for v in digitado if v]:
                raiz.clipboard_clear()
                limpou = True
        except tk.TclError:
            pass
        if r["gravados"]:
            liberar_trava()
        faltam = estado(base)["faltam"]
        if not r["gravados"]:
            texto, cor = "Nenhum campo preenchido: nada foi alterado no cofre.", "#8a6d00"
        elif faltam:
            texto, cor = "Gravado. Ainda falta: " + ", ".join(faltam) + ".", "#8a6d00"
        else:
            texto, cor = (
                "Gravado. Confira se o código abaixo é igual ao do seu aplicativo "
                "autenticador. Se estiver cadastrando um novo dispositivo no PJe, é "
                "este código que você digita lá para confirmar; só avise o "
                "assistente depois de confirmar."), "#1b5e20"
        if limpou:
            texto += (" A área de transferência foi limpa; se o histórico do Windows "
                      "(Win+V) estiver ligado, apague o item lá também.")
        aviso.config(text=texto, foreground=cor)

    # Sem atalho de Enter: ele salvaria com o cursor ainda num campo, e o que
    # fosse digitado em seguida cairia no lugar errado.
    botoes = ttk.Frame(quadro)
    botoes.grid(row=12, column=0, columnspan=2, sticky="e", pady=(14, 0))
    ttk.Button(botoes, text="Fechar", command=raiz.destroy).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(botoes, text="Salvar no cofre", command=salvar).grid(row=0, column=1)

    raiz.attributes("-topmost", True)
    raiz.lift()
    raiz.after(200, lambda: (raiz.focus_force(), campos["usuario"].focus_set()))
    atualizar_codigo()
    raiz.mainloop()
    return resultado


def autoteste() -> None:
    import tempfile
    assert normalizar_usuario(" 123.456.789-09 ") == "12345678909"
    assert normalizar_usuario("12345678909.juiz") == "12345678909.juiz"
    chave = "JBSW Y3DP EHPK 3PXP"
    assert normalizar_totp(chave) == "JBSWY3DPEHPK3PXP"
    assert normalizar_totp("otpauth://totp/PJe:x?secret=jbswy3dpehpk3pxp&issuer=PJe") == "JBSWY3DPEHPK3PXP"
    for ruim in ("123456", "JBSWY3DPEHPK3PX1", ""):
        try:
            normalizar_totp(ruim)
            raise AssertionError(f"aceitou chave inválida: {ruim!r}")
        except ValueError:
            pass
    with tempfile.TemporaryDirectory() as base:
        assert estado(base)["faltam"] == list(ROTULOS.values())
        try:
            gravar_cofre(base, "12345678909", "segredo", "chave ruim!", True)
            raise AssertionError("gravou com chave inválida")
        except ValueError:
            pass
        assert not os.path.exists(os.path.join(base, ARQUIVOS["senha"])), "gravação parcial"
        try:
            gravar_cofre(base, "", " senha com espaco na ponta", "", True)
            raise AssertionError("aceitou senha que o servidor leria diferente")
        except ValueError:
            pass
        r = gravar_cofre(base, "12345678909", "Senh@ 1", chave, True)
        assert r["gravados"] == ["senha", "totp", "usuario"]
        assert "caracteres" not in json.dumps(estado(base)), "tamanho da senha exposto"
        with open(os.path.join(base, ARQUIVOS["senha"]), "rb") as f:
            assert f.read() == b"Senh@ 1"
        if not sys.platform.startswith("win"):
            assert os.stat(os.path.join(base, ARQUIVOS["senha"])).st_mode & 0o777 == 0o600
        assert estado(base)["completo"] and estado(base)["auto_login"]
        gravar_cofre(base, "", "", "", False)
        with open(os.path.join(base, ARQUIVOS["senha"]), "rb") as f:
            assert f.read() == b"Senh@ 1", "campo vazio deveria manter o gravado"
        assert not estado(base)["auto_login"]
        atual = codigo_atual(base)
        assert atual is None or (len(atual[0]) == 6 and atual[0].isdigit())
    print("autoteste do cofre: ok")
    try:
        import tkinter
        raiz = tkinter.Tk()
        raiz.withdraw()
        raiz.destroy()
        print("janela (tkinter): ok")
    except Exception as e:
        print(f"janela (tkinter): FALHOU - {type(e).__name__}: {e}")
        raise SystemExit(3)


def main() -> int:
    base = os.path.expanduser("~")
    if "--autoteste" in sys.argv:
        autoteste()
        return 0
    # JSON em ASCII puro: no Windows a saída redirecionada sai em cp1252 e
    # quem a lê como UTF-8 veria os acentos quebrados.
    if "--estado" in sys.argv:
        print(json.dumps(estado(base), indent=2))
        return 0
    try:
        resultado = janela(base)
    except Exception as e:
        print(json.dumps({"erro": f"nao foi possivel abrir a janela: {type(e).__name__}: {e}"}))
        return 2
    print(json.dumps({**resultado, "estado": estado(base)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
