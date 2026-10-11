# Comando para desinstalar — Windows e macOS

Desfaz a instalação feita por este pacote: tira o servidor `pje-tjma` da
configuração do Claude Desktop (guardando cópia de segurança), remove a skill
`pje-login` da aba Code e apaga a pasta do servidor. Outros servidores e
outras skills não são tocados. O cofre só é apagado se você pedir.

Cole o bloco do seu sistema no chat da aba Code, em sessão Local, no modo que
pede permissão a cada comando.

**No Windows são duas passadas.** Enquanto o Claude Desktop está aberto, o
servidor fica rodando e a pasta dele não pode ser apagada. Na primeira passada
o registro e a skill saem; você encerra o Claude Desktop, abre de novo e cola
o mesmo comando outra vez, para a pasta sair.

## Windows

```text
Desinstale deste computador com Windows o servidor do PJe instalado pelo pacote pje-tjma-curso. Trabalhe um passo de cada vez, mostre o resultado real de cada comando e pare no primeiro erro. Não use privilégio de administrador.

Regras:
- Não abra nem leia os arquivos .pje_usuario, .pje_senha e .pje_totp.secret. Não faça login no PJe nem chame ferramentas do PJe.
- Não apague pastas nem edite configurações por conta própria: só o desinstalador faz isso.
- Os comandos abaixo estão em PowerShell. Se a sua ferramenta de terminal for o PowerShell, execute cada bloco como está, numa única chamada. Se for o Bash, execute cada bloco assim: powershell -NoProfile -ExecutionPolicy Bypass -Command '<bloco>' — com aspas SIMPLES por fora. Não use %NOME%, "~" nem "$HOME".

Passo 1 — pergunte-me antes de executar qualquer coisa, e espere a resposta
"Quer apagar também o cofre (usuário, senha e chave do autenticador gravados)?" Explique que, se eu for refazer a instalação diante de outras pessoas ou gravando a tela, o melhor é MANTER o cofre, para não digitar credenciais na frente de ninguém.

Passo 2 — baixar e conferir o desinstalador
    $f = Join-Path $env:TEMP "desinstalar_pje.py"
    Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/RodrigoTercas/pje-tjma-curso/desinstalar-1/desinstalar.py" -OutFile $f
    (Get-FileHash $f -Algorithm SHA256).Hash
O valor tem de ser este, sem diferença de maiúsculas ou minúsculas:
    cb2a13a2fbb2185e39364d5af8d63c8b6ea91d1bd91874d9098b59fd3e4c3e3b
Se for diferente, PARE: não execute o arquivo e me avise.

Passo 3 — executar
Se eu respondi que NÃO quero apagar o cofre:
    & "$env:USERPROFILE\.local\bin\uv.exe" run (Join-Path $env:TEMP "desinstalar_pje.py")
Se eu respondi que SIM:
    & "$env:USERPROFILE\.local\bin\uv.exe" run (Join-Path $env:TEMP "desinstalar_pje.py") --cofre
Se o uv.exe não estiver nesse caminho, procure em "$env:LOCALAPPDATA\Microsoft\WinGet\Links".
Mostre-me as linhas OK, AVISO e ERRO.

Passo 4 — conforme o resultado
- Se apareceu ERRO dizendo que a pasta está em uso: é o esperado na primeira passada. Diga-me para sair do Claude Desktop por completo (ícone do Claude na bandeja, perto do relógio, opção de sair), abrir de novo e colar este mesmo comando outra vez. Pare aqui.
- Se terminou com FIM: diga-me para sair do Claude Desktop por completo e abrir de novo, e lembre-me de que a skill enviada pelo menu Personalizar > Skills (para o Chat e o Cowork), se eu a enviei, precisa ser removida por lá. Diga também o que ficou no computador: o uv e, se eu o mantive, o cofre.
```

## macOS

```text
Desinstale deste Mac o servidor do PJe instalado pelo pacote pje-tjma-curso. Trabalhe um passo de cada vez, mostre o resultado real de cada comando e pare no primeiro erro. Não use sudo.

Regras:
- Não abra nem leia os arquivos ~/.pje_usuario, ~/.pje_senha e ~/.pje_totp.secret. Não faça login no PJe nem chame ferramentas do PJe.
- Não apague pastas nem edite configurações por conta própria: só o desinstalador faz isso.

Passo 1 — pergunte-me antes de executar qualquer coisa, e espere a resposta
"Quer apagar também o cofre (usuário, senha e chave do autenticador gravados)?" Explique que, se eu for refazer a instalação diante de outras pessoas ou gravando a tela, o melhor é MANTER o cofre, para não digitar credenciais na frente de ninguém.

Passo 2 — baixar e conferir o desinstalador
    d=$(mktemp -d) && curl -fsSL https://raw.githubusercontent.com/RodrigoTercas/pje-tjma-curso/desinstalar-1/desinstalar.py -o "$d/desinstalar_pje.py" && shasum -a 256 "$d/desinstalar_pje.py"
O valor tem de ser este:
    cb2a13a2fbb2185e39364d5af8d63c8b6ea91d1bd91874d9098b59fd3e4c3e3b
Se for diferente, PARE: não execute o arquivo e me avise.

Passo 3 — executar
Use o caminho absoluto do uv, sem til (o que "which uv" mostrar ou "$HOME/.local/bin/uv") e o caminho do arquivo que o passo 2 mostrou.
Se eu respondi que NÃO quero apagar o cofre:
    "<caminho absoluto do uv>" run "<caminho do desinstalar_pje.py>"
Se eu respondi que SIM:
    "<caminho absoluto do uv>" run "<caminho do desinstalar_pje.py>" --cofre
Mostre-me as linhas OK, AVISO e ERRO.

Passo 4 — conforme o resultado
- Se apareceu ERRO: pare e mostre-me a mensagem.
- Se terminou com FIM: diga-me para encerrar o Claude Desktop por completo (Cmd+Q) e abrir de novo, e lembre-me de que a skill enviada pelo menu Personalizar > Skills (para o Chat e o Cowork), se eu a enviei, precisa ser removida por lá. Diga também o que ficou no computador: o uv e, se eu o mantive, o cofre.
```

## O que o desinstalador não remove

- O `uv` e o Python que ele baixou. Outros programas podem usá-los.
- A skill enviada pelo menu Personalizar > Skills: ela fica na sua conta, não no computador.
- As cópias de segurança da configuração (`claude_desktop_config.json.antes-...`), que ficam ao lado do arquivo original.
