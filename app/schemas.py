from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContextoOrigem(BaseModel):
    """Contexto opcional vindo de outro módulo; a API continua utilizável sem ele."""

    model_config = ConfigDict(extra="forbid")

    origem: str = Field(default="manual", max_length=40)
    score_diagnostico: float | None = Field(default=None, ge=0, le=100)
    mercados_recomendados: list[str] = Field(default_factory=list, max_length=20)


class CriarProspeccaoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hs6: str | None = None
    ncm: str | None = None
    nome_produto: str = Field(min_length=3, max_length=250)
    pais_alvo: str = Field(min_length=2, max_length=120)
    idioma_alvo: str = Field(default="Português", min_length=2, max_length=60)
    disponibilidade: str | None = Field(default=None, max_length=120)
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


class FontePesquisa(BaseModel):
    titulo: str
    url: str


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


class ProspeccaoResponse(BaseModel):
    status: str = "sucesso"
    relatorio: str
    fontes: list[FontePesquisa] = Field(default_factory=list)
    leads: list[LeadPotencial] = Field(default_factory=list)
    aviso: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    servico: str = "exportai-modulo-vendas"
