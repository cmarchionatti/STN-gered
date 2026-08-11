# =============================================================================
#
#   HIATO FISCAL DAS UNIDADES DA FEDERAÇÃO DO BRASIL
#   NOTEBOOK 2 — SILVER: Tratamento e Transformação dos Dados
#
#   Inputs (Delta Tables Bronze):
#     - bronze_siconfi_raw
#     - bronze_fcdf_raw
#     - bronze_populacao_total
#     - bronze_ipca
#     - bronze_pib_uf
#     - bronze_pop_faixa_etaria
#     - bronze_homicidios
#     - bronze_codigos_contabeis
#
#   Outputs (Delta Tables Silver):
#     - silver_receitas_siconfi
#     - silver_despesas_siconfi
#     - silver_fcdf
#     - silver_populacao_total
#     - silver_ipca_deflator
#     - silver_pib_uf
#     - silver_pop_faixa_etaria
#     - silver_homicidios
#     - silver_codigos_contabeis
#     - silver_deputados_uf
#
# =============================================================================

# =============================================================================
# CÉLULA 1 — Configurações do Usuário
# =============================================================================

ANO_INICIO_ANALISE = 2019
ANO_FIM_ANALISE    = 2022
ANO_BASE_DEFLACAO  = 2022   # ano-base para deflação (IPCA de dezembro)
TIPO_DESPESA       = "Despesas Empenhadas"

periodo_analise = list(range(ANO_INICIO_ANALISE, ANO_FIM_ANALISE + 1))
print(f"Período de análise: {periodo_analise}")
print(f"Ano-base deflação: {ANO_BASE_DEFLACAO}")
print(f"Tipo de despesa: {TIPO_DESPESA}")


# =============================================================================
# CÉLULA 2 — Imports
# =============================================================================

import pandas as pd
import numpy as np
import re

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, IntegerType

spark = SparkSession.builder.getOrCreate()

# Mapeamento nome completo → sigla (necessário para dados Sidra)
NOME_PARA_SIGLA = {
    "Acre": "AC", "Alagoas": "AL", "Amapá": "AP", "Amazonas": "AM",
    "Bahia": "BA", "Ceará": "CE", "Distrito Federal": "DF",
    "Espírito Santo": "ES", "Goiás": "GO", "Maranhão": "MA",
    "Mato Grosso": "MT", "Mato Grosso do Sul": "MS", "Minas Gerais": "MG",
    "Pará": "PA", "Paraíba": "PB", "Paraná": "PR", "Pernambuco": "PE",
    "Piauí": "PI", "Rio de Janeiro": "RJ", "Rio Grande do Norte": "RN",
    "Rio Grande do Sul": "RS", "Rondônia": "RO", "Roraima": "RR",
    "Santa Catarina": "SC", "São Paulo": "SP", "Sergipe": "SE",
    "Tocantins": "TO"
}

# Mapeamento código IBGE → sigla (para THOMIC/IPEA)
UF_MAP = {
    11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO",
    21: "MA", 22: "PI", 23: "CE", 24: "RN", 25: "PB", 26: "PE", 27: "AL",
    28: "SE", 29: "BA", 31: "MG", 32: "ES", 33: "RJ", 35: "SP",
    41: "PR", 42: "SC", 43: "RS", 50: "MS", 51: "MT", 52: "GO", 53: "DF"
}

print("Imports concluídos.")


# =============================================================================
# CÉLULA 3 — Silver: IPCA — Deflator por Ano
# =============================================================================

print("\n===== Processando IPCA =====")

ipca_raw = spark.table("bronze_ipca").toPandas()

# As colunas do Sidra têm nomes padronizados como "D2N" (mês/ano) e "V" (valor)
# Identificamos as colunas relevantes
col_mes  = [c for c in ipca_raw.columns if c == "D3N"][0]   # mês/ano: "dezembro 2022"
col_val  = [c for c in ipca_raw.columns if c in ("V", "Valor")][0]

ipca_pd = ipca_raw[[col_mes, col_val]].copy()
ipca_pd.columns = ["mes_ano", "ipca_mensal"]
ipca_pd["ipca_mensal"] = pd.to_numeric(ipca_pd["ipca_mensal"], errors="coerce")
ipca_pd["Ano"]  = ipca_pd["mes_ano"].str.extract(r"(\d{4})$").astype(int)
ipca_pd["mes"]  = ipca_pd["mes_ano"].str.replace(r"\s\d{4}$", "", regex=True).str.strip()

# Mantém apenas dezembro
ipca_dez = ipca_pd[ipca_pd["mes"].str.lower() == "dezembro"].copy()
ipca_dez = ipca_dez.dropna(subset=["ipca_mensal"]).reset_index(drop=True)

# Calcula deflator em relação ao ano-base escolhido
ipca_base_val = ipca_dez.loc[ipca_dez["Ano"] == ANO_BASE_DEFLACAO, "ipca_mensal"].values[0]
ipca_dez["deflator"] = ipca_base_val / ipca_dez["ipca_mensal"]

# Filtra apenas os anos do período de análise
ipca_silver = ipca_dez[ipca_dez["Ano"].isin(periodo_analise)][["Ano", "deflator"]].copy()
ipca_silver = ipca_silver.rename(columns={"deflator": f"deflator_dez_{ANO_BASE_DEFLACAO % 100:02d}"})

df_ipca_spark = spark.createDataFrame(ipca_silver)
df_ipca_spark.write.format("delta").mode("overwrite").saveAsTable("silver_ipca_deflator")
print(f"silver_ipca_deflator: {len(ipca_silver)} linhas")
print(ipca_silver)


# =============================================================================
# CÉLULA 4 — Silver: Populações Totais por UF
# =============================================================================

print("\n===== Processando Populações Totais =====")

pop_raw = spark.table("bronze_populacao_total").toPandas()

# Colunas esperadas do Sidra (D1N = UF, D2N = ano, V = valor)
col_uf_nome = "D1N"   # nome da UF
col_ano_pop = "D3N"   # ano (ex: "2020")
col_pop_val = "V"     # valor

pop_pd = pop_raw[[col_uf_nome, col_ano_pop, col_pop_val]].copy()
pop_pd.columns = ["uf_nome", "Ano", "populacao_total"]
pop_pd["Ano"]            = pd.to_numeric(pop_pd["Ano"], errors="coerce")
pop_pd["populacao_total"] = pd.to_numeric(pop_pd["populacao_total"], errors="coerce")
pop_pd["SIGLA"]          = pop_pd["uf_nome"].map(NOME_PARA_SIGLA)

pop_silver = pop_pd.dropna(subset=["SIGLA", "populacao_total"])[
    ["SIGLA", "Ano", "populacao_total"]
].sort_values(["SIGLA", "Ano"]).reset_index(drop=True)

# Filtra período de análise
pop_silver = pop_silver[pop_silver["Ano"].isin(periodo_analise)]

df_pop_spark = spark.createDataFrame(pop_silver)
df_pop_spark.write.format("delta").mode("overwrite").saveAsTable("silver_populacao_total")
print(f"silver_populacao_total: {len(pop_silver)} linhas")


# =============================================================================
# CÉLULA 5 — Silver: PIB por UF
# =============================================================================

print("\n===== Processando PIB por UF =====")

pib_raw = spark.table("bronze_pib_uf").toPandas()

col_uf_pib  = "D1N"   # nome da UF
col_ano_pib = "D3N"   # ano
col_pib_val = "V"     # valor

pib_pd = pib_raw[[col_uf_pib, col_ano_pib, col_pib_val]].copy()
pib_pd.columns = ["Estado", "ano", "pib"]
pib_pd["ano"]   = pd.to_numeric(pib_pd["ano"], errors="coerce")
pib_pd["pib"]   = pd.to_numeric(pib_pd["pib"], errors="coerce")
pib_pd["Sigla"] = pib_pd["Estado"].map(NOME_PARA_SIGLA)

pib_silver = pib_pd.dropna(subset=["Sigla", "pib"])[
    ["Sigla", "Estado", "ano", "pib"]
].sort_values(["Sigla", "ano"]).reset_index(drop=True)

pib_silver = pib_silver[pib_silver["ano"].isin(periodo_analise)]

df_pib_spark = spark.createDataFrame(pib_silver)
df_pib_spark.write.format("delta").mode("overwrite").saveAsTable("silver_pib_uf")
print(f"silver_pib_uf: {len(pib_silver)} linhas")


# =============================================================================
# CÉLULA 6 — Silver: FCDF — Fundo Constitucional do DF
# =============================================================================

print("\n===== Processando FCDF =====")

fcdf_raw = spark.table("bronze_fcdf_raw").toPandas()

# Endpoint resultadoGrafico devolve, por ano, várias linhas com o campo `valores`
# em texto livre ("Pagamentos realizados pelo próprio órgão: R$ 1.234.567,89").
# Para reproduzir o script R, filtramos as linhas com "próprio órgão" e extraímos
# o valor monetário com regex.
_re_valor_br = re.compile(r"R\$\s*([\d\.\,]+)")

def parse_valor_br_texto(texto):
    """Extrai valor R$ de texto livre no formato brasileiro."""
    if not isinstance(texto, str):
        return np.nan
    m = _re_valor_br.search(texto)
    if not m:
        return np.nan
    try:
        return float(m.group(1).replace(".", "").replace(",", "."))
    except ValueError:
        return np.nan

fcdf_pd = fcdf_raw.copy()
fcdf_pd["Ano"]     = pd.to_numeric(fcdf_pd["ano"], errors="coerce")
fcdf_pd["valores"] = fcdf_pd["valores"].astype(str)

fcdf_pd = fcdf_pd[fcdf_pd["valores"].str.contains("próprio órgão", case=False, na=False)]
fcdf_pd["Valor"] = fcdf_pd["valores"].apply(parse_valor_br_texto)

fcdf_silver = fcdf_pd.dropna(subset=["Valor", "Ano"])[["Ano", "Valor"]].copy()
fcdf_silver["Descricao"] = "FCDF"
fcdf_silver = fcdf_silver[fcdf_silver["Ano"].isin(periodo_analise)]

df_fcdf_spark = spark.createDataFrame(fcdf_silver)
(df_fcdf_spark.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("silver_fcdf"))
print(f"silver_fcdf: {len(fcdf_silver)} linhas")
print(fcdf_silver)


# =============================================================================
# CÉLULA 7 — Silver: Receitas Siconfi
# =============================================================================

print("\n===== Processando Receitas Siconfi =====")

siconfi_raw = spark.table("bronze_siconfi_raw").toPandas()

# Padroniza tipos
for col_num in ["valor", "exercicio"]:
    if col_num in siconfi_raw.columns:
        siconfi_raw[col_num] = pd.to_numeric(siconfi_raw[col_num], errors="coerce")

# Receitas: Anexo I-C, coluna Receitas Brutas Realizadas
receitas_pd = siconfi_raw[
    (siconfi_raw["anexo"] == "DCA-Anexo I-C") &
    (siconfi_raw["coluna"] == "Receitas Brutas Realizadas")
].copy()

receitas_pd = receitas_pd.rename(columns={
    "exercicio": "AN_EXERCICIO",
    "uf":        "SG_ENTE",
    "conta":     "ELEMENTLABEL",
    "coluna":    "NO_LABEL_EIXO_X",
    "valor":     "VALUE"
})

# Deflaciona
deflator_col = f"deflator_dez_{ANO_BASE_DEFLACAO % 100:02d}"
ipca_silver_local = ipca_silver.rename(columns={deflator_col: "deflator"})
receitas_pd = receitas_pd.merge(
    ipca_silver_local[["Ano", "deflator"]],
    left_on="AN_EXERCICIO", right_on="Ano", how="left"
)
receitas_pd["VALUE"] = receitas_pd["VALUE"] * receitas_pd["deflator"]

# Adiciona população total
receitas_pd = receitas_pd.merge(
    pop_silver, left_on=["AN_EXERCICIO", "SG_ENTE"], right_on=["Ano", "SIGLA"], how="left"
)

receitas_silver = receitas_pd[
    receitas_pd["AN_EXERCICIO"].isin(periodo_analise)
][[
    "AN_EXERCICIO", "SG_ENTE", "ELEMENTLABEL", "NO_LABEL_EIXO_X",
    "VALUE", "populacao_total"
]].copy()

df_rec_spark = spark.createDataFrame(receitas_silver)
df_rec_spark.write.format("delta").mode("overwrite").saveAsTable("silver_receitas_siconfi")
print(f"silver_receitas_siconfi: {len(receitas_silver)} linhas")


# =============================================================================
# CÉLULA 8 — Silver: Despesas Siconfi
# =============================================================================

print("\n===== Processando Despesas Siconfi =====")

# Despesas: Anexo I-E
despesas_pd = siconfi_raw[
    siconfi_raw["anexo"] == "DCA-Anexo I-E"
].copy()

# Renomeia
despesas_pd = despesas_pd.rename(columns={
    "exercicio":   "Ano",
    "uf":          "UF",
    "conta":       "NO_LABEL_EIXO_Y",   # função de gasto
    "coluna":      "NO_LABEL_EIXO_X",   # tipo de despesa
    "valor":       "VALUE",
    "instituicao": "NO_ORGAO",
    "id_ente":     "NO_ENTE",
    "populacao":   "QT_HABITANTE",
    "rotulo":      "ELEMENTLABEL"
})

for col_num in ["VALUE", "Ano"]:
    if col_num in despesas_pd.columns:
        despesas_pd[col_num] = pd.to_numeric(despesas_pd[col_num], errors="coerce")

# Deflaciona
despesas_pd = despesas_pd.merge(
    ipca_silver_local[["Ano", "deflator"]],
    on="Ano", how="left"
)
despesas_pd["Valor"] = despesas_pd["VALUE"] * despesas_pd["deflator"]

# Adiciona população (IBGE) — mantém QT_HABITANTE (Siconfi) como coluna separada
despesas_pd["QT_HABITANTE"] = pd.to_numeric(despesas_pd["QT_HABITANTE"], errors="coerce")

# Adiciona PIB
despesas_pd = despesas_pd.merge(
    pib_silver[["Sigla", "ano", "pib"]],
    left_on=["UF", "Ano"], right_on=["Sigla", "ano"], how="left"
)

# Filtra pelo tipo de despesa e período de análise
despesas_silver = despesas_pd[
    (despesas_pd["NO_LABEL_EIXO_X"] == TIPO_DESPESA) &
    (despesas_pd["Ano"].isin(periodo_analise))
][[
    "Ano", "UF", "ELEMENTLABEL", "NO_LABEL_EIXO_X", "NO_LABEL_EIXO_Y",
    "VALUE", "Valor", "NO_ORGAO", "QT_HABITANTE", "pib"
]].copy()

# População usada nas regressões de gasto = QT_HABITANTE (Siconfi), igual ao R.
# A população do IBGE Sidra é mantida como coluna separada (Populacao_IBGE).
despesas_silver = despesas_silver.merge(
    pop_silver.rename(columns={"SIGLA": "UF", "Ano": "Ano_pop",
                               "populacao_total": "Populacao_IBGE"}),
    left_on=["UF", "Ano"], right_on=["UF", "Ano_pop"], how="left"
)
despesas_silver = despesas_silver.drop(columns=["Ano_pop"], errors="ignore")
despesas_silver = despesas_silver.rename(columns={
    "QT_HABITANTE":    "População",
    "NO_LABEL_EIXO_Y": "Conta",
})

df_desp_spark = spark.createDataFrame(despesas_silver.astype(str))
(df_desp_spark.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("silver_despesas_siconfi"))
print(f"silver_despesas_siconfi: {len(despesas_silver)} linhas")


# =============================================================================
# CÉLULA 9 — Silver: Populações por Faixa Etária
# =============================================================================

print("\n===== Processando Populações por Faixa Etária =====")

pop_faixa_raw = spark.table("bronze_pop_faixa_etaria").toPandas()

pop_faixa_raw["idade"] = pd.to_numeric(pop_faixa_raw["idade"], errors="coerce")
pop_faixa_raw["pop"]   = pd.to_numeric(pop_faixa_raw["pop"],   errors="coerce")
pop_faixa_raw["Ano"]   = pd.to_numeric(pop_faixa_raw["Ano"],   errors="coerce")
pop_faixa_raw["SIGLA"] = pop_faixa_raw["SIGLA"].str.strip()

pop_faixa_raw = pop_faixa_raw[pop_faixa_raw["Ano"].isin(periodo_analise)]

# Faixa 0–6 anos e 60+ anos (driver Saúde)
pop_6_60 = (
    pop_faixa_raw
    .assign(faixa_6_60=lambda df: df["idade"].apply(
        lambda i: "pop_0_a_6" if i <= 6 else ("pop_60_mais" if i >= 60 else None)
    ))
    .dropna(subset=["faixa_6_60"])
    .groupby(["SIGLA", "Ano"])
    .agg(
        pop_0_a_6_anos   = ("pop", lambda x: x[pop_faixa_raw.loc[x.index, "idade"] <= 6].sum()),
        pop_60_mais_anos = ("pop", lambda x: x[pop_faixa_raw.loc[x.index, "idade"] >= 60].sum()),
    )
    .reset_index()
)
pop_6_60["pop_6_60_anos"] = pop_6_60["pop_0_a_6_anos"] + pop_6_60["pop_60_mais_anos"]

# Forma mais robusta: calcula diretamente com filtros
pop_faixa_raw["grupo"] = None
pop_faixa_raw.loc[pop_faixa_raw["idade"] <= 6,  "grupo"] = "pop_0_a_6_anos"
pop_faixa_raw.loc[pop_faixa_raw["idade"] >= 60, "grupo"] = "pop_60_mais_anos"

pop_6_60 = (
    pop_faixa_raw.dropna(subset=["grupo"])
    .groupby(["SIGLA", "Ano", "grupo"])["pop"]
    .sum()
    .unstack("grupo")
    .fillna(0)
    .reset_index()
)
pop_6_60["pop_6_60_anos"] = pop_6_60["pop_0_a_6_anos"] + pop_6_60["pop_60_mais_anos"]

# Faixa 10–19 anos (driver Educação)
pop_10_19 = (
    pop_faixa_raw[pop_faixa_raw["idade"].between(10, 19)]
    .groupby(["SIGLA", "Ano"])["pop"]
    .sum()
    .reset_index()
    .rename(columns={"pop": "pop_10_19_anos"})
)

df_pop6_spark  = spark.createDataFrame(pop_6_60)
df_pop10_spark = spark.createDataFrame(pop_10_19)

df_pop6_spark.write.format("delta").mode("overwrite").saveAsTable("silver_pop_faixa_saude")
df_pop10_spark.write.format("delta").mode("overwrite").saveAsTable("silver_pop_faixa_educacao")
print(f"silver_pop_faixa_saude (0–6 + 60+): {len(pop_6_60)} linhas")
print(f"silver_pop_faixa_educacao (10–19): {len(pop_10_19)} linhas")


# =============================================================================
# CÉLULA 10 — Silver: Taxa de Homicídios
# =============================================================================

print("\n===== Processando Taxa de Homicídios =====")

homic_raw = spark.table("bronze_homicidios").toPandas()

# CSV Atlas da Violência: colunas Período, Região_ID, Valor
homic_pd = homic_raw.rename(columns={
    "Período":   "data",
    "Região_ID": "tcode",
    "Valor":     "value"
})

homic_pd["tcode"] = pd.to_numeric(homic_pd["tcode"], errors="coerce").astype("Int64")
homic_pd["value"] = pd.to_numeric(homic_pd["value"], errors="coerce")
# Extrai o ano direto dos 4 primeiros caracteres da data (formato ISO YYYY-MM-DD...).
# Evita o shift de fuso horário causado por pd.to_datetime(..., utc=True), que
# poderia jogar 2018-12-31T23:00-03:00 para o dia 2019-01-01 UTC e divergir do R.
homic_pd["Ano"] = pd.to_numeric(
    homic_pd["data"].astype(str).str[:4], errors="coerce"
).astype("Int64")

# Filtra apenas nível estado (códigos IBGE de 2 dígitos: 11–53)
homic_pd = homic_pd[homic_pd["tcode"].between(11, 53)]
homic_pd["UF"] = homic_pd["tcode"].map(UF_MAP)

homic_silver = homic_pd.dropna(subset=["UF", "value", "Ano"])[
    ["UF", "Ano", "value"]
].rename(columns={"value": "homicidios"})

homic_silver = homic_silver[homic_silver["Ano"].isin(periodo_analise)]

df_homic_spark = spark.createDataFrame(homic_silver)
(df_homic_spark.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("silver_homicidios"))
print(f"silver_homicidios: {len(homic_silver)} linhas")


# =============================================================================
# CÉLULA 11 — Silver: Códigos Contábeis
# =============================================================================

print("\n===== Processando Códigos Contábeis =====")

cod_raw = spark.table("bronze_codigos_contabeis").toPandas()

# O Bronze grava tudo como string (astype(str) antes do createDataFrame), o que
# transforma NaN em literal "nan". Normaliza antes do dropna para reproduzir o
# complete.cases() do R.
cod_raw = cod_raw.replace(
    {"nan": pd.NA, "NaN": pd.NA, "None": pd.NA, "": pd.NA}
)

# Remove linhas sem dado na coluna do primeiro ano de análise
cod_raw = cod_raw.dropna(subset=[str(ANO_INICIO_ANALISE)])

# Mantém apenas colunas: Imposto + anos de análise
colunas_manter = ["Imposto"] + [str(a) for a in periodo_analise if str(a) in cod_raw.columns]
cod_silver = cod_raw[colunas_manter].copy()

df_cod_spark = spark.createDataFrame(cod_silver.astype(str))
df_cod_spark.write.format("delta").mode("overwrite").saveAsTable("silver_codigos_contabeis")
print(f"silver_codigos_contabeis: {len(cod_silver)} linhas")
print(cod_silver)


# =============================================================================
# CÉLULA 12 — Silver: Número de Deputados por UF
# =============================================================================

print("\n===== Calculando Deputados por UF =====")

pop_ano_fim = pop_silver[pop_silver["Ano"] == ANO_FIM_ANALISE].copy()

quociente_populacional = pop_ano_fim["populacao_total"].sum() / 513

def calcular_dep_federais(pop, quociente):
    dep = pop / quociente
    if dep < 8:
        return 8
    elif dep > 70:
        return 70
    return round(dep)

pop_ano_fim["dep_federais"] = pop_ano_fim["populacao_total"].apply(
    lambda p: calcular_dep_federais(p, quociente_populacional)
)
pop_ano_fim["dep_estaduais"] = pop_ano_fim["dep_federais"].apply(
    lambda d: 3 * d if d < 13 else 36 + (d - 12)
)
pop_ano_fim["Percentual"] = pop_ano_fim["dep_estaduais"] / pop_ano_fim["dep_estaduais"].sum()
pop_ano_fim = pop_ano_fim.rename(columns={"SIGLA": "UF"})

deputados_silver = pop_ano_fim[[
    "UF", "populacao_total", "dep_federais", "dep_estaduais", "Percentual"
]]

df_dep_spark = spark.createDataFrame(deputados_silver)
df_dep_spark.write.format("delta").mode("overwrite").saveAsTable("silver_deputados_uf")
print(f"silver_deputados_uf: {len(deputados_silver)} linhas")
print(deputados_silver)


# =============================================================================
# CÉLULA 13 — Resumo das Tabelas Silver
# =============================================================================

tabelas_silver = [
    "silver_receitas_siconfi",
    "silver_despesas_siconfi",
    "silver_fcdf",
    "silver_populacao_total",
    "silver_ipca_deflator",
    "silver_pib_uf",
    "silver_pop_faixa_saude",
    "silver_pop_faixa_educacao",
    "silver_homicidios",
    "silver_codigos_contabeis",
    "silver_deputados_uf",
]

print("\n========== RESUMO SILVER ==========")
for tabela in tabelas_silver:
    try:
        n = spark.table(tabela).count()
        print(f"  {tabela}: {n:,} linhas")
    except Exception as e:
        print(f"  {tabela}: ERRO ({e})")

print("\nNotebook Silver concluído.")
