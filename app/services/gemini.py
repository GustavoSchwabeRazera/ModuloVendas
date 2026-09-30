from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlparse

from google import genai
from google.genai import types

from app.config import Settings
from app.schemas import ConteudoComercial, ContextoTarifario, CriarProspeccaoRequest, LeadPotencial

logger = logging.getLogger(__name__)


class ErroProvedorIA(RuntimeError):
    pass


def _carregar_json(texto: str):
    """Aceita JSON puro e também a resposta que o modelo envolveu em Markdown."""
    conteudo = texto.strip()
    if conteudo.startswith("```"):
        conteudo = conteudo.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(conteudo)


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
- O valor de `panorama_comercial` pode usar Markdown internamente. Estruture-o com os títulos `### Canais de entrada`, `### Inteligência e networking`, `### Tendências e requisitos`, `### Oportunidades e riscos` e `### Logística e documentação`, nessa ordem. Abaixo de cada título, escreva um parágrafo curto; não use listas nem tabelas.
- O panorama precisa preservar a profundidade de um plano comercial completo. Em parágrafos distintos, cubra quando houver evidência: (1) principais canais de entrada B2B — distribuidores, importadores, atacadistas e trading companies; (2) feiras, câmaras e associações úteis para pesquisa ou networking; (3) notícias, tendências e mudanças regulatórias relevantes; (4) oportunidades e riscos; (5) cuidados logísticos, documentais e comerciais.
- Ao mencionar feira, evento, câmara ou associação, use somente nome oficial completo e atual, no idioma local ou em inglês, e apenas se houver evidência pública. Se não houver, omita em vez de inventar.
- Não omita uma informação relevante só porque o resultado será exibido em um único bloco de texto.

RESPONDA SOMENTE COM JSON VÁLIDO, sem texto adicional fora do JSON. O valor textual de `panorama_comercial` pode conter os títulos Markdown solicitados acima:
{{
  "panorama_comercial": "### Canais de entrada\\nParágrafo...\\n\\n### Inteligência e networking\\nParágrafo...\\n\\n### Tendências e requisitos\\nParágrafo...\\n\\n### Oportunidades e riscos\\nParágrafo...\\n\\n### Logística e documentação\\nParágrafo...",
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
                    conteudo = ConteudoComercial.model_validate(_carregar_json(texto))
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
) -> tuple[list[LeadPotencial], str | None]:
    """Pesquisa candidatos via Gemini; os links ainda passam por teste HTTP no servidor."""
    if not settings.gemini_api_key:
        raise ErroProvedorIA("GEMINI_API_KEY não está configurada no servidor.")

    prompt_empresas = f"""
 Aja como um pesquisador de mercado B2B de alto nível.
 Pesquise de 1 a 5 empresas REAIS e ativas no país '{entrada.pais_alvo}' que atuem como {entrada.perfil_parceiro or 'importadores/distribuidores'} do produto ou categoria '{entrada.nome_produto}'.

Sua resposta deve ser EXATAMENTE um array JSON. Não adicione textos antes ou depois.
Estrutura obrigatória de cada objeto:
{{
    "nome": "Nome oficial da empresa",
    "perfil_parceiro": "Ex: Distribuidor B2B / Atacadista (em português)",
    "justificativa": "Por que faz sentido prospectar esta empresa (uma frase factual em português)",
    "site": "https://dominio-oficial.exemplo"
}}

REGRAS:
1. USE A PESQUISA WEB para confirmar empresa, atuação B2B e domínio oficial.
2. Não inclua feiras, eventos, associações, câmaras, diretórios, marketplaces ou órgãos públicos.
3. Não adivinhe domínios. Se não houver URL oficial confiável, não inclua a empresa.
4. Não gere e-mails, telefones ou dados de contato.
5. Priorize precisão sobre quantidade; retorne [] se não houver candidatos confiáveis.
"""

    client = genai.Client(api_key=settings.gemini_api_key)
    
    # O prompt exige JSON; sem response_mime_type aqui a pesquisa Google aceita
    # respostas em bloco Markdown, que são normalizadas por _carregar_json.
    config = types.GenerateContentConfig(
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
                    empresas_encontradas = _carregar_json(texto_json)
                except json.JSONDecodeError as json_err:
                    logger.error("Erro ao fazer o parse do JSON do Gemini: %s", json_err)
                    raise ErroProvedorIA("A resposta da IA não veio em um formato estruturado válido.")
                if not isinstance(empresas_encontradas, list):
                    raise ErroProvedorIA("A resposta da IA não trouxe uma lista de empresas.")

                leads: list[LeadPotencial] = []
                for empresa in empresas_encontradas[:5]:
                    if not isinstance(empresa, dict):
                        continue
                    nome = str(empresa.get("nome") or "").strip()
                    site = str(empresa.get("site") or "").strip()
                    dominio = (urlparse(site).hostname or "").lower().removeprefix("www.")
                    if not nome or not dominio or not site.startswith(("https://", "http://")):
                        continue
                    leads.append(
                        LeadPotencial(
                            nome=nome,
                            dominio=dominio,
                            site=site,
                            fonte="Gemini + Google Search",
                            justificativa_validacao=str(empresa.get("justificativa") or "")[:240] or None,
                        )
                    )
                aviso = None if leads else "A pesquisa não encontrou empresas com domínio oficial confiável."
                return leads, aviso
            
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
