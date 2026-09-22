from __future__ import annotations

import logging
import time

from google import genai
from google.genai import types

from app.config import Settings
from app.schemas import CriarProspeccaoRequest

logger = logging.getLogger(__name__)


class ErroProvedorIA(RuntimeError):
    pass


def extrair_fontes(response) -> list[dict[str, str]]:
    """Retorna apenas fontes públicas do Google Search Grounding."""
    fontes: list[dict[str, str]] = []
    vistos: set[str] = set()
    for candidate in getattr(response, "candidates", None) or []:
        metadata = getattr(candidate, "grounding_metadata", None)
        for chunk in getattr(metadata, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            url = getattr(web, "uri", None)
            if not url or url in vistos:
                continue
            vistos.add(url)
            fontes.append({"titulo": getattr(web, "title", None) or url, "url": url})
    return fontes[:12]


def gerar_prospeccao(entrada: CriarProspeccaoRequest, settings: Settings) -> tuple[str, list[dict[str, str]]]:
    if not settings.gemini_api_key:
        raise ErroProvedorIA("GEMINI_API_KEY não está configurada no servidor.")

    codigo = entrada.ncm or entrada.hs6 or "não informado"
    contexto = entrada.contexto_origem
    origem = "Preenchimento manual" if not contexto else (
        f"Origem: {contexto.origem}; score de diagnóstico: {contexto.score_diagnostico}; "
        f"mercados recomendados: {', '.join(contexto.mercados_recomendados) or 'não informado'}"
    )
    prompt = f"""
Você é um especialista em comércio exterior e vendas B2B internacionais.
Crie um plano comercial acionável, responsável e objetivo para exportar:
- Produto: {entrada.nome_produto}
- Código HS6/NCM: {codigo}
- País-alvo: {entrada.pais_alvo}
- Disponibilidade: {entrada.disponibilidade or 'não informada'}
- Perfil de parceiro procurado: {entrada.perfil_parceiro or 'a definir'}
- Contexto: {origem}

Faça pesquisa web para esta solicitação, priorizando sites oficiais de empresas,
associações setoriais, organizadores de feiras e órgãos reguladores. Use poucas
consultas bem focadas.

Entregue em Markdown:
1. Principais canais: distribuidores, importadores, atacadistas, câmaras e feiras;
2. Empresas e organizações potenciais a validar, com justificativa factual;
3. Notícias, tendências e mudanças regulatórias relevantes;
4. Oportunidades e riscos;
5. Plano de ação em 30 dias;
6. E-mail inicial em (Idioma do local de destino), usando campos [entre colchetes].

Nunca invente empresas, as empresas precisam ter correlação com o produto,empresas precisam pertencer  ao destino escolhido,contatos, sites, volumes ou certificações. Para cada empresa,
use o status "potencial a validar". Não a chame de cliente confirmado. Sempre de os links das empresas
""".strip()

    client = genai.Client(api_key=settings.gemini_api_key)
    config = None
    if settings.exportai_pesquisa_web_ativa:
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )

    for tentativa in range(3):
        try:
            response = client.models.generate_content(
                model=settings.exportai_gemini_model,
                contents=prompt,
                config=config,
            )
            texto = getattr(response, "text", None)
            if texto:
                return texto, extrair_fontes(response)
            raise ErroProvedorIA("O provedor de IA retornou uma resposta vazia.")
        except Exception as exc:
            logger.warning("Falha no Gemini, tentativa %s/3: %s", tentativa + 1, type(exc).__name__)
            if tentativa == 2:
                raise ErroProvedorIA("Não foi possível gerar a prospecção agora.") from exc
            time.sleep(2 ** tentativa)

    raise ErroProvedorIA("Não foi possível gerar a prospecção agora.")
