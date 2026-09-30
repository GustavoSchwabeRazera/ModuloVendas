from __future__ import annotations

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.schemas import CriarProspeccaoRequest, HealthResponse, ProspeccaoResponse
from app.services.dados_tarifarios import buscar_contexto_tarifario
from app.services.gemini import (
    ErroProvedorIA,
    buscar_empresas_potenciais,
    extrair_leads_sugeridos,
    gerar_prospeccao,
)
from app.services.validacao_leads import validar_sites_oficiais

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
        relatorio, fontes, conteudo_comercial = gerar_prospeccao(entrada, settings, contexto_tarifario)
    except ErroProvedorIA as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    leads, aviso = extrair_leads_sugeridos(conteudo_comercial)
    # Alguns modelos priorizam o plano e omitem empresas no JSON principal. Nesse
    # caso fazemos uma única busca complementar, sem reintroduzir o Hunter.
    if not leads:
        try:
            leads, aviso_complementar = buscar_empresas_potenciais(entrada, settings)
            aviso = None if leads else (aviso_complementar or aviso)
        except ErroProvedorIA:
            aviso = "Nenhuma empresa com domínio oficial confiável foi sugerida nesta consulta."
    leads, aviso_site = validar_sites_oficiais(leads)
    avisos = (aviso_tarifario, aviso, aviso_site)
    return ProspeccaoResponse(
        relatorio=relatorio,
        fontes=fontes,
        leads=leads,
        contexto_tarifario=contexto_tarifario,
        conteudo_comercial=conteudo_comercial,
        aviso=" ".join(item for item in avisos if item) or None,
    )
