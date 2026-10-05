from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

from app.schemas import (
    ComponentesLeadScore,
    CriarProspeccaoRequest,
    LeadPotencial,
    ResultadoLeadScore,
)


def _normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"[^a-z0-9]+", " ", sem_acentos.lower()).strip()


def _contem(texto: str, termo: str) -> bool:
    padrao = (
        r"(?<![a-z0-9])"
        + re.escape(_normalizar(termo))
        + r"(?![a-z0-9])"
    )
    return bool(re.search(padrao, texto))


def _papeis_solicitados(perfil: str | None) -> set[str]:
    texto = _normalizar(perfil)
    mapa = {
        "IMPORTADOR": (
            "importador",
            "importadora",
            "importacao",
            "importacion",
        ),
        "TRADING": (
            "trading",
            "trading company",
            "comercio exterior",
        ),
        "DISTRIBUIDOR": (
            "distribuidor",
            "distribuidora",
            "distribuicao",
            "distribucion",
        ),
        "ATACADISTA": (
            "atacadista",
            "atacado",
            "mayorista",
            "grossista",
        ),
        "FABRICANTE": (
            "fabricante",
            "industria",
            "produtor",
        ),
        "VAREJISTA": (
            "varejista",
            "varejo",
            "retail",
        ),
        "HORECA": (
            "horeca",
            "food service",
            "foodservice",
            "gastronomico",
        ),
    }
    return {
        papel
        for papel, termos in mapa.items()
        if any(_contem(texto, termo) for termo in termos)
    }


def _pontuar_aderencia(lead: LeadPotencial) -> tuple[int, str]:
    mapa = {
        "PRODUTO_EXATO": (
            100,
            "Produto exato comprovado por evidência pública.",
        ),
        "CATEGORIA_RELACIONADA": (
            70,
            "Categoria relacionada comprovada; variante exata ainda não confirmada.",
        ),
        "SETOR_RELACIONADO": (
            35,
            "Apenas o setor relacionado foi comprovado.",
        ),
        "NAO_COMPROVADO": (
            10,
            "Produto ou categoria não comprovados.",
        ),
        "INCOMPATIVEL": (
            0,
            "Produto incompatível com a busca.",
        ),
    }
    return mapa[lead.correspondencia_produto]


def _pontuar_presenca(lead: LeadPotencial) -> tuple[int, str]:
    comprovada = any(
        "PRESENCA_MERCADO" in evidencia.criterios
        for evidencia in lead.evidencias
    )
    if comprovada and lead.nivel_cobertura == "LOCAL":
        return 100, "Presença local no mercado-alvo confirmada."
    if comprovada:
        return 70, "Presença de mercado confirmada sem operação local comprovada."
    if lead.nivel_cobertura == "REGIONAL":
        return 45, "Cobertura regional informada sem presença local comprovada."
    return 15, "Presença no mercado-alvo não comprovada."


def _pontuar_evidencia(lead: LeadPotencial) -> tuple[int, str]:
    dominios = {
        (urlparse(evidencia.url).hostname or "")
        .lower()
        .removeprefix("www.")
        for evidencia in lead.evidencias
    }
    dominios.discard("")

    if lead.fontes_independentes >= 2:
        return 100, "Duas ou mais fontes independentes sustentam a avaliação."
    if lead.fontes_independentes == 1 and len(dominios) >= 2:
        return 90, "O site da empresa e uma fonte independente sustentam a avaliação."
    if len(lead.evidencias) >= 2:
        return 75, (
            "Duas ou mais páginas sustentam a avaliação, "
            "sem independência suficiente."
        )
    if len(lead.evidencias) == 1:
        return 60, "Uma única fonte pública sustenta a avaliação."
    return 0, "Nenhuma evidência pública estruturada foi registrada."


def _pontuar_dominio(lead: LeadPotencial) -> tuple[int, str]:
    if lead.dominio_oficial_confirmado and lead.site_validado:
        return 100, "Domínio oficial confirmado e URL acessível."
    if lead.dominio_oficial_confirmado:
        return 75, "Domínio oficial confirmado, mas URL não acessível no teste."
    if lead.site_validado:
        return 55, "URL acessível sem confirmação de domínio oficial."
    if lead.site:
        return 20, "Há uma URL informada, mas o domínio não foi confirmado."
    return 0, "Domínio não confirmado."


def _pontuar_perfil(
    entrada: CriarProspeccaoRequest,
    lead: LeadPotencial,
) -> tuple[int, str]:
    solicitados = _papeis_solicitados(entrada.perfil_parceiro)
    comprovados = set(lead.papeis_comerciais) - {"NAO_COMPROVADO"}

    if not solicitados:
        return 50, (
            "Perfil solicitado não reconhecido pelas regras atuais; "
            "aplicada pontuação neutra."
        )

    correspondentes = solicitados & comprovados
    if correspondentes:
        if "IMPORTADOR" in solicitados and not lead.importacao_produto_comprovada:
            return 70, (
                "Papel de importador indicado, mas a importação do produto "
                "buscado não foi comprovada."
            )
        return 100, (
            "Perfil solicitado comprovado: "
            f"{', '.join(sorted(correspondentes))}."
        )

    if comprovados & {"DISTRIBUIDOR", "ATACADISTA", "HORECA"}:
        return 50, (
            "Compatibilidade comercial parcial, sem o papel exato solicitado."
        )
    if comprovados & {"FABRICANTE", "VAREJISTA"}:
        return 25, (
            "Atuação setorial comprovada, sem perfil comprador solicitado."
        )
    return 10, "Perfil comercial solicitado não comprovado."


def _perfil_comprador_confirmado(
    entrada: CriarProspeccaoRequest,
    lead: LeadPotencial,
) -> bool:
    """Define aptidão de modo coerente com o perfil pedido pelo usuário."""
    solicitados = _papeis_solicitados(entrada.perfil_parceiro)
    papeis = set(lead.papeis_comerciais) - {"NAO_COMPROVADO"}

    if not solicitados:
        return bool(
            lead.importacao_produto_comprovada
            or papeis & {"TRADING", "DISTRIBUIDOR", "ATACADISTA"}
        )

    correspondentes = solicitados & papeis
    if not correspondentes:
        return False

    # Quando o usuário pede importador, a empresa só fica apta se houver prova
    # da importação do produto buscado. Importação de matéria-prima relacionada
    # não é suficiente.
    if "IMPORTADOR" in solicitados and "IMPORTADOR" in correspondentes:
        if lead.importacao_produto_comprovada:
            return True
        # Um perfil alternativo explicitamente solicitado pode sustentar a
        # abordagem, mesmo sem prova de importação do produto exato.
        correspondentes_alternativos = correspondentes - {"IMPORTADOR"}
        return bool(correspondentes_alternativos)

    return True


def calcular_lead_score(
    entrada: CriarProspeccaoRequest,
    lead: LeadPotencial,
) -> LeadPotencial:
    """Calcula score e aptidão por regras determinísticas e auditáveis."""
    aderencia, justificativa_aderencia = _pontuar_aderencia(lead)
    presenca, justificativa_presenca = _pontuar_presenca(lead)
    evidencia, justificativa_evidencia = _pontuar_evidencia(lead)
    dominio, justificativa_dominio = _pontuar_dominio(lead)
    perfil, justificativa_perfil = _pontuar_perfil(entrada, lead)

    total = round(
        aderencia * 0.40
        + presenca * 0.20
        + evidencia * 0.20
        + dominio * 0.10
        + perfil * 0.10
    )
    classe = "A" if total >= 80 else "B" if total >= 60 else "C"

    score = ResultadoLeadScore(
        componentes=ComponentesLeadScore(
            aderencia_produto=aderencia,
            presenca_mercado=presenca,
            evidencia_publica=evidencia,
            qualidade_dominio=dominio,
            perfil_solicitado=perfil,
        ),
        score_total=total,
        classe=classe,
        justificativas=[
            justificativa_aderencia,
            justificativa_presenca,
            justificativa_evidencia,
            justificativa_dominio,
            justificativa_perfil,
        ],
    )

    apto = bool(
        lead.correspondencia_produto
        in {"PRODUTO_EXATO", "CATEGORIA_RELACIONADA"}
        and perfil >= 50
        and _perfil_comprador_confirmado(entrada, lead)
        and lead.dominio_oficial_confirmado
        and bool(lead.evidencias)
    )

    return lead.model_copy(
        update={
            "lead_score": score,
            "apto_para_abordagem": apto,
        }
    )


def pontuar_e_ordenar_leads(
    entrada: CriarProspeccaoRequest,
    leads: list[LeadPotencial],
) -> list[LeadPotencial]:
    pontuados = [calcular_lead_score(entrada, lead) for lead in leads]
    return sorted(
        pontuados,
        key=lambda lead: (
            lead.lead_score.score_total if lead.lead_score else -1,
            lead.nome.casefold(),
        ),
        reverse=True,
    )
