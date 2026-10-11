---
name: pje-login
description: >
  Entrega uma sessão autenticada no PJe pelo servidor MCP pje-tjma e cuida do
  cofre local de credenciais (usuário, senha e chave do autenticador), sempre
  por janela com campos velados e nunca pela conversa. Use antes de ler fila,
  conclusos, autos ou peças, e quando o usuário pedir para "conectar no PJe",
  "logar no PJe", "testar a conexão com o PJe", "abrir o cofre", "trocar a senha
  do PJe" ou "cadastrar a chave do autenticador".
---

# Login no PJe (servidor pje-tjma)

Faz uma coisa só: entregar uma sessão autenticada e confirmada. Não lê autos,
não minuta e não assina.

## Como as peças se encaixam

- O servidor MCP `pje-tjma` roda no computador do usuário e faz o login
  sozinho, com usuário, senha e o código de 6 dígitos que ele mesmo calcula a
  partir da chave do autenticador.
- Essas três informações ficam num cofre local: arquivos na pasta do usuário,
  que só o servidor lê. O login relê o cofre a cada tentativa, então trocar
  uma credencial não exige reiniciar nada.
- Quem preenche o cofre é o usuário, numa janela com campos velados aberta
  pela ferramenta `abrir_cofre`. O que ele digita ali não passa pela conversa.

## Sequência (uma passada, sem laço)

### 1. Verificar

Chame `status_conexao`. Se as ferramentas do servidor estiverem adiadas,
carregue antes: `ToolSearch` com
`select:mcp__pje-tjma__status_conexao,mcp__pje-tjma__abrir_cofre`.

- **✅** → informe a linha de saída (seção final) e siga com a tarefa.
- **🛑** → nada foi lido. Leia o trecho `origem:` da mensagem e vá ao passo 2.
  Não chame `status_conexao` de novo ainda.
- **A ferramenta não existe** → o servidor não carregou. Não tente login por
  outro meio. Peça ao usuário para encerrar o Claude Desktop por completo
  (no Windows, pelo ícone da bandeja) e abrir de novo; persistindo, o motivo
  está no arquivo `mcp-server-pje-tjma.log`, na pasta de logs do Claude, e
  em Configurações > Desenvolvedor.

### 2. Identificar o elo quebrado

| O que aparece em `origem:` | O que aconteceu | O que fazer |
|---|---|---|
| `NÃO TENTADO — falta configurar` | O cofre está incompleto. Nenhuma senha foi enviada. | `abrir_cofre`; quando o usuário disser que salvou, `status_conexao` uma vez. Se o que falta é a chave e ele não a tem, passo 3. |
| `DESLIGADO` | O cofre está completo, mas o login automático não foi ligado. | É decisão do usuário: `abrir_cofre` e marcar "Ativar login automático". |
| `recusados pelo Keycloak` | Usuário ou senha não conferem. A mesma mensagem aparece se a conta estiver em bloqueio temporário. | Não repetir. `abrir_cofre` para regravar; depois `status_conexao` uma vez. |
| `recusou o código TOTP` | Senha certa; ou o relógio do computador está errado, ou a chave do cofre não é a do autenticador cadastrado na conta. | Primeiro conferir o relógio (data, hora e fuso automáticos). Só se estiver certo, recadastrar o autenticador (passo 3). Depois, regravar a chave no cofre libera a trava. |
| `senha aceita, mas a página seguinte não pede código` | A conta ainda não tem aplicativo autenticador cadastrado, ou o SSO exige outra ação. | Cadastrar o autenticador (passo 3) ou concluir a ação no navegador. |
| `trava de proteção` | Uma tentativa anterior falhou e o servidor parou de insistir para não bloquear a conta. | Não insistir. Regravar a credencial no cofre libera a trava. |
| `NÃO TENTADO — sem acesso ao SSO`, `formulário ... não encontrado` ou `não é do SSO do PJe` | Rede, proxy, portal de acesso da rede ou PJe fora do ar. Nenhuma senha foi enviada. | Conferir a internet e tentar mais tarde. Não mexer no cofre. |
| qualquer outro texto | Falha fora da credencial (rede no meio do login, SSO com erro, sessão não validada). | Não mexer no cofre nem repetir. Informar o texto literal e tentar mais tarde. |

Diga ao usuário qual elo falhou, com essas palavras. "O login falhou" não é
diagnóstico.

### 3. Cadastrar ou recadastrar o autenticador (ato do usuário)

O servidor precisa da chave do autenticador, e quem já usa o aplicativo no
celular normalmente nunca a viu: ela só aparece no momento do cadastro. Por
isso é preciso cadastrar um dispositivo novo. As telas do PJe podem mudar;
descreva o objetivo e deixe o usuário achar o botão.

1. No navegador, o usuário entra no PJe com usuário e senha e, na tela do
   código, escolhe configurar um novo dispositivo. O PJe mostra um QR code e,
   em geral, a mesma chave em texto (às vezes atrás de um link como "não
   consegue ler o código?").
2. Ele escaneia o QR code no aplicativo autenticador do celular, para o login
   manual continuar funcionando.
3. Chame `abrir_cofre`. Ele digita a chave em texto no campo "Chave do
   autenticador" e salva. Se a tela só mostrar o QR, serve o endereço
   `otpauth://...` inteiro, lido com um leitor de QR que exiba o texto. A
   janela passa a mostrar o código de 6 dígitos do momento, que deve ser igual
   ao do celular.
4. Ele digita esse código no PJe para confirmar o dispositivo.
5. **Só depois de ele dizer que confirmou no PJe**, chame `status_conexao`
   uma vez. Antes disso a chave ainda não vale, e a tentativa seria recusada.

Nunca peça a chave, o QR code ou uma captura dessa tela na conversa.

### 4. Reconfirmar

Depois de o usuário dizer que salvou o cofre (ou, no passo 3, que confirmou o
código no PJe), chame `status_conexao` **uma única vez**. Se voltar 🛑,
informe o elo e pare.

## Regras invioláveis

- **Credencial nunca passa pela conversa.** Não peça, não aceite e não repita
  senha, CPF ou chave. Se o usuário colar uma senha no chat, não a use nem a
  repita: peça que a digite no cofre e avise que, por ter ficado registrada na
  conversa, convém trocá-la.
- **Nunca abra nem leia os arquivos do cofre**, por nenhum meio (leitura de
  arquivo, terminal, script). Para saber o que está gravado, use `abrir_cofre`
  ou `cofre.py --estado`, que não mostram valores.
- **No máximo duas chamadas de `status_conexao` por tarefa:** uma antes e uma
  depois de o cofre ser regravado. O PJe bloqueia a conta após poucas recusas.
- **Não apague a trava de proteção e não crie nem remova o arquivo de login
  automático por conta própria.** Quem decide é o usuário, pela janela.
- **Uma sessão por usuário.** O login do servidor derruba a sessão que o
  usuário tiver aberta no navegador, e o login no navegador derruba a do
  servidor. Não abra o PJe em navegador automatizado nem em sessão paralela.
- **🛑 nunca é "fila vazia"**, "processo inexistente" ou "autos sem peças".
- **Assinar e protocolar são atos pessoais do usuário.** Nunca os execute.

## Onde ficam as coisas

- Cofre: `~/.pje_usuario`, `~/.pje_senha`, `~/.pje_totp.secret` e
  `~/.pje_auto_login`. No Windows, `~` é `C:\Users\<nome>`. São arquivos de
  texto restritos ao usuário do sistema: não devem ser copiados, sincronizados
  em nuvem nem enviados a ninguém.
- Servidor: `~/mcp-pje-tjma`.
- Na aba Code, que tem terminal, dá para consultar o cofre sem ver valores com
  `uv run --directory <pasta do servidor> python cofre.py --estado`.

## Saída

Uma linha, antes de a tarefa principal começar:

> **Sessão PJe:** ✅ ativa · origem `<cache | credenciais+TOTP | chrome/...>` · órgão `<id>`

ou

> **Sessão PJe:** 🛑 caída — `<elo quebrado, nomeado>`. Nenhum dado foi lido.

<!-- pacote-pje-tjma -->
