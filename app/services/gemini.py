from __future__ import annotations

import json
import logging
import time

from google import genai
from google.genai import types

from app.config import Settings
from app.schemas import ConteudoComercial, ContextoTarifario, CriarProspeccaoRequest

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


def _montar_relatorio(conteudo: ConteudoComercial) -> str:
    """Mantém o Markdown esperado pelo front atual durante a migração para JSON."""
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
    return (
        f"# PANORAMA_COMERCIAL\n{conteudo.panorama_comercial}\n\n"
        "# COMPRADORES_POTENCIAIS\nConsulte os compradores validados exibidos abaixo.\n\n"
        f"# PLANO_DE_ACAO_30_DIAS\n{plano_markdown}\n\n"
        "# E-MAIL_COMERCIAL\n"
        f"ASSUNTO: {conteudo.email_comercial.assunto}\n"
        f"CORPO:\n{conteudo.email_comercial.corpo}"
    )


def gerar_prospeccao(
    entrada: CriarProspeccaoRequest,
    settings: Settings,
    contexto_tarifario: ContextoTarifario | None = None,
) -> tuple[str, list[dict[str, str]], ConteudoComercial]:
    """Gera conteúdo estruturado para evitar seções misturadas ou e-mail vazio."""
    if not settings.gemini_api_key:
        raise ErroProvedorIA("GEMINI_API_KEY não está configurada no servidor.")

    codigo = entrada.ncm or entrada.hs6 or "não informado"
    contexto = entrada.contexto_origem
    origem = "Preenchimento manual" if not contexto else (
        f"Origem: {contexto.origem}; score de diagnóstico: {contexto.score_diagnostico}; "
        f"mercados recomendados: {', '.join(contexto.mercados_recomendados) or 'não informado'}"
    )
    dados_tarifarios = _formatar_contexto_tarifario(contexto_tarifario)
    
    prompt = f"""
Você é um especialista sênior em comércio exterior e vendas B2B internacionais.

CONTEXTO
- Produto: {entrada.nome_produto}
- Código HS6/NCM: {codigo}
- País-alvo: {entrada.pais_alvo}
- Disponibilidade: {entrada.disponibilidade or 'não informada'}
- Perfil de parceiro: {entrada.perfil_parceiro or 'a definir'}
- Contexto de origem: {origem}
- Dados históricos brasileiros: {dados_tarifarios}

REGRAS
- Use somente informações verificáveis; não invente empresas, contatos, URLs, certificações, tarifas, exigências ou dados de mercado.
- Dados históricos brasileiros são somente contexto, nunca demanda atual, tarifa ou regra do país-alvo.
- Feiras, eventos, associações, câmaras de comércio, diretórios, marketplaces e órgãos públicos podem ser mencionados apenas como fontes de inteligência, canais de acesso ou locais de networking; nunca como compradores.
- Não informe empresas, contatos ou links: compradores são pesquisados e validados pelo servidor separadamente.
- Não indique certificado fitossanitário para produto industrializado ou beneficiado sem evidência específica.
- Quando algo exigir confirmação, escreva exatamente: "Validar com o importador ou órgão competente".
- O e-mail deve ser escrito no idioma comercial predominante do país-alvo, sem mencionar IA.
- No panorama, mantenha toda a inteligência relevante em texto contínuo, sem listas, tabelas, subtítulos ou rótulos internos.
- O panorama precisa incluir, quando houver evidência: perfil e canais de entrada B2B (importadores, distribuidores, atacadistas e trading companies); tendências ou notícias setoriais; requisitos comerciais, logísticos e regulatórios; oportunidades e riscos; e feiras/câmaras/associações úteis para pesquisa ou networking. Não omita esses pontos só porque serão exibidos em um único bloco.

RESPONDA SOMENTE COM JSON VÁLIDO, sem Markdown ou texto adicional:
{{
  "panorama_comercial": "texto contínuo de 4 a 6 parágrafos, sem subtítulos internos, cobrindo canais B2B, tendências, requisitos, oportunidades, riscos e fontes de networking relevantes",
  "plano_acao_30_dias": {{
    "dias_1_7": ["2 a 4 ações objetivas"],
    "dias_8_14": ["2 a 4 ações objetivas"],
    "dias_15_21": ["2 a 4 ações objetivas"],
    "dias_22_30": ["2 a 4 ações objetivas"]
  }},
  "email_comercial": {{
    "assunto": "assunto comercial curto",
    "corpo": "e-mail completo, editável, com saudação, proposta, chamada para conversa e assinatura com [Nome da empresa] e [Nome do responsável]"
  }}
}}
""".strip()

    client = genai.Client(api_key=settings.gemini_api_key)
    if settings.exportai_pesquisa_web_ativa:
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.2,
        )
    else:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)

    for tentativa in range(3):
        try:
            response = client.models.generate_content(
                model=settings.exportai_gemini_model,
                contents=prompt,
                config=config,
            )
            texto = getattr(response, "text", None)
            if texto:
                try:
                    conteudo = ConteudoComercial.model_validate(json.loads(texto))
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ErroProvedorIA("A IA não retornou o formato comercial esperado.") from exc
                return _montar_relatorio(conteudo), extrair_fontes(response), conteudo
            raise ErroProvedorIA("O provedor de IA retornou uma resposta vazia.")
        except Exception as exc:
            logger.warning("Falha no Gemini (Plano Comercial), tentativa %s/3: %s", tentativa + 1, type(exc).__name__)
            if tentativa == 2:
                raise ErroProvedorIA("Não foi possível gerar a prospecção agora.") from exc
            time.sleep(2 ** tentativa)

    raise ErroProvedorIA("Não foi possível gerar a prospecção agora.")


def buscar_empresas_potenciais(
    entrada: CriarProspeccaoRequest,
    settings: Settings,
) -> list[dict]:
    """
    Substitui o Hunter.io. Retorna um array de dicionários (JSON) com 4 empresas reais 
    para o front-end renderizar os quadrados (cards) de validação de mercado.
    """
    if not settings.gemini_api_key:
        raise ErroProvedorIA("GEMINI_API_KEY não está configurada no servidor.")

    prompt_empresas = f"""
Aja como um pesquisador de mercado B2B de alto nível.
Busque 4 empresas REAIS e ativas no país '{entrada.pais_alvo}' que atuem como {entrada.perfil_parceiro or 'importadores/distribuidores'} do produto '{entrada.nome_produto}'.

Sua resposta deve ser EXATAMENTE um array JSON contendo 4 objetos. Não adicione textos antes ou depois.
Estrutura obrigatória de cada objeto:
{{
    "nome_empresa": "Nome oficial da empresa",
    "perfil_parceiro": "Ex: Distribuidor B2B / Atacadista (em português)",
    "justificativa": "Por que faz sentido prospectar esta empresa (1 frase curta, em português)",
    "site": "URL oficial completa (certifique-se da validade ou retorne null)",
    "email_contato": "E-mail de contato público geral, ex: info@, sales@ (se não encontrar na web, retorne null obrigatoriamente. NUNCA invente e-mails)"
}}

REGRAS:
1. USE A PESQUISA WEB para confirmar que as empresas e os sites são reais e pertencem a {entrada.pais_alvo}.
2. Se não tiver certeza absoluta do site ou do e-mail, coloque null.
3. Não use blocos de código Markdown (` ```json `), devolva apenas o JSON puro.
"""

    client = genai.Client(api_key=settings.gemini_api_key)
    
    # Configuramos a API para retornar estritamente JSON e forçamos o uso do Google Search
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2, # Temperatura baixa para focar em precisão e evitar alucinação
    )

    for tentativa in range(3):
        try:
            response = client.models.generate_content(
                model=settings.exportai_gemini_model,
                contents=prompt_empresas,
                config=config,
            )
            texto_json = getattr(response, "text", None)
            
            if texto_json:
                try:
                    # Faz o parse da string devolvida pela IA para um objeto Python nativo
                    empresas_encontradas = json.loads(texto_json)
                    return empresas_encontradas
                except json.JSONDecodeError as json_err:
                    logger.error("Erro ao fazer o parse do JSON do Gemini: %s", json_err)
                    raise ErroProvedorIA("A resposta da IA não veio em um formato estruturado válido.")
            
            raise ErroProvedorIA("O provedor de IA retornou uma resposta vazia.")
        except Exception as exc:
            logger.warning("Falha no Gemini (Busca de Empresas), tentativa %s/3: %s", tentativa + 1, type(exc).__name__)
            if tentativa == 2:
                raise ErroProvedorIA("Não foi possível buscar empresas potenciais agora.") from exc
            time.sleep(2 ** tentativa)

    raise ErroProvedorIA("Não foi possível buscar empresas potenciais agora.")


def _formatar_contexto_tarifario(contexto: ContextoTarifario | None) -> str:
    if not contexto:
        return "não disponível"
    if not contexto.operacoes:
        return contexto.observacao or "sem operações registradas"
    return (
        f"NCM {contexto.ncm}; HS6 {contexto.hs6}; {contexto.operacoes} operações entre "
        f"{contexto.ano_inicial} e {contexto.ano_final}; {contexto.kg_liquido} kg; "
        f"FOB US$ {contexto.valor_fob_usd}; alíquota média de II {contexto.aliquota_media_ii}% "
        f"(apenas importações brasileiras)."
    )
