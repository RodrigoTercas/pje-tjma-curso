"""Modelos de dados para processos e documentos do PJe."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Documento:
    """Representa um documento/peça processual."""
    id: str
    tipo: str
    descricao: str
    data: str
    mimetype: str = "application/pdf"
    conteudo_base64: Optional[str] = None

    def resumo(self) -> str:
        return f"[{self.tipo}] {self.descricao} ({self.data}) - ID: {self.id}"


@dataclass
class Parte:
    """Representa uma parte do processo."""
    nome: str
    tipo_parte: str  # autor, réu, terceiro, etc.
    documento: str = ""  # CPF/CNPJ
    advogados: list[str] = field(default_factory=list)

    def resumo(self) -> str:
        advs = ", ".join(self.advogados) if self.advogados else "sem advogado"
        return f"{self.tipo_parte}: {self.nome} ({advs})"


@dataclass
class Processo:
    """Representa um processo judicial."""
    numero: str
    classe: str
    assuntos: list[str] = field(default_factory=list)
    orgao_julgador: str = ""
    polo_ativo: list[Parte] = field(default_factory=list)
    polo_passivo: list[Parte] = field(default_factory=list)
    documentos: list[Documento] = field(default_factory=list)
    valor_causa: float = 0.0
    data_ajuizamento: str = ""
    situacao: str = ""
    prioridade: str = ""

    def resumo(self) -> str:
        autores = ", ".join(p.nome for p in self.polo_ativo) or "N/I"
        reus = ", ".join(p.nome for p in self.polo_passivo) or "N/I"
        assuntos_str = ", ".join(self.assuntos) or "N/I"
        return (
            f"Processo: {self.numero}\n"
            f"Classe: {self.classe}\n"
            f"Assuntos: {assuntos_str}\n"
            f"Autor(es): {autores}\n"
            f"Réu(s): {reus}\n"
            f"Órgão Julgador: {self.orgao_julgador}\n"
            f"Valor da causa: R$ {self.valor_causa:,.2f}\n"
            f"Data de ajuizamento: {self.data_ajuizamento}\n"
            f"Situação: {self.situacao}\n"
            f"Prioridade: {self.prioridade}"
        )


@dataclass
class Aviso:
    """Representa um aviso/intimação pendente."""
    id: str
    processo_numero: str
    tipo: str
    data_disponibilizacao: str
    prazo: str = ""
    situacao: str = ""
    teor: str = ""

    def resumo(self) -> str:
        return (
            f"Aviso {self.id} - Processo {self.processo_numero}\n"
            f"Tipo: {self.tipo} | Data: {self.data_disponibilizacao}\n"
            f"Prazo: {self.prazo} | Situação: {self.situacao}"
        )
