from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TipoCorrespondenciaProduto = Literal[
    "PRODUTO_EXATO",
    "CATEGORIA_RELACIONADA",
    "SETOR_RELACIONADO",
    "NAO_COMPROVADO",
    "INCOMPATIVEL",
]

PapelComercial = Literal[
    "IMPORTADOR",
    "TRADING",
    "DISTRIBUIDOR",
    "ATACADISTA",
    "FABRICANTE",
    "VAREJISTA",
    "HORECA",
    "PRESTADOR_SERVICO",
    "OUTRO",
    "NAO_COMPROVADO",
]

CriterioEvidencia = Literal[
    "ADERENCIA_PRODUTO",
    "IMPORTACAO_COMPRA",
    "PRESENCA_MERCADO",
    "PERFIL_COMERCIAL",
    "QUALIDADE_DOMINIO",
]

TipoFonteEvidencia = Literal[
    "SITE_EMPRESA",
    "FONTE_OFICIAL",
    "FONTE_INDEPENDENTE",
    "DIRETORIO_COMERCIAL",
    "OUTRA",
]


class SchemaBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ContextoOrigem(SchemaBase):
    origem: str = Field(default="manual", max_length=40)
    score_diagnostico: float | None = Field(default=None, ge=0, le=100)
    mercados_recomendados: list[str] = Field(default_factory=list, max_length=20)


class CriarProspeccaoRequest(SchemaBase):
    hs6: str | None = None
    ncm: str | None = None
    nome_produto: str = Field(min_length=3, max_length=250)
    pais_alvo: str = Field(min_length=2, max_length=120)
    idioma_alvo: str = Field(default="Português", min_length=2, max_length=60)
    disponibilidade: str | None = Field(default=None, max_length=120)
    quantidade_disponivel: float | None = Field(default=None, gt=0)
    unidade_disponibilidade: str | None = Field(default=None, min_length=1, max_length=40)
    perfil_parceiro: str | None = Field(default=None, max_length=160)
    contexto_origem: ContextoOrigem | None = None

    @field_validator("ncm", "hs6", mode="before")
    @classmethod
    def normalizar_codigo(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return re.sub(r"\D", "", str(value))

    @field_validator("ncm")
    @classmethod
    def validar_ncm(cls, value: str | None) -> str | None:
        if value is not None and len(value) != 8:
            raise ValueError("NCM deve conter 8 dígitos.")
        return value

    @field_validator("hs6")
    @classmethod
    def validar_hs6(cls, value: str | None) -> str | None:
        if value is not None and len(value) != 6:
            raise ValueError("HS6 deve conter 6 dígitos.")
        return value


class FontePesquisa(SchemaBase):
    titulo: str = Field(min_length=2, max_length=300)
    url: str = Field(min_length=8, max_length=2_000)


class ContextoTarifario(SchemaBase):
    ncm: str | None = None
    hs6: str | None = None
    descricao_ncm: str | None = None
    ano_inicial: int | None = None
    ano_final: int | None = None
    operacoes: int | None = None
    kg_liquido: int | None = None
    valor_fob_usd: int | None = None
    valor_frete_usd: int | None = None
    valor_seguro_usd: int | None = None
    aliquota_media_ii: float | None = None
    observacao: str | None = None


class EvidenciaLead(SchemaBase):
    """Uma URL única pode comprovar vários critérios sem duplicação."""

    titulo: str = Field(min_length=2, max_length=300)
    url: str = Field(min_length=8, max_length=2_000)
    descricao: str = Field(min_length=5, max_length=800)
    criterios: list[CriterioEvidencia] = Field(min_length=1, max_length=5)
    tipo_fonte: TipoFonteEvidencia
    correspondencia_produto: TipoCorrespondenciaProduto = "NAO_COMPROVADO"
    papeis_comerciais: list[PapelComercial] = Field(default_factory=list, max_length=9)
    consultado_em: str | None = Field(default=None, max_length=40)


class ComponentesLeadScore(SchemaBase):
    aderencia_produto: int = Field(ge=0, le=100)
    presenca_mercado: int = Field(ge=0, le=100)
    evidencia_publica: int = Field(ge=0, le=100)
    qualidade_dominio: int = Field(ge=0, le=100)
    perfil_solicitado: int = Field(ge=0, le=100)


class ResultadoLeadScore(SchemaBase):
    componentes: ComponentesLeadScore
    score_total: int = Field(ge=0, le=100)
    classe: Literal["A", "B", "C"]
    justificativas: list[str] = Field(default_factory=list, max_length=5)


class LeadPotencial(SchemaBase):
    nome: str = Field(min_length=2, max_length=240)
    dominio: str | None = Field(default=None, max_length=500)
    site: str | None = Field(default=None, max_length=2_000)
    status: Literal[
        "potencial a validar",
        "site não informado",
        "site não verificado",
        "site verificado",
        "aderente validado",
        "parceiro regional a validar",
        "evidência parcial",
        "não aderente",
    ] = "potencial a validar"
    fonte: str = Field(default="Gemini + Google Search", max_length=160)
    emails_profissionais_disponiveis: int | None = Field(default=None, ge=0)
    validacao_produto: Literal["ADERENTE", "NAO_ADERENTE", "NAO_CONFIRMADA"] = "NAO_CONFIRMADA"
    correspondencia_produto: TipoCorrespondenciaProduto = "NAO_COMPROVADO"
    papeis_comerciais: list[PapelComercial] = Field(default_factory=list, max_length=9)
    importacao_produto_comprovada: bool = False
    justificativa_validacao: str | None = Field(default=None, max_length=800)
    evidencia_url: str | None = Field(default=None, max_length=2_000)
    evidencias: list[EvidenciaLead] = Field(default_factory=list, max_length=12)
    fontes_independentes: int = Field(default=0, ge=0, le=20)
    apto_para_abordagem: bool = False
    site_validado: bool = False
    motivo_validacao_site: str | None = Field(default=None, max_length=300)
    nivel_cobertura: Literal["LOCAL", "REGIONAL"] = "LOCAL"
    mercado_atendido: str | None = Field(default=None, max_length=160)
    dominio_oficial_confirmado: bool = False
    lead_score: ResultadoLeadScore | None = None


class PlanoAcao30Dias(SchemaBase):
    dias_1_7: list[str] = Field(min_length=2, max_length=4)
    dias_8_14: list[str] = Field(min_length=2, max_length=4)
    dias_15_21: list[str] = Field(min_length=2, max_length=4)
    dias_22_30: list[str] = Field(min_length=2, max_length=4)


class EmailComercial(SchemaBase):
    assunto: str = Field(min_length=3, max_length=240)
    corpo: str = Field(min_length=20, max_length=8_000)


class EmpresaSugerida(SchemaBase):
    nome: str = Field(min_length=2, max_length=240)
    justificativa: str = Field(min_length=10, max_length=400)
    site: str | None = Field(default=None, max_length=500)
    nivel_cobertura: Literal["LOCAL", "REGIONAL"]
    mercado_atendido: str = Field(min_length=2, max_length=160)


class FeiraEvento(SchemaBase):
    nome: str = Field(min_length=2, max_length=240)
    descricao: str = Field(min_length=10, max_length=600)
    localizacao: str | None = Field(default=None, max_length=240)
    periodicidade: str | None = Field(default=None, max_length=120)
    site: str | None = Field(default=None, max_length=2_000)


class FonteOficial(SchemaBase):
    nome: str = Field(min_length=2, max_length=240)
    finalidade: str = Field(min_length=10, max_length=600)
    url: str = Field(min_length=8, max_length=2_000)


class ConteudoComercial(SchemaBase):
    panorama_comercial: str = Field(min_length=80, max_length=8_000)
    feiras_eventos: list[FeiraEvento] = Field(default_factory=list, max_length=8)
    fontes_oficiais: list[FonteOficial] = Field(default_factory=list, max_length=8)
    plano_acao_30_dias: PlanoAcao30Dias
    email_comercial: EmailComercial
    empresas_sugeridas: list[EmpresaSugerida] = Field(default_factory=list, max_length=5)


class ProspeccaoResponse(SchemaBase):
    status: str = "sucesso"
    relatorio: str
    contexto_tarifario: ContextoTarifario | None = None
    fontes: list[FontePesquisa] = Field(default_factory=list)
    leads: list[LeadPotencial] = Field(default_factory=list)
    conteudo_comercial: ConteudoComercial | None = None
    aviso: str | None = None


class HealthResponse(SchemaBase):
    status: str = "ok"
    servico: str = "exportai-modulo-vendas"
