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
Você é um analista sênior de comércio exterior e prospecção B2B internacional.

Crie uma inteligência comercial atual, objetiva e baseada exclusivamente em
pesquisa pública verificável para a exportação abaixo:

- Produto: {entrada.nome_produto}
- HS6/NCM: {codigo}
- País-alvo: {entrada.pais_alvo}
- Quantidade disponível: {entrada.disponibilidade or "não informada"}
- Perfil de comprador: {entrada.perfil_parceiro or "Importador B2B / Distribuidor / Atacadista"}
- Contexto recebido: {origem}
- Dados históricos brasileiros: {dados_tarifarios}

Use pesquisa web para obter informações atuais. Entregue o resultado em Markdown
com exatamente estas três seções:

# 1. Panorama atual do mercado
Explique de forma prática:
- cenário atual, canais de entrada e perfil de demanda;
- tendências, oportunidades e riscos;
- requisitos regulatórios, técnicos, sanitários, ambientais e aduaneiros;
- recomendação de posicionamento para o volume informado.

Só cite dados específicos, normas, tarifas ou tendências quando houver evidência
pública. Dados históricos brasileiros são apenas contexto e nunca representam
demanda, tarifa ou regra atual do país-alvo.

# 2. Compradores potenciais a validar
Liste somente empresas reais do país-alvo que atuem como importadoras,
distribuidoras B2B, atacadistas ou compradoras industriais aderentes ao produto.

Para cada empresa, informe:
- **Nome**
- **Perfil B2B**
- **Justificativa**: uma frase factual
- **Status**: Potencial a validar
- **Site**: [Visitar site](URL)

REGRAS PARA LINKS:
- Inclua apenas links encontrados e confirmados em pesquisa pública.
- A URL precisa ser o domínio oficial da empresa.
- Nunca invente empresa, domínio, link, contato ou e-mail.
- Se não houver site oficial confiável, não inclua a empresa.
- Exclua supermercados, varejistas, restaurantes, órgãos públicos,
  associações e empresas de máquinas/equipamentos quando não forem o parceiro
  B2B adequado ao produto.

# 3. E-mail comercial inicial

Gere um e-mail B2B pronto para revisão no idioma principal de
{entrada.pais_alvo}.

Formato obrigatório:

**Assunto:** curto, específico e profissional.

**E-mail:**
- Saudação formal adequada à cultura do país;
- Apresentação breve da empresa exportadora com campos editáveis:
  [Nome da empresa], [Cidade/País], [Site];
- Apresente {entrada.nome_produto}, disponibilidade de
  {entrada.disponibilidade or "volume a confirmar"} e proposta de valor;
- Não invente certificações, preços, prazos, estoque, clientes ou capacidade
  produtiva. Use campos como [certificação, se aplicável] quando necessário;
- Solicite uma ação simples: reunião breve, envio de catálogo ou avaliação de
  amostra;
- Encerramento profissional e assinatura editável:
  [Nome], [Cargo], [Empresa], [E-mail], [Telefone].

O texto deve ser direto, personalizado para o perfil
{entrada.perfil_parceiro or "importador/distribuidor B2B"} e ter no máximo
180 palavras. Não escreva explicações antes ou depois do e-mail.

Não invente certificações, preços, prazos, contatos, compradores confirmados ou
parcerias existentes. Quando não houver evidência suficiente, indique a limitação
de forma clara.
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
