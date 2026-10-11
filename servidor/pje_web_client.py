"""Cliente Web para o PJe/TJMA — via requests + cookies do Chrome.

Estratégia:
1. O usuário loga manualmente em https://pje.tjma.jus.br/pje no Chrome
   (completa o 2FA por email).
2. Este cliente lê os cookies da sessão diretamente do perfil do Chrome
   e reaproveita-os numa requests.Session.
3. Chamamos os endpoints HTTP/REST descobertos via engenharia reversa
   do SPA Angular que o CNJ embute em iframe no painel.

Não usa browser automation — só HTTP direto com cookies válidos.
Thread-safe: cada instância é isolada; seguro para FastMCP.
"""

import glob
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Optional

import browser_cookie3
import requests
from bs4 import BeautifulSoup

from config import PJE_CPF, PJE_SENHA, PJE_TOTP_SECRET, PJE_USUARIO

logger = logging.getLogger("pje-web")

# O FastMCP roda as ferramentas em threads: duas chamadas simultâneas fariam
# dois logins no Keycloak com o mesmo código TOTP, e o segundo seria recusado.
_LOGIN_LOCK = threading.RLock()

BASE_URL = "https://pje.tjma.jus.br/pje"
HOME_URL = f"{BASE_URL}/home.seam"
LOGIN_URL = f"{BASE_URL}/login.seam"

# Endpoint Seam que renderiza HTML com a lista de tarefas do painel.
# É o que o SPA Angular do CNJ embute em iframe.
LIST_TASKS_URL = f"{BASE_URL}/Painel/painel_usuario/include/listTasksUsuarioPje2.seam"
# APIs REST internas do PJe-legacy
TAREFAS_API_URL = f"{BASE_URL}/seam/resource/rest/pje-legacy/painelUsuario/tarefas"
MINUTAS_API_URL = f"{BASE_URL}/seam/resource/rest/pje-legacy/painelUsuario/tarefas/minutas"
PAINEL_SESSAO_URL = f"{BASE_URL}/seam/resource/rest/pje-legacy/usuario/visualizaPainelSessao"
# Endpoint leve para validar a sessão SEM disparar OAuth (evita criar
# sessão concorrente com a do navegador — o PJe permite 1 sessão/usuário).
CURRENT_USER_URL = f"{BASE_URL}/seam/resource/rest/pje-legacy/usuario/currentUser"
# API MNI sobre REST: consulta de processo (capa, partes, assuntos)
API_MNI_URL = f"{BASE_URL}/seam/resource/rest/pje-legacy/api/v1/processos-judiciais"
# Serviço do painel (tarefas, etiquetas, chave de acesso)
PAINEL_USUARIO_URL = f"{BASE_URL}/seam/resource/rest/pje-legacy/painelUsuario"
# Fila real do gabinete: POST com filtro retorna os buckets de tarefa
# (ex.: "Concluso para decisão") com a quantidade pendente em cada um.
TAREFAS_POST_URL = f"{PAINEL_USUARIO_URL}/tarefas"
# Resumo do processo por idProcesso interno (classe, OJ, status)
PROCESSOS_URL = f"{BASE_URL}/seam/resource/rest/pje-legacy/processos"
# Download do conteúdo de uma peça (após selecioná-la nos autos)
DOC_DOWNLOAD_URL = f"{BASE_URL}/seam/resource/rest/pje-legacy/documento/download"
# Autos digitais (JSF) — leitura completa das peças; exige idProcesso interno
AUTOS_URL = f"{BASE_URL}/Processo/ConsultaProcesso/Detalhe/listAutosDigitais.seam"
# Editor de minuta do PJe (TinyMCE+JSF). Abre o ato para o magistrado
# escrever/revisar e ASSINAR — o salvar/assinar é feito na UI validada do PJe.
# TIPO_EDITOR do TJMA = "TN" (confirmado na sessão; configurável por tribunal).
TIPO_EDITOR = "TN"
EDITOR_MINUTA_URL = (
    f"{BASE_URL}/Processo/Fluxo/AtividadesLote/minutarLotePainelNovo{TIPO_EDITOR}.seam"
)

COOKIE_DOMAINS = [
    "pje.tjma.jus.br",
    "sso.cloud.pje.jus.br",
    "cloud.pje.jus.br",
    "pje.jus.br",
    "tjma.jus.br",
]

CNJ_REGEX = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")

# Tempo de vida do estado dos autos em cache (segundos). Dentro desta janela,
# todas as peças de UM processo são lidas com uma única abertura dos autos.
AUTOS_TTL = 300

# Intervalo mínimo entre duas tentativas de recuperação da sessão (segundos).
# Recuperar = reler os cookies do Chrome. NUNCA em loop: se o navegador do
# magistrado também está sem sessão, insistir só gera ruído (e, no limite,
# tentativas de login recusadas no Keycloak).
RECUPERACAO_COOLDOWN = 60


# Trava de habilitação do login autônomo (CPF+senha+TOTP). Enquanto o arquivo
# não existir, NENHUMA tentativa de senha é enviada ao Keycloak — proteção
# deliberada contra bloqueio da conta do magistrado por tentativas recusadas.
AUTO_LOGIN_FLAG = os.path.expanduser("~/.pje_auto_login")

# Perfil do Chrome DEDICADO ao PJe, aberto por abrir_pje.sh com --user-data-dir.
# É a única fonte de sessão que não disputa com o navegador do dia a dia.
CHROME_PERFIL_PJE = os.path.expanduser("~/.pje-chrome-debug")


def _dir_dados_chrome() -> str:
    """Pasta de dados do Chrome conforme o sistema operacional."""
    import sys as _sys
    if _sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return os.path.join(local, "Google", "Chrome", "User Data")
    if _sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    return os.path.expanduser("~/.config/google-chrome")


def _nome_perfil(cookie_file: str) -> str:
    """Nome do perfil a partir do caminho do banco de cookies."""
    pasta = os.path.dirname(cookie_file)
    if os.path.basename(pasta) == "Network":
        pasta = os.path.dirname(pasta)
    return os.path.basename(pasta)


def _user_agent() -> str:
    import sys as _sys
    if _sys.platform.startswith("win"):
        so = "Windows NT 10.0; Win64; x64"
    elif _sys.platform == "darwin":
        so = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        so = "X11; Linux x86_64"
    return (f"Mozilla/5.0 ({so}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/120.0.0.0 Safari/537.36")

# Trava PERSISTENTE entre processos após uma senha recusada. A trava de
# instância não basta: a triagem roda de hora em hora num processo novo, e sem
# marca em disco uma senha errada volta ao Keycloak a cada execução — que é
# exatamente como se bloqueia a conta do magistrado.
CRED_COOLDOWN_FILE = os.path.join(os.path.dirname(__file__), ".pje_cred_cooldown")
CRED_COOLDOWN = 1800  # 30 min
# Credencial RECUSADA (senha ou TOTP) não melhora com o tempo. A trava, nesse
# caso, só cai quando a credencial for regravada em disco (ver
# cred_bloqueado_ate), e nunca por decurso de prazo.
TRAVA_CREDENCIAL_RECUSADA = 10 * 365 * 86400


def auto_login_habilitado() -> bool:
    """O login autônomo por CPF+senha+TOTP está habilitado pelo magistrado?"""
    return os.path.exists(AUTO_LOGIN_FLAG)


def cred_bloqueado_ate() -> float:
    """Timestamp até quando novas tentativas de senha estão suspensas.

    Credencial gravada DEPOIS da trava a invalida: se o magistrado corrigiu a
    senha, o usuário ou o segredo TOTP, a tentativa seguinte não é insistência
    — é credencial nova, e faria pouco sentido esperar 30 minutos por ela.
    """
    try:
        with open(CRED_COOLDOWN_FILE) as f:
            ate = float(f.read().strip() or 0)
    except Exception:
        return 0.0
    try:
        marca = os.path.getmtime(CRED_COOLDOWN_FILE)
        for arq in ("~/.pje_senha", "~/.pje_usuario", "~/.pje_totp.secret"):
            p = os.path.expanduser(arq)
            if os.path.exists(p) and os.path.getmtime(p) > marca:
                return 0.0
    except Exception:
        pass
    return ate


def travar_credenciais(segundos: int = CRED_COOLDOWN) -> None:
    """Registra em disco que uma senha foi recusada agora."""
    import time as _t
    try:
        with open(CRED_COOLDOWN_FILE, "w") as f:
            f.write(str(_t.time() + segundos))
    except Exception:
        pass


def destravar_credenciais() -> None:
    """Limpa a trava — só após um login BEM-SUCEDIDO ou troca de credencial."""
    try:
        os.remove(CRED_COOLDOWN_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def aceitar_sessao_de_outro_orgao(oj_da_sessao: str) -> bool:
    """POLÍTICA: usar uma sessão de órgão julgador DIFERENTE do alvo?

    O magistrado atua em vários juízos e cada perfil do Chrome pode estar
    logado num órgão diferente. Quando nenhum perfil corresponde ao
    ORGAO_ALVO (335 = Vara Única de Alcântara), há duas saídas defensáveis:

      • ACEITAR com aviso (comportamento atual): a rotina funciona, mas pode
        ler a fila e os autos de OUTRO juízo achando que é Alcântara.
      • RECUSAR: nada é lido fora do juízo-alvo, ao custo de a rotina parar
        quando o magistrado estiver logado em outra vara.

    TODO (decisão do magistrado): trocar o corpo desta função se preferir
    recusar — bastando `return oj_da_sessao == ORGAO_ALVO`, ou aceitar apenas
    uma lista de órgãos onde a triagem automática seja admissível.
    """
    return True


class SessaoPJeExpirada(RuntimeError):
    """A camada REST do PJe (pje-legacy, guardada por OAuth) não responde mais.

    Levantada SEMPRE que o servidor redireciona uma chamada REST para o SSO.
    Existe para que uma falha de sessão nunca mais seja convertida em "fila
    vazia", "0 peças" ou "processo não encontrado" — enganos que, num gabinete,
    valem mais caro do que um erro explícito.
    """

    def __init__(self, detalhe: str = ""):
        msg = (
            "SESSÃO DO PJe DEGRADADA — a API do PJe (pje-legacy) passou a exigir "
            "reautenticação e devolveu a tela de login do SSO.\n"
            "ATENÇÃO: isto NÃO significa fila vazia, processo inexistente nem autos "
            "sem peças. Nenhum dado foi lido.\n"
            "Como restabelecer: veja o motivo em 'origem' abaixo. Se for credencial "
            "(falta configurar, recusada ou em trava), chame a ferramenta abrir_cofre "
            "para o usuário regravar usuário, senha ou chave do autenticador; depois "
            "chame status_conexao uma vez."
        )
        if not os.getenv("PJE_SEM_COOKIES"):
            msg += " Alternativa: login manual no PJe pelo Chrome."
        if detalhe:
            msg += f"\n(origem: {detalhe})"
        super().__init__(msg)

# Órgão julgador-alvo (idOrgaoJulgador). O magistrado atua em vários juízos e
# cada perfil/sessão do Chrome mostra conclusos diferentes; para consistência
# o login PREFERE a sessão neste OJ. 335 = Vara Única de Alcântara. Configurável
# por env PJE_ORGAO_ALVO. Vazio = aceita qualquer sessão válida.
import os as _os
ORGAO_ALVO = _os.getenv("PJE_ORGAO_ALVO", "335").strip()

# Cache local de cookies (ao lado deste arquivo), para a sessão sobreviver
# a reinícios do servidor MCP sem precisar reler o Chrome toda vez.
COOKIE_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".pje_session.pkl")


@dataclass
class ProcessoConcluso:
    """Processo no gabinete do magistrado."""
    numero: str
    classe: str = ""         # reaproveitado para "Tarefa atual"
    assunto: str = ""        # reaproveitado para "Tarefa executada"
    polo_ativo: str = ""
    polo_passivo: str = ""
    data_conclusao: str = ""
    tipo_conclusao: str = "" # despacho/decisão/sentença/outro
    prioridade: str = ""
    situacao: str = ""       # duração há quanto tempo

    def resumo(self) -> str:
        prio = f" ⚠️ {self.prioridade}" if self.prioridade else ""
        linhas = [f"{self.numero} | {self.classe}{prio}"]
        if self.assunto:
            linhas.append(f"  A executar: {self.assunto}")
        if self.tipo_conclusao:
            linhas.append(f"  Tipo: {self.tipo_conclusao}")
        if self.data_conclusao:
            linhas.append(f"  Desde: {self.data_conclusao}")
        if self.situacao:
            linhas.append(f"  {self.situacao}")
        return "\n".join(linhas)


class PJeWebClient:
    """Cliente HTTP do painel do PJe-TJMA com reuso de sessão do Chrome."""

    def __init__(
        self,
        cpf: Optional[str] = None,
        senha: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        # PJE_USUARIO (~/.pje_usuario) é a fonte PRIMÁRIA do login — é ele que vai
        # no campo username do Keycloak. Sem este fallback, configurar apenas
        # ~/.pje_usuario deixava self.cpf vazio e o login autônomo desistia por
        # "falta usuário" mesmo estando configurado.
        self.cpf = cpf or PJE_CPF or PJE_USUARIO
        self.senha = senha or PJE_SENHA
        self.base_url = base_url or BASE_URL

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": _user_agent(),
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        })
        self._logged_in = False
        self._perfil_ativo: Optional[str] = None
        # Estado dos autos em cache: idProcesso -> {documentos, url, viewstate, ts}.
        # Uma abertura de autos por PROCESSO, não uma por peça (ver _estado_autos).
        self._autos_cache: dict[str, dict] = {}
        self._autos_atual: Optional[str] = None
        self._ultima_recuperacao: float = 0.0
        self._diag_sessao: str = ""

    # ------------------------------------------------------------
    # Cookies do Chrome
    # ------------------------------------------------------------

    def _perfis_chrome(self) -> list[str]:
        """Caminhos dos arquivos Cookies de todos os perfis do Chrome.

        Inclui o perfil DEDICADO ao PJe (o que `abrir_pje.sh` abre com
        --user-data-dir). É nele que o magistrado loga pessoalmente e deixa a
        janela aberta; enquanto ele ficou de fora desta lista, aquela janela era
        um beco sem saída — o login() nunca via a sessão que ela mantinha viva.
        """
        base = _dir_dados_chrome()
        paths = []
        for p in sorted(
            glob.glob(os.path.join(base, "Default"))
            + glob.glob(os.path.join(base, "Profile *"))
        ) + [os.path.join(CHROME_PERFIL_PJE, "Default")]:
            # Chrome 96+ guarda o banco em <perfil>/Network/Cookies (padrão no
            # Windows); versões antigas e o macOS ainda usam <perfil>/Cookies.
            for cookies in (os.path.join(p, "Network", "Cookies"),
                            os.path.join(p, "Cookies")):
                if os.path.exists(cookies):
                    paths.append(cookies)
                    break
        # Mais recentes primeiro
        paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return paths

    def _cookies_relevantes(self, jar) -> int:
        """Adiciona cookies dos domínios-alvo do jar à sessão. Retorna quantos.

        DEDUPLICA por (domínio, nome), mantendo o cookie de expiração MAIS
        RECENTE. Sem isso, o jar do Chrome traz várias sessões acumuladas
        (já observados 4 JSESSIONID distintos ao mesmo tempo) e a sessão HTTP
        envia cookies conflitantes — o servidor escolhe um deles ao acaso e a
        rotina fica intermitente: ora lê o painel, ora responde vazio.
        """
        candidatos: dict[tuple[str, str], object] = {}
        for c in jar:
            dominio = (c.domain or "").lstrip(".").lower()
            if not any(dominio.endswith(d) for d in COOKIE_DOMAINS):
                continue
            # Cookies de sessão da aplicação (JSESSIONID e afins) só valem para
            # o host do PJe. Vindos de outros subdomínios do tribunal, são lixo
            # de outras aplicações e confundem o servidor — descarta.
            if c.name in ("JSESSIONID", "ROUTER_ID") and "pje.tjma.jus.br" not in dominio:
                continue
            chave = (dominio, c.name)
            atual = candidatos.get(chave)
            # sem expires (cookie de sessão) conta como 0; vence o maior
            if atual is None or (c.expires or 0) >= (atual.expires or 0):
                candidatos[chave] = c

        for c in candidatos.values():
            self._session.cookies.set_cookie(c)
        return len(candidatos)

    # ------------------------------------------------------------
    # Validação de sessão
    # ------------------------------------------------------------

    def _pagina_eh_login(self, resp) -> bool:
        """Detecta se a resposta é a tela de login do Keycloak/PJe."""
        url = (resp.url or "").lower()
        texto = (resp.text or "").lower()
        if "login.seam" in url or "openid-connect/auth" in url:
            return True
        # Form de senha do Keycloak/PJe
        if 'name="password"' in texto and ('name="username"' in texto or "kc-form" in texto):
            return True
        if "acesso negado" in texto and "login" in texto:
            return True
        return False

    def _sessao_valida(self) -> bool:
        """Valida a sessão com uma chamada REAL à camada que o cliente usa.

        O PJe tem DUAS camadas de autenticação com vidas independentes:

          • páginas ``*.seam`` (JSF/Seam) → guardadas só pelo JSESSIONID;
          • ``/seam/resource/rest/pje-legacy/*`` → guardadas pelo adaptador
            Keycloak (OAuth), que é por onde passam fila, capa, chave de
            acesso aos autos e download de peças — ou seja, TUDO.

        Validar na camada JSF (como se fazia até 27/07/2026) produz falso
        positivo: medido em 27/07, os 24 perfis do Chrome devolviam 200 no
        ``.seam`` e 302→SSO no REST. Era essa a origem do "✅ Conexão ativa"
        com a rotina inteira quebrada.

        Por isso sondamos o REST, SEM seguir redirects — 200 + JSON é sessão
        viva; qualquer redirect é sessão morta. Não seguir o redirect é
        deliberado: completar o code-flow do OAuth aqui criaria uma sessão
        NOVA e derrubaria a do navegador do magistrado (o PJe admite uma
        sessão por usuário).
        """
        return self._probe_rest()[0]

    def _probe_rest(self) -> tuple[bool, str]:
        """Sonda a camada REST. Retorna (viva, diagnóstico legível)."""
        body = json.dumps({"numeroProcesso": "", "competencia": "", "etiquetas": []})
        try:
            r = self._session.post(
                TAREFAS_POST_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-pje-legacy-app": "pje",
                },
                data=body,
                timeout=20,
                allow_redirects=False,
            )
        except Exception as e:
            return False, f"erro de rede ao sondar o painel: {type(e).__name__}: {e}"

        if r.status_code in (301, 302, 303, 307, 308):
            destino = r.headers.get("location", "")
            if "openid-connect" in destino or "sso." in destino:
                return False, "API do painel redirecionou para o SSO (OAuth expirado)"
            return False, f"API do painel redirecionou para {destino[:80]}"
        if r.status_code in (401, 403):
            return False, f"API do painel negou acesso (HTTP {r.status_code})"
        if r.status_code != 200:
            return False, f"API do painel respondeu HTTP {r.status_code}"
        if "json" not in r.headers.get("content-type", "").lower():
            return False, "API do painel respondeu HTML (provável tela de login)"
        try:
            data = r.json()
        except Exception:
            return False, "API do painel respondeu conteúdo não-JSON"
        if not isinstance(data, list):
            return False, "API do painel respondeu JSON inesperado (não é lista)"
        total = sum((b.get("quantidadePendente") or 0) for b in data if isinstance(b, dict))
        return True, f"painel respondeu {len(data)} grupo(s), {total} processo(s) pendente(s)"

    def _tem_cookies_de_sessao(self) -> bool:
        """Há cookie de sessão do PJe no jar? Evita sondar perfis sem chance."""
        return any(
            c.name in ("JSESSIONID", "KEYCLOAK_IDENTITY")
            and (c.domain or "").lstrip(".").lower().endswith("pje.tjma.jus.br")
            for c in self._session.cookies
        )

    # ------------------------------------------------------------
    # Cache local de cookies (sobrevive a restart do MCP)
    # ------------------------------------------------------------

    def _salvar_cache_cookies(self) -> None:
        try:
            import pickle
            # Os cookies valem como a sessão: mesma restrição de acesso do cofre.
            fd = os.open(COOKIE_CACHE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                pickle.dump(list(self._session.cookies), f)
            os.chmod(COOKIE_CACHE_FILE, 0o600)
        except Exception as e:
            logger.debug(f"Não salvou cache de cookies: {e}")

    def _carregar_cache_cookies(self) -> bool:
        try:
            import pickle
            if not os.path.exists(COOKIE_CACHE_FILE):
                return False
            with open(COOKIE_CACHE_FILE, "rb") as f:
                cookies = pickle.load(f)
            self._session.cookies.clear()
            for c in cookies:
                self._session.cookies.set_cookie(c)
            return len(self._session.cookies) > 0
        except Exception as e:
            logger.debug(f"Não carregou cache de cookies: {e}")
            return False

    # ------------------------------------------------------------
    # Login
    # ------------------------------------------------------------

    def login_por_credenciais(self) -> bool:
        """Login AUTOMÁTICO via CPF + senha + TOTP (Google Authenticator).

        Não precisa do navegador nem de cookies: faz todo o fluxo do Keycloak
        (usuário/senha → código OTP gerado localmente a partir do segredo) e
        estabelece a sessão. É o que permite a conexão "ficar direto".

        Requer PJE_CPF, PJE_SENHA e PJE_TOTP_SECRET configurados.
        Retorna True se autenticou.
        """
        # Lê o cofre AGORA, não o que havia em disco quando o processo subiu:
        # credencial regravada derruba a trava, e sem reler aqui o servidor
        # reenviaria a antiga, já recusada, ao Keycloak.
        import config as _cfg
        PJE_USUARIO = _cfg._carregar_usuario() or self.cpf
        PJE_TOTP_SECRET = _cfg._carregar_totp_secret()
        self.senha = _cfg._carregar_senha() or self.senha
        # Diz exatamente O QUE falta: "login automático falhou" quando na verdade
        # faltava o nome de usuário mandava caçar problema de senha e de relógio
        # por horas. O que falta aqui é configuração, não credencial recusada.
        faltando = [
            nome for nome, valor in (
                ("nome de usuário (~/.pje_usuario ou PJE_CPF — costuma ser 'CPF.juiz')", PJE_USUARIO),
                ("senha (~/.pje_senha)", self.senha),
                ("segredo TOTP (~/.pje_totp.secret)", PJE_TOTP_SECRET),
            ) if not valor
        ]
        if faltando:
            self._auto_login_estado = (
                "login autônomo NÃO TENTADO — falta configurar: " + "; ".join(faltando)
            )
            logger.info(self._auto_login_estado)
            return False
        # TRAVA DE HABILITAÇÃO: o login autônomo só dispara se o arquivo
        # ~/.pje_auto_login existir. Enquanto a senha não estiver CONFIRMADA
        # correta, mantemos isso desligado para não gerar tentativas recusadas
        # (que podem bloquear a conta no Keycloak). Modo cookies segue ativo.
        if not auto_login_habilitado():
            self._auto_login_estado = (
                "login autônomo DESLIGADO (nenhuma senha foi enviada ao Keycloak); "
                "para habilitar, abra o cofre e marque 'Ativar login automático'"
            )
            logger.info(self._auto_login_estado + " — usando modo cookies.")
            return False
        try:
            import pyotp
        except Exception:
            logger.warning("pyotp não instalado; login por credenciais indisponível.")
            return False

        sess = requests.Session()
        sess.headers.update(dict(self._session.headers))
        enviou = False
        try:
            # 1. Página de login do Keycloak (via PJe)
            r = sess.get(LOGIN_URL, timeout=(8, 30), allow_redirects=True)
            soup = BeautifulSoup(r.text, "html.parser")
            form = soup.find("form", id="loginForm") or soup.find(
                "form", attrs={"action": re.compile("login-actions/authenticate")}
            )
            if not form:
                self._auto_login_estado = (
                    "login autônomo NÃO TENTADO — formulário de login do SSO não "
                    "encontrado; nenhuma senha foi enviada"
                )
                logger.warning(self._auto_login_estado)
                return False

            # 2. POST usuário/senha (preserva hidden fields do form)
            action = self._acao_do_sso(r.url, form.get("action"))
            if not action:
                self._auto_login_estado = (
                    "login autônomo NÃO TENTADO — a página de login não é do SSO do "
                    "PJe (portal da rede ou proxy no caminho?); nenhuma senha foi enviada"
                )
                logger.warning(self._auto_login_estado)
                return False
            data = {inp.get("name"): (inp.get("value") or "")
                    for inp in form.find_all("input") if inp.get("name")}
            data["username"] = PJE_USUARIO or self.cpf   # ex.: 'CPF.juiz'
            data["password"] = self.senha
            data.pop("login-pje-office", None)  # não usar via certificado
            enviou = True
            r2 = sess.post(action, data=data, timeout=30, allow_redirects=True)

            # 3. Página de OTP? (campo otp/totp)
            #    Chegar aqui já é informação valiosa: o Keycloak só pede o segundo
            #    fator DEPOIS de validar usuário e senha. Registrar isso separa
            #    "credencial errada" de "segundo fator errado" — distinção que
            #    faltava e que mandava procurar problema de senha por horas.
            soup2 = BeautifulSoup(r2.text, "html.parser")
            otp_input = soup2.find("input", attrs={"name": re.compile(r"^(otp|totp)$", re.I)})
            final = r2
            erro_otp = ""
            if not otp_input:
                # A ordem importa: recusa primeiro. Rebaixar uma recusa verdadeira
                # a falha passageira faria a senha errada voltar ao Keycloak.
                recusa = self._recusa_do_keycloak(soup2)
                parada = ""
                if recusa is not None:
                    parada = ("usuário/senha recusados pelo Keycloak"
                              + (f": {recusa}" if recusa else " (não chegou ao segundo fator)"))
                elif r2.status_code >= 500:
                    parada = (f"tentativa não concluída: o SSO respondeu HTTP {r2.status_code} "
                              f"depois da senha; nada indica erro na credencial")
                if parada:
                    self._auto_login_estado = parada
                    logger.warning(parada)
                    return False
            else:
                form2 = otp_input.find_parent("form")
                action2 = self._acao_do_sso(r2.url, form2.get("action"))
                if not action2:
                    self._auto_login_estado = (
                        "tentativa não concluída: o formulário do código não é do SSO "
                        "do PJe; o código não foi enviado"
                    )
                    logger.warning(self._auto_login_estado)
                    return False
                data2 = {inp.get("name"): (inp.get("value") or "")
                         for inp in form2.find_all("input") if inp.get("name")}
                # Código gerado a menos de 5 s da virada da janela de 30 s pode
                # expirar em trânsito e ser recusado, o que conta como tentativa
                # inválida no Keycloak. Nesses casos, espera a janela seguinte.
                import time as _tt
                _resto = 30 - (_tt.time() % 30)
                if _resto < 5:
                    _tt.sleep(_resto + 1)
                codigo = pyotp.TOTP(PJE_TOTP_SECRET).now()
                data2[otp_input.get("name")] = codigo
                final = sess.post(action2, data=data2, timeout=30, allow_redirects=True)

                # 3-B. O código foi recusado? Dizer isso com todas as letras.
                soup3 = BeautifulSoup(final.text, "html.parser")
                erro_otp = self._erro_do_keycloak(soup3)
                if erro_otp and "inv" in erro_otp.lower():
                    self._auto_login_estado = (
                        f"usuário e SENHA aceitos, mas o Keycloak recusou o código TOTP "
                        f"(\"{erro_otp}\"). Ou o relógio deste computador está errado, ou "
                        f"a chave do cofre não é a do autenticador cadastrado hoje na "
                        f"conta — nesse caso recadastre o autenticador no PJe e grave a "
                        f"chave nova. NÃO insista: falhas de OTP também contam para "
                        f"bloqueio de conta no Keycloak"
                    )
                    logger.warning(self._auto_login_estado)
                    return False

            # 4. Deu certo? Não pode ter voltado ao login.
            if self._pagina_eh_login(final):
                self._auto_login_estado = (
                    "o fluxo terminou de volta na tela de login do SSO"
                    + (f" ({erro_otp})" if erro_otp else "")
                )
                logger.warning(self._auto_login_estado)
                return False

            # 5. Adota a sessão e valida
            self._session = sess
            if self._sessao_valida():
                self._logged_in = True
                self._perfil_ativo = "credenciais+TOTP"
                self._salvar_cache_cookies()
                logger.info("Sessão PJe estabelecida por CPF+senha+TOTP (automático).")
                return True
            if not otp_input:
                aviso = self._erro_do_keycloak(soup2)
                # Sem a palavra "recusados": senha certa não pode cair na trava
                # permanente de credencial recusada.
                self._auto_login_estado = (
                    "senha aceita, mas a página seguinte não pede código de "
                    "aplicativo autenticador — cadastre o autenticador (TOTP) na "
                    "conta do PJe ou conclua a ação pendente que o SSO exige"
                    + (f" (aviso na página: {aviso})" if aviso else "")
                )
                logger.warning(self._auto_login_estado)
            return False
        except Exception as e:
            if not enviou:
                self._auto_login_estado = (
                    f"login autônomo NÃO TENTADO — sem acesso ao SSO do PJe "
                    f"({type(e).__name__}); nenhuma senha foi enviada"
                )
            logger.warning(f"Erro no login por credenciais: {e}")
            return False

    @staticmethod
    def _acao_do_sso(pagina_url: str, action) -> str:
        """URL absoluta do formulário, só se for https num domínio do PJe.

        Em rede com inspeção de TLS, a página devolvida pode ser a do portal do
        firewall; sem esta conferência a senha iria para o endereço que ele
        indicasse.
        """
        from urllib.parse import urljoin, urlparse
        url = urljoin(pagina_url or "", action or "")
        partes = urlparse(url)
        host = (partes.hostname or "").lower()
        if partes.scheme == "https" and any(
            host == d or host.endswith("." + d) for d in COOKIE_DOMAINS
        ):
            return url
        return ""

    @staticmethod
    def _recusa_do_keycloak(soup):
        """Texto da recusa de usuário/senha; "" se recusou sem texto; None se não recusou.

        Só conta como recusa o que é inequívoco (campo de senha de volta, erro
        em destaque ou frase conhecida): avisos comuns usam as mesmas classes
        genéricas e não podem armar a trava permanente.
        """
        for sel in (".alert-error", "#input-error"):
            for el in soup.select(sel):
                txt = el.get_text(" ", strip=True)
                if txt:
                    return txt[:120]
        texto = soup.get_text(" ", strip=True).lower()
        for marca in ("Credenciais inválidas", "Usuário ou senha inválidos",
                      "conta está temporariamente"):
            if marca.lower() in texto:
                return marca
        if soup.find("input", attrs={"name": "password"}):
            return ""
        return None

    @staticmethod
    def _erro_do_keycloak(soup) -> str:
        """Mensagem de erro exibida pelo Keycloak na página ('Código inválido' etc.)."""
        for sel in (".alert-error", ".kc-feedback-text", "#input-error",
                    ".pf-c-alert__title", ".alert", ".message-text"):
            for el in soup.select(sel):
                txt = el.get_text(" ", strip=True)
                if txt:
                    return txt[:120]
        # Fallback: o tema do PJe às vezes só imprime o texto solto
        texto = soup.get_text(" ", strip=True)
        for marca in ("Código inválido", "Credenciais inválidas",
                      "Usuário ou senha inválidos", "conta está temporariamente"):
            if marca.lower() in texto.lower():
                return marca
        return ""

    def _orgao_da_sessao(self) -> str:
        """idOrgaoJulgador da sessão atual (via currentUser). '' se indisponível."""
        try:
            r = self._session.get(
                CURRENT_USER_URL,
                headers={"Accept": "application/json", "X-pje-legacy-app": "pje"},
                timeout=15, allow_redirects=False,
            )
            if r.status_code == 200 and "json" in r.headers.get("content-type", "").lower():
                return str(r.json().get("idOrgaoJulgador", ""))
        except Exception:
            pass
        return ""

    def login(self, forcar_chrome: bool = False) -> bool:
        with _LOGIN_LOCK:
            return self._login(forcar_chrome)

    def _login(self, forcar_chrome: bool = False) -> bool:
        """Estabelece sessão autenticada no PJe, PREFERINDO o órgão-alvo.

        O magistrado atua em vários juízos; cada perfil do Chrome pode ter uma
        sessão em um OJ diferente (com conclusos diferentes). Para consistência,
        varremos as sessões válidas e escolhemos a do ORGAO_ALVO (Alcântara);
        se nenhuma casar, usamos a primeira sessão válida encontrada.

        Args:
            forcar_chrome: ignora o cache e relê direto do Chrome.
        """
        if self._logged_in:
            return True

        candidatos = []  # (origem, cookies_snapshot)
        self._diag_sessao = ""

        # 1. Cache local (sessão anterior)
        if not forcar_chrome and self._carregar_cache_cookies():
            if self._sessao_valida():
                candidatos.append(("cache", list(self._session.cookies)))

        # 2. Cookies de cada perfil do Chrome. PJE_SEM_COOKIES pula a etapa: no
        #    Windows o Chrome cifra os cookies de um jeito que não se lê de fora.
        try:
            perfis = [] if os.getenv("PJE_SEM_COOKIES") else self._perfis_chrome()
        except Exception:
            perfis = []
        for cookie_file in perfis:
            perfil = (
                "chrome-dedicado-pje"
                if cookie_file.startswith(CHROME_PERFIL_PJE)
                else _nome_perfil(cookie_file)
            )
            try:
                jar = browser_cookie3.chrome(cookie_file=cookie_file)
            except Exception as e:
                logger.debug(f"{perfil}: {e}")
                continue
            self._session.cookies.clear()
            if self._cookies_relevantes(jar) == 0:
                continue
            # Perfis que só têm cookies de analytics não podem ter sessão:
            # descartar antes de gastar uma sondagem no servidor.
            if not self._tem_cookies_de_sessao():
                continue
            viva, diag = self._probe_rest()
            if viva:
                candidatos.append((f"chrome/{perfil}", list(self._session.cookies)))
            else:
                logger.debug(f"{perfil}: {diag}")
                self._diag_sessao = self._diag_sessao or diag

        # 3. Escolhe: primeiro o do órgão-alvo; senão o primeiro válido.
        escolhido = None
        for origem, cookies in candidatos:
            self._session.cookies.clear()
            for ck in cookies:
                self._session.cookies.set_cookie(ck)
            oj = self._orgao_da_sessao()
            if ORGAO_ALVO and oj == ORGAO_ALVO:
                escolhido = (origem, oj)
                break
            if escolhido is None:
                escolhido = (origem, oj)  # fallback: primeiro válido

        if escolhido and not aceitar_sessao_de_outro_orgao(escolhido[1]):
            raise SessaoPJeExpirada(
                f"a única sessão válida do Chrome é do órgão julgador "
                f"{escolhido[1] or '(desconhecido)'}, e não do alvo {ORGAO_ALVO}"
            )

        if escolhido:
            origem, oj = escolhido
            if ORGAO_ALVO and oj != ORGAO_ALVO:
                logger.warning(
                    f"ATENÇÃO: sessão do órgão {oj or '(desconhecido)'}, diferente do "
                    f"alvo {ORGAO_ALVO}. A fila e os autos lidos podem ser de OUTRO juízo."
                )
            # restaura os cookies do escolhido (o loop pode ter mexido)
            for o, cookies in candidatos:
                if o == origem:
                    self._session.cookies.clear()
                    for ck in cookies:
                        self._session.cookies.set_cookie(ck)
                    break
            self._perfil_ativo = origem
            self._logged_in = True
            self._salvar_cache_cookies()
            logger.info(f"Sessão PJe ativa via {origem} (OJ={oj}, alvo={ORGAO_ALVO}).")
            return True

        # 4. Nenhum cookie válido → login AUTOMÁTICO por credenciais + TOTP.
        #    TRAVA DE SEGURANÇA: se falhar UMA vez, não tenta de novo por 30 min.
        #    O Keycloak bloqueia a conta após poucas senhas recusadas; nunca
        #    devemos martelá-lo automaticamente.
        import time as _t
        self._session.cookies.clear()
        # DESLIGADO ≠ FALHOU: não anunciar "o login automático falhou" quando ele
        # sequer foi tentado — isso mandava investigar senha e relógio do Mac por
        # um problema que era só a trava de habilitação ausente.
        # O disco governa: é ele que o cofre regravado neutraliza. A marca em
        # memória só vale de reserva, quando a gravação do arquivo falhou.
        bloqueado_ate = (
            cred_bloqueado_ate() if os.path.exists(CRED_COOLDOWN_FILE)
            else getattr(self, "_cred_bloqueado_ate", 0)
        )
        if _t.time() < bloqueado_ate:
            restante = int((bloqueado_ate - _t.time()) // 60) + 1
            permanente = restante > 24 * 60
            prazo = ("até a credencial ser regravada" if permanente
                     else f"por mais ~{restante} min")
            saida = ("só cai ao regravar a credencial no cofre" if permanente
                     else "a trava cai sozinha, ou some ao gravar credencial nova")
            self._auto_login_estado = (
                f"login autônomo em trava de proteção {prazo} "
                f"após uma tentativa que falhou (evita bloqueio da conta no Keycloak); "
                f"{saida}"
            )
        else:
            self._auto_login_estado = ""
            if self.login_por_credenciais():
                self._cred_bloqueado_ate = 0
                destravar_credenciais()
                return True
            # Só travar por 30 min se uma senha REALMENTE foi ao Keycloak. Faltar
            # configuração ou estar desligado não gera tentativa — logo não há
            # risco de bloqueio de conta e não faz sentido penalizar 30 min.
            tentou_de_fato = not any(
                marca in self._auto_login_estado
                for marca in ("NÃO TENTADO", "DESLIGADO")
            )
            if tentou_de_fato:
                # Reenviar a cada 30 min uma credencial já RECUSADA (como fazia
                # o monitor agendado) é o caminho do bloqueio da conta. Recusa
                # de senha ou de TOTP trava até a credencial ser regravada;
                # falha de rede ou de formulário mantém a trava de 30 min.
                recusada = any(
                    m in self._auto_login_estado
                    for m in ("recusou o código TOTP", "recusados pelo Keycloak")
                )
                if recusada:
                    self._cred_bloqueado_ate = 0  # a trava em disco governa
                    travar_credenciais(TRAVA_CREDENCIAL_RECUSADA)
                else:
                    self._cred_bloqueado_ate = _t.time() + CRED_COOLDOWN
                    travar_credenciais()
                if not self._auto_login_estado:
                    self._auto_login_estado = (
                        "o login autônomo (CPF+senha+TOTP) foi TENTADO e falhou depois "
                        "do envio da senha (rede, SSO ou sessão não validada no painel); "
                        "não é necessariamente a credencial. Aguarde a trava de 30 min "
                        "ou regrave o cofre com abrir_cofre"
                    )
                logger.warning(
                    "Login por credenciais falhou; TRAVADO para proteger "
                    "a conta contra bloqueio por tentativas no Keycloak."
                )

        # Nenhum perfil do Chrome tem sessão REST viva. Isto é o MESMO problema
        # de uma sessão que morre no meio da rotina — por isso viaja pelo mesmo
        # canal (SessaoPJeExpirada), e não como RuntimeError genérico: assim as
        # ferramentas do MCP devolvem o aviso claro em vez de estourar.
        detalhe = ("não há sessão guardada" if os.getenv("PJE_SEM_COOKIES")
                   else "nenhum perfil do Chrome tem sessão válida na API do PJe")
        if self._diag_sessao:
            detalhe += f"; {self._diag_sessao}"
        estado_auto = getattr(self, "_auto_login_estado", "")
        if estado_auto:
            detalhe += f"; {estado_auto}"
        raise SessaoPJeExpirada(detalhe)

    def _ensure_logged_in(self) -> None:
        if not self._logged_in:
            self.login()

    def _request(self, method: str, url: str, **kwargs):
        """GET/POST de PÁGINA JSF (.seam), com reautenticação 1x se cair no login."""
        self._ensure_logged_in()
        kwargs.setdefault("timeout", 30)
        resp = self._session.request(method, url, **kwargs)
        # Se caiu no login no meio da operação, reautentica e repete 1x
        if self._pagina_eh_login(resp):
            logger.info("Sessão expirou durante a operação; reautenticando...")
            if self._recuperar_sessao():
                resp = self._session.request(method, url, **kwargs)
        else:
            self._salvar_cache_cookies()
        return resp

    # ------------------------------------------------------------
    # Camada REST (pje-legacy, guardada por OAuth)
    # ------------------------------------------------------------

    @staticmethod
    def _redirect_para_sso(resp) -> bool:
        """A resposta é um redirect para o SSO/Keycloak (= sessão OAuth morta)?"""
        if resp.status_code not in (301, 302, 303, 307, 308):
            return False
        destino = (resp.headers.get("location") or "").lower()
        return "openid-connect" in destino or "sso.cloud.pje.jus.br" in destino

    def _recuperar_sessao(self) -> bool:
        """Tenta restabelecer a sessão RELENDO os cookies do Chrome. Uma vez.

        Deliberadamente NÃO completa o authorization-code flow do OAuth a
        partir daqui: isso criaria uma sessão nova no PJe e derrubaria a do
        navegador do magistrado (uma sessão por usuário). Se o navegador
        estiver logado, seus cookies já trazem a sessão OAuth boa — basta
        relê-los. Se não estiver, nada que façamos aqui resolve, e insistir
        em loop só arrisca bloquear a conta no Keycloak; por isso há cooldown.
        """
        import time as _t
        agora = _t.time()
        if agora - self._ultima_recuperacao < RECUPERACAO_COOLDOWN:
            logger.info("Recuperação de sessão em cooldown; não vou insistir.")
            return False
        self._ultima_recuperacao = agora
        self._logged_in = False
        self._invalidar_autos()
        logger.info("Sessão REST degradada; relendo cookies do Chrome...")
        try:
            self.login(forcar_chrome=True)
        except Exception as e:
            logger.warning(f"Recuperação de sessão falhou: {e}")
            return False
        return self._logged_in

    def _rest(self, method: str, url: str, **kwargs):
        """Chamada à API REST do PJe, com detecção honesta de sessão morta.

        NÃO segue o redirect do SSO (ver _recuperar_sessao). Se o servidor
        mandar a chamada para o Keycloak, tenta UMA recuperação e, persistindo,
        levanta SessaoPJeExpirada — em vez de devolver lista vazia, que era o
        que fazia a rotina do gabinete concluir "não há processos".
        """
        self._ensure_logged_in()
        kwargs.setdefault("timeout", 30)
        kwargs["allow_redirects"] = False

        def _chamar():
            r = self._session.request(method, url, **kwargs)
            # Redirect que NÃO é para o SSO (ex.: storage de documento): seguir.
            if r.status_code in (301, 302, 303, 307, 308) and not self._redirect_para_sso(r):
                destino = r.headers.get("location") or ""
                if destino:
                    r = self._session.get(destino, timeout=kwargs.get("timeout", 30))
            return r

        resp = _chamar()
        if self._redirect_para_sso(resp) or self._pagina_eh_login(resp):
            curta = url.replace(BASE_URL, "")
            if self._recuperar_sessao():
                resp = _chamar()
                if not (self._redirect_para_sso(resp) or self._pagina_eh_login(resp)):
                    return resp
            raise SessaoPJeExpirada(f"{method} {curta}")
        if resp.status_code >= 500:
            # Erro do servidor não é sessão morta: rotular certo evita mandar o
            # magistrado relogar à toa — mas ainda assim NÃO devolve vazio.
            raise RuntimeError(
                f"O PJe respondeu HTTP {resp.status_code} em {url.replace(BASE_URL, '')}. "
                f"É falha do servidor, não da sessão. Nenhum dado foi lido — não "
                f"conclua que a fila ou os autos estão vazios."
            )
        self._salvar_cache_cookies()
        return resp

    def _rest_json(self, method: str, url: str, **kwargs):
        """_rest + exigência de JSON. Levanta SessaoPJeExpirada se vier HTML."""
        r = self._rest(method, url, **kwargs)
        if r.status_code != 200:
            return r, None
        if "json" not in r.headers.get("content-type", "").lower():
            raise SessaoPJeExpirada(
                f"{method} {url.replace(BASE_URL, '')} respondeu HTML em vez de JSON"
            )
        try:
            return r, r.json()
        except Exception:
            raise SessaoPJeExpirada(
                f"{method} {url.replace(BASE_URL, '')} respondeu conteúdo não-JSON"
            )

    # ------------------------------------------------------------
    # Painel: lista de processos
    # ------------------------------------------------------------

    def _get_painel_html(self) -> str:
        """Baixa o HTML da lista de tarefas do painel (página 1)."""
        r = self._request(
            "GET",
            LIST_TASKS_URL,
            headers={"Accept": "text/html"},
            allow_redirects=True,
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"Falha ao carregar painel ({r.status_code}). "
                f"URL: {LIST_TASKS_URL}"
            )
        # Conteúdo vem em ISO-8859-1
        if not r.encoding or r.encoding.lower() != "iso-8859-1":
            r.encoding = "iso-8859-1"
        return r.text

    def _inferir_tipo(self, *textos: str) -> str:
        """Classifica o concluso em sentença/decisão/despacho/outro."""
        t = " ".join(textos).lower()
        if "sentenç" in t or "sentenc" in t:
            return "sentença"
        if "decis" in t:
            return "decisão"
        if "despach" in t:
            return "despacho"
        return "outro"

    def buckets_pendentes(self, numero_processo: str = "") -> list[dict]:
        """Retorna os grupos de tarefa do gabinete COM a quantidade pendente.

        Esta é a "fila real" do magistrado: cada item é um tipo de tarefa
        (ex.: "Concluso para decisão", "Concluso para sentença",
        "Minutar decisão") com quantos processos estão pendentes nele.

        Args:
            numero_processo: se informado, filtra os buckets desse processo.

        Returns:
            Lista de dicts {id, nome, quantidadePendente}, só com pendência>0.
        """
        body = json.dumps({
            "numeroProcesso": numero_processo or "",
            "competencia": "",
            "etiquetas": [],
        })
        # IMPORTANTE (2026-07): a API REST do painel é guardada por OAuth.
        # Se a resposta não for uma LISTA JSON, NÃO devolver [] (isso diria
        # falsamente "fila vazia" a um magistrado) — sinalizar que não leu.
        r, data = self._rest_json(
            "POST", TAREFAS_POST_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-pje-legacy-app": "pje",
            },
            data=body,
        )
        if r.status_code != 200 or data is None:
            raise SessaoPJeExpirada(f"POST painelUsuario/tarefas → HTTP {r.status_code}")
        if not isinstance(data, list):
            raise SessaoPJeExpirada("POST painelUsuario/tarefas → JSON fora do formato")
        # 200 + lista JSON legítima → aqui sim, vazio significa vazio de verdade.
        return [
            b for b in data
            if isinstance(b, dict) and (b.get("quantidadePendente") or 0) > 0
        ]

    def processos_da_tarefa(
        self,
        nome_tarefa: str,
        pagina: int = 0,
        max_results: int = 30,
    ) -> list[dict]:
        """Lista os PROCESSOS de um bucket de tarefa pendente (conclusos).

        Resolve o elo fila→idProcesso: dado o nome do bucket (ex.:
        "Concluso para decisão"), retorna a lista de processos com
        idProcesso, CNJ, partes, classe, assunto e idTaskInstance.

        Args:
            nome_tarefa: nome exato do bucket (de buckets_pendentes).
            pagina: página (0-based).
            max_results: tamanho da página.
        """
        import urllib.parse
        e = urllib.parse.quote(nome_tarefa, safe="")
        body = json.dumps({
            "_page": pagina + 1,
            "_maxResults": max_results,
            "conferidos": False,
        })
        # Falha aqui NUNCA pode virar "[]": era assim que a sessão degradada
        # aparecia ao magistrado como "Nenhum processo no grupo".
        r, d = self._rest_json(
            "POST",
            f"{PAINEL_USUARIO_URL}/recuperarProcessosTarefaPendenteComCriterios/{e}/{pagina}",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-pje-legacy-app": "pje",
            },
            data=body,
        )
        if r.status_code != 200 or d is None:
            raise SessaoPJeExpirada(
                f"recuperarProcessosTarefaPendenteComCriterios → HTTP {r.status_code}"
            )
        return d.get("entities", []) if isinstance(d, dict) else (d or [])

    def processos_a_assinar(self, tipo_documento_id: int, max_results: int = 30) -> list[dict]:
        """Lista processos com minuta pendente de ASSINATURA, por tipo.

        Args:
            tipo_documento_id: 64=Decisão, 63=Despacho, 62=Sentença (varia).
            max_results: tamanho da página.
        """
        body = json.dumps({"_page": 1, "_maxResults": max_results, "conferidos": False})
        r, d = self._rest_json(
            "POST",
            f"{PAINEL_USUARIO_URL}/recuperarProcessosTarefaAssinaturaComCriterios/{tipo_documento_id}",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-pje-legacy-app": "pje",
            },
            data=body,
        )
        if r.status_code != 200 or d is None:
            raise SessaoPJeExpirada(
                f"recuperarProcessosTarefaAssinaturaComCriterios → HTTP {r.status_code}"
            )
        return d.get("entities", []) if isinstance(d, dict) else (d or [])

    def listar_conclusos(
        self,
        tipo: Optional[str] = None,
        pagina: int = 1,
    ) -> list[ProcessoConcluso]:
        """Lista os grupos de conclusos pendentes no gabinete (fila real).

        Cada item representa um tipo de tarefa pendente com a quantidade de
        processos. Esta é a visão fiel do que está concluso para o
        magistrado agir (decidir, despachar, sentenciar).

        Args:
            tipo: filtra por "despacho" | "decisao" | "sentenca".
            pagina: não usado (a fila é agregada por tipo de tarefa).
        """
        buckets = self.buckets_pendentes()
        out: list[ProcessoConcluso] = []
        for b in buckets:
            nome = b.get("nome", "")
            tipo_conclusao = self._inferir_tipo(nome)
            if tipo:
                alvo = tipo.lower().replace("ç", "c").replace("ã", "a")
                if alvo not in tipo_conclusao.lower().replace("ç", "c").replace("ã", "a"):
                    continue
            out.append(ProcessoConcluso(
                numero="",  # bucket agregado; processos via abrir_bucket
                classe=nome,
                assunto=f"{b.get('quantidadePendente', 0)} processo(s) pendente(s)",
                tipo_conclusao=tipo_conclusao,
                situacao=f"bucket id={b.get('id')}",
            ))
        # Ordena por quantidade desc (extrai do assunto)
        return out

    def total_fila_painel(self) -> int:
        """Total de processos conclusos pendentes (soma de todos os buckets)."""
        try:
            return sum(b.get("quantidadePendente", 0) for b in self.buckets_pendentes())
        except SessaoPJeExpirada:
            raise  # "0 conclusos" e "não consegui ler" não podem se confundir
        except Exception:
            return 0

    # ------------------------------------------------------------
    # Resolução de idProcesso (interno) a partir do navegador
    # ------------------------------------------------------------

    def processos_abertos(self) -> list[dict]:
        """Lê as abas de autos digitais abertas no Chrome (via AppleScript).

        Quando o magistrado abre um processo no PJe, a URL dos autos carrega
        o idProcesso interno. Lemos essas abas para resolver CNJ→idProcesso
        sem depender da API stateful do painel.

        Returns:
            Lista de {idProcesso, idTaskInstance, numero, url} (numero pode
            vir vazio; é resolvido via resumo_processo quando preciso).
        """
        # No modo só cofre o servidor não toca no navegador: no macOS, consultar
        # o Chrome abriria um pedido de permissão do sistema que ninguém espera.
        if os.getenv("PJE_SEM_COOKIES"):
            return []
        import subprocess
        script = (
            'tell application "Google Chrome"\n'
            ' set out to ""\n'
            ' repeat with w in windows\n'
            '  repeat with t in tabs of w\n'
            '   set out to out & (URL of t) & "\\n"\n'
            '  end repeat\n'
            ' end repeat\n'
            ' return out\n'
            'end tell'
        )
        try:
            res = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=15,
            )
            urls = res.stdout.splitlines()
        except Exception as e:
            logger.debug(f"Falha ao ler abas do Chrome: {e}")
            return []

        abertos = []
        for u in urls:
            if "listAutosDigitais.seam" not in u:
                continue
            idp = re.search(r"idProcesso=(\d+)", u)
            iti = re.search(r"idTaskInstance=(\d+)", u)
            if idp:
                abertos.append({
                    "idProcesso": idp.group(1),
                    "idTaskInstance": iti.group(1) if iti else "",
                    "numero": "",
                    "url": u,
                })
        return abertos

    def resolver_idprocesso(self, numero_processo: str = "") -> str:
        """Resolve o idProcesso interno de um CNJ.

        Ordem:
        1. Sem CNJ → idProcesso da aba de autos aberta mais recente no Chrome.
        2. Com CNJ → procura na FILA do gabinete (buckets de conclusos e de
           minutas a assinar), onde cada processo já vem com idProcesso+CNJ.
        3. Fallback → procura entre as abas de autos abertas no Chrome.

        Returns:
            idProcesso (str) ou "" se não encontrado.
        """
        # 1. Sem número: usa a aba aberta mais recente
        if not numero_processo:
            abertos = self.processos_abertos()
            return abertos[-1]["idProcesso"] if abertos else ""

        alvo = re.sub(r"\D", "", numero_processo)

        # 2. Procura na fila: descobre em qual bucket o processo está pendente.
        #    SessaoPJeExpirada PROPAGA de propósito: "não resolvi o CNJ" e "a
        #    sessão morreu" são diagnósticos diferentes, e confundi-los foi o
        #    que mascarou os incidentes de 21 e 27/07/2026.
        try:
            buckets = self.buckets_pendentes(numero_processo=numero_processo)
            for b in buckets:
                for p in self.processos_da_tarefa(b["nome"], max_results=50):
                    if re.sub(r"\D", "", p.get("numeroProcesso", "")) == alvo:
                        return str(p.get("idProcesso"))
        except SessaoPJeExpirada:
            raise
        except Exception as e:
            logger.debug(f"Busca na fila falhou: {e}")

        # 3. Fallback: abas abertas no Chrome
        for ab in self.processos_abertos():
            try:
                num = re.sub(r"\D", "", self.resumo_processo(ab["idProcesso"]).get("numeroProcesso", ""))
                if num == alvo:
                    return ab["idProcesso"]
            except Exception:
                continue
        return ""

    def localizar_na_fila(self, numero_processo: str) -> Optional[dict]:
        """Localiza um processo na fila e devolve seus dados (com idTaskInstance).

        Procura nos buckets em que o processo tem tarefa pendente. Retorna o
        dict do processo (idProcesso, idTaskInstance, numeroProcesso, partes,
        nomeTarefa, ...) ou None se não estiver na fila.
        """
        alvo = re.sub(r"\D", "", numero_processo)
        try:
            buckets = self.buckets_pendentes(numero_processo=numero_processo)
        except SessaoPJeExpirada:
            raise
        except Exception:
            buckets = []
        for b in buckets:
            for p in self.processos_da_tarefa(b["nome"], max_results=50):
                if re.sub(r"\D", "", p.get("numeroProcesso", "")) == alvo:
                    p["_bucket"] = b["nome"]
                    return p
        return None

    def url_editor_minuta(self, id_task_instance: str) -> str:
        """URL do editor de minuta do PJe para a tarefa indicada.

        Abrir esta URL (no navegador logado) leva ao editor do ato, no
        processo/tarefa certos, pronto para escrever, revisar e ASSINAR.
        """
        return f"{EDITOR_MINUTA_URL}?processoSelecionado={id_task_instance}"

    def resumo_processo(self, id_processo: str) -> dict:
        """Resumo rápido do processo por idProcesso interno.

        Endpoint: pje-legacy/processos/{idProcesso}
        Retorna numeroProcesso (CNJ), classeJudicial, orgaoJulgador, status.
        """
        r, d = self._rest_json(
            "GET", f"{PROCESSOS_URL}/{id_processo}",
            headers={"Accept": "application/json", "X-pje-legacy-app": "pje"},
        )
        if r.status_code != 200 or d is None:
            raise RuntimeError(
                f"Resumo indisponível para idProcesso={id_processo} ({r.status_code})."
            )
        return d

    # ------------------------------------------------------------
    # Estatísticas
    # ------------------------------------------------------------

    def obter_estatisticas_painel(self) -> dict:
        """Estatísticas do painel agregadas a partir da fila real (buckets)."""
        stats = {
            "conclusos_despacho": 0,
            "conclusos_decisao": 0,
            "conclusos_sentenca": 0,
            "outros": 0,
            "total_conclusos": 0,
            "buckets": [],
            "erro": None,
        }
        try:
            buckets = self.buckets_pendentes()
        except SessaoPJeExpirada:
            raise  # jamais reportar estatística zerada com a sessão morta
        except Exception as e:
            stats["erro"] = f"Falha ao ler a fila: {e}"
            return stats

        for b in buckets:
            nome = b.get("nome", "")
            qtd = int(b.get("quantidadePendente") or 0)
            tipo = self._inferir_tipo(nome)
            if tipo == "despacho":
                stats["conclusos_despacho"] += qtd
            elif tipo == "decisão":
                stats["conclusos_decisao"] += qtd
            elif tipo == "sentença":
                stats["conclusos_sentenca"] += qtd
            else:
                stats["outros"] += qtd
            stats["total_conclusos"] += qtd
            stats["buckets"].append({"nome": nome, "quantidade": qtd, "id": b.get("id")})

        # Ordena os buckets por quantidade (maiores primeiro)
        stats["buckets"].sort(key=lambda x: x["quantidade"], reverse=True)
        return stats

    # ------------------------------------------------------------
    # Consulta de processo (MNI sobre REST)
    # ------------------------------------------------------------

    # Polo: AT=Ativo(autor) PA=Passivo(réu) TC=Terceiro/Custos(MP fiscal)
    _POLO_LABEL = {
        "AT": "Autor", "PA": "Réu", "TC": "Terceiro/MP",
        "FL": "Fiscal da Lei", "VI": "Vítima", "TJ": "Terceiro",
    }

    def consultar_processo(self, numero_processo: str) -> dict:
        """Consulta a capa do processo via API MNI-REST do PJe.

        Endpoint: pje-legacy/api/v1/processos-judiciais/{cnj}
        Retorna classe, assuntos, partes, órgão julgador, valor e datas.

        OBS: o MNI entrega documentos apenas a quem é parte; para o
        magistrado, a leitura das peças é feita pelos autos digitais
        (ver listar_documentos_processo / baixar_documento).
        """
        from models import Processo, Parte

        cnj = numero_processo.strip()
        url = f"{API_MNI_URL}/{cnj}"
        r, payload = self._rest_json(
            "GET", url,
            headers={"Accept": "application/json", "X-pje-legacy-app": "pje"},
        )
        if r.status_code != 200 or payload is None:
            raise RuntimeError(f"Falha ao consultar {cnj} ({r.status_code}).")
        res = payload.get("result") or {}
        if not res:
            raise RuntimeError(
                f"Processo {cnj} não retornou dados. Verifique o número "
                f"ou se você tem acesso (pode ser sigiloso de outro juízo)."
            )

        db = res.get("dadosBasicos", {})
        # Nome da classe vem em 'any[].nomeClasseProcessual'; senão usa o código
        nome_classe = ""
        for extra in (res.get("any") or []):
            if isinstance(extra, dict) and extra.get("nomeClasseProcessual"):
                nome_classe = extra["nomeClasseProcessual"]
                break
        classe = nome_classe or f"Classe {db.get('classeProcessual', '?')}"

        assuntos = []
        for a in (res.get("assunto") or []):
            al = a.get("assuntoLocal") or {}
            desc = al.get("descricao")
            if desc:
                assuntos.append(desc + (" (principal)" if a.get("principal") else ""))

        polo_ativo, polo_passivo = [], []
        for polo in (res.get("polo") or []):
            cod = polo.get("polo", "")
            label = self._POLO_LABEL.get(cod, cod)
            for parte in (polo.get("parte") or []):
                pessoa = (parte.get("pessoa") or {}).get("dadosBasicos", {})
                nome = pessoa.get("nome", "")
                doc = (pessoa.get("numeroDocumentoPrincipal") or {}).get("value", "")
                # advogados
                advs = []
                for adv in (parte.get("advogado") or []):
                    adb = (adv.get("dadosBasicos") or adv)
                    nm = adb.get("nome") if isinstance(adb, dict) else None
                    if nm:
                        advs.append(nm)
                p = Parte(nome=nome, tipo_parte=label, documento=doc, advogados=advs)
                if cod == "AT":
                    polo_ativo.append(p)
                else:
                    polo_passivo.append(p)

        # Datas (epoch ms)
        def _data(ms):
            if not ms:
                return ""
            try:
                import datetime
                return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%d/%m/%Y")
            except Exception:
                return str(ms)

        oj = res.get("orgaoJulgador") or db.get("orgaoJulgador") or {}
        valor = res.get("valorCausa") or 0.0
        try:
            valor = float(valor)
        except Exception:
            valor = 0.0

        sigilo = res.get("nivelSigilo", 0)
        situacao = f"Sigilo nível {sigilo}" if sigilo else ""

        proc = Processo(
            numero=cnj,
            classe=classe,
            assuntos=assuntos,
            orgao_julgador=oj.get("nome", ""),
            polo_ativo=polo_ativo,
            polo_passivo=polo_passivo,
            valor_causa=valor,
            data_ajuizamento=_data(res.get("dataAjuizamento")),
            situacao=situacao,
        )
        return {"processo": proc, "raw": res}

    # --- Autos digitais (leitura de peças) ---
    # Mecanismo confirmado por engenharia reversa do SPA do CNJ:
    #   window.open(WEB_ROOT + "/Processo/ConsultaProcesso/Detalhe/
    #               listAutosDigitais.seam?idProcesso={idLegacy}&ca={chave}")
    # onde a chave vem de painelUsuario/gerarChaveAcessoProcesso/{idLegacy}.

    def chave_acesso_processo(self, id_processo: str) -> str:
        """Gera a chave de acesso (ca) para abrir os autos de um processo.

        Args:
            id_processo: idProcesso interno (idLegacy) do PJe.
        """
        url = f"{PAINEL_USUARIO_URL}/gerarChaveAcessoProcesso/{id_processo}"
        r = self._rest(
            "GET", url,
            headers={"Accept": "text/plain", "X-pje-legacy-app": "pje"},
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"Falha ao gerar chave de acesso para idProcesso={id_processo} "
                f"({r.status_code})."
            )
        return r.text.strip().strip('"')

    def _abrir_autos(self, id_processo: str):
        """Abre os autos digitais e devolve (html, url_final, viewstate).

        Detecta ativamente a página ERRADA: com a sessão OAuth morta, o PJe
        devolve a tela de login com HTTP 200; o parser não acha nenhum link
        A4J e o resultado sai como "0 peças nos autos" — foi assim que os
        incidentes de 21 e 27/07/2026 se disfarçaram de processo vazio. Por
        isso exigimos a marca estrutural da timeline dos autos.
        """
        ca = self.chave_acesso_processo(id_processo)
        url = f"{AUTOS_URL}?idProcesso={id_processo}&ca={ca}"
        r = self._request("GET", url, headers={"Accept": "text/html"}, allow_redirects=True)
        if r.status_code != 200 or "error.seam" in (r.url or ""):
            raise RuntimeError(
                f"Não foi possível abrir os autos de idProcesso={id_processo}."
            )
        if not r.encoding or r.encoding.lower() != "iso-8859-1":
            r.encoding = "iso-8859-1"
        if self._pagina_eh_login(r):
            raise SessaoPJeExpirada(f"autos de idProcesso={id_processo} → tela de login")
        if "divTimeLine" not in r.text:
            raise SessaoPJeExpirada(
                f"autos de idProcesso={id_processo} → página sem a timeline dos autos "
                f"(não é a página dos autos; NÃO conclua que o processo está sem peças)"
            )
        vs = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"', r.text)
        return r.text, r.url, (vs.group(1) if vs else "")

    def _parsear_documentos(self, html: str) -> list[dict]:
        """Extrai o índice de peças do HTML dos autos."""
        soup = BeautifulSoup(html, "html.parser")
        documentos = []
        for a in soup.find_all("a", onclick=re.compile(r"A4J\.AJAX\.Submit\('divTimeLine'")):
            span = a.find("span")
            if not span:
                continue
            m = re.match(r"\s*(\d+)\s*-\s*(.+)", span.get_text(" ", strip=True))
            if not m:
                continue
            # O componente A4J é o parâmetro que começa com 'divTimeLine:'
            comps = re.findall(r"'(divTimeLine:[^']+)'", a.get("onclick", ""))
            componente = comps[0] if comps else "ajaxSingle"
            documentos.append({
                "id": m.group(1),
                "tipo": m.group(2).strip()[:80],
                "componente": componente,
            })
        return documentos

    def _invalidar_autos(self, id_processo: Optional[str] = None) -> None:
        """Descarta o estado dos autos em cache (de um processo ou de todos)."""
        if id_processo is None:
            self._autos_cache.clear()
            self._autos_atual = None
        else:
            self._autos_cache.pop(str(id_processo), None)
            if self._autos_atual == str(id_processo):
                self._autos_atual = None

    def _encerrar_contexto_autos(self) -> None:
        """Sai dos autos abertos, voltando ao painel do gabinete.

        O PJe mantém "autos abertos" por sessão (contexto Seam do processo).
        Ao trocar de processo numa mesma execução, voltar ao painel devolve a
        sessão ao estado neutro — é o que o navegador faz quando o magistrado
        fecha os autos. Usa o endpoint do próprio painel (nenhum postback
        inventado), e falhar aqui nunca é fatal: é higiene, não pré-requisito.
        """
        if not self._autos_atual:
            return
        try:
            self._session.get(
                LIST_TASKS_URL,
                headers={"Accept": "text/html", "X-pje-legacy-app": "pje"},
                timeout=20, allow_redirects=False,
            )
        except Exception as e:
            logger.debug(f"Falha ao voltar ao painel após os autos: {e}")
        self._autos_atual = None

    def _estado_autos(self, id_processo: str, forcar: bool = False) -> dict:
        """Estado dos autos de UM processo — aberto uma vez, reusado nas peças.

        Esta é a correção central do incidente. Antes, baixar_documento()
        chamava ler_autos() para CADA peça: uma chave de acesso nova (REST) e
        uma recarga inteira dos autos por peça. Um analisar_processo de 10
        peças disparava ~30 idas à API guardada por OAuth — e era esse volume
        que queimava a sessão única do magistrado, fazendo o processo seguinte
        falhar. Agora são 2 chamadas por processo, independentemente do número
        de peças.
        """
        import time as _t
        idp = str(id_processo)
        cache = self._autos_cache.get(idp)
        if cache and not forcar and (_t.time() - cache["ts"]) < AUTOS_TTL:
            return cache

        # Trocando de processo: encerra o contexto do anterior antes de abrir.
        if self._autos_atual and self._autos_atual != idp:
            self._encerrar_contexto_autos()

        html, url, viewstate = self._abrir_autos(idp)
        estado = {
            "documentos": self._parsear_documentos(html),
            "html": html,
            "url": url,
            "viewstate": viewstate,
            "ts": _t.time(),
            "selecionado": None,
        }
        self._autos_cache[idp] = estado
        self._autos_atual = idp
        return estado

    def ler_autos(self, id_processo: str, forcar: bool = False) -> dict:
        """Abre os autos e extrai o índice completo de peças.

        Args:
            id_processo: idProcesso interno (idLegacy) do PJe.
            forcar: ignora o cache e reabre os autos no servidor.

        Returns:
            dict com:
              - 'documentos': lista de {id, tipo, componente} (cada peça)
              - 'html', 'url', 'viewstate': estado dos autos (uso interno)
        """
        return self._estado_autos(id_processo, forcar=forcar)

    def baixar_documento(self, id_processo: str, id_documento: str, incluir_bytes: bool = False) -> dict:
        """Lê o conteúdo de uma peça específica dos autos.

        Mecanismo (engenharia reversa do PJe 2.9): abre os autos, seleciona
        o documento via postback A4J e então baixa seu conteúdo pelo
        endpoint /documento/download/{idProcessoDocumento}.

        Args:
            id_processo: idProcesso interno.
            id_documento: idProcessoDocumento da peça (de ler_autos).

        Returns:
            dict {id, tipo, texto, mimetype, bytes_len}.
        """
        # Estado dos autos REUSADO (aberto uma única vez por processo).
        autos = self._estado_autos(id_processo)
        doc = next((d for d in autos["documentos"] if d["id"] == str(id_documento)), None)
        componente = doc["componente"] if doc else None
        tipo = doc["tipo"] if doc else ""

        # Seleciona o documento via postback A4J (torna-o o "ativo")
        if componente and componente != "ajaxSingle" and autos.get("selecionado") != componente:
            data = {
                "AJAXREQUEST": "_viewRoot",
                "divTimeLine": "divTimeLine",
                componente: componente,
                "ajaxSingle": componente,
                "javax.faces.ViewState": autos["viewstate"],
            }
            try:
                pb = self._session.post(
                    autos["url"], data=data,
                    headers={
                        "Faces-Request": "partial/ajax",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=30, allow_redirects=False,
                )
                if self._redirect_para_sso(pb) or self._pagina_eh_login(pb):
                    self._invalidar_autos(id_processo)
                    raise SessaoPJeExpirada(f"postback A4J da peça {id_documento}")
                # O JSF pode rotacionar o ViewState a cada postback; se veio um
                # novo na resposta, é ele que vale para a próxima peça.
                novo_vs = re.search(
                    r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"', pb.text or ""
                ) or re.search(
                    r'<update id="javax\.faces\.ViewState"><!\[CDATA\[([^\]]+)\]\]>', pb.text or ""
                )
                if novo_vs:
                    autos["viewstate"] = novo_vs.group(1)
                autos["selecionado"] = componente
            except SessaoPJeExpirada:
                raise
            except Exception as e:
                logger.debug(f"Postback A4J falhou para doc {id_documento}: {e}")

        # Baixa o conteúdo
        r = self._rest(
            "GET", f"{DOC_DOWNLOAD_URL}/{id_documento}",
            headers={"X-pje-legacy-app": "pje"},
        )
        if r.status_code != 200 or not r.content:
            raise RuntimeError(
                f"Não foi possível baixar a peça {id_documento} "
                f"(HTTP {r.status_code}). Algumas peças digitalizadas "
                f"(PDF/imagem antigos) podem exigir download manual."
            )
        ct = r.headers.get("content-type", "")
        body = r.content
        texto = self._extrair_texto(body, ct)
        out = {
            "id": str(id_documento), "tipo": tipo,
            "texto": texto, "mimetype": ct, "bytes_len": len(body),
        }
        if incluir_bytes:
            out["body"] = body  # bruto, para mídia (áudio/vídeo) — ver server.baixar_midia
        return out

    def _extrair_texto(self, body: bytes, content_type: str) -> str:
        """Extrai texto de HTML ou PDF."""
        ct = (content_type or "").lower()
        if body[:4] == b"%PDF" or "pdf" in ct:
            try:
                import io
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(body))
                return "\n".join((p.extract_text() or "") for p in reader.pages).strip()
            except Exception:
                return f"[PDF não extraível, {len(body)} bytes]"
        # HTML / texto
        try:
            txt = body.decode("utf-8", errors="replace")
        except Exception:
            txt = body.decode("iso-8859-1", errors="replace")
        if "<" in txt[:200] and ">" in txt[:400]:
            return BeautifulSoup(txt, "html.parser").get_text(" ", strip=True)
        return txt.strip()

    def listar_documentos_processo(self, numero_processo: str = "", id_processo: str = "") -> list:
        """Lista as peças do processo (índice de documentos dos autos).

        Args:
            numero_processo: CNJ (usado para resolver idProcesso via aba aberta).
            id_processo: idProcesso interno; se ausente, tenta resolver.
        """
        idp = id_processo or self.resolver_idprocesso(numero_processo)
        if not idp:
            return []
        return self.ler_autos(idp).get("documentos", [])

    def encerrar_leitura(self, id_processo: Optional[str] = None) -> None:
        """Fecha os autos abertos e devolve a sessão ao painel.

        Chamar ao TERMINAR a leitura de cada processo, para que o próximo
        comece com o contexto limpo. É o equivalente a fechar a aba dos autos
        no navegador — sem isso, o PJe segue com "autos abertos" na sessão.
        """
        self._encerrar_contexto_autos()
        self._invalidar_autos(id_processo)

    # ------------------------------------------------------------
    # Diagnóstico
    # ------------------------------------------------------------

    def status(self) -> dict:
        """Diagnóstico da sessão — VALIDADO contra a API, não contra cookies.

        Reportar "ativa" só porque há cookies no jar foi o que permitiu à
        rotina do gabinete seguir horas com a sessão morta. Aqui a resposta
        depende de uma chamada real ao painel do PJe.
        """
        viva, diag = self._probe_rest()
        oj = self._orgao_da_sessao() if viva else ""
        return {
            "logged_in": self._logged_in,
            "sessao_viva": viva,
            "diagnostico": diag,
            "perfil_chrome": self._perfil_ativo,
            "cookies_session": len(self._session.cookies),
            "autos_abertos": self._autos_atual,
            "orgao_julgador": oj,
            "orgao_alvo": ORGAO_ALVO,
            "orgao_ok": (not ORGAO_ALVO) or oj == ORGAO_ALVO,
        }
