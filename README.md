# PJe (TJMA) no Claude Desktop — pacote de instalação

Liga o Claude Desktop ao PJe do TJMA (1º grau) por um servidor MCP local, com
login automático a partir de um cofre preenchido pelo próprio usuário numa
janela com campos velados. Material do curso Nova Prática Jurídica com IA.

## Pré-requisitos

- Claude Desktop com um plano que inclua a aba **Code**, em sessão **Local**.
- Entrar no PJe com **usuário e senha** (quem só usa certificado ou token não
  consegue) e ter o **aplicativo autenticador** exigido pelo tribunal.
- Computador pessoal, com senha de acesso e disco criptografado.

## O que o pacote instala

| Peça | Onde fica | Para quê |
|---|---|---|
| Servidor MCP `pje-tjma` | `~/mcp-pje-tjma` | fila, capa, autos, peças e preparo de minuta |
| Registro no Claude Desktop | `claude_desktop_config.json` | disponibiliza o servidor no aplicativo |
| Janela do cofre (`cofre.py`) | junto do servidor | o usuário digita usuário, senha e chave do autenticador |
| Skill `pje-login` | `~/.claude/skills/pje-login` e `pje-login.zip` | ensina o assistente a conduzir o login sem pedir senha no chat |

No Windows, `~` é `C:\Users\<nome>`.

## Como instalar

Cole no chat da aba Code o texto de [COMANDO-WINDOWS.md](COMANDO-WINDOWS.md) ou
de [COMANDO-MAC.md](COMANDO-MAC.md). Depois, encerre o Claude Desktop por
completo, abra de novo e peça "teste a conexão com o PJe".

Para trocar a senha ou cadastrar a chave mais tarde, peça "abra o cofre do PJe".

## Segurança: leia antes de usar

- O cofre são arquivos de texto na pasta do usuário (`.pje_usuario`,
  `.pje_senha`, `.pje_totp.secret`), e a pasta do servidor guarda a sessão
  aberta (`.pje_session.pkl`). Tudo protegido apenas pela conta do sistema
  operacional. Nunca copie, sincronize em nuvem nem envie esses arquivos.
- Guardar a chave do autenticador no mesmo computador da senha reduz a
  proteção do segundo fator: quem tiver acesso à sua conta do computador
  entra no PJe. Avalie se isso é compatível com as regras do seu tribunal.
- O PJe admite uma sessão por usuário. O login do servidor derruba a sessão
  aberta no navegador, e o login no navegador derruba a do servidor.
- O PJe bloqueia a conta depois de poucas senhas recusadas. Por isso o
  servidor tenta uma vez e trava; a trava cai quando a credencial é regravada
  no cofre.
- O comando confere a impressão digital do instalador, e o instalador confere
  a de cada arquivo que instala. Se algo não conferir, nada é instalado.
- O servidor nunca assina nem protocola. Esses atos são pessoais.

## O que foi e o que não foi testado

- macOS: instalação do zero em pasta de usuário vazia, conversa MCP com o
  servidor instalado, janela do cofre e login real.
- Windows: **ainda não testado em máquina real**. O código segue a
  documentação da Microsoft, do uv e do Claude Desktop, mas precisa de um
  ensaio completo antes de qualquer gravação.
- Chat e aba Code (sessão Local): é o que a documentação do Claude garante
  para servidores do `claude_desktop_config.json`. Para o **Cowork** não há
  garantia; verifique no seu aplicativo.
- Só o PJe do TJMA. Outros tribunais exigem trocar o endereço no código e
  não foram testados.

## Limitações no Windows

- O servidor não aproveita a sessão do Chrome (o Chrome para Windows cifra os
  cookies de um modo que não se lê de fora). O login é sempre pelo cofre.
- `processos_abertos` e o modo "processo aberto no Chrome" só existem no
  macOS. Informe o CNJ de um processo da fila ou o idProcesso.
- `preparar_minuta_no_pje` copia o texto e devolve o endereço do editor, mas
  não abre o navegador: entrar no PJe por ele derrubaria a sessão do servidor.

## Se o servidor não aparecer

1. Encerre o Claude Desktop por completo (no Windows, pelo ícone da bandeja;
   no Mac, Cmd+Q) e abra de novo.
2. Em Configurações > Desenvolvedor deve constar `pje-tjma`.
3. O motivo de uma falha fica em `mcp-server-pje-tjma.log`, na pasta de logs
   do Claude (`%APPDATA%\Claude\logs` no Windows, `~/Library/Logs/Claude` no Mac).
4. Se a entrada sumiu da configuração, rode o instalador de novo e encerre o
   aplicativo em seguida.

## Para gravar a aula sem expor dados

- Preencha o cofre antes, fora da gravação. Com tudo gravado, a janela só
  mostra "já gravado em ..." e os campos podem ficar em branco.
- Nunca filme a tela do QR code do autenticador: a chave não expira.
- Na demonstração, prefira `listar_conclusos`, que só mostra quantidades;
  `listar_processos_bucket` e as leituras mostram partes e números reais.

## Publicar (para o professor)

O comando baixa o pacote de `github.com/RodrigoTercas/pje-tjma-curso`, numa
versão fixa. Para publicar ou atualizar:

1. `./atualizar.sh` (copia do servidor de produção só o código) e, se as
   dependências mudaram, `uv lock` dentro de `servidor/`.
2. `python3 selar.py v1` (use `v2`, `v3`... nas próximas). Ele grava as
   impressões digitais em `instalar.py` e nos dois `COMANDO-*.md`.
3. Commit, tag com o mesmo nome e push para um repositório **público**.
4. De uma máquina sem login no GitHub, confira que o endereço do `instalar.py`
   citado no comando responde, e só então ensaie.

Depois de selar, não altere nenhum arquivo sem selar de novo: a conferência
de integridade recusaria a instalação. Para o vídeo, a versão fica congelada;
versões novas ganham um comando novo neste README.

## Desinstalar

Apagar a entrada `pje-tjma` do `claude_desktop_config.json` (há cópia
`.antes-do-pje-<data>` ao lado), a pasta `~/mcp-pje-tjma`, a pasta
`~/.claude/skills/pje-login` e os arquivos `~/.pje_*`.
