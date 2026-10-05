from __future__ import annotations

import ipaddress
import json
import logging
import random
import socket
import time
from datetime import date
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import requests
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.schemas import (
    CriterioEvidencia,
    CriarProspeccaoRequest,
    EvidenciaLead,
    LeadPotencial,
    PapelComercial,
    TipoCorrespondenciaProduto,
    TipoFonteEvidencia,
)

logger = logging.getLogger(__name__)

_CABECALHOS_HTTP = {"User-Agent": "ExportAI-LinkVerifier/2.1", "Accept": "text/html,application/xhtml+xml"}
_TERMOS_NAO_COMPRADORES = {
    "fair", "feira", "event", "evento", "exhibition", "exposicao",
    "association", "associacao", "directory", "diretorio", "marketplace",
}
_STATUS_REDIRECIONAMENTO = {301, 302, 303, 307, 308}
_MAX_REDIRECIONAMENTOS = 5
_PORTAS_PERMITIDAS = {80, 443}


class _EvidenciaAvaliacao(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    titulo: str = Field(min_length=2, max_length=300)
    url: str = Field(min_length=8, max_length=2_000)
    descricao: str = Field(min_length=5, max_length=800)
    criterios: list[CriterioEvidencia] = Field(min_length=1, max_length=5)
    tipo_fonte: TipoFonteEvidencia
    correspondencia_produto: TipoCorrespondenciaProduto = "NAO_COMPROVADO"
    papeis_comerciais: list[PapelComercial] = Field(default_factory=list, max_length=9)


class _AvaliacaoAderencia(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    dominio: str | None = None
    nome: str | None = None
    validacao: Literal["ADERENTE", "NAO_ADERENTE", "NAO_CONFIRMADA"]
    correspondencia_produto: TipoCorrespondenciaProduto
    papeis_comerciais: list[PapelComercial] = Field(default_factory=list, max_length=9)
    importacao_produto_comprovada: bool = False
    presenca_mercado_confirmada: bool = False
    dominio_oficial_confirmado: bool = False
    justificativa: str = Field(min_length=5, max_length=800)
    evidencias: list[_EvidenciaAvaliacao] = Field(default_factory=list, max_length=8)


class _ListaAvaliacoes(BaseModel):
    model_config = ConfigDict(extra="ignore")
    avaliacoes: list[_AvaliacaoAderencia] = Field(default_factory=list, max_length=10)


def _normalizar_texto(valor: str | None) -> str:
    if not valor:
        return ""
    return " ".join("".join(c if c.isalnum() else " " for c in valor.lower()).split())


def _parece_nao_comprador(lead: LeadPotencial) -> bool:
    termos = set(_normalizar_texto(lead.nome).split())
    termos.update(_normalizar_texto(lead.dominio).split())
    return bool(termos & _TERMOS_NAO_COMPRADORES)


def _ip_publico(endereco: str) -> bool:
    try:
        ip = ipaddress.ip_address(endereco)
    except ValueError:
        return False
    return not any((ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast, ip.is_reserved, ip.is_unspecified))


def _validar_destino_publico(url: str) -> tuple[bool, str | None]:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False, "A URL não usa HTTP ou HTTPS."
        if not parsed.hostname:
            return False, "A URL não possui domínio válido."
        if parsed.username or parsed.password:
            return False, "A URL contém credenciais embutidas."
        porta = parsed.port or (443 if parsed.scheme == "https" else 80)
        if porta not in _PORTAS_PERMITIDAS:
            return False, "A URL utiliza uma porta não permitida."
        infos = socket.getaddrinfo(parsed.hostname, porta, type=socket.SOCK_STREAM)
        enderecos = {info[4][0] for info in infos}
        if not enderecos:
            return False, "O domínio não pôde ser resolvido."
        if any(not _ip_publico(endereco) for endereco in enderecos):
            return False, "O domínio resolve para endereço não público."
        return True, None
    except (OSError, ValueError):
        return False, "Não foi possível validar o destino da URL."


def _requisitar_com_redirecionamentos_seguros(url: str) -> tuple[requests.Response | None, str | None]:
    atual = url
    sessao = requests.Session()
    try:
        for _ in range(_MAX_REDIRECIONAMENTOS + 1):
            seguro, motivo = _validar_destino_publico(atual)
            if not seguro:
                return None, motivo
            resposta = sessao.get(
                atual,
                headers=_CABECALHOS_HTTP,
                timeout=(4, 10),
                allow_redirects=False,
                stream=True,
            )
            if resposta.status_code not in _STATUS_REDIRECIONAMENTO:
                return resposta, None
            destino = resposta.headers.get("Location")
            resposta.close()
            if not destino:
                return None, "O servidor retornou redirecionamento sem destino."
            atual = urljoin(atual, destino)
        return None, "O link excedeu o limite de redirecionamentos."
    except requests.RequestException:
        return None, "O servidor não conseguiu acessar o link informado."
    finally:
        sessao.close()


def validar_sites_oficiais(leads: list[LeadPotencial]) -> tuple[list[LeadPotencial], str | None]:
    resultados: list[LeadPotencial] = []
    nao_verificados = 0
    descartados = 0

    for lead in leads:
        if _parece_nao_comprador(lead):
            descartados += 1
            continue
        if not lead.site:
            nao_verificados += 1
            resultados.append(lead.model_copy(update={
                "status": "site não informado",
                "site_validado": False,
                "motivo_validacao_site": "A empresa foi sugerida sem URL oficial confirmada.",
            }))
            continue

        resposta, motivo = _requisitar_com_redirecionamentos_seguros(lead.site)
        if resposta is None:
            nao_verificados += 1
            resultados.append(lead.model_copy(update={
                "status": "site não verificado",
                "site_validado": False,
                "motivo_validacao_site": motivo,
            }))
            continue

        try:
            disponivel = 200 <= resposta.status_code < 400 or resposta.status_code in {401, 403}
            if not disponivel:
                nao_verificados += 1
                resultados.append(lead.model_copy(update={
                    "status": "site não verificado",
                    "site_validado": False,
                    "motivo_validacao_site": f"A URL respondeu com status HTTP {resposta.status_code}.",
                }))
                continue
            url_final = resposta.url
            dominio_final = (urlparse(url_final).hostname or "").lower().removeprefix("www.") or lead.dominio
            resultados.append(lead.model_copy(update={
                "site": url_final,
                "dominio": dominio_final,
                "site_validado": True,
                "motivo_validacao_site": "A URL respondeu ao teste de acessibilidade. Isso não confirma que o domínio seja oficial.",
                "status": "site verificado",
            }))
        finally:
            resposta.close()

    avisos: list[str] = []
    if nao_verificados:
        avisos.append(f"{nao_verificados} empresa(s) foram mantidas com URL ausente ou não verificada.")
    if descartados:
        avisos.append(f"{descartados} resultado(s) foram descartados por aparentarem ser feira, evento, associação, diretório ou marketplace.")
    return resultados, " ".join(avisos) or None


def _schema_avaliacoes() -> dict[str, Any]:
    correspondencias = ["PRODUTO_EXATO", "CATEGORIA_RELACIONADA", "SETOR_RELACIONADO", "NAO_COMPROVADO", "INCOMPATIVEL"]
    papeis = ["IMPORTADOR", "TRADING", "DISTRIBUIDOR", "ATACADISTA", "FABRICANTE", "VAREJISTA", "HORECA", "PRESTADOR_SERVICO", "OUTRO", "NAO_COMPROVADO"]
    criterios = ["ADERENCIA_PRODUTO", "IMPORTACAO_COMPRA", "PRESENCA_MERCADO", "PERFIL_COMERCIAL", "QUALIDADE_DOMINIO"]
    tipos_fonte = ["SITE_EMPRESA", "FONTE_OFICIAL", "FONTE_INDEPENDENTE", "DIRETORIO_COMERCIAL", "OUTRA"]
    nullable_string = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {
        "type": "object",
        "properties": {
            "avaliacoes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "dominio": nullable_string,
                        "nome": nullable_string,
                        "validacao": {"type": "string", "enum": ["ADERENTE", "NAO_ADERENTE", "NAO_CONFIRMADA"]},
                        "correspondencia_produto": {"type": "string", "enum": correspondencias},
                        "papeis_comerciais": {"type": "array", "items": {"type": "string", "enum": papeis}},
                        "importacao_produto_comprovada": {"type": "boolean"},
                        "presenca_mercado_confirmada": {"type": "boolean"},
                        "dominio_oficial_confirmado": {"type": "boolean"},
                        "justificativa": {"type": "string"},
                        "evidencias": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "titulo": {"type": "string"},
                                    "url": {"type": "string"},
                                    "descricao": {"type": "string"},
                                    "criterios": {"type": "array", "items": {"type": "string", "enum": criterios}},
                                    "tipo_fonte": {"type": "string", "enum": tipos_fonte},
                                    "correspondencia_produto": {"type": "string", "enum": correspondencias},
                                    "papeis_comerciais": {"type": "array", "items": {"type": "string", "enum": papeis}},
                                },
                                "required": ["titulo", "url", "descricao", "criterios", "tipo_fonte", "correspondencia_produto", "papeis_comerciais"],
                            },
                        },
                    },
                    "required": ["dominio", "nome", "validacao", "correspondencia_produto", "papeis_comerciais", "importacao_produto_comprovada", "presenca_mercado_confirmada", "dominio_oficial_confirmado", "justificativa", "evidencias"],
                },
            }
        },
        "required": ["avaliacoes"],
    }


def _montar_prompt_validacao(entrada: CriarProspeccaoRequest, leads: list[LeadPotencial]) -> str:
    candidatos = [{
        "nome": lead.nome,
        "dominio": lead.dominio,
        "site": lead.site,
        "nivel_cobertura": lead.nivel_cobertura,
        "mercado_atendido": lead.mercado_atendido,
        "justificativa_inicial": lead.justificativa_validacao,
    } for lead in leads]
    return f"""
Valide por pesquisa web os candidatos B2B abaixo para qualquer produto classificável por NCM/HS.

PRODUTO SOLICITADO
- Nome informado: {entrada.nome_produto}
- NCM: {entrada.ncm or 'não informado'}
- HS6: {entrada.hs6 or (entrada.ncm[:6] if entrada.ncm else 'não informado')}
- País-alvo: {entrada.pais_alvo}
- Perfil de parceiro solicitado: {entrada.perfil_parceiro or 'não informado'}

CANDIDATOS
{json.dumps(candidatos, ensure_ascii=False, indent=2)}

REGRAS DE CORRESPONDÊNCIA DO PRODUTO
- PRODUTO_EXATO: a evidência menciona o produto com características materiais compatíveis com o nome, NCM/HS ou descrição solicitada.
- CATEGORIA_RELACIONADA: pertence à mesma categoria, mas variante, composição, processamento, apresentação ou finalidade não está comprovadamente igual.
- SETOR_RELACIONADO: a empresa atua no setor, porém o produto ou categoria específica não está comprovado.
- NAO_COMPROVADO: não há prova suficiente.
- INCOMPATIVEL: há prova de produto incompatível.
- Não use conhecimento presumido do NCM. Compare o produto informado com as páginas encontradas.

REGRAS DE PAPEL COMERCIAL
- Classifique somente papéis explicitamente sustentados: IMPORTADOR, TRADING, DISTRIBUIDOR, ATACADISTA, FABRICANTE, VAREJISTA, HORECA, PRESTADOR_SERVICO, OUTRO ou NAO_COMPROVADO.
- importacao_produto_comprovada só pode ser true quando a evidência comprovar importação ou compra internacional do produto exato, não apenas matérias-primas, insumos, produtos relacionados ou atuação genérica.
- Uma fabricante ou varejista não é automaticamente importadora, trading ou compradora potencial.

REGRAS DE EVIDÊNCIA
- Retorne uma evidência por URL; reúna nessa evidência todos os critérios sustentados pela página.
- Busque, quando possível, pelo menos duas fontes de domínios distintos: site da empresa e fonte oficial/independente.
- Cada afirmação precisa de critério correspondente: ADERENCIA_PRODUTO, IMPORTACAO_COMPRA, PRESENCA_MERCADO, PERFIL_COMERCIAL ou QUALIDADE_DOMINIO.
- O site da empresa pode provar produtos, identidade e autodeclarações; não conte como confirmação independente.
- dominio_oficial_confirmado exige prova de vínculo entre empresa e domínio.
- presenca_mercado_confirmada exige sede ou operação comprovada no país-alvo.
- ADERENTE exige PRODUTO_EXATO ou CATEGORIA_RELACIONADA e ao menos uma evidência pública.
- Use NAO_CONFIRMADA quando faltar prova do produto ou do papel comercial solicitado.
- Não invente URLs, relações de compra, volumes, certificados ou contatos.
- Retorne exatamente um item por candidato.
""".strip()


def _chave_dominio(dominio: str | None) -> str:
    return (dominio or "").lower().removeprefix("www.").rstrip("/")


def _chave_avaliacao(item: _AvaliacaoAderencia) -> str:
    return _chave_dominio(item.dominio) or _normalizar_texto(item.nome)


def _chave_lead(lead: LeadPotencial) -> str:
    return _chave_dominio(lead.dominio) or _normalizar_texto(lead.nome)


def _mesmo_dominio(url: str | None, dominio: str | None) -> bool:
    if not url or not dominio:
        return False
    hostname = _chave_dominio(urlparse(url).hostname)
    esperado = _chave_dominio(dominio)
    return hostname == esperado or hostname.endswith(f".{esperado}")


def _deduplicar_evidencias(avaliacao: _AvaliacaoAderencia) -> list[EvidenciaLead]:
    por_url: dict[str, dict[str, Any]] = {}
    for evidencia in avaliacao.evidencias:
        url = evidencia.url.strip()
        chave = url.rstrip("/").lower()
        if chave not in por_url:
            por_url[chave] = {
                "titulo": evidencia.titulo,
                "url": url,
                "descricao": evidencia.descricao,
                "criterios": set(evidencia.criterios),
                "tipo_fonte": evidencia.tipo_fonte,
                "correspondencia_produto": evidencia.correspondencia_produto,
                "papeis_comerciais": set(evidencia.papeis_comerciais),
            }
        else:
            por_url[chave]["criterios"].update(evidencia.criterios)
            por_url[chave]["papeis_comerciais"].update(evidencia.papeis_comerciais)
            if len(evidencia.descricao) > len(por_url[chave]["descricao"]):
                por_url[chave]["descricao"] = evidencia.descricao

    return [EvidenciaLead(
        titulo=item["titulo"],
        url=item["url"],
        descricao=item["descricao"],
        criterios=sorted(item["criterios"]),
        tipo_fonte=item["tipo_fonte"],
        correspondencia_produto=item["correspondencia_produto"],
        papeis_comerciais=sorted(item["papeis_comerciais"]),
        consultado_em=date.today().isoformat(),
    ) for item in por_url.values()][:12]


def _contar_fontes_independentes(evidencias: list[EvidenciaLead], dominio_empresa: str | None) -> int:
    dominios = set()
    empresa = _chave_dominio(dominio_empresa)
    for evidencia in evidencias:
        dominio = _chave_dominio(urlparse(evidencia.url).hostname)
        if dominio and dominio != empresa and not dominio.endswith(f".{empresa}"):
            dominios.add(dominio)
    return len(dominios)


def validar_aderencia_produto(
    entrada: CriarProspeccaoRequest,
    leads: list[LeadPotencial],
    settings: Settings,
) -> tuple[list[LeadPotencial], str | None]:
    if not leads:
        return leads, None
    if not settings.gemini_api_key:
        return leads, "A aderência não foi validada porque a chave do Gemini não está configurada."
    if not settings.exportai_validacao_leads_ativa:
        return leads, None
    if not settings.exportai_pesquisa_web_ativa:
        return leads, "A aderência dos leads não foi validada porque a pesquisa web está desativada."

    client = genai.Client(api_key=settings.gemini_api_key)
    resposta = None
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=_schema_avaliacoes(),
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0,
    )
    for tentativa in range(1, 3):
        try:
            resposta = client.models.generate_content(
                model=settings.exportai_gemini_model,
                contents=_montar_prompt_validacao(entrada, leads),
                config=config,
            )
            break
        except errors.ClientError as exc:
            logger.warning("Falha do cliente ao validar leads: code=%s status=%s", getattr(exc, "code", None), getattr(exc, "status", None))
            return leads, "Não foi possível validar a aderência dos leads por limite ou configuração da pesquisa web."
        except errors.ServerError as exc:
            logger.warning("Gemini indisponível ao validar leads: code=%s status=%s", getattr(exc, "code", None), getattr(exc, "status", None))
            if tentativa == 2:
                return leads, "A validação de aderência está temporariamente indisponível. Revise os leads antes da abordagem."
            time.sleep(1 + random.uniform(0, 0.5))
        except Exception as exc:
            logger.warning("Falha inesperada ao validar leads: %s", type(exc).__name__)
            return leads, "Não foi possível validar a aderência dos leads agora. Revise os resultados manualmente."

    try:
        texto = getattr(resposta, "text", None)
        if not texto:
            raise ValueError("Resposta vazia")
        lista = _ListaAvaliacoes.model_validate_json(texto)
    except (ValidationError, ValueError) as exc:
        logger.warning("Resposta inválida na validação de leads: %s", str(exc)[:500])
        return leads, "A pesquisa foi concluída, mas a validação dos leads retornou formato inválido."

    por_chave = {_chave_avaliacao(item): item for item in lista.avaliacoes}
    atualizados: list[LeadPotencial] = []
    nao_aderentes = 0
    inconclusivos = 0

    for lead in leads:
        avaliacao = por_chave.get(_chave_lead(lead))
        if not avaliacao:
            inconclusivos += 1
            atualizados.append(lead.model_copy(update={
                "status": "evidência parcial",
                "validacao_produto": "NAO_CONFIRMADA",
                "correspondencia_produto": "NAO_COMPROVADO",
                "apto_para_abordagem": False,
            }))
            continue

        if avaliacao.validacao == "NAO_ADERENTE" or avaliacao.correspondencia_produto == "INCOMPATIVEL":
            nao_aderentes += 1
            continue

        evidencias = _deduplicar_evidencias(avaliacao)
        fontes_independentes = _contar_fontes_independentes(evidencias, lead.dominio)
        evidencia_principal = next((e.url for e in evidencias if "ADERENCIA_PRODUTO" in e.criterios), None)
        dominio_confirmado = bool(
            avaliacao.dominio_oficial_confirmado
            and any(_mesmo_dominio(e.url, lead.dominio) and "QUALIDADE_DOMINIO" in e.criterios for e in evidencias)
        )

        if avaliacao.validacao == "ADERENTE" and avaliacao.correspondencia_produto in {"PRODUTO_EXATO", "CATEGORIA_RELACIONADA"}:
            status = "aderente validado"
        elif lead.nivel_cobertura == "REGIONAL":
            status = "parceiro regional a validar"
            inconclusivos += 1
        else:
            status = "evidência parcial"
            inconclusivos += 1

        atualizados.append(lead.model_copy(update={
            "status": status,
            "validacao_produto": avaliacao.validacao,
            "correspondencia_produto": avaliacao.correspondencia_produto,
            "papeis_comerciais": avaliacao.papeis_comerciais or ["NAO_COMPROVADO"],
            "importacao_produto_comprovada": avaliacao.importacao_produto_comprovada,
            "justificativa_validacao": avaliacao.justificativa,
            "evidencia_url": evidencia_principal,
            "evidencias": evidencias,
            "fontes_independentes": fontes_independentes,
            "apto_para_abordagem": False,
            "dominio_oficial_confirmado": dominio_confirmado,
            "mercado_atendido": entrada.pais_alvo if avaliacao.presenca_mercado_confirmada else lead.mercado_atendido,
        }))

    avisos: list[str] = []
    if nao_aderentes:
        avisos.append(f"{nao_aderentes} resultado(s) foram ocultados por evidência de não aderência.")
    if inconclusivos:
        avisos.append(f"{inconclusivos} resultado(s) foram mantidos com evidência inconclusiva e exigem validação manual.")
    return atualizados, " ".join(avisos) or None