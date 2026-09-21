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
Você é um especialista em comércio exterior e vendas B2B internacionais.

Crie um plano comercial acionável, responsável e objetivo para exportar:
- Produto: {entrada.nome_produto}
- Código HS6/NCM: {codigo}
- País-alvo: {entrada.pais_alvo}
- Disponibilidade: {entrada.disponibilidade or 'não informada'}
- Perfil de parceiro procurado: {entrada.perfil_parceiro or 'a definir'}
- Contexto: {origem}

Entregue em Markdown, com as seções:
1. Estratégia para o mercado-alvo;
2. Perfil de comprador/parceiro ideal;
3. Critérios de qualificação de parceiros;
4. Plano de abordagem em 30 dias;
5. E-mail inicial em {entrada.idioma_alvo};
6. Próximas ações e riscos a validar.

Não invente empresas, contatos ou dados comerciais verificados.
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
