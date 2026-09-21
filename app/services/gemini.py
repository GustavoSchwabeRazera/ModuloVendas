from __future__ import annotations

import logging
import time

from google import genai

from app.config import Settings
from app.schemas import CriarProspeccaoRequest

logger = logging.getLogger(__name__)


class ErroProvedorIA(RuntimeError):
    pass


def gerar_prospeccao(
    entrada: CriarProspeccaoRequest,
    settings: Settings,
) -> str:
    if not settings.gemini_api_key:
        raise ErroProvedorIA("GEMINI_API_KEY não está configurada no servidor.")

    codigo = entrada.ncm or entrada.hs6 or "não informado"
    contexto = entrada.contexto_origem
    origem = "Preenchimento manual" if not contexto else (
        f"Origem: {contexto.origem}; "
        f"score de diagnóstico: {contexto.score_diagnostico}; "
        f"mercados recomendados: "
        f"{', '.join(contexto.mercados_recomendados) or 'não informado'}"
    )

    prompt = f"""
Entregue em Markdown, com esta estrutura:

# Inteligência comercial — {entrada.pais_alvo}

## Principais canais
- Distribuidores
- Importadores
- Atacadistas
- Câmaras de comércio
- Feiras e eventos do setor

## Empresas e organizações potenciais a validar
Para cada uma, informe nome, tipo, cidade/país, motivo da relevância, site/fonte pública quando conhecido e status “potencial a validar”.

## Feiras e eventos
Liste nome, cidade, período aproximado e site oficial quando conhecido.

## Notícias, tendências e mudanças regulatórias
Indique oportunidades e riscos do mercado, sempre com nível de confiança: alto, médio ou baixo.

## Oportunidades
- Tendências de demanda
- Canais prioritários
- Perfil de comprador ideal
- Ações recomendadas

## Riscos e validações necessárias
- Regulamentação e documentação
- Barreiras logísticas ou tarifárias
- Dados a confirmar antes de abordar empresas

## Plano de ação em 30 dias

## E-mail inicial
Escreva em (Veja o Idioma local do destino), usando campos [entre colchetes] para personalização.

Nunca invente empresas, contatos, sites, volumes, certificações ou dados comerciais.
Empresas listadas devem ser tratadas apenas como “potenciais a validar”, nunca como clientes confirmados.
Não alegue acesso a informações em tempo real ou bases privadas.
""".strip()


    client = genai.Client(api_key=settings.gemini_api_key)

    for tentativa in range(3):
        try:
            response = client.models.generate_content(
                model=settings.exportai_gemini_model,
                contents=prompt,
            )
            texto = getattr(response, "text", None)
            if texto:
                return texto
            raise ErroProvedorIA("O provedor de IA retornou uma resposta vazia.")
        except Exception as exc:
            logger.warning(
                "Falha no Gemini, tentativa %s/3: %s",
                tentativa + 1,
                type(exc).__name__,
            )
            if tentativa == 2:
                raise ErroProvedorIA(
                    "Não foi possível gerar a prospecção agora."
                ) from exc
            time.sleep(2 ** tentativa)

    raise ErroProvedorIA("Não foi possível gerar a prospecção agora.")
