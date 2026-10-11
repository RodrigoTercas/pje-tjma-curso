"""MCP Server para integração com o PJe/TJMA 1º grau.

Arquitetura de acesso (toda via sessão web autenticada do Chrome):
- Fila/painel do magistrado: REST pje-legacy (painelUsuario/tarefas).
- Capa do processo: API MNI-REST (api/v1/processos-judiciais/{cnj}).
- Leitura das peças (autos digitais): JSF listAutosDigitais.seam +
  postback A4J para selecionar a peça + /documento/download/{id}.
- Resolução CNJ→idProcesso interno: lê a aba de autos aberta no Chrome.
- Minutas: geração local a partir da capa + peças + instruções.

O webservice SOAP/MNI clássico (intercomunicacao?wsdl) está bloqueado
pelo TJMA (HTTP 403); por isso TODA a integração usa o cliente web.
"""

import functools
import logging
import sys
from typing import Optional

try:
    # Rede de tribunal costuma inspecionar o TLS com certificado próprio, que
    # só o repositório de certificados do sistema operacional conhece.
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from fastmcp import FastMCP

from pje_web_client import PJeWebClient, SessaoPJeExpirada

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("pje-mcp")

INSTRUCOES = (
    "Servidor do PJe (TJMA, 1º grau). Antes de ler fila, autos ou peças, chame "
    "status_conexao. Resposta iniciada por 🛑 significa que NADA foi lido: não a "
    "trate como fila vazia, processo inexistente ou autos sem peças.\n"
    "Credenciais: nunca peça, aceite ou repita senha, CPF ou chave do autenticador "
    "na conversa, e nunca abra nem leia os arquivos do cofre (.pje_usuario, "
    ".pje_senha, .pje_totp.secret). Para gravar ou trocar credencial, chame "
    "abrir_cofre (abre uma janela local com campos velados) e, quando o usuário "
    "disser que salvou, chame status_conexao UMA única vez. Exceção: se ele estiver "
    "cadastrando ou trocando o aplicativo autenticador, só chame status_conexao "
    "depois de ele dizer que CONFIRMOU o código no PJe. Não insista em login: o PJe "
    "bloqueia a conta depois de poucas recusas.\n"
    "Assinar e protocolar são atos pessoais do usuário: nunca os execute."
)

mcp = FastMCP(name="PJe TJMA - 1º Grau", instructions=INSTRUCOES)

_web_client: Optional[PJeWebClient] = None


def get_web() -> PJeWebClient:
    global _web_client
    if _web_client is None:
        _web_client = PJeWebClient()
        logger.info("Cliente Web PJe inicializado.")
    return _web_client


def _guarda_sessao(fn):
    """Converte SessaoPJeExpirada numa resposta INEQUÍVOCA para o assistente.

    Sem isto, uma sessão morta chegava ao agente disfarçada de "nenhum
    processo no grupo" / "0 peças nos autos" — e a rotina de triagem seguia
    adiante gravando conclusões sobre dados que nunca foram lidos.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SessaoPJeExpirada as e:
            logger.warning(f"{fn.__name__}: sessão degradada — {e}")
            return (
                f"🛑 {e}\n\n"
                "NÃO registre este processo como analisado, sem peças ou fora da fila: "
                "a leitura não aconteceu. Interrompa a rotina, restabeleça a sessão e "
                "confirme com status_conexao antes de prosseguir."
            )
    return wrapper


def _resolver_id(web: PJeWebClient, numero_ou_id: str) -> tuple[str, str]:
    """Resolve a entrada (CNJ ou idProcesso) para (idProcesso, numeroCNJ).

    - Se vier só dígitos curtos (<14), trata como idProcesso interno.
    - Se vier um CNJ, resolve via aba de autos aberta no Chrome.
    """
    valor = (numero_ou_id or "").strip()
    so_digitos = valor.replace("-", "").replace(".", "")
    # idProcesso interno costuma ter <= 9 dígitos; CNJ tem 20
    if so_digitos.isdigit() and len(so_digitos) <= 12:
        idp = valor
        try:
            cnj = web.resumo_processo(idp).get("numeroProcesso", "")
        except Exception:
            cnj = ""
        return idp, cnj
    # É um CNJ
    idp = web.resolver_idprocesso(valor)
    return idp, valor


# ============================================================
# PAINEL DO MAGISTRADO
# ============================================================


@mcp.tool()
@_guarda_sessao
def listar_conclusos(tipo: str = "") -> str:
    """Lista a fila real de conclusos do gabinete, agrupada por tarefa.

    Mostra cada tipo de tarefa pendente (ex.: "Concluso para decisão",
    "Concluso para sentença", "Minutar decisão") com a quantidade de
    processos. Use listar_processos_bucket(nome) para ver os processos
    de um grupo específico (com CNJ e partes).

    Args:
        tipo: filtra por "despacho", "decisao" ou "sentenca". Vazio = todos.
    """
    logger.info(f"Listando conclusos (tipo={tipo or 'todos'})")
    web = get_web()
    conclusos = web.listar_conclusos(tipo=tipo or None)

    if not conclusos:
        return (
            "Nenhum bucket de conclusos pendente retornado.\n"
            "Se você sabe que há conclusos, a sessão pode ter expirado — "
            "confira com status_conexao e tente de novo."
        )

    total = web.total_fila_painel()
    linhas = [f"=== FILA DO GABINETE — {total} processo(s) conclusos ===", ""]
    for proc in conclusos:
        qt = proc.assunto.replace(" processo(s) pendente(s)", "")
        linhas.append(f"  • {proc.classe}: {qt}")
    linhas.append("")
    linhas.append(
        'Para ver os processos de um grupo: listar_processos_bucket("nome do grupo").'
    )
    return "\n".join(linhas)


def _fmt_processo_fila(p: dict, i: int) -> str:
    prio = " ⚠️PRIORIDADE" if p.get("prioridade") else ""
    sig = " 🔒SIGILOSO" if p.get("sigiloso") else ""
    linhas = [
        f"{i}. {p.get('numeroProcesso','?')} [{p.get('classeJudicial','')}]{prio}{sig}",
        f"     Autor: {p.get('poloAtivo','')}  ×  Réu: {p.get('poloPassivo','')}",
    ]
    if p.get("assuntoPrincipal"):
        linhas.append(f"     Assunto: {p['assuntoPrincipal']}")
    if p.get("descricaoUltimoMovimento"):
        linhas.append(f"     Último mov.: {p['descricaoUltimoMovimento']}")
    linhas.append(f"     idProcesso={p.get('idProcesso')}")
    return "\n".join(linhas)


@mcp.tool()
@_guarda_sessao
def listar_processos_bucket(nome_grupo: str, pagina: int = 0) -> str:
    """Lista os processos de um grupo de conclusos (com CNJ, partes, assunto).

    Resolve a fila → processos. Cada item traz CNJ, partes, classe, assunto
    e idProcesso (use em analisar_processo). O nome do grupo deve ser exato
    (de listar_conclusos), ex.: "Concluso para decisão", "Concluso para
    sentença", "(CRIM-APN) Concluso para decisão".

    Args:
        nome_grupo: nome exato do bucket de conclusos.
        pagina: página (0-based), 30 por página.
    """
    logger.info(f"Listando processos do bucket: {nome_grupo} (pág {pagina})")
    web = get_web()
    procs = web.processos_da_tarefa(nome_grupo, pagina=pagina, max_results=30)
    if not procs:
        return (
            f"Nenhum processo no grupo '{nome_grupo}' (verifique o nome exato "
            f"em listar_conclusos) ou página fora do intervalo."
        )
    linhas = [f"=== {nome_grupo} — {len(procs)} processo(s) (pág {pagina}) ==="]
    for i, p in enumerate(procs, 1 + pagina * 30):
        linhas.append(_fmt_processo_fila(p, i))
    linhas.append("\nUse analisar_processo(CNJ ou idProcesso) para ler um deles.")
    return "\n".join(linhas)


@mcp.tool()
@_guarda_sessao
def estatisticas_painel() -> str:
    """Resumo estatístico da fila do gabinete por tipo de ato.

    Total de conclusos e distribuição entre despacho, decisão e sentença,
    com os maiores grupos de tarefa.
    """
    logger.info("Obtendo estatísticas do painel")
    web = get_web()
    stats = web.obter_estatisticas_painel()

    linhas = [
        "=== PAINEL DO MAGISTRADO ===",
        "",
        f"📊 TOTAL DE CONCLUSOS:  {stats.get('total_conclusos', 0)}",
        "",
        f"  ⚖️  Decisão:   {stats.get('conclusos_decisao', 0)}",
        f"  📋 Despacho:  {stats.get('conclusos_despacho', 0)}",
        f"  📜 Sentença:  {stats.get('conclusos_sentenca', 0)}",
        f"  • Outros:    {stats.get('outros', 0)}",
    ]
    buckets = stats.get("buckets", [])
    if buckets:
        linhas.append("")
        linhas.append("Maiores grupos:")
        for b in buckets[:8]:
            linhas.append(f"  {b['quantidade']:>3} — {b['nome']}")
    if stats.get("erro"):
        linhas.append(f"\n⚠️ {stats['erro']}")
    return "\n".join(linhas)


# ============================================================
# CONSULTA E LEITURA DE PROCESSO
# ============================================================


@mcp.tool()
@_guarda_sessao
def consultar_processo(numero_processo: str) -> str:
    """Consulta a capa de um processo pelo número CNJ.

    Retorna classe, assuntos, partes (autor/réu com advogados), órgão
    julgador, valor da causa e data de ajuizamento.

    Args:
        numero_processo: Número CNJ (ex: 0000000-00.0000.8.10.0000)
    """
    logger.info(f"Consultando processo: {numero_processo}")
    web = get_web()
    dados = web.consultar_processo(numero_processo)
    proc = dados["processo"]

    resultado = proc.resumo()
    if proc.polo_ativo or proc.polo_passivo:
        resultado += "\n\n--- PARTES ---\n"
        for p in proc.polo_ativo + proc.polo_passivo:
            resultado += f"  {p.resumo()}\n"
    return resultado


@mcp.tool()
@_guarda_sessao
def processos_abertos() -> str:
    """Lista os processos cujos autos estão abertos agora no Chrome.

    Útil para o magistrado: basta abrir o processo no PJe e o assistente
    passa a poder lê-lo (resolução automática do idProcesso interno).
    """
    web = get_web()
    web.login()
    abertos = web.processos_abertos()
    if not abertos:
        if sys.platform != "darwin":
            return (
                "A leitura das abas do Chrome só existe no macOS. Neste sistema, "
                "informe o CNJ de um processo que esteja na fila ou o idProcesso."
            )
        return (
            "Nenhuma aba de autos digitais aberta no Chrome.\n"
            "Abra o processo no PJe (botão de autos) e tente novamente."
        )
    linhas = ["=== Processos abertos nos autos (Chrome) ==="]
    for a in abertos:
        try:
            cnj = web.resumo_processo(a["idProcesso"]).get("numeroProcesso", "?")
        except Exception:
            cnj = "?"
        linhas.append(f"  • {cnj}  (idProcesso={a['idProcesso']})")
    return "\n".join(linhas)


@mcp.tool()
@_guarda_sessao
def listar_documentos_processo(numero_processo: str = "") -> str:
    """Lista as peças (índice de documentos) dos autos de um processo.

    Args:
        numero_processo: CNJ ou idProcesso interno. Se vazio, usa o
            processo aberto no Chrome.
    """
    logger.info(f"Listando documentos: {numero_processo or '(aba aberta)'}")
    web = get_web()
    idp, cnj = _resolver_id(web, numero_processo)
    if not idp:
        return (
            "Não consegui resolver o processo. Abra os autos no PJe (Chrome) "
            "ou informe o idProcesso interno."
        )
    docs = web.ler_autos(idp).get("documentos", [])
    if not docs:
        return f"Nenhuma peça encontrada nos autos (idProcesso={idp})."
    linhas = [f"=== Peças de {cnj or idp} ({len(docs)}) ==="]
    for i, d in enumerate(docs, 1):
        linhas.append(f"{i:>3}. [id {d['id']}] {d['tipo']}")
    linhas.append("\nUse ler_peca(numero, id_documento) para o conteúdo de uma peça.")
    return "\n".join(linhas)


@mcp.tool()
@_guarda_sessao
def ler_peca(numero_processo: str, id_documento: str) -> str:
    """Lê o conteúdo textual de uma peça específica dos autos.

    Args:
        numero_processo: CNJ ou idProcesso interno (ou vazio = aba aberta).
        id_documento: idProcessoDocumento da peça (de listar_documentos_processo).
    """
    logger.info(f"Lendo peça {id_documento} de {numero_processo}")
    web = get_web()
    idp, cnj = _resolver_id(web, numero_processo)
    if not idp:
        return "Não consegui resolver o processo. Abra os autos no PJe ou informe o idProcesso."
    r = web.baixar_documento(idp, id_documento)
    return (
        f"=== {r['tipo']} (id {r['id']}) — {cnj or idp} ===\n\n{r['texto']}"
    )


@mcp.tool()
@_guarda_sessao
def baixar_midia(numero_processo: str, id_documento: str) -> str:
    """Baixa uma peça de MÍDIA (áudio/vídeo) dos autos para disco local, sem
    tentar extrair texto. Para degravação com Whisper local.

    Grava em <pasta de mídia>/<CNJ>/<id>.<ext> e devolve o caminho, o mimetype
    e o tamanho. A pasta vem de PJE_MIDIA_DIR (padrão: ~/pje-midia). Recusa
    HTML (página de erro) e não substitui arquivo já existente. Só leitura no PJe.

    Args:
        numero_processo: CNJ ou idProcesso interno.
        id_documento: idProcessoDocumento da peça (de listar_documentos_processo).
    """
    import mimetypes, os, pathlib, re as _re
    if not str(id_documento).strip().isdigit():
        return "id_documento deve ser numérico (use o id listado em listar_documentos_processo)."
    web = get_web()
    idp, cnj = _resolver_id(web, numero_processo)
    if not idp:
        return "Não consegui resolver o processo. Informe o CNJ ou o idProcesso."
    r = web.baixar_documento(idp, id_documento, incluir_bytes=True)
    ct = (r.get("mimetype") or "").split(";")[0].strip().lower()
    body = r.get("body") or b""
    if not body:
        return f"Peça {id_documento} veio vazia (HTTP ok, 0 bytes)."
    if ct.startswith("text/html") or body[:15].lower().lstrip().startswith(b"<!doctype html") or body[:6].lower() == b"<html>":
        return f"Peça {id_documento} devolveu HTML ({len(body)} bytes), não mídia — provável página de erro ou peça textual. Use ler_peca."
    # ponytail: extensão por mimetype, fallback pela assinatura dos bytes
    ext = mimetypes.guess_extension(ct) or ""
    if not ext:
        if body[4:8] == b"ftyp": ext = ".mp4"
        elif body[:4] == b"OggS": ext = ".ogg"
        elif body[:3] == b"ID3" or body[:2] in (b"\xff\xfb", b"\xff\xf3"): ext = ".mp3"
        elif body[:4] == b"RIFF": ext = ".wav"
        elif body[:4] == b"%PDF": ext = ".pdf"
        else: ext = ".bin"
    # Sem PJE_MIDIA_DIR, mantém a pasta do gabinete onde ela já existe.
    gabinete = pathlib.Path.home() / "Documents/04 - Tecnologia e IA/Projetos de Código/sistemas/agentes-gabinete"
    base = pathlib.Path(os.getenv("PJE_MIDIA_DIR") or (
        gabinete / "audiencias/midia" if gabinete.is_dir() else pathlib.Path.home() / "pje-midia"))
    pasta = base / _re.sub(r"[^0-9.\-]", "_", cnj or str(idp))
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"{str(id_documento).strip()}{ext}"
    if destino.exists():
        return f"Já existe: {destino} ({destino.stat().st_size} bytes). Não sobrescrevi."
    destino.write_bytes(body)
    whisper = pathlib.Path.home() / ".claude/skills/whisper-transcribe/scripts/transcribe_with_context.py"
    dica = f'\nDegravar com: python3 "{whisper}" "{destino}"' if whisper.exists() else ""
    return (f"Mídia salva: {destino}\nmimetype: {ct or 'desconhecido'} · {len(body)} bytes · "
            f"tipo no índice: {r.get('tipo') or '—'}{dica}")


@mcp.tool()
@_guarda_sessao
def analisar_processo(numero_processo: str = "", max_pecas: int = 8) -> str:
    """Analisa um processo: capa + índice de peças + conteúdo das principais.

    Ferramenta central para "analisar e decidir". Resolve o processo
    (CNJ, idProcesso ou aba aberta no Chrome), traz a capa, lista as peças
    e lê o conteúdo das mais relevantes (decisões, sentenças, petições,
    laudos, manifestações do MP).

    Args:
        numero_processo: CNJ ou idProcesso interno. Vazio = processo aberto no Chrome.
        max_pecas: quantidade de peças cujo conteúdo será incluído (padrão 8).
    """
    logger.info(f"Analisando processo: {numero_processo or '(aba aberta)'}")
    web = get_web()
    idp, cnj = _resolver_id(web, numero_processo)
    if not idp:
        return (
            "Não consegui resolver o processo. Abra os autos no PJe (Chrome) "
            "e use processos_abertos, ou informe o idProcesso interno."
        )

    partes = []

    # Capa (MNI) — pode falhar para alguns juízos; não é fatal
    if cnj:
        try:
            proc = web.consultar_processo(cnj)["processo"]
            cap = proc.resumo()
            if proc.polo_ativo or proc.polo_passivo:
                cap += "\n\n--- PARTES ---\n"
                for p in proc.polo_ativo + proc.polo_passivo:
                    cap += f"  {p.resumo()}\n"
            partes.append(cap)
        except Exception as e:
            partes.append(f"(capa MNI indisponível: {e})")

    # Índice de peças
    autos = web.ler_autos(idp)
    docs = autos.get("documentos", [])
    partes.append(f"\n=== {len(docs)} PEÇAS NOS AUTOS ===")
    for i, d in enumerate(docs, 1):
        partes.append(f"{i:>3}. [id {d['id']}] {d['tipo']}")

    # Conteúdo das peças mais relevantes
    prioridade = ("sentença", "decisão", "petição", "laudo", "parecer",
                  "manifesta", "denúncia", "contestação", "inicial", "ata",
                  "despacho", "ofício")
    def score(d):
        t = d["tipo"].lower()
        for i, k in enumerate(prioridade):
            if k in t:
                return i
        return len(prioridade)
    relevantes = sorted(docs, key=score)[:max_pecas]

    partes.append(f"\n=== CONTEÚDO DAS {len(relevantes)} PEÇAS PRINCIPAIS ===")
    try:
        for d in relevantes:
            try:
                r = web.baixar_documento(idp, d["id"])
                texto = (r["texto"] or "").strip()
                if len(texto) > 6000:
                    texto = texto[:6000] + "\n[...truncado...]"
                partes.append(f"\n----- {r['tipo']} (id {r['id']}) -----\n{texto}")
            except SessaoPJeExpirada:
                raise  # peça ilegível é um caso; sessão morta é outro
            except Exception as e:
                partes.append(f"\n----- {d['tipo']} (id {d['id']}) -----\n(não lido: {e})")
    finally:
        # Fecha os autos e volta ao painel: sem isso o PJe segue com "autos
        # abertos" na sessão e o PRÓXIMO processo da fila falha ao resolver.
        # É o que permite ler N processos em série numa mesma execução.
        web.encerrar_leitura(idp)

    return "\n".join(partes)


# ============================================================
# MINUTAS (geração local — não protocola)
# ============================================================


@mcp.tool()
def minutar_despacho(numero_processo: str = "", instrucoes: str = "") -> str:
    """Prepara o CONTEXTO (capa + peças) para uma minuta de despacho.

    Não protocola nada. Traz os autos para o assistente redigir. Use a
    skill 'magistrado-otimizado' para a redação e 'jarvis-revisor-judicial'
    para revisar antes de protocolar manualmente no PJe.

    Args:
        numero_processo: CNJ ou idProcesso (vazio = aba aberta no Chrome).
        instrucoes: Diretrizes (ex: "intimar autor para emendar a inicial").
    """
    logger.info(f"Contexto p/ minuta de despacho: {numero_processo}")
    ctx = analisar_processo(numero_processo, max_pecas=6)
    return (
        f"=== CONTEXTO PARA MINUTA DE DESPACHO ===\n{ctx}\n\n"
        f"--- INSTRUÇÕES ---\n"
        f"{instrucoes or 'Elaborar despacho adequado à fase processual.'}\n\n"
        f"NOTA: minuta a redigir pelo assistente; revise e protocole manualmente."
    )


@mcp.tool()
def minutar_decisao(numero_processo: str = "", instrucoes: str = "") -> str:
    """Prepara o CONTEXTO (capa + peças) para uma minuta de decisão/sentença.

    Args:
        numero_processo: CNJ ou idProcesso (vazio = aba aberta no Chrome).
        instrucoes: Diretrizes (ex: "deferir tutela", "sentença de extinção").
    """
    logger.info(f"Contexto p/ minuta de decisão: {numero_processo}")
    ctx = analisar_processo(numero_processo, max_pecas=10)
    return (
        f"=== CONTEXTO PARA MINUTA DE DECISÃO/SENTENÇA ===\n{ctx}\n\n"
        f"--- INSTRUÇÕES ---\n"
        f"{instrucoes or 'Elaborar decisão fundamentada com base nos autos.'}\n\n"
        f"NOTA: minuta a redigir pelo assistente; revise e protocole manualmente."
    )


# ============================================================
# PREPARAR MINUTA NO PJE (abre o editor — NÃO assina/protocola)
# ============================================================


@mcp.tool()
@_guarda_sessao
def preparar_minuta_no_pje(numero_processo: str, conteudo: str) -> str:
    """Abre o editor de minuta do PJe no processo certo, com o texto pronto.

    Coloca a minuta na área de transferência e abre o editor de ato do
    próprio PJe (no Chrome logado), já no processo/tarefa correspondente.
    Você revisa, cola (Cmd+V) e ASSINA na interface validada do PJe.

    IMPORTANTE: esta ferramenta NÃO salva e NÃO assina nada. Ela só abre o
    editor e disponibiliza o texto — o salvar/assinar é ato pessoal seu,
    feito por você no PJe. Nenhum dado é gravado nos autos por aqui.

    Args:
        numero_processo: CNJ do processo (deve estar concluso na sua fila).
        conteudo: texto da minuta (revisado) a ser inserido no ato.
    """
    import os, subprocess
    logger.info(f"Preparando minuta no PJe: {numero_processo}")
    web = get_web()
    web.login()

    proc = web.localizar_na_fila(numero_processo)
    if not proc:
        return (
            f"Não encontrei {numero_processo} concluso na sua fila. "
            f"Confirme o CNJ em listar_conclusos / listar_processos_bucket. "
            f"(O editor de minuta abre a partir de um processo concluso.)"
        )

    iti = proc.get("idTaskInstance")
    url = web.url_editor_minuta(str(iti))

    # Coloca o texto na área de transferência (local; não toca no PJe)
    try:
        if sys.platform.startswith("win"):
            # O clip.exe só preserva acentos se receber UTF-16 com BOM.
            subprocess.run(["clip"], input=conteudo.encode("utf-16"), timeout=10, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            subprocess.run(["pbcopy"], input=conteudo, text=True, timeout=10, check=True)
        clip_ok = True
    except Exception as e:
        logger.debug(f"cópia para a área de transferência falhou: {e}")
        clip_ok = False

    # Sem o modo cookies, o navegador não compartilha a sessão do servidor:
    # abrir o editor ali cairia na tela de login e, logando, derrubaria esta sessão.
    sem_navegador = bool(os.getenv("PJE_SEM_COOKIES"))

    # Abre o editor na JANELA LOGADA do Chrome (a frontmost, mesmo perfil da
    # sessão do magistrado). Usar `open -a` cairia no perfil padrão, que pode
    # não ser o logado — por isso navegamos via AppleScript na janela ativa.
    aberto = False
    if not sem_navegador:
        try:
            script = (
                'tell application "Google Chrome"\n'
                '  activate\n'
                '  if (count of windows) = 0 then make new window\n'
                f'  tell front window to make new tab with properties {{URL:"{url}"}}\n'
                'end tell'
            )
            subprocess.run(["osascript", "-e", script], timeout=15, check=True)
            aberto = True
        except Exception as e:
            logger.debug(f"osascript open falhou: {e}")
            try:
                import webbrowser
                aberto = webbrowser.open(url)
            except Exception:
                pass
    colar = "Cmd+V" if sys.platform == "darwin" else "Ctrl+V"

    linhas = [
        f"📝 Editor de minuta preparado para {proc.get('numeroProcesso')}",
        f"   Tarefa: {proc.get('_bucket')}  |  {proc.get('poloAtivo')} × {proc.get('poloPassivo')}",
        "",
    ]
    if sem_navegador:
        linhas += [
            f"➡️ Endereço do editor: {url}",
            "⚠️ Neste computador o navegador não compartilha a sessão do servidor. Ao "
            "entrar no PJe pelo navegador, a sessão do servidor cai (o PJe admite uma "
            "sessão por usuário) e ele entra de novo sozinho na próxima consulta.",
        ]
    elif aberto:
        linhas.append("✅ Editor do PJe ABERTO no navegador, no processo certo.")
    else:
        linhas.append(f"➡️ Abra no navegador: {url}")
    if clip_ok:
        linhas.append(f"✅ Texto da minuta na ÁREA DE TRANSFERÊNCIA — cole com {colar}.")
    else:
        linhas.append("⚠️ Não consegui copiar o texto; copie-o manualmente da conversa.")
    linhas += [
        "",
        f"No editor do PJe: cole ({colar}) → revise → ASSINE.",
        "Nada foi salvo ou assinado automaticamente — o ato é seu.",
    ]
    return "\n".join(linhas)


# ============================================================
# DIAGNÓSTICO
# ============================================================


def _como_restabelecer() -> str:
    import os
    chrome = "" if os.getenv("PJE_SEM_COOKIES") else " ou faça o login no PJe pelo Chrome"
    return f"Confira o cofre com abrir_cofre{chrome} e chame status_conexao de novo."


@mcp.tool()
def status_conexao() -> str:
    """Verifica o estado REAL da sessão com o PJe-TJMA.

    Não se contenta com a existência de cookies: faz uma chamada autenticada
    à API do painel (a mesma camada que fila, autos e peças usam) e só
    reporta "ativa" se ela responder dados. Se a sessão tiver degradado,
    tenta UMA reconexão (cookies do Chrome e, se o cofre estiver completo e o
    login automático ligado, usuário + senha + código do autenticador) e
    informa o resultado. Se o motivo for credencial, use abrir_cofre.

    Motivo da checagem real: as páginas .seam do PJe seguem respondendo 200
    depois que a sessão OAuth morre, e só a API mostra o estado verdadeiro.
    """
    web = get_web()
    try:
        web.login()
    except SessaoPJeExpirada as e:
        return f"🛑 {e}"
    except Exception as e:
        return f"❌ Sessão indisponível: {e}\n\n{_como_restabelecer()}"

    st = web.status()
    if not st["sessao_viva"]:
        logger.warning(f"Sessão degradada detectada: {st['diagnostico']}")
        # Rede fora ou PJe com erro 5xx não se conserta logando de novo, e um
        # segundo login reenviaria a senha e o mesmo código do autenticador.
        import re as _re
        passageiro = (st["diagnostico"].startswith("erro de rede")
                      or _re.search(r"HTTP 5\d\d", st["diagnostico"]))
        recuperou = False if passageiro else web._recuperar_sessao()
        st = web.status() if recuperou else st
        if not st["sessao_viva"]:
            conserto = ("Falha passageira da rede ou do PJe: não mexa no cofre; tente "
                        "status_conexao de novo daqui a alguns minutos."
                        if passageiro else _como_restabelecer())
            return (
                "🛑 SESSÃO DEGRADADA — há cookies, mas a API do PJe NÃO responde.\n"
                f"   Diagnóstico: {st['diagnostico']}\n"
                f"   Origem da sessão: {st['perfil_chrome']} | "
                f"cookies: {st['cookies_session']}\n\n"
                "Qualquer leitura agora devolveria 'fila vazia' ou '0 peças' sem que "
                "nada tenha sido lido. NÃO prossiga com a triagem.\n"
                f"{conserto} Só siga quando status_conexao responder ✅."
            )
        logger.info("Sessão restabelecida pela releitura dos cookies do Chrome.")

    oj = st["orgao_julgador"] or "(não informado)"
    if not st["orgao_ok"]:
        oj += f"  ⚠️ diferente do órgão-alvo ({st['orgao_alvo']})"

    abertos = web.processos_abertos()
    return "\n".join([
        "✅ Conexão com o PJe-TJMA ativa (verificada contra a API, não só cookies).",
        f"   Verificação: {st['diagnostico']}",
        f"   Origem da sessão: {st['perfil_chrome']}",
        f"   Cookies na sessão: {st['cookies_session']}",
        f"   Órgão julgador da sessão: {oj}",
        f"   Autos abertos no Chrome: {len(abertos)}",
    ])


_janela_cofre = None


@mcp.tool()
def abrir_cofre() -> str:
    """Abre, no computador do usuário, a janela do cofre do PJe.

    A janela tem campos velados para usuário, senha e chave do autenticador,
    e grava tudo localmente. Use quando status_conexao disser que falta
    configurar, que a credencial foi recusada ou que há trava de proteção, e
    quando o usuário pedir para gravar ou trocar senha ou chave.

    NUNCA peça senha, CPF ou chave na conversa: é para isso que a janela existe.
    Esta ferramenta retorna em poucos segundos, sem esperar o usuário. Quando
    ele disser que salvou, chame status_conexao UMA única vez. Exceção: no
    cadastro ou na troca do aplicativo autenticador, espere ele dizer que
    CONFIRMOU o código no PJe.
    """
    global _janela_cofre
    import os, subprocess, threading
    import cofre

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cofre.py")
    if _janela_cofre is not None and _janela_cofre.poll() is None:
        situacao = "A janela do cofre já está aberta."
    else:
        try:
            # stdout precisa ir para DEVNULL: o do servidor é o canal do protocolo MCP.
            _janela_cofre = subprocess.Popen(
                [sys.executable, script],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            return f"❌ Não consegui abrir a janela do cofre: {type(e).__name__}: {e}"
        try:
            codigo = _janela_cofre.wait(timeout=3)  # morreu na partida?
        except subprocess.TimeoutExpired:
            codigo = None
        if codigo:
            return (
                f"❌ A janela do cofre não abriu (o processo saiu com código {codigo}). "
                f"Para ver o erro, rode num terminal: \"{sys.executable}\" \"{script}\""
            )
        threading.Thread(target=_janela_cofre.wait, daemon=True).start()
        situacao = ("Janela do cofre aberta no computador do usuário. Ela pode surgir "
                    "atrás das outras: peça que clique dentro dela antes de digitar.")

    e = cofre.estado(os.path.expanduser("~"))
    linhas = [situacao, "", "O que já está gravado (sem valores):"]
    for chave, rotulo in cofre.ROTULOS.items():
        item = e["itens"][chave]
        linhas.append(f"  • {rotulo}: " + (f"gravado em {item['em']}" if item["gravado"] else "FALTA"))
    linhas.append("  • Login automático: " + ("ligado" if e["auto_login"] else "desligado"))
    linhas += [
        "",
        "Peça ao usuário que preencha o que falta (campo em branco mantém o que já "
        "existe) e clique em 'Salvar no cofre'. Não peça credenciais na conversa. "
        "Quando ele disser que salvou, chame status_conexao uma única vez; se ele "
        "estiver cadastrando ou trocando o autenticador, só depois de ele dizer que "
        "confirmou o código no PJe.",
    ]
    return "\n".join(linhas)


if __name__ == "__main__":
    logger.info("Iniciando MCP Server PJe/TJMA...")
    mcp.run(transport="stdio")
