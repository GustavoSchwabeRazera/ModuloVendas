from __future__ import annotations

import logging

import requests

from app.config import Settings
from app.schemas import CriarProspeccaoRequest, LeadPotencial

logger = logging.getLogger(__name__)

HUNTER_DISCOVER_URL = "https://api.hunter.io/v2/discover"


def buscar_leads(
    entrada: CriarProspeccaoRequest, settings: Settings
) -> tuple[list[LeadPotencial], str | None]:
    """Busca organizações no Hunter, sem expor nem revelar e-mails pessoais."""
    if not settings.exportai_hunter_ativo or not settings.hunter_api_key:
        return [], None

    limite = max(1, min(settings.exportai_hunter_limite, 5))
    consulta = (
        f"Importadores, distribuidores ou atacadistas de {entrada.nome_produto} "
        f"em {entrada.pais_alvo}"
    )
    try:
        response = requests.post(
            HUNTER_DISCOVER_URL,
            params={"api_key": settings.hunter_api_key},
            # O plano gratuito sempre retorna a página padrão (até 100 itens).
            # `limit` é Premium no Discover; aplicamos nosso limite localmente.
            json={"query": consulta},
            timeout=12,
        )
        response.raise_for_status()
        dados = response.json().get("data", [])
    except requests.HTTPError as exc:
        codigo = exc.response.status_code if exc.response is not None else None
        logger.warning("Hunter respondeu com HTTP %s", codigo)
        if codigo in {401, 403}:
            return [], "O Hunter recusou a chave configurada. Verifique HUNTER_API_KEY no Render."
        if codigo == 429:
            return [], "A quota do Hunter foi atingida. Tente novamente após renovar os créditos."
        if codigo in {400, 422}:
            return [], "O Hunter não aceitou os critérios desta busca. Ajuste produto ou país e tente novamente."
        if codigo is not None and codigo >= 500:
            return [], "O Hunter está temporariamente indisponível. Tente novamente em instantes."
        return [], "Os leads do Hunter não puderam ser carregados agora. O plano continua disponível."
    except requests.RequestException as exc:
        logger.warning("Falha de conexão ao consultar Hunter: %s", type(exc).__name__)
        return [], "Os leads do Hunter não puderam ser carregados agora. O plano continua disponível."
    except ValueError:
        logger.warning("Resposta inválida do Hunter")
        return [], "Os leads do Hunter retornaram em formato inválido."

    leads: list[LeadPotencial] = []
    for item in dados[:limite]:
        dominio = str(item.get("domain") or "").strip().lower()
        nome = str(item.get("organization") or "").strip()
        if not dominio or not nome:
            continue
        contagem = item.get("emails_count") or {}
        total = contagem.get("total")
        leads.append(
            LeadPotencial(
                nome=nome,
                dominio=dominio,
                site=f"https://{dominio}",
                emails_profissionais_disponiveis=total if isinstance(total, int) else None,
            )
        )
    return leads, None
