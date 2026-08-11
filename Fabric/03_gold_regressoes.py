# =============================================================================
#
#   HIATO FISCAL DAS UNIDADES DA FEDERAÇÃO DO BRASIL
#   NOTEBOOK 3 — GOLD: Regressões e Estimativas
#
#   Inputs (Delta Tables Silver):
#     - silver_receitas_siconfi
#     - silver_despesas_siconfi
#     - silver_fcdf
#     - silver_populacao_total
#     - silver_pib_uf
#     - silver_pop_faixa_saude
#     - silver_pop_faixa_educacao
#     - silver_homicidios
#     - silver_codigos_contabeis
#     - silver_deputados_uf
#
#   Outputs (Delta Tables Gold):
#     - gold_capacidade_fiscal
#     - gold_receita_media_impostos
#     - gold_necessidade_gasto
#     - gold_gasto_medio_funcoes
#     - gold_gasto_proporcional_ano_fim
#     - gold_hiato_fiscal   ← tabela síntese final
#
# =============================================================================

# =============================================================================
# CÉLULA 0 — Instalação de Dependências (Fabric)
# =============================================================================
# Executar SEMPRE como primeira célula. O Fabric reinicia a sessão Spark após
# o %pip install, então variáveis criadas antes desta célula seriam perdidas.
# Para evitar reinstalar a cada execução, configure um Environment do Fabric
# com 'linearmodels' adicionado via "Public libraries".

%pip install linearmodels


# =============================================================================
# CÉLULA 1 — Configurações do Usuário
# =============================================================================

ANO_INICIO_ANALISE = 2019
ANO_FIM_ANALISE    = 2022

periodo_analise = list(range(ANO_INICIO_ANALISE, ANO_FIM_ANALISE + 1))
print(f"Período de análise: {periodo_analise}")
print(f"Ano de referência (ano-fim): {ANO_FIM_ANALISE}")


# =============================================================================
# CÉLULA 2 — Imports
# =============================================================================

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

from pyspark.sql import SparkSession
from linearmodels.panel import PooledOLS
from linearmodels import RandomEffects
import statsmodels.api as sm
from functools import reduce

spark = SparkSession.builder.getOrCreate()

print("Imports concluídos.")


# =============================================================================
# CÉLULA 3 — Carregamento das tabelas Silver
# =============================================================================

print("\n===== Carregando tabelas Silver =====")

receitas        = spark.table("silver_receitas_siconfi").toPandas()
despesas        = spark.table("silver_despesas_siconfi").toPandas()
fcdf            = spark.table("silver_fcdf").toPandas()
pop_total       = spark.table("silver_populacao_total").toPandas()
pib_uf          = spark.table("silver_pib_uf").toPandas()
pop_saude       = spark.table("silver_pop_faixa_saude").toPandas()
pop_educacao    = spark.table("silver_pop_faixa_educacao").toPandas()
homicidios      = spark.table("silver_homicidios").toPandas()
codigos         = spark.table("silver_codigos_contabeis").toPandas()
deputados       = spark.table("silver_deputados_uf").toPandas()

# Normaliza tipos numéricos
for df, cols in [
    (receitas,     ["AN_EXERCICIO", "VALUE", "populacao_total"]),
    (despesas,     ["Ano", "VALUE", "Valor", "População", "pib"]),
    (fcdf,         ["Ano", "Valor"]),
    (pop_total,    ["Ano", "populacao_total"]),
    (pib_uf,       ["ano", "pib"]),
    (pop_saude,    ["Ano", "pop_0_a_6_anos", "pop_60_mais_anos", "pop_6_60_anos"]),
    (pop_educacao, ["Ano", "pop_10_19_anos"]),
    (homicidios,   ["Ano", "homicidios"]),
    (deputados,    ["populacao_total", "dep_federais", "dep_estaduais", "Percentual"]),
]:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

print("Tabelas carregadas.")


# =============================================================================
# CÉLULA 4 — Função auxiliar: regressão pooled OLS (linearmodels)
# =============================================================================

def pooled_ols(df, dep_var, indep_vars, entity_col, time_col, sem_intercepto=False):
    """
    Estima um modelo Pooled OLS com efeitos de entidade e tempo via linearmodels.
    Retorna o objeto de resultado do modelo.
    """
    df_reg = df[[dep_var] + indep_vars + [entity_col, time_col]].dropna().copy()
    df_reg = df_reg.set_index([entity_col, time_col])

    y = df_reg[dep_var]
    X = df_reg[indep_vars]
    if not sem_intercepto:
        X = sm.add_constant(X)

    modelo = PooledOLS(y, X)
    resultado = modelo.fit(cov_type="unadjusted")
    return resultado, df_reg


def random_effects(df, dep_var, indep_vars, entity_col, time_col):
    """Estima modelo de Efeitos Aleatórios."""
    df_reg = df[[dep_var] + indep_vars + [entity_col, time_col]].dropna().copy()
    df_reg = df_reg.set_index([entity_col, time_col])

    y = df_reg[dep_var]
    X = sm.add_constant(df_reg[indep_vars])

    modelo = RandomEffects(y, X)
    resultado = modelo.fit()
    return resultado, df_reg


# =============================================================================
# CÉLULA 5 — Preparação: PIB, TTI e dados de receita por imposto
# =============================================================================

print("\n===== Preparando bases de capacidade fiscal =====")

# Junta PIB nas receitas
dados_pib = receitas.merge(
    pib_uf[["Sigla", "ano", "pib"]],
    left_on=["SG_ENTE", "AN_EXERCICIO"],
    right_on=["Sigla", "ano"],
    how="left"
).drop(columns=["Sigla", "ano"], errors="ignore")

dados_pib["pib_per_capita"] = dados_pib["pib"] * 1000 / dados_pib["populacao_total"]

# ── Transferências (residual) ──────────────────────────────────────────────
def get_codigos(codigos_df, imposto):
    """Retorna lista de códigos contábeis para um imposto."""
    linha = codigos_df[codigos_df["Imposto"] == imposto]
    if linha.empty:
        return []
    return [str(v).strip() for v in linha.iloc[0, 1:].tolist() if pd.notna(v) and str(v).strip()]

codigos_transf  = get_codigos(codigos, "Transferências Totais")
codigos_t_ded   = get_codigos(codigos, "Subtração FPE")

def filtrar_por_codigos(df, codigos_lista, col="ELEMENTLABEL"):
    """Filtra linhas cujo ELEMENTLABEL contém algum dos códigos."""
    if not codigos_lista:
        return df.head(0)
    pattern = "|".join(re.escape(c) for c in codigos_lista)
    return df[df[col].str.contains(pattern, na=False)]

import re

dados_transf     = filtrar_por_codigos(dados_pib, codigos_transf)
dados_transf_ded = filtrar_por_codigos(dados_pib, codigos_t_ded)

transf_agregado = (
    dados_transf.groupby("SG_ENTE")["VALUE"].mean().reset_index()
    .rename(columns={"VALUE": "Transferencias"})
)
transf_ded_agregado = (
    dados_transf_ded.groupby("SG_ENTE")["VALUE"].mean().reset_index()
    .rename(columns={"VALUE": "Transferencias_deducao"})
)

# Calcula TTI (base tributária própria = PIB - Transferências)
transf_ano = (
    dados_transf.groupby(["SG_ENTE", "AN_EXERCICIO"])["VALUE"].sum().reset_index()
    .rename(columns={"VALUE": "Transferencias_ano"})
)
dados_pib = dados_pib.merge(transf_ano, on=["SG_ENTE", "AN_EXERCICIO"], how="left")
dados_pib["tti"] = (dados_pib["pib"] * 1000 - dados_pib["Transferencias_ano"]) / 1000

# TTI e PIB do ano-fim (para predição)
tti_ano_fim = (
    dados_pib[dados_pib["AN_EXERCICIO"] == ANO_FIM_ANALISE]
    .groupby("SG_ENTE")[["pib", "tti"]].first().reset_index()
    .rename(columns={"pib": "pib_ano_fim", "tti": "tti_ano_fim"})
)

media_pib = (
    dados_pib.groupby("SG_ENTE")["pib"].mean().reset_index()
    .rename(columns={"pib": "pib_medio"})
    .merge(tti_ano_fim, on="SG_ENTE")
)

print(f"Base dados_pib: {len(dados_pib)} linhas | UFs: {dados_pib['SG_ENTE'].nunique()}")


# =============================================================================
# CÉLULA 6 — Regressões de Capacidade Fiscal (ICMS, IPVA, ITCD, Prev., Outros)
# =============================================================================

print("\n===== Regressões — Capacidade Fiscal =====\n")

def preparar_imposto(codigos_df, nome_imposto, dados_base, fator=1.0, col_valor="VALUE"):
    """Filtra, agrega e aplica fator de retenção líquida ao imposto."""
    cods = get_codigos(codigos_df, nome_imposto)
    df = filtrar_por_codigos(dados_base, cods).copy()
    df["VALUE2"] = df[col_valor] * fator
    return df

# ── ICMS (fator líquido: 60%) ─────────────────────────────────────────────
dados_icms = preparar_imposto(codigos, "ICMS", dados_pib, fator=0.6)
res_icms, _ = pooled_ols(dados_icms, "VALUE2", ["tti"], "SG_ENTE", "AN_EXERCICIO")
print("=== ICMS ==="); print(res_icms.summary.tables[1])

fitted_icms = media_pib[["SG_ENTE", "tti_ano_fim"]].copy()
coef = res_icms.params
fitted_icms["Fitted_Pooled_icms"] = coef.get("const", 0) + fitted_icms["tti_ano_fim"] * coef["tti"]

# ── IPVA (sem intercepto — fator líquido: 40%) ───────────────────────────
dados_ipva = preparar_imposto(codigos, "IPVA", dados_pib, fator=0.4)
res_ipva, _ = pooled_ols(dados_ipva, "VALUE2", ["tti"], "SG_ENTE", "AN_EXERCICIO",
                          sem_intercepto=True)
print("\n=== IPVA ==="); print(res_ipva.summary.tables[1])

fitted_ipva = media_pib[["SG_ENTE", "tti_ano_fim"]].copy()
fitted_ipva["Fitted_Pooled_ipva"] = fitted_ipva["tti_ano_fim"] * res_ipva.params["tti"]

# ── ITCD (sem intercepto — fator líquido: 80%) ───────────────────────────
dados_itcd = preparar_imposto(codigos, "ITCD", dados_pib, fator=0.8)
res_itcd, _ = pooled_ols(dados_itcd, "VALUE2", ["tti"], "SG_ENTE", "AN_EXERCICIO",
                          sem_intercepto=True)
print("\n=== ITCD ==="); print(res_itcd.summary.tables[1])

fitted_itcd = media_pib[["SG_ENTE", "tti_ano_fim"]].copy()
fitted_itcd["Fitted_Pooled_itcd"] = fitted_itcd["tti_ano_fim"] * res_itcd.params["tti"]

# ── Previdência Estadual ──────────────────────────────────────────────────
dados_prev = preparar_imposto(
    codigos, "Contribuições para  Regimes Próprios de Previdência", dados_pib
)
res_prev, _ = pooled_ols(dados_prev, "VALUE2", ["tti"], "SG_ENTE", "AN_EXERCICIO")
print("\n=== Previdência ==="); print(res_prev.summary.tables[1])

coef_prev = res_prev.params
fitted_prev = media_pib[["SG_ENTE", "tti_ano_fim"]].copy()
fitted_prev["Fitted_Pooled_prev"] = (
    coef_prev.get("const", 0) + fitted_prev["tti_ano_fim"] * coef_prev["tti"]
)

# ── Outras Receitas ───────────────────────────────────────────────────────
impostos_outros = [
    "Taxas", "Outras Contribuições Sociais", "Receitas Patrimonial",
    "Receita Agropecuária", "Receita Industrial", "Receita de Serviços",
    "Outras receitas correntes"
]
cods_outros = []
for imp in impostos_outros:
    cods_outros.extend(get_codigos(codigos, imp))

dados_outros = filtrar_por_codigos(dados_pib, cods_outros).copy()
dados_outros_agg = (
    dados_outros
    .groupby(["SG_ENTE", "AN_EXERCICIO"])
    .agg(VALUE=("VALUE", "sum"), tti=("tti", "first"))
    .reset_index()
)

res_outros, _ = pooled_ols(dados_outros_agg, "VALUE", ["tti"], "SG_ENTE", "AN_EXERCICIO")
print("\n=== Outras Receitas ==="); print(res_outros.summary.tables[1])

coef_outros = res_outros.params
fitted_outros = media_pib[["SG_ENTE", "tti_ano_fim"]].copy()
fitted_outros["Fitted_Pooled_outros"] = (
    coef_outros.get("const", 0) + fitted_outros["tti_ano_fim"] * coef_outros["tti"]
)

# ── IRRF Servidores (valor observado no ano-fim) ──────────────────────────
cods_ir = get_codigos(codigos, "IRRF servidores")
dados_ir = filtrar_por_codigos(dados_pib, cods_ir)
ir_ano_fim = (
    dados_ir[dados_ir["AN_EXERCICIO"] == ANO_FIM_ANALISE]
    .groupby("SG_ENTE")["VALUE"].sum().reset_index()
    .rename(columns={"VALUE": "IR_Servidores"})
)

# ── Receitas exclusivas do DF ────────────────────────────────────────────
impostos_df = ["ISSQN", "IPTU", "ITR", "ITBI"]
cods_df = []
for imp in impostos_df:
    cods_df.extend(get_codigos(codigos, imp))

dados_df = (
    filtrar_por_codigos(dados_pib[dados_pib["SG_ENTE"] == "DF"], cods_df)
    .groupby(["AN_EXERCICIO", "SG_ENTE"])["VALUE"].sum().reset_index()
)

fcdf_num = fcdf.copy()
fcdf_num["Ano"] = pd.to_numeric(fcdf_num["Ano"], errors="coerce")
fcdf_num["Valor"] = pd.to_numeric(fcdf_num["Valor"], errors="coerce")

dados_df_total = dados_df.merge(
    fcdf_num[["Ano", "Valor"]], left_on="AN_EXERCICIO", right_on="Ano", how="left"
)
dados_df_total["total"] = dados_df_total["VALUE"] + dados_df_total["Valor"].fillna(0)

dados_df_ano_fim = (
    dados_df_total[dados_df_total["AN_EXERCICIO"] == ANO_FIM_ANALISE]
    [["SG_ENTE", "total"]]
)


# =============================================================================
# CÉLULA 7 — Compilando Capacidade Fiscal (Gold)
# =============================================================================

print("\n===== Compilando Capacidade Fiscal =====")

fitted_all = reduce(
    lambda l, r: l.merge(r, on=["SG_ENTE", "tti_ano_fim"]),
    [fitted_icms, fitted_ipva, fitted_itcd, fitted_prev, fitted_outros]
)

fitted_cols = [c for c in fitted_all.columns if c.startswith("Fitted_Pooled")]
fitted_all["Fitted_Pooled_Total"] = fitted_all[fitted_cols].sum(axis=1)

cap_fiscal = (
    fitted_all[["SG_ENTE", "Fitted_Pooled_Total"]]
    .merge(transf_agregado,     on="SG_ENTE", how="left")
    .merge(dados_df_ano_fim,    on="SG_ENTE", how="left")
    .merge(transf_ded_agregado, on="SG_ENTE", how="left")
    .merge(ir_ano_fim,          on="SG_ENTE", how="left")
    .fillna(0)
)

cap_fiscal["total_pooled"] = (
    cap_fiscal["Fitted_Pooled_Total"]
    + cap_fiscal["Transferencias"]
    + cap_fiscal["total"]
    - cap_fiscal["Transferencias_deducao"]
    + cap_fiscal["IR_Servidores"]
)

df_cap_spark = spark.createDataFrame(cap_fiscal)
df_cap_spark.write.format("delta").mode("overwrite").saveAsTable("gold_capacidade_fiscal")
print(f"gold_capacidade_fiscal: {len(cap_fiscal)} linhas")
print(cap_fiscal[["SG_ENTE", "Fitted_Pooled_Total", "total_pooled"]].to_string(index=False))

# Tabela de receita média por imposto
def media_e_ano_fim(df_imp, col_val, nome_medio, nome_ano_fim):
    medio   = df_imp.groupby("SG_ENTE")[col_val].mean().reset_index().rename(columns={col_val: nome_medio})
    ano_fim = (
        df_imp[df_imp["AN_EXERCICIO"] == ANO_FIM_ANALISE]
        .groupby("SG_ENTE")[col_val].sum().reset_index()
        .rename(columns={col_val: nome_ano_fim})
    )
    return medio.merge(ano_fim, on="SG_ENTE", how="left")

rec_icms  = media_e_ano_fim(dados_icms,  "VALUE2", "ICMS_MEDIO",   f"ICMS_{ANO_FIM_ANALISE}")
rec_ipva  = media_e_ano_fim(dados_ipva,  "VALUE2", "IPVA_MEDIO",   f"IPVA_{ANO_FIM_ANALISE}")
rec_itcd  = media_e_ano_fim(dados_itcd,  "VALUE2", "ITCD_MEDIO",   f"ITCD_{ANO_FIM_ANALISE}")
rec_prev  = media_e_ano_fim(dados_prev,  "VALUE2", "PREV_MEDIO",   f"PREV_{ANO_FIM_ANALISE}")
rec_outros_full = (
    dados_outros.groupby(["SG_ENTE", "AN_EXERCICIO"])["VALUE"].sum().reset_index()
)
rec_outros = media_e_ano_fim(rec_outros_full, "VALUE", "OUTROS_MEDIO", f"OUTROS_{ANO_FIM_ANALISE}")

receita_estimativas = reduce(
    lambda l, r: l.merge(r, on="SG_ENTE", how="left"),
    [rec_icms, rec_ipva, rec_itcd, rec_prev, rec_outros]
).merge(fitted_all, on="SG_ENTE", how="left")

cols_ano_fim = [f"ICMS_{ANO_FIM_ANALISE}", f"IPVA_{ANO_FIM_ANALISE}",
                f"ITCD_{ANO_FIM_ANALISE}", f"PREV_{ANO_FIM_ANALISE}",
                f"OUTROS_{ANO_FIM_ANALISE}"]
receita_estimativas["Total_ano_fim"] = receita_estimativas[cols_ano_fim].fillna(0).sum(axis=1)

df_rec_est_spark = spark.createDataFrame(receita_estimativas)
df_rec_est_spark.write.format("delta").mode("overwrite").saveAsTable("gold_receita_media_impostos")
print(f"gold_receita_media_impostos: {len(receita_estimativas)} linhas")


# =============================================================================
# CÉLULA 8 — Regressões de Necessidade de Gasto
# =============================================================================

print("\n===== Regressões — Necessidade de Gasto =====\n")

# Carrega despesas com tipos corretos
desp = despesas.copy()
for c in ["Valor", "Population", "pib", "Ano"]:
    if c in desp.columns:
        desp[c] = pd.to_numeric(desp[c], errors="coerce")
desp["Ano"]       = pd.to_numeric(desp["Ano"], errors="coerce")
desp["Valor"]     = pd.to_numeric(desp["Valor"], errors="coerce")
desp["População"] = pd.to_numeric(desp["População"], errors="coerce")
desp["pib"]       = pd.to_numeric(desp["pib"], errors="coerce")

pop_ano_fim_uf = pop_total[pop_total["Ano"] == ANO_FIM_ANALISE].rename(columns={"SIGLA": "UF"})
pop_total_anual = pop_total.groupby("Ano")["populacao_total"].sum().reset_index()

estimativas = pop_ano_fim_uf[["UF", "populacao_total"]].copy()

def estimar_gasto(df_func, dep_var, indep_vars, entity, time,
                  nome_col, sem_intercepto=False, loglog=False):
    """Estima pooled OLS e adiciona coluna na tabela de estimativas."""
    global estimativas

    df_r = df_func.copy()
    if loglog:
        for c in [dep_var] + indep_vars:
            df_r[f"log_{c}"] = np.log(df_r[c].clip(lower=1e-9))
        dep_var_r   = f"log_{dep_var}"
        indep_vars_r = [f"log_{v}" for v in indep_vars]
    else:
        dep_var_r    = dep_var
        indep_vars_r = indep_vars

    res, _ = pooled_ols(df_r, dep_var_r, indep_vars_r, entity, time,
                        sem_intercepto=sem_intercepto)
    print(f"\n=== {nome_col} ==="); print(res.summary.tables[1])

    coef = res.params
    return res, coef


# ── Administração ──────────────────────────────────────────────────────────
df_adm = desp[desp["Conta"] == "04 - Administração"].copy()
res_adm, coef_adm = estimar_gasto(df_adm, "Valor", ["População"], "UF", "Ano", "Administração")
pop_fim = pop_ano_fim_uf.merge(pop_total_anual.rename(columns={"populacao_total": "pop_anual"}),
                                on="Ano", how="left")
estimativas["gastos_pooled_adm"] = (
    coef_adm.get("const", 0) + estimativas["populacao_total"] * coef_adm["População"]
)

# ── Judiciário (sem DF) ───────────────────────────────────────────────────
df_jud = desp[(desp["Conta"] == "02 - Judiciária") & (desp["UF"] != "DF")].copy()
res_jud, coef_jud = estimar_gasto(df_jud, "Valor", ["População"], "UF", "Ano", "Judiciário")
estimativas["gastos_pooled_jud"] = (
    coef_jud.get("const", 0) + estimativas["populacao_total"] * coef_jud["População"]
)

# ── Essencial à Justiça ──────────────────────────────────────────────────
df_jus = desp[desp["Conta"] == "03 - Essencial à Justiça"].copy()
res_jus, coef_jus = estimar_gasto(df_jus, "Valor", ["População"], "UF", "Ano", "Essencial Justiça")
estimativas["gastos_pooled_justica"] = (
    coef_jus.get("const", 0) + estimativas["populacao_total"] * coef_jus["População"]
)

# ── Legislativo (proporcional ao número de legisladores) ─────────────────
df_leg = desp[desp["Conta"] == "01 - Legislativa"].copy()
df_leg_total = df_leg.groupby("Ano")["Valor"].sum().reset_index().rename(columns={"Valor": "Valor_total"})
df_leg = df_leg.merge(df_leg_total, on="Ano", how="left")
df_leg = df_leg.merge(deputados[["UF", "Percentual"]], on="UF", how="left")
df_leg = df_leg.dropna(subset=["Percentual"])
df_leg["estimado"] = df_leg["Valor_total"] * df_leg["Percentual"]

leg_medio = (
    df_leg.groupby("UF")["estimado"].mean().reset_index()
    .rename(columns={"estimado": "estimado_legislativo"})
)
estimativas = estimativas.merge(leg_medio, on="UF", how="left")

# ── Outras funções ────────────────────────────────────────────────────────
outras_funcs = [
    "08 - Assistência Social", "11 - Trabalho", "13 - Cultura",
    "14 - Direitos da Cidadania", "15 - Urbanismo", "16 - Habitação",
    "17 - Saneamento", "18 - Gestão Ambiental", "19 - Ciência e Tecnologia",
    "20 - Agricultura", "21 - Organização Agrária", "22 - Indústria",
    "23 - Comércio e Serviços", "24 - Comunicações", "25 - Energia",
    "27 - Desporto e Lazer"
]
df_outras_agg = (
    desp[desp["Conta"].isin(outras_funcs)]
    .groupby(["UF", "Ano"])
    .agg(Valor=("Valor", "sum"), População=("População", "first"))
    .reset_index()
)
res_out, coef_out = estimar_gasto(df_outras_agg, "Valor", ["População"], "UF", "Ano", "Outras")
estimativas["gastos_pooled_outros"] = (
    coef_out.get("const", 0) + estimativas["populacao_total"] * coef_out["População"]
)

# ── Educação (driver: pop 10–19 anos) ────────────────────────────────────
df_edu = desp[desp["Conta"] == "12 - Educação"].copy()
df_edu = df_edu.merge(pop_educacao.rename(columns={"SIGLA": "UF"}), on=["UF", "Ano"], how="left")
res_edu, coef_edu = estimar_gasto(
    df_edu, "Valor", ["pop_10_19_anos"], "UF", "Ano", "Educação", sem_intercepto=True
)
pop_edu_fim = pop_educacao[pop_educacao["Ano"] == ANO_FIM_ANALISE].rename(columns={"SIGLA": "UF"})
estimativas = estimativas.merge(pop_edu_fim[["UF", "pop_10_19_anos"]], on="UF", how="left")
estimativas["gastos_pooled_educacao"] = (
    estimativas["pop_10_19_anos"] * coef_edu["pop_10_19_anos"]
)

# ── Saúde (driver: pop 0–6 e 60+ anos) ───────────────────────────────────
df_sau = desp[desp["Conta"] == "10 - Saúde"].copy()
df_sau = df_sau.merge(pop_saude.rename(columns={"SIGLA": "UF"}), on=["UF", "Ano"], how="left")
res_sau, coef_sau = estimar_gasto(
    df_sau, "Valor", ["pop_6_60_anos"], "UF", "Ano", "Saúde"
)
pop_sau_fim = pop_saude[pop_saude["Ano"] == ANO_FIM_ANALISE].rename(columns={"SIGLA": "UF"})
estimativas = estimativas.merge(pop_sau_fim[["UF", "pop_6_60_anos"]], on="UF", how="left")
estimativas["gastos_pooled_saude"] = (
    coef_sau.get("const", 0) + estimativas["pop_6_60_anos"] * coef_sau["pop_6_60_anos"]
)

# ── Segurança Pública (drivers: pop + homicídios) ─────────────────────────
df_seg = desp[desp["Conta"] == "06 - Segurança Pública"].copy()
df_seg = df_seg.merge(homicidios, on=["UF", "Ano"], how="left")

# Pesos via log-log (efeitos aleatórios)
df_seg_clean = df_seg.dropna(subset=["Valor", "População", "homicidios"]).copy()
df_seg_clean = df_seg_clean[(df_seg_clean["Valor"] > 0) &
                             (df_seg_clean["homicidios"] > 0) &
                             (df_seg_clean["População"] > 0)]
df_seg_clean["log_Valor"]      = np.log(df_seg_clean["Valor"])
df_seg_clean["log_Pop"]        = np.log(df_seg_clean["População"])
df_seg_clean["log_homicidios"] = np.log(df_seg_clean["homicidios"])

res_seg_log, _ = pooled_ols(
    df_seg_clean, "log_Valor", ["log_Pop", "log_homicidios"],
    "UF", "Ano", sem_intercepto=True
)
print("\n=== Segurança (log-log) ==="); print(res_seg_log.summary.tables[1])

coef_seg_log = res_seg_log.params
total_coef = coef_seg_log["log_Pop"] + coef_seg_log["log_homicidios"]
peso_pop = coef_seg_log["log_Pop"] / total_coef
peso_hom = coef_seg_log["log_homicidios"] / total_coef

# Modelo linear para obter coeficientes de predição
res_seg_lin, _ = pooled_ols(df_seg, "Valor", ["População", "homicidios"], "UF", "Ano")
coef_seg = res_seg_lin.params
print("\n=== Segurança (linear) ==="); print(res_seg_lin.summary.tables[1])

homic_fim = homicidios[homicidios["Ano"] == ANO_FIM_ANALISE]
estimativas = estimativas.merge(homic_fim[["UF", "homicidios"]], on="UF", how="left")
estimativas["gastos_pooled_seguranca"] = (
    coef_seg.get("const", 0)
    + estimativas["populacao_total"] * coef_seg["População"]
    + estimativas["homicidios"]      * coef_seg["homicidios"]
)

# ── Transporte (driver: PIB) ──────────────────────────────────────────────
df_trp = desp[desp["Conta"] == "26 - Transporte"].copy()
df_trp = df_trp.merge(
    pib_uf[["Sigla", "ano", "pib"]].rename(columns={"Sigla": "UF", "ano": "Ano"}),
    on=["UF", "Ano"], how="left", suffixes=("_desp", "_pib")
)
df_trp["pib"] = df_trp["pib_pib"].fillna(df_trp.get("pib_desp", np.nan))

res_trp, coef_trp = estimar_gasto(df_trp, "Valor", ["pib"], "UF", "Ano", "Transporte")

pib_fim = pib_uf[pib_uf["ano"] == ANO_FIM_ANALISE][["Sigla", "pib"]].rename(columns={"Sigla": "UF"})
estimativas = estimativas.merge(pib_fim, on="UF", how="left")
estimativas["gastos_pooled_transporte"] = (
    coef_trp.get("const", 0) + estimativas["pib"] * coef_trp["pib"]
)

# ── Previdência Estadual ──────────────────────────────────────────────────
df_prev_desp = desp[desp["Conta"] == "09 - Previdência Social"].copy()
res_prev_d, coef_prev_d = estimar_gasto(
    df_prev_desp, "Valor", ["População"], "UF", "Ano", "Previdência (gasto)"
)
estimativas["gastos_pooled_previdencia"] = (
    coef_prev_d.get("const", 0) + estimativas["populacao_total"] * coef_prev_d["População"]
)

# ── Encargos Especiais (sem intercepto) ───────────────────────────────────
df_enc = desp[desp["Conta"] == "28 - Encargos Especiais"].copy()
res_enc, coef_enc = estimar_gasto(
    df_enc, "Valor", ["População"], "UF", "Ano", "Encargos", sem_intercepto=True
)
estimativas["gastos_pooled_encargos"] = (
    estimativas["populacao_total"] * coef_enc["População"]
)


# =============================================================================
# CÉLULA 9 — Totalizando Necessidade de Gasto
# =============================================================================

print("\n===== Totalizando Necessidade de Gasto =====")

gasto_cols = [
    "gastos_pooled_adm", "gastos_pooled_jud", "gastos_pooled_justica",
    "estimado_legislativo", "gastos_pooled_outros", "gastos_pooled_educacao",
    "gastos_pooled_saude", "gastos_pooled_seguranca", "gastos_pooled_transporte",
    "gastos_pooled_previdencia"
]

estimativas[gasto_cols] = estimativas[gasto_cols].fillna(0)
estimativas["gastos_pooled_total"] = estimativas[gasto_cols].sum(axis=1)
estimativas["gastos_pooled_total_com_encargos"] = (
    estimativas["gastos_pooled_total"] + estimativas["gastos_pooled_encargos"].fillna(0)
)

df_nec_spark = spark.createDataFrame(estimativas)
df_nec_spark.write.format("delta").mode("overwrite").saveAsTable("gold_necessidade_gasto")
print(f"gold_necessidade_gasto: {len(estimativas)} linhas")
print(estimativas[["UF", "gastos_pooled_total", "gastos_pooled_total_com_encargos"]].to_string(index=False))


# =============================================================================
# CÉLULA 10 — Tabela Síntese: Hiato Fiscal
# =============================================================================

print("\n===== Calculando Hiato Fiscal =====")

hiato = (
    cap_fiscal[["SG_ENTE", "total_pooled"]]
    .rename(columns={"SG_ENTE": "UF", "total_pooled": "capacidade_fiscal"})
    .merge(
        estimativas[["UF", "gastos_pooled_total", "gastos_pooled_total_com_encargos"]],
        on="UF", how="outer"
    )
    .merge(
        pop_ano_fim_uf[["UF", "populacao_total"]],
        on="UF", how="left"
    )
)

hiato["hiato_fiscal"]              = hiato["capacidade_fiscal"] - hiato["gastos_pooled_total"]
hiato["hiato_fiscal_com_encargos"] = hiato["capacidade_fiscal"] - hiato["gastos_pooled_total_com_encargos"]
hiato["cap_fiscal_per_capita"]     = hiato["capacidade_fiscal"]    / hiato["populacao_total"]
hiato["nec_gasto_per_capita"]      = hiato["gastos_pooled_total"]  / hiato["populacao_total"]
hiato["hiato_per_capita"]          = hiato["hiato_fiscal"]         / hiato["populacao_total"]

df_hiato_spark = spark.createDataFrame(hiato)
df_hiato_spark.write.format("delta").mode("overwrite").saveAsTable("gold_hiato_fiscal")
print(f"gold_hiato_fiscal: {len(hiato)} linhas")
print(hiato[["UF", "capacidade_fiscal", "gastos_pooled_total", "hiato_fiscal"]].to_string(index=False))


# =============================================================================
# CÉLULA 11 — Resumo das Tabelas Gold
# =============================================================================

tabelas_gold = [
    "gold_capacidade_fiscal",
    "gold_receita_media_impostos",
    "gold_necessidade_gasto",
    "gold_hiato_fiscal",
]

print("\n========== RESUMO GOLD ==========")
for tabela in tabelas_gold:
    try:
        n = spark.table(tabela).count()
        print(f"  {tabela}: {n:,} linhas")
    except Exception as e:
        print(f"  {tabela}: ERRO ({e})")

print("\nNotebook Gold concluído.")
