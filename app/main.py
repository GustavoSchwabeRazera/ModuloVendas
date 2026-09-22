from __future__ import annotations

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.schemas import CriarProspeccaoRequest, HealthResponse, ProspeccaoResponse
from app.services.dados_tarifarios import buscar_contexto_tarifario
from app.services.gemini import ErroProvedorIA, gerar_prospeccao
from app.services.hunter import buscar_leads
from app.services.validacao_leads import validar_aderencia_produto

settings = get_settings()
app = FastAPI(title="ExportAI - Módulo Vendas", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/health", response_model=HealthResponse, tags=["Operação"])
def health() -> HealthResponse:
    return HealthResponse()


@app.post(
    "/api/v1/prospeccoes",
    response_model=ProspeccaoResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Prospecções"],
)
def criar_prospeccao(entrada: CriarProspeccaoRequest) -> ProspeccaoResponse:
    contexto_tarifario, aviso_tarifario = buscar_contexto_tarifario(entrada, settings)
    try:
        relatorio, fontes = gerar_prospeccao(entrada, settings, contexto_tarifario)
    except ErroProvedorIA as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    leads, aviso = buscar_leads(entrada, settings)
    leads, aviso_validacao = validar_aderencia_produto(entrada, leads, settings)
    avisos = (aviso_tarifario, aviso, aviso_validacao)
    return ProspeccaoResponse(
        relatorio=relatorio,
        fontes=fontes,
        leads=leads,
        contexto_tarifario=contexto_tarifario,
        aviso=" ".join(item for item in avisos if item) or None,
    )
