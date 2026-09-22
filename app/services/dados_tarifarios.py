from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from app.config import Settings
from app.schemas import ContextoTarifario, CriarProspeccaoRequest

logger = logging.getLogger(__name__)

RAIZ_PROJETO = Path(__file__).resolve().parents[2]
ARQUIVO_INDICE = RAIZ_PROJETO / "data" / "indice_ncm_hs6.parquet"
ARQUIVO_FATO = RAIZ_PROJETO / "data" / "fato_importacoes_tarifas.parquet"


def buscar_contexto_tarifario(
    entrada: CriarProspeccaoRequest, settings: Settings
) -> tuple[ContextoTarifario | None, str | None]:
    """Resume a base local de importações brasileiras para o NCM/HS6 informado.

    A fonte registra operações de importação no Brasil. Por isso, seus números são
    usados como contexto de mercado e referência de alíquota de importação brasileira,
    nunca como tarifa do país-alvo de exportação.
    """
    if not settings.exportai_dados_tarifarios_ativos:
        return None, None
    if not ARQUIVO_INDICE.exists() or not ARQUIVO_FATO.exists():
        return None, "A base tarifária local não está disponível no servidor."

    try:
        with duckdb.connect(database=":memory:") as conexao:
            ncm, hs6, descricao = _resolver_codigo(conexao, entrada)
            if not ncm and not hs6:
                return None, "Não foi encontrado NCM/HS6 correspondente na base tarifária local."

            condicao, parametros = _condicao_consulta(ncm, hs6)
            linha = conexao.execute(
                f"""
                SELECT
                    MIN(CO_ANO) AS ano_inicial,
                    MAX(CO_ANO) AS ano_final,
                    COUNT(*) AS operacoes,
                    COALESCE(SUM(KG_LIQUIDO), 0) AS kg_liquido,
                    COALESCE(SUM(VL_FOB), 0) AS valor_fob,
                    COALESCE(SUM(VL_FRETE), 0) AS valor_frete,
                    COALESCE(SUM(VL_SEGURO), 0) AS valor_seguro,
                    AVG(PERCENTUAL_II) AS aliquota_media_ii
                FROM read_parquet(?)
                WHERE {condicao}
                """,
                [str(ARQUIVO_FATO), *parametros],
            ).fetchone()

        if not linha or not linha[2]:
            return ContextoTarifario(
                ncm=ncm,
                hs6=hs6,
                descricao_ncm=descricao,
                observacao="Não há operações de importação brasileira registradas para este código na base local.",
            ), None

        return ContextoTarifario(
            ncm=ncm,
            hs6=hs6,
            descricao_ncm=descricao,
            ano_inicial=int(linha[0]) if linha[0] is not None else None,
            ano_final=int(linha[1]) if linha[1] is not None else None,
            operacoes=int(linha[2]),
            kg_liquido=int(linha[3]),
            valor_fob_usd=int(linha[4]),
            valor_frete_usd=int(linha[5]),
            valor_seguro_usd=int(linha[6]),
            aliquota_media_ii=round(float(linha[7]), 2) if linha[7] is not None else None,
            observacao="Dados históricos de importações brasileiras; não representam a tarifa do país-alvo.",
        ), None
    except Exception as exc:
        logger.exception("Falha ao consultar a base tarifária local")
        return None, "Não foi possível consultar a base tarifária local agora."


def _resolver_codigo(conexao: duckdb.DuckDBPyConnection, entrada: CriarProspeccaoRequest) -> tuple[str | None, str | None, str | None]:
    if entrada.ncm:
        linha = conexao.execute(
            """
            SELECT NCM, HS6, descricao_ncm
            FROM read_parquet(?)
            WHERE NCM = ?
            LIMIT 1
            """,
            [str(ARQUIVO_INDICE), entrada.ncm],
        ).fetchone()
        if linha:
            return str(linha[0]), str(linha[1]), str(linha[2]) if linha[2] else None
        return entrada.ncm, entrada.ncm[:6], None

    if entrada.hs6:
        linha = conexao.execute(
            """
            SELECT NCM, HS6, descricao_ncm
            FROM read_parquet(?)
            WHERE HS6 = ?
            ORDER BY NCM
            LIMIT 1
            """,
            [str(ARQUIVO_INDICE), entrada.hs6],
        ).fetchone()
        if linha:
            return str(linha[0]), str(linha[1]), str(linha[2]) if linha[2] else None
        return None, entrada.hs6, None

    return None, None, None


def _condicao_consulta(ncm: str | None, hs6: str | None) -> tuple[str, list[str]]:
    if ncm:
        return "CO_NCM = ?", [ncm]
    return "CO_NCM LIKE ?", [f"{hs6}%"]
