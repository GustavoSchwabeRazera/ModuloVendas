from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Iterable
from urllib.parse import urlparse

from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from app.config import Settings
from app.schemas import (
    ConteudoComercial,
    ContextoTarifario,
    CriarProspeccaoRequest,
    FontePesquisa,
    LeadPotencial,
)

logger = logging.getLogger(__name__)


class ErroProvedorIA(RuntimeError):
    """Erro controlado ao consultar ou interpretar o provedor de IA."""


def _carregar_json(texto: str) -> Any:
    """Extrai o primeiro JSON válido, mesmo com Markdown ou texto residual."""
    conteudo = texto.strip()
    if not conteudo:
        raise json.JSONDecodeError("Resposta vazia", conteudo, 0)

    if conteudo.startswith("```"):
        primeira_quebra = conteudo.find("\n")
        ultima_cerca = conteudo.rfind("```")
        if primeira_quebra >= 0 and ultima_cerca > primeira_quebra:
            conteudo = conteudo[primeira_quebra + 1 : ultima_cerca].strip()

    decoder = json.JSONDecoder()
    try:
        return decoder.decode(conteudo)
    except json.JSONDecodeError:
        pass

    for indice, caractere in enumerate(conteudo):
        if caractere not in "[{":
            continue
        try:
            valor, _ = decoder.raw_decode(conteudo[indice:])
            return valor
        except json.JSONDecodeError:
            continue

    raise json.JSONDecodeError("Nenhum JSON válido encontrado", conteudo, 0)


def _ler(objeto: Any, *nomes: str, padrao: Any = None) -> Any:
    """Lê atributos ou chaves em snake_case e camelCase."""
    for nome in nomes:
        if isinstance(objeto, dict) and nome in objeto:
            return objeto[nome]
        valor = getattr(objeto, nome, None)
        if valor is not None:
            return valor
    return padrao


def _como_dict(objeto: Any) -> dict[str, Any] | None:
    if isinstance(objeto, dict):
        return objeto
    for metodo in ("model_dump", "to_dict"):
        funcao = getattr(objeto, metodo, None)
        if callable(funcao):
            try:
                valor = funcao()
                if isinstance(valor, dict):
                    return valor
            except Exception:
                pass
    return None


def _normalizar_url(valor: str | None) -> str | None:
    if not valor:
        return None
    url = valor.strip().strip('"\'<>(),.;')
    if not url:
        return None
    if not url.startswith(("https://", "http://")):
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return url


def _adicionar_fonte(
    fontes: list[FontePesquisa],
    vistos: set[str],
    titulo: str | None,
    url: str | None,
) -> None:
    normalizada = _normalizar_url(url)
    if not normalizada:
        return
    chave = normalizada.rstrip("/").lower()
    if chave in vistos:
        return
    vistos.add(chave)
    fontes.append(
        FontePesquisa(
            titulo=(titulo or url or normalizada).strip()[:300],
            url=normalizada,
        )
    )


def extrair_fontes(response: Any) -> list[FontePesquisa]:
    """Extrai fontes do grounding em formatos snake_case ou camelCase."""
    fontes: list[FontePesquisa] = []
    vistos: set[str] = set()

    candidatos = _ler(response, "candidates", padrao=[]) or []
    for candidato in candidatos:
        metadata = _ler(
            candidato,
            "grounding_metadata",
            "groundingMetadata",
        )
        if not metadata:
            continue

        chunks = _ler(
            metadata,
            "grounding_chunks",
            "groundingChunks",
            padrao=[],
        ) or []
        for chunk in chunks:
            web = _ler(chunk, "web")
            if not web:
                continue
            _adicionar_fonte(
                fontes,
                vistos,
                _ler(web, "title", "titulo"),
                _ler(web, "uri", "url"),
            )

        # Alguns formatos expõem resultados da pesquisa separadamente.
        resultados = _ler(
            metadata,
            "web_search_results",
            "webSearchResults",
            padrao=[],
        ) or []
        for resultado in resultados:
            _adicionar_fonte(
                fontes,
                vistos,
                _ler(resultado, "title", "titulo"),
                _ler(resultado, "uri", "url"),
            )

    # Fallback para versões do SDK que serializam melhor do que expõem atributos.
    serializado = _como_dict(response)
    if serializado:
        for candidato in serializado.get("candidates", []):
            metadata = (
                candidato.get("grounding_metadata")
                or candidato.get("groundingMetadata")
                or {}
            )
            chunks = (
                metadata.get("grounding_chunks")
                or metadata.get("groundingChunks")
                or []
            )
            for chunk in chunks:
                web = chunk.get("web") or {}
                _adicionar_fonte(
                    fontes,
                    vistos,
                    web.get("title"),
                    web.get("uri") or web.get("url"),
                )

    return fontes[:20]


def _fontes_do_conteudo(conteudo: ConteudoComercial) -> list[FontePesquisa]:
    fontes: list[FontePesquisa] = []
    vistos: set[str] = set()
    for fonte in conteudo.fontes_oficiais:
        _adicionar_fonte(fontes, vistos, fonte.nome, fonte.url)
    for evento in conteudo.feiras_eventos:
        if evento.site:
            _adicionar_fonte(fontes, vistos, evento.nome, evento.site)
    return fontes


def combinar_fontes(
    grounding: Iterable[FontePesquisa],
    conteudo: ConteudoComercial,
) -> list[FontePesquisa]:
    """Combina grounding e URLs estruturadas sem duplicação."""
    fontes: list[FontePesquisa] = []
    vistos: set[str] = set()
    for fonte in [*grounding, *_fontes_do_conteudo(conteudo)]:
        _adicionar_fonte(fontes, vistos, fonte.titulo, fonte.url)
    return fontes[:20]


def _normalizar_site(site: str | None) -> str | None:
    return _normalizar_url(site)


def _montar_relatorio(conteudo: ConteudoComercial) -> str:
    plano = conteudo.plano_acao_30_dias
    etapas = (
        ("Dias 1–7", plano.dias_1_7),
        ("Dias 8–14", plano.dias_8_14),
        ("Dias 15–21", plano.dias_15_21),
        ("Dias 22–30", plano.dias_22_30),
    )
    plano_markdown = "\n\n".join(
        f"## {titulo}\n" + "\n".join(f"- {item}" for item in itens)
        for titulo, itens in etapas
    )
    blocos = [
        f"# PANORAMA_COMERCIAL\n{conteudo.panorama_comercial}",
        f"# PLANO_DE_ACAO_30_DIAS\n{plano_markdown}",
    ]

    if conteudo.feiras_eventos:
        linhas: list[str] = []
        for feira in conteudo.feiras_eventos:
            linha = f"- {feira.nome}"
            if feira.site:
                linha += f" ({feira.site})"
            if feira.localizacao:
                linha += f" — {feira.localizacao}"
            linhas.append(linha)
        blocos.append("# FEIRAS_E_EVENTOS\n" + "\n".join(linhas))

    if conteudo.fontes_oficiais:
        linhas = [
            f"- {fonte.nome}: {fonte.url}"
            for fonte in conteudo.fontes_oficiais
        ]
        blocos.append("# FONTES_OFICIAIS\n" + "\n".join(linhas))

    blocos.append(
        "# E-MAIL_COMERCIAL\n"
        f"ASSUNTO: {conteudo.email_comercial.assunto}\n"
        f"CORPO:\n{conteudo.email_comercial.corpo}"
    )
    return "\n\n".join(blocos)


def extrair_leads_sugeridos(
    conteudo: ConteudoComercial,
) -> tuple[list[LeadPotencial], str | None]:
    leads: list[LeadPotencial] = []
    chaves_vistas: set[str] = set()

    for empresa in conteudo.empresas_sugeridas:
        site = _normalizar_site(empresa.site)
        dominio = None
        if site:
            dominio = (
                (urlparse(site).hostname or "")
                .lower()
                .removeprefix("www.")
                or None
            )
        chave = dominio or empresa.nome.casefold()
        if chave in chaves_vistas:
            continue
        chaves_vistas.add(chave)
        leads.append(
            LeadPotencial(
                nome=empresa.nome,
                dominio=dominio,
                site=site,
                fonte="Gemini + Google Search" if site else "Gemini",
                justificativa_validacao=empresa.justificativa,
                nivel_cobertura=empresa.nivel_cobertura,
                mercado_atendido=empresa.mercado_atendido,
            )
        )

    aviso = None
    if not leads:
        aviso = (
            "Nenhuma empresa com evidência pública suficiente foi sugerida "
            "nesta consulta."
        )
    return leads, aviso


def _formatar_disponibilidade(entrada: CriarProspeccaoRequest) -> str:
    if entrada.quantidade_disponivel is not None:
        unidade = entrada.unidade_disponibilidade or "unidade não informada"
        return f"{entrada.quantidade_disponivel:g} {unidade}"
    return entrada.disponibilidade or "não informada"


def _formatar_contexto_tarifario(
    contexto: ContextoTarifario | None,
) -> str:
    if not contexto:
        return "não disponível"
    if not contexto.operacoes:
        return contexto.observacao or "sem operações registradas"
    return (
        "Contexto histórico de importações brasileiras, não aplicável como "
        f"tarifa do país-alvo: NCM {contexto.ncm}; HS6 {contexto.hs6}; "
        f"{contexto.operacoes} operações entre {contexto.ano_inicial} e "
        f"{contexto.ano_final}; {contexto.kg_liquido} kg; "
        f"FOB US$ {contexto.valor_fob_usd}."
    )


def _montar_prompt(
    entrada: CriarProspeccaoRequest,
    contexto_tarifario: ContextoTarifario | None,
) -> str:
    codigo = entrada.ncm or entrada.hs6 or "não informado"
    contexto = entrada.contexto_origem
    origem = "Preenchimento manual"
    if contexto:
        origem = (
            f"Origem: {contexto.origem}; score de diagnóstico: "
            f"{contexto.score_diagnostico}; mercados recomendados: "
            f"{', '.join(contexto.mercados_recomendados) or 'não informado'}"
        )

    return f"""
Você é especialista sênior em comércio exterior e vendas B2B internacionais.
Produza um Plano de Entrada no Mercado factual, prático e auditável.

DADOS FORNECIDOS PELO USUÁRIO
- Produto: {entrada.nome_produto}
- Código HS6/NCM: {codigo}
- País-alvo: {entrada.pais_alvo}
- Disponibilidade declarada: {_formatar_disponibilidade(entrada)}
- Perfil de parceiro: {entrada.perfil_parceiro or 'a definir'}
- Idioma solicitado para o e-mail: {entrada.idioma_alvo}
- Contexto de origem: {origem}
- Dados históricos brasileiros: {_formatar_contexto_tarifario(contexto_tarifario)}

REGRAS DE VERACIDADE
- Pesquise na web quando a ferramenta estiver disponível.
- Não invente empresas, URLs, feiras, órgãos, contatos, certificações, tarifas,
  exigências, capacidades ou dados de mercado.
- Diferencie informação verificada, recomendação e ponto a confirmar.
- Dados históricos brasileiros são contexto e não representam demanda, tarifa ou
  regra do país-alvo.
- Não afirme que a empresa do usuário possui fábrica, certificação, controle de
  qualidade, rastreabilidade, embalagem especial, regularidade logística,
  capacidade produtiva ou experiência exportadora, pois esses dados não foram
  fornecidos.
- A disponibilidade declarada é uma informação do usuário. Não a converta em
  capacidade produtiva garantida nem em compromisso de fornecimento regular.
- Não afirme que documentos estão anexados.
- Quando uma exigência depender de confirmação, escreva exatamente:
  "Validar com o importador ou órgão competente".
- O e-mail deve estar integralmente no idioma solicitado, sem misturar português,
  e deve usar linguagem condicional para capacidades não comprovadas.

PANORAMA COMERCIAL
- Use Markdown apenas em panorama_comercial.
- Use, nesta ordem, os títulos: "### Canais de entrada",
  "### Inteligência e networking", "### Tendências e requisitos",
  "### Oportunidades e riscos" e "### Logística e documentação".
- Escreva um parágrafo curto sob cada título, sem listas nem tabelas.

EMPRESAS SUGERIDAS
- Sugira de zero a cinco empresas apenas com evidência pública.
- Priorize o perfil solicitado e o produto/categoria informados.
- Não trate feira, evento, associação, câmara, diretório, marketplace ou órgão
  público como comprador.
- Não afirme importação do produto exato sem evidência específica.
- Use LOCAL apenas com evidência de sede ou operação no país-alvo.
- Não adivinhe URLs. Use null quando não houver domínio oficial confiável.

FEIRAS E FONTES
- Retorne somente itens reais encontrados e URLs oficiais verificáveis.
- Retorne de zero a oito feiras/eventos e de zero a oito fontes oficiais.
- Omita qualquer item sem evidência suficiente.

PLANO DE 30 DIAS
- Forneça de duas a quatro ações por período.
- Não suponha resposta, aprovação, envio de amostra ou negociação concluída.
- Após o primeiro contato, use condicionais como "Se houver retorno positivo".

E-MAIL COMERCIAL
- Não prometa características não fornecidas pelo usuário.
- Apresente o produto e a disponibilidade declarada como informação preliminar.
- Convide o destinatário a informar requisitos e interesse.
- Use campos editáveis para nome, empresa e contato.

Responda exclusivamente conforme o schema estruturado fornecido pela API.
""".strip()


def _sanitizar_json_schema(valor: Any) -> Any:
    if isinstance(valor, dict):
        bloqueadas = {"additionalProperties", "title", "default", "examples"}
        return {
            chave: _sanitizar_json_schema(conteudo)
            for chave, conteudo in valor.items()
            if chave not in bloqueadas
        }
    if isinstance(valor, list):
        return [_sanitizar_json_schema(item) for item in valor]
    return valor


def _configurar_geracao(settings: Settings) -> types.GenerateContentConfig:
    parametros: dict[str, Any] = {
        "response_mime_type": "application/json",
        "response_json_schema": _sanitizar_json_schema(
            ConteudoComercial.model_json_schema()
        ),
        "temperature": 0.1,
    }
    if settings.exportai_pesquisa_web_ativa:
        parametros["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    return types.GenerateContentConfig(**parametros)


def _interpretar_resposta(response: Any) -> ConteudoComercial:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, ConteudoComercial):
        return parsed
    if parsed is not None:
        return ConteudoComercial.model_validate(parsed)
    texto = getattr(response, "text", None)
    if not texto:
        raise ErroProvedorIA("O provedor de IA retornou uma resposta vazia.")
    return ConteudoComercial.model_validate(_carregar_json(texto))


def _erro_transitorio(exc: Exception) -> bool:
    codigo = getattr(exc, "code", None)
    return isinstance(exc, errors.ServerError) or codigo in {
        408,
        429,
        500,
        502,
        503,
        504,
    }


def gerar_prospeccao(
    entrada: CriarProspeccaoRequest,
    settings: Settings,
    contexto_tarifario: ContextoTarifario | None = None,
) -> tuple[str, list[FontePesquisa], ConteudoComercial]:
    """Gera conteúdo estruturado, fontes deduplicadas e relatório compatível."""
    if not settings.gemini_api_key:
        raise ErroProvedorIA("GEMINI_API_KEY não está configurada no servidor.")

    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = _montar_prompt(entrada, contexto_tarifario)
    config = _configurar_geracao(settings)

    for tentativa in range(1, 4):
        try:
            response = client.models.generate_content(
                model=settings.exportai_gemini_model,
                contents=prompt,
                config=config,
            )
            conteudo = _interpretar_resposta(response)
            fontes = combinar_fontes(extrair_fontes(response), conteudo)
            return _montar_relatorio(conteudo), fontes, conteudo

        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            logger.warning("Resposta comercial inválida do Gemini: %s", str(exc)[:800])
            raise ErroProvedorIA(
                "A IA respondeu, mas o conteúdo não correspondeu ao formato "
                "comercial esperado."
            ) from exc

        except errors.ClientError as exc:
            codigo = getattr(exc, "code", None)
            logger.warning(
                "Falha do cliente Gemini: code=%s status=%s message=%s",
                codigo,
                getattr(exc, "status", None),
                str(getattr(exc, "message", exc))[:800],
            )
            if codigo == 429 and settings.exportai_pesquisa_web_ativa:
                raise ErroProvedorIA(
                    "A pesquisa web do Gemini está sem cota disponível para "
                    "este projeto."
                ) from exc
            if not _erro_transitorio(exc) or tentativa == 3:
                raise ErroProvedorIA(
                    "O Gemini recusou a solicitação. Verifique modelo, cota e "
                    "configuração do projeto."
                ) from exc

        except errors.ServerError as exc:
            logger.warning(
                "Gemini temporariamente indisponível: code=%s status=%s message=%s",
                getattr(exc, "code", None),
                getattr(exc, "status", None),
                str(getattr(exc, "message", exc))[:800],
            )
            if tentativa == 3:
                raise ErroProvedorIA(
                    "O Gemini está temporariamente indisponível. "
                    "Tente novamente em alguns minutos."
                ) from exc

        except ErroProvedorIA:
            raise

        except Exception as exc:
            logger.exception("Falha inesperada ao gerar o plano comercial")
            raise ErroProvedorIA(
                "Ocorreu uma falha interna ao gerar a prospecção."
            ) from exc

        time.sleep((2 ** (tentativa - 1)) + random.uniform(0, 0.5))

    raise ErroProvedorIA("Não foi possível gerar a prospecção agora.")
