from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ==========================================================
# CONTEXTO ENTRE MÓDULOS
# ==========================================================

class ContextoOrigem(BaseModel):
    """Contexto opcional vindo de outro módulo."""

    model_config = ConfigDict(extra="forbid")

    origem: str = Field(default="manual", max_length=40)

    score_diagnostico: float | None = Field(
        default=None,
        ge=0,
        le=100,
    )

    mercados_recomendados: list[str] = Field(
        default_factory=list,
        max_length=20,
    )


# ==========================================================
# REQUEST
# ==========================================================

class CriarProspeccaoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hs6: str | None = None
    ncm: str | None = None

    nome_produto: str = Field(
        min_length=3,
        max_length=250,
    )

    pais_alvo: str = Field(
        min_length=2,
        max_length=120,
    )

    idioma_alvo: str = Field(
        default="Português",
        min_length=2,
        max_length=60,
    )

    disponibilidade: str | None = Field(
        default=None,
        max_length=120,
    )

    perfil_parceiro: str | None = Field(
        default=None,
        max_length=160,
    )

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


# ==========================================================
# FONTES DE PESQUISA
# ==========================================================

class FontePesquisa(BaseModel):
    titulo: str
    url: str


# ==========================================================
# CONTEXTO TARIFÁRIO
# ==========================================================

class ContextoTarifario(BaseModel):
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


# ==========================================================
# FEIRAS E EVENTOS
# ==========================================================

class FeiraEvento(BaseModel):
    nome: str = Field(
        min_length=2,
        max_length=250,
    )

    descricao: str = Field(
        min_length=10,
        max_length=500,
    )

    localizacao: str | None = Field(
        default=None,
        max_length=160,
    )

    periodicidade: str | None = Field(
        default=None,
        max_length=80,
    )

    site: str | None = Field(
        default=None,
        max_length=500,
    )


# ==========================================================
# FONTES OFICIAIS
# ==========================================================

class FonteOficial(BaseModel):
    nome: str = Field(
        min_length=2,
        max_length=250,
    )

    finalidade: str = Field(
        min_length=10,
        max_length=300,
    )

    url: str = Field(
        min_length=12,
        max_length=500,
    )


# ==========================================================
# LEADS
# ==========================================================

class LeadPotencial(BaseModel):
    nome: str

    dominio: str

    site: str

    status: str = "potencial a validar"

    fonte: str = "Hunter Discover"

    emails_profissionais_disponiveis: int | None = None

    validacao_produto: str = "NAO_CONFIRMADA"

    justificativa_validacao: str | None = None

    evidencia_url: str | None = None

    apto_para_abordagem: bool = False

    site_validado: bool = False

    motivo_validacao_site: str | None = None

    nivel_cobertura: Literal[
        "LOCAL",
        "REGIONAL",
    ] = "LOCAL"

    mercado_atendido: str | None = None

    # Preparação para Sprint 2

    score_oportunidade: int | None = Field(
        default=None,
        ge=0,
        le=100,
    )

    classificacao: Literal[
        "A",
        "B",
        "C",
    ] | None = None

    justificativa_score: str | None = Field(
        default=None,
        max_length=300,
    )


# ==========================================================
# PLANO DE AÇÃO
# ==========================================================

class PlanoAcao30Dias(BaseModel):
    dias_1_7: list[str] = Field(
        min_length=2,
        max_length=4,
    )

    dias_8_14: list[str] = Field(
        min_length=2,
        max_length=4,
    )

    dias_15_21: list[str] = Field(
        min_length=2,
        max_length=4,
    )

    dias_22_30: list[str] = Field(
        min_length=2,
        max_length=4,
    )


# ==========================================================
# EMAIL COMERCIAL
# ==========================================================

class EmailComercial(BaseModel):
    assunto: str = Field(
        min_length=3,
        max_length=240,
    )

    corpo: str = Field(
        min_length=20,
        max_length=8000,
    )


# ==========================================================
# EMPRESAS SUGERIDAS
# ==========================================================

class EmpresaSugerida(BaseModel):
    nome: str = Field(
        min_length=2,
        max_length=240,
    )

    justificativa: str = Field(
        min_length=10,
        max_length=400,
    )

    site: str | None = Field(
        default=None,
        max_length=500,
    )

    nivel_cobertura: Literal[
        "LOCAL",
        "REGIONAL",
    ]

    mercado_atendido: str = Field(
        min_length=2,
        max_length=160,
    )


# ==========================================================
# CONTEÚDO COMERCIAL
# ==========================================================

class ConteudoComercial(BaseModel):
    # Mantido para compatibilidade total com o Lovable atual
    panorama_comercial: str = Field(
        min_length=80,
        max_length=10000,
    )

    # Novidades Sprint 1
    feiras_eventos: list[FeiraEvento] = Field(
        default_factory=list,
        max_length=10,
    )

    fontes_oficiais: list[FonteOficial] = Field(
        default_factory=list,
        max_length=10,
    )

    plano_acao_30_dias: PlanoAcao30Dias

    email_comercial: EmailComercial

    empresas_sugeridas: list[EmpresaSugerida] = Field(
        default_factory=list,
        max_length=5,
    )


# ==========================================================
# RESPONSE
# ==========================================================

class ProspeccaoResponse(BaseModel):
    status: str = "sucesso"

    relatorio: str

    contexto_tarifario: ContextoTarifario | None = None

    fontes: list[FontePesquisa] = Field(
        default_factory=list,
    )

    leads: list[LeadPotencial] = Field(
        default_factory=list,
    )

    conteudo_comercial: ConteudoComercial | None = None

    aviso: str | None = None


# ==========================================================
# HEALTHCHECK
# ==========================================================

class HealthResponse(BaseModel):
    status: str = "ok"
    servico: str = "exportai-modulo-vendas"