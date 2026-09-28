from __future__ import annotations

import json
import logging
import time

from google import genai
from google.genai import types

from app.config import Settings
from app.schemas import ContextoTarifario, CriarProspeccaoRequest

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


def gerar_prospeccao(
    entrada: CriarProspeccaoRequest,
    settings: Settings,
    contexto_tarifario: ContextoTarifario | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Gera o plano comercial em Markdown (Tópicos 1 ao 6)."""
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
Você é um especialista em comércio exterior e vendas B2B internacionais.
Crie um plano comercial acionável, responsável e objetivo para exportar:
- Produto: {entrada.nome_produto}
- Código HS6/NCM: {codigo}
- País-alvo: {entrada.pais_alvo}
- Disponibilidade: {entrada.disponibilidade or 'não informada'}
- Perfil de parceiro procurado: {entrada.perfil_parceiro or 'a definir'}
- Contexto: {origem}
- Dados históricos de importação brasileira: {dados_tarifarios}

Entregue o relatório em Markdown, exatamente nos seis tópicos abaixo:

1. Análise de Mercado e Adequação do Produto
Avalie a entrada de {entrada.nome_produto} em {entrada.pais_alvo}, o posicionamento
adequado ao volume informado e requisitos comerciais relevantes.

2. Empresas Potenciais a Validar
Liste apenas importadores B2B, distribuidores, atacadistas ou compradores
industriais reais e aderentes ao produto no país-alvo. Para cada empresa, use:
- Nome oficial;
- Perfil ou papel B2B;
- Status: Potencial a validar;
- Justificativa factual curta;
- Link oficial em Markdown no formato [Visitar site](URL).

Inclua o link somente se ele for um domínio oficial confirmado em pesquisa
pública. Quando não houver URL confiável, escreva "Site oficial não confirmado".
Escreva exatamente: "Contato profissional: disponível no card, quando localizado
pelo sistema." Nunca invente ou deduza e-mails, contatos, domínios, empresas
ou relações comerciais.

3. Estratégia de Abordagem B2B
Descreva o primeiro contato, proposta de valor, tom de voz e CTA adequados ao
perfil de parceiro e à cultura de negócios de {entrada.pais_alvo}.

4. Termos de Comércio e Logística Recomendados
Sugira Incoterms e documentação inicial compatíveis com o produto, o volume e
o estágio comercial. Diferencie amostras ou lotes piloto de operações recorrentes.

5. Requisitos Regulatórios e Aduaneiros
Explique requisitos técnicos, sanitários, ambientais, regulatórios e aduaneiros
aplicáveis a {entrada.nome_produto} em {entrada.pais_alvo}. Só cite normas
específicas quando houver evidência pública suficiente.

6. Plano de Ação Imediato
Liste de três a cinco ações práticas, sequenciais e cronológicas para as
próximas semanas, incluindo preparação comercial e validação dos parceiros.

REGRAS CRÍTICAS ANTI-ALUCINAÇÃO:
- Nunca invente empresas, organizações, contatos, e-mails, volumes,
certificações, preços, tarifas, relações comerciais ou fontes.
- Não inclua supermercados, varejistas, restaurantes, governos, associações
ou empresas de máquinas e equipamentos quando não forem o perfil B2B adequado.
- Trate toda empresa como potencial a validar, jamais como cliente confirmado.
- Dados históricos de importação brasileira são somente contexto de mercado;
nunca os apresente como tarifa vigente, demanda atual ou volume garantido no
país-alvo.
- Se não houver evidência pública suficiente, declare a limitação claramente.
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
