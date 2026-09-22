# ExportAI — Módulo Vendas

Backend independente para gerar um plano de prospecção B2B internacional com IA.

## O que foi reaproveitado

A lógica central do backend anterior: produto, código fiscal, país e idioma são enviados ao Gemini para montar uma estratégia comercial e um e-mail inicial.

## O que mudou

- endpoint versionado: `POST /api/v1/prospeccoes`;
- CORS definido por `EXPORTAI_CORS_ORIGINS`, sem curinga;
- chave `GEMINI_API_KEY` fica apenas no Render;
- entrada aceita HS6 com NCM opcional;
- contexto de módulos anteriores é opcional: a API também funciona por preenchimento manual;
- a IA é instruída a não inventar empresas, contatos ou dados comerciais verificados.
- leads do Hunter são validados em lote por evidências públicas antes de receberem
  o status de aderência ao produto; essa etapa pode ser desligada com
  `EXPORTAI_VALIDACAO_LEADS_ATIVA=false`.

## Executar localmente

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Documentação: `http://127.0.0.1:8000/docs`.

## Exemplo de requisição

```json
{
  "hs6": "090111",
  "nome_produto": "Café especial arábica em grãos",
  "pais_alvo": "Alemanha",
  "idioma_alvo": "Alemão",
  "disponibilidade": "30 toneladas por mês",
  "perfil_parceiro": "Distribuidor B2B / Importador",
  "contexto_origem": {
    "origem": "modulo-diagnostico",
    "score_diagnostico": 74,
    "mercados_recomendados": ["Alemanha", "Estados Unidos"]
  }
}
```

O campo `contexto_origem` é opcional. Não envie tokens, chaves ou informações pessoais pelo navegador.
# Base tarifária local

Os arquivos `data/indice_ncm_hs6.parquet` e `data/fato_importacoes_tarifas.parquet`
compõem a base local do módulo. O primeiro converte NCM em HS6; o segundo resume
operações históricas de importação no Brasil. Os dados enriquecem a prospecção, mas
não devem ser tratados como tarifas ou demanda do país-alvo.
