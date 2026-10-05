from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.schemas import CriarProspeccaoRequest, HealthResponse, ProspeccaoResponse
from app.services.dados_tarifarios import buscar_contexto_tarifario
from app.services.gemini import (
    ErroProvedorIA,
    extrair_leads_sugeridos,
    gerar_prospeccao,
)
from app.services.lead_score import pontuar_e_ordenar_leads
from app.services.validacao_leads import (
    validar_aderencia_produto,
    validar_sites_oficiais,
)

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(title="ExportAI - Módulo Vendas", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


def _juntar_avisos(*avisos: str | None) -> str | None:
    unicos: list[str] = []
    vistos: set[str] = set()
    for aviso in avisos:
        if not aviso:
            continue
        texto = aviso.strip()
        if texto and texto not in vistos:
            vistos.add(texto)
            unicos.append(texto)
    return " ".join(unicos) or None


def processar_prospeccao(entrada: CriarProspeccaoRequest) -> ProspeccaoResponse:
    contexto_tarifario, aviso_tarifario = buscar_contexto_tarifario(entrada, settings)

    relatorio, fontes, conteudo_comercial = gerar_prospeccao(
        entrada,
        settings,
        contexto_tarifario,
    )
    leads, aviso_extracao = extrair_leads_sugeridos(conteudo_comercial)

    aviso_site: str | None = None
    if leads:
        try:
            leads, aviso_site = validar_sites_oficiais(leads)
        except Exception:
            logger.exception("Falha não crítica ao verificar URLs dos leads")
            aviso_site = (
                "Não foi possível verificar a acessibilidade dos sites agora. "
                "Os leads foram mantidos para validação manual."
            )

    aviso_aderencia: str | None = None
    if leads:
        try:
            leads, aviso_aderencia = validar_aderencia_produto(
                entrada,
                leads,
                settings,
            )
        except Exception:
            logger.exception("Falha não crítica ao validar aderência dos leads")
            aviso_aderencia = (
                "Não foi possível validar a aderência dos leads agora. "
                "Revise as evidências antes de iniciar uma abordagem comercial."
            )

    # O score é sempre calculado no backend, depois das validações, e os leads
    # são devolvidos em ordem decrescente de prioridade.
    leads = pontuar_e_ordenar_leads(entrada, leads)

    return ProspeccaoResponse(
        relatorio=relatorio,
        fontes=fontes,
        leads=leads,
        contexto_tarifario=contexto_tarifario,
        conteudo_comercial=conteudo_comercial,
        aviso=_juntar_avisos(
            aviso_tarifario,
            aviso_extracao,
            aviso_site,
            aviso_aderencia,
        ),
    )


@app.get("/health", response_model=HealthResponse, tags=["Operação"])
def health() -> HealthResponse:
    return HealthResponse()


@app.post(
    "/api/v1/prospeccoes",
    response_model=ProspeccaoResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Provedor de IA indisponível ou sem cota.",
        }
    },
    tags=["Prospecções"],
)
def criar_prospeccao(entrada: CriarProspeccaoRequest) -> ProspeccaoResponse:
    try:
        return processar_prospeccao(entrada)
    except ErroProvedorIA as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Falha interna ao processar a prospecção")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ocorreu uma falha interna ao processar a prospecção.",
        ) from exc
