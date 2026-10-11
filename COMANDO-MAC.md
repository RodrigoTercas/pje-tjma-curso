# Comando para colar no chat do Claude Code — macOS

## Antes de colar

- Você precisa de um plano do Claude que tenha a aba **Code** e de uma sessão **Local** (o ambiente aparece ao lado do campo de mensagem).
- No seletor de modo, ao lado do botão de enviar, escolha o modo que **pede permissão a cada comando** e aprove um por um.
- Tenha em mãos o usuário e a senha com que você entra no PJe. Quem só entra por certificado ou token não consegue usar este login.
- Se for gravar a tela, preencha o cofre antes, fora da gravação (veja o README).

## O comando

Copie tudo o que está dentro do bloco abaixo e cole no chat da aba Code.

```text
Instale e configure o acesso ao PJe (TJMA, 1º grau) neste Mac, para uso no Claude Desktop. Trabalhe um passo de cada vez, mostre o resultado real de cada comando e pare no primeiro erro, explicando o que aconteceu. Não use sudo.

Regras que valem para a tarefa inteira:
- Nunca peça, leia, exiba nem grave minha senha, meu usuário ou a chave do meu autenticador. Sou eu quem digita esses dados, numa janela com campos velados que você vai abrir no passo 3. Se eu colar alguma credencial aqui no chat, não a use e me avise.
- Não abra nem leia os arquivos ~/.pje_usuario, ~/.pje_senha e ~/.pje_totp.secret, por nenhum meio.
- Nesta tarefa não faça login no PJe, não abra o PJe em navegador e não chame ferramentas do PJe.
- Se um comando passar do tempo limite, ele continua em segundo plano: não o execute de novo; espere terminar e leia a saída inteira.

Passo 1 — uv (programa que baixa o Python sozinho)
Verifique se existe ~/.local/bin/uv (ou se "which uv" responde). Se não existir, instale com o instalador oficial:
    curl -LsSf https://astral.sh/uv/install.sh | sh
Mostre a versão usando o caminho absoluto, sem til (o til não funciona entre aspas): se "which uv" respondeu, use o caminho que ele mostrou; senão, "$HOME/.local/bin/uv" --version. Use esse caminho absoluto daqui em diante.

Passo 2 — instalador
Baixe o instalador para uma pasta temporária só sua e mostre a impressão digital (SHA-256) do arquivo:
    d=$(mktemp -d) && curl -fsSL https://raw.githubusercontent.com/RodrigoTercas/pje-tjma-curso/v2/instalar.py -o "$d/instalar_pje.py" && shasum -a 256 "$d/instalar_pje.py"
O valor tem de ser este:
    635a60430ebec6721f2a6347344e32d12cb86b070c986a3a369e24eb232469b4
Se for diferente, PARE: não execute o arquivo e me avise. Se for igual, execute-o com tempo limite de 10 minutos (na primeira vez ele baixa o Python e as bibliotecas), usando o caminho que o comando anterior mostrou:
    UV_NATIVE_TLS=1 UV_SYSTEM_CERTS=1 UV_PYTHON_PREFERENCE=only-managed "<caminho completo do uv>" run "<caminho do instalar_pje.py>"
Ele confere a integridade do pacote, copia o servidor para ~/mcp-pje-tjma, roda um autoteste que não faz login, registra o servidor "pje-tjma" na configuração do Claude Desktop (guardando cópia de segurança) e instala a skill pje-login. Mostre-me as linhas OK, AVISO e ERRO. Se houver ERRO, pare aqui. Guarde o valor de "pasta_do_servidor" que aparece no final.

Passo 3 — cofre (sou eu quem digita)
Antes de abrir a janela, explique-me em poucas linhas:
- vai abrir uma janela chamada "Cofre do PJe" (no Dock ela aparece como um ícone chamado "Python"); ela pode surgir atrás desta, e eu preciso clicar dentro dela antes de digitar;
- nela eu digito o usuário e a senha com que entro no PJe e a chave do autenticador, clico em "Salvar no cofre" e depois em "Fechar"; os três campos ficam velados;
- a chave do autenticador é o texto que o PJe mostra junto do QR code ao configurar um novo dispositivo (ou o endereço otpauth:// lido do QR), e não o código de 6 dígitos. Se eu não a tiver agora, posso salvar só usuário e senha e cadastrar a chave depois;
- eu não devo colar nenhuma dessas informações aqui no chat.
Depois execute, em primeiro plano e com tempo limite de 10 minutos:
    "<caminho completo do uv>" run --directory "<pasta_do_servidor>" python cofre.py
Se passar do tempo limite, a janela continua aberta: não abra outra e não siga adiante; espere eu dizer que fechei e só então leia a saída.
Da saída, diga-me apenas o que ficou gravado, o que falta e as datas. Não cole o JSON. Se a saída vier vazia (janela encerrada sem o botão "Fechar"), não abra outra: siga para o passo 4 e responda pelo que ele mostrar. Se faltar algo que eu queira preencher agora, abra a janela outra vez.

Passo 4 — conferência (sem login)
Execute:
    "<caminho completo do uv>" run --directory "<pasta_do_servidor>" python cofre.py --estado
e diga só se o cofre está completo e se o login automático está ligado.

Passo 5 — o que eu faço em seguida
Explique-me, em lista curta:
1. Agora, sem mexer em mais nada: encerrar o Claude Desktop por completo (Cmd+Q) e abrir de novo. Só fechar a janela não basta.
2. Depois de reabrir, no Chat ou numa sessão Local da aba Code, pedir: "teste a conexão com o PJe".
3. Se o servidor não aparecer: em Configurações > Desenvolvedor deve constar "pje-tjma"; o motivo de uma falha fica no arquivo mcp-server-pje-tjma.log, em ~/Library/Logs/Claude. Se a entrada tiver sumido da configuração, rodar de novo o passo 2 e encerrar o aplicativo em seguida.
4. Para trocar a senha ou cadastrar a chave depois, pedir: "abra o cofre do PJe". Essa janela não tem limite de tempo. Se eu estiver cadastrando o autenticador, só peço o teste de conexão depois de confirmar o código no PJe.
5. A skill pje-login já vale na aba Code. Para o Chat e o Cowork, enviar o arquivo pje-login.zip, que está na pasta do servidor, em Personalizar > Skills.
6. No Cowork o servidor pode não aparecer: a documentação só garante o Chat e as sessões locais da aba Code. Testar lá antes de contar com isso.
7. Cuidados: o cofre guarda as credenciais em arquivos de texto na minha pasta de usuário, e a pasta do servidor guarda a sessão aberta; tudo protegido só pela minha conta do Mac. Usar apenas em computador pessoal, com senha de acesso e FileVault ligado; nunca copiar, sincronizar em nuvem nem enviar esses arquivos. O login do servidor derruba a sessão do navegador, e vice-versa: não usar o PJe no navegador ao mesmo tempo.

Termine com um resumo do que foi instalado, onde ficou e o que ainda depende de mim.
```
