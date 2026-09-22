from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types

from app.config import Settings
from app.schemas import CriarProspeccaoRequest, LeadPotencial

logger = logging.getLogger(__name__)


def _extrair_json(texto: str) -> list[dict[str, Any]]:
    """Aceita JSON puro ou o mesmo conteúdo envolvido em bloco Markdown."""
    conteudo = texto.strip()
    if conteudo.startswith("```"):
        conteudo = conteudo.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    resultado = json.loads(conteudo)
    if not isinstance(resultado, list):
        raise ValueError("A validação não retornou uma lista.")
    return resultado


def validar_aderencia_produto(
    entrada: CriarProspeccaoRequest,
    leads: list[LeadPotencial],
    settings: Settings,
) -> tuple[list[LeadPotencial], str | None]:
    """Valida leads em lote por evidências públicas, sem expor contatos pessoais."""
    if (
        not leads
        or not settings.gemini_api_key
        or not settings.exportai_pesquisa_web_ativa
        or not settings.exportai_validacao_leads_ativa
    ):
        return leads, None

    dominios = "\n".join(f"- {lead.dominio}" for lead in leads)
    prompt = f"""
Valide, por pesquisa web, se cada empresa abaixo tem evidência pública em seu
site oficial de atuar com o produto ou categoria solicitada. Não suponha
aderência pelo nome da empresa, país ou quantidade de contatos.

Produto: {entrada.nome_produto}
HS6/NCM: {entrada.ncm or entrada.hs6 or 'não informado'}
País-alvo: {entrada.pais_alvo}
Perfil procurado: {entrada.perfil_parceiro or 'não informado'}
Domínios a validar:
{dominios}

Pesquise apenas fontes públicas, priorizando cada domínio informado. Responda
somente um JSON, sem Markdown, contendo um item por domínio com:
dominio, validacao (ADERENTE, NAO_ADERENTE ou NAO_CONFIRMADA), justificativa
(máximo 240 caracteres) e evidencia_url. Use NAO_CONFIRMADA quando não houver
evidência suficiente. evidencia_url deve ser uma URL pública encontrada ou null.
Não invente fatos, certificados, compras ou relações comerciais.
""".strip()

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.exportai_gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0,
            ),
        )
        avaliacoes = _extrair_json(str(getattr(response, "text", "")))
    except Exception as exc:
        logger.warning("Falha ao validar aderência dos leads: %s", type(exc).__name__)
        return leads, "Não foi possível validar a aderência dos leads agora. Revise os sites antes de abordar."

    por_dominio = {
        str(item.get("dominio") or "").strip().lower(): item
        for item in avaliacoes
        if isinstance(item, dict)
    }
    atualizados: list[LeadPotencial] = []
    for lead in leads:
        avaliacao = por_dominio.get(lead.dominio.lower(), {})
        status = str(avaliacao.get("validacao") or "NAO_CONFIRMADA").upper()
        if status not in {"ADERENTE", "NAO_ADERENTE", "NAO_CONFIRMADA"}:
            status = "NAO_CONFIRMADA"
        justificativa = avaliacao.get("justificativa")
        evidencia_url = avaliacao.get("evidencia_url")
        atualizados.append(
            lead.model_copy(
                update={
                    "status": (
                        "aderente validado"
                        if status == "ADERENTE"
                        else "não aderente"
                        if status == "NAO_ADERENTE"
                        else "potencial a validar"
                    ),
                    "validacao_produto": status,
                    "justificativa_validacao": str(justificativa)[:240] if justificativa else None,
                    "evidencia_url": str(evidencia_url) if evidencia_url else None,
                    "apto_para_abordagem": status == "ADERENTE",
                }
            )
        )
    return atualizados, None
