"""
auditoria_modelo_excel.py
=========================
Gera uma planilha Excel detalhada para auditar o modelo SARIMAX/SARIMA
escolhido para uma UF específica.

A planilha contém:
  - serie_historica   : série original + log + fitted values + resíduos
  - parametros_modelo : coeficientes do modelo (AR, MA, SAR, SMA, intercepto)
  - diferenciacoes    : séries diferenciadas (sazonal e regular) usadas no ajuste
  - backtest          : previsões do holdout vs. valores reais
  - previsoes         : previsões futuras 12 meses + IC 95 %
  - exogena_pib       : dados do PIB usados como variável exógena
  - equacao_modelo    : equação ARIMA escrita com os coeficientes estimados
"""

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from pmdarima import auto_arima
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

warnings.filterwarnings("ignore")

# =============================================================================
# CONFIGURAÇÃO — altere aqui conforme necessário
# =============================================================================
UF_AUDITORIA = "RS"          # UF a auditar
HOLDOUT      = 0             # calculado abaixo, após carregar y (25 % da série, mín. 12)

# Candidatos de exógena (deve bater com o script principal sarimax_icms.py).
# O auditor testa todos no backtest e escolhe o melhor pelo RMSE.
cols_exog_candidatas = ["log_pib", "pib_var_12m", "pib_var_mom"]

arquivo_icms = Path(r"C:\Users\carlos.marchionatti\OneDrive - Tesouro Nacional\VSCode\STN\Planilhas\rreo_anexo3_todos_anos.xlsx")
arquivo_pib  = Path(r"C:\Users\carlos.marchionatti\OneDrive - Tesouro Nacional\VSCode\STN\Planilhas\Grade SPE 05 03 2026.xlsx")
saida_auditoria = Path(f"auditoria_modelo_{UF_AUDITORIA}.xlsx")

# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================
def calcular_metricas(y_real, y_prev):
    y_real = np.asarray(y_real, dtype=float)
    y_prev = np.asarray(y_prev, dtype=float)
    erro   = y_real - y_prev
    rmse   = float(np.sqrt(np.mean(erro ** 2)))
    mae    = float(np.mean(np.abs(erro)))
    mask   = y_real != 0
    mape   = float(np.mean(np.abs(erro[mask] / y_real[mask])) * 100) if mask.any() else np.nan
    return rmse, mae, mape


def formatar_planilha(ws, header_row=1, zebra=True):
    """Aplica formatação básica (cabeçalho + zebra) a uma worksheet openpyxl."""
    azul_cabecalho = PatternFill("solid", fgColor="1F497D")
    cinza_claro    = PatternFill("solid", fgColor="F2F2F2")
    branco         = PatternFill("solid", fgColor="FFFFFF")
    fonte_cabecalho = Font(bold=True, color="FFFFFF")
    borda = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"),  bottom=Side(style="thin"),
    )

    max_col = ws.max_column
    max_row = ws.max_row

    for col_idx in range(1, max_col + 1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.fill        = azul_cabecalho
        cell.font        = fonte_cabecalho
        cell.alignment   = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border      = borda
        ws.column_dimensions[get_column_letter(col_idx)].width = 20

    if zebra:
        for row_idx in range(header_row + 1, max_row + 1):
            fill = cinza_claro if (row_idx - header_row) % 2 == 0 else branco
            for col_idx in range(1, max_col + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.fill   = fill
                cell.border = borda
                cell.alignment = Alignment(horizontal="center", vertical="center")

    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)

# =============================================================================
# MAPEAMENTO DE COLUNAS SICONFI
# =============================================================================
mapa_colunas = {
    "<MR-11>": ("Jan", 1), "<MR-10>": ("Fev", 2), "<MR-9>":  ("Mar", 3),
    "<MR-8>":  ("Abr", 4), "<MR-7>":  ("Mai", 5), "<MR-6>":  ("Jun", 6),
    "<MR-5>":  ("Jul", 7), "<MR-4>":  ("Ago", 8), "<MR-3>":  ("Set", 9),
    "<MR-2>":  ("Out",10), "<MR-1>":  ("Nov",11), "<MR>":    ("Dez",12),
}

# =============================================================================
# LEITURA DOS DADOS
# =============================================================================
print("Lendo dados...")

df = pd.read_excel(arquivo_icms)
df_icms = df[
    (df["conta"].astype(str).str.upper() == "ICMS") &
    (df["periodo_consulta"] == 6) &
    (df["coluna"].isin(mapa_colunas.keys()))
].copy()

df_icms["mes_num"]      = df_icms["coluna"].map(lambda x: mapa_colunas[x][1])
df_icms["valor"]        = pd.to_numeric(df_icms["valor"], errors="coerce")
df_icms["ano_consulta"] = pd.to_numeric(df_icms["ano_consulta"], errors="coerce").astype("Int64")
df_icms["data"]         = pd.to_datetime(
    dict(year=df_icms["ano_consulta"], month=df_icms["mes_num"], day=1), errors="coerce"
)
df_icms = (
    df_icms.groupby(["uf_consulta", "data"], as_index=False)["valor"]
    .sum().sort_values(["uf_consulta", "data"])
)

df_pib = pd.read_excel(
    arquivo_pib, sheet_name="PIB Mensal", header=None,
    skiprows=5, names=["data", "pib"]
).copy()
df_pib["data"] = pd.to_datetime(df_pib["data"], errors="coerce")
df_pib["pib"]  = pd.to_numeric(df_pib["pib"], errors="coerce")
df_pib = df_pib.dropna(subset=["data", "pib"]).sort_values("data").reset_index(drop=True)

df_pib["pib_var_mom"] = df_pib["pib"].pct_change() * 100
df_pib["pib_var_12m"] = df_pib["pib"].pct_change(12) * 100
df_pib["log_pib"]     = np.where(df_pib["pib"] > 0, np.log(df_pib["pib"]), np.nan)

dfs_exog = {
    nome: df_pib[["data", nome]].dropna().copy()
    for nome in cols_exog_candidatas
}

# =============================================================================
# PREPARAÇÃO DA SÉRIE PARA A UF ESCOLHIDA
# =============================================================================
print(f"Preparando série para {UF_AUDITORIA}...")

base = df_icms[df_icms["uf_consulta"] == UF_AUDITORIA].copy()
y = (
    base.sort_values("data")
    .set_index("data")["valor"]
    .asfreq("MS")
)

assert not y.isna().any(), f"{UF_AUDITORIA}: série ICMS tem NaN"

# HOLDOUT = 25 % da série, mínimo 12 meses
HOLDOUT = max(12, int(round(len(y) * 0.25)))
print(f"Série: {len(y)} obs  |  HOLDOUT: {HOLDOUT} meses  ({HOLDOUT/len(y)*100:.0f} %)")
assert len(y) - HOLDOUT >= 24, (
    f"{UF_AUDITORIA}: série curta demais ({len(y)} obs) para holdout de {HOLDOUT}. "
    "Colete mais anos de dados."
)

ultima_data  = y.index[-1]
datas_futuras = pd.date_range(
    ultima_data + pd.offsets.MonthBegin(1), periods=12, freq="MS"
)

# Alinha cada exógena candidata (histórico + horizonte futuro) ao índice mensal de y
exogs = {}  # nome -> (X_hist alinhado a y, X_fut alinhado ao horizonte)
for nome in cols_exog_candidatas:
    X_hist = (
        dfs_exog[nome].sort_values("data")
        .set_index("data")[[nome]]
        .asfreq("MS")
    )
    X_uf = X_hist.reindex(y.index)
    X_uf[nome] = X_uf[nome].interpolate(method="time").ffill().bfill()

    X_fut_uf = X_hist.reindex(datas_futuras)
    if X_fut_uf[nome].isna().any():
        X_fut_uf[nome] = X_fut_uf[nome].interpolate(method="time").ffill().bfill()
        if X_fut_uf[nome].isna().any():
            X_fut_uf[nome] = X_fut_uf[nome].fillna(X_uf[nome].iloc[-1])

    if X_uf.isna().any().any() or X_fut_uf.isna().any().any():
        print(f"Aviso: exógena {nome} ficou com NaN; descartando essa candidata.")
        continue

    exogs[nome] = (X_uf, X_fut_uf)

assert exogs, f"{UF_AUDITORIA}: nenhuma exógena válida disponível."

# =============================================================================
# SELEÇÃO DO MELHOR MODELO (backtest idêntico ao script principal)
# =============================================================================
print("Executando seleção de modelo (backtest)...")

y_treino, y_teste = y.iloc[:-HOLDOUT], y.iloc[-HOLDOUT:]
y_treino_log      = np.log(y_treino)
y_log             = np.log(y)

candidatos = {}

# SARIMAX, um por exógena disponível
for nome, (X_uf, _) in exogs.items():
    X_treino = X_uf.iloc[:-HOLDOUT]
    X_teste  = X_uf.iloc[-HOLDOUT:]
    try:
        m = auto_arima(y_treino_log, X=X_treino, seasonal=True, m=12,
                       stepwise=True, suppress_warnings=True, error_action="ignore",
                       information_criterion="aicc", max_p=3, max_q=3, max_P=2, max_Q=2,
                       max_D=1, with_intercept=True)
        prev = np.exp(m.predict(n_periods=HOLDOUT, X=X_teste))
        candidatos[f"SARIMAX_{nome}"] = (m, prev)
    except Exception as e:
        print(f"SARIMAX_{nome} falhou: {e}")

# SARIMA puro (sem exógena)
try:
    m2 = auto_arima(y_treino_log, seasonal=True, m=12,
                    stepwise=True, suppress_warnings=True, error_action="ignore",
                    information_criterion="aicc", max_p=3, max_q=3, max_P=2, max_Q=2,
                    max_D=1, with_intercept=True)
    prev2 = np.exp(m2.predict(n_periods=HOLDOUT))
    candidatos["SARIMA_puro"] = (m2, prev2)
except Exception as e:
    print(f"SARIMA_puro falhou: {e}")

# Naïve sazonal
if len(y_treino) >= 12:
    ult12 = y_treino.iloc[-12:].values
    prev3 = np.array([ult12[i % 12] for i in range(HOLDOUT)])
    candidatos["naive_sazonal"] = (None, prev3)

metricas_uf = {}
for nome, (mod, prev) in candidatos.items():
    rmse, mae, mape = calcular_metricas(y_teste.values, prev)
    metricas_uf[nome] = {"rmse": rmse, "mae": mae, "mape": mape}

# Seleciona o melhor modelo pelo RMSE, incluindo o naïve sazonal.
melhor_nome = min(metricas_uf, key=lambda k: metricas_uf[k]["rmse"])

print(f"Melhor modelo no backtest: {melhor_nome}")
for k, v in metricas_uf.items():
    print(f"  {k}: RMSE={v['rmse']:,.0f}  MAE={v['mae']:,.0f}  MAPE={v['mape']:.2f}%")

# =============================================================================
# REAJUSTE FINAL COM TODA A SÉRIE
# =============================================================================
print("Reajustando modelo final com toda a série...")

modelo = None
res    = None
params = None
param_names = []

# Identifica a exógena vencedora (se houver) e o conjunto X (hist + fut) correspondente
if melhor_nome.startswith("SARIMAX_"):
    exog_vencedora = melhor_nome.replace("SARIMAX_", "")
    X_full, X_fut  = exogs[exog_vencedora]
else:
    exog_vencedora = ""
    X_full, X_fut  = None, None

if melhor_nome.startswith("SARIMAX_"):
    modelo = auto_arima(y_log, X=X_full, seasonal=True, m=12,
                        stepwise=True, suppress_warnings=True, error_action="ignore",
                        information_criterion="aicc", max_p=3, max_q=3, max_P=2, max_Q=2,
                        max_D=1, with_intercept=True)
    prev_log, conf_int_log = modelo.predict(n_periods=12, X=X_fut,
                                            return_conf_int=True, alpha=0.05)
    previsao  = np.exp(prev_log)
    conf_int  = np.exp(conf_int_log)
    res       = modelo.arima_res_
    params    = res.params
    param_names = list(res.param_names)

elif melhor_nome == "SARIMA_puro":
    modelo = auto_arima(y_log, seasonal=True, m=12,
                        stepwise=True, suppress_warnings=True, error_action="ignore",
                        information_criterion="aicc", max_p=3, max_q=3, max_P=2, max_Q=2,
                        max_D=1, with_intercept=True)
    prev_log, conf_int_log = modelo.predict(n_periods=12,
                                            return_conf_int=True, alpha=0.05)
    previsao  = np.exp(prev_log)
    conf_int  = np.exp(conf_int_log)
    res       = modelo.arima_res_
    params    = res.params
    param_names = list(res.param_names)

else:  # naive_sazonal
    # Previsão: repete os últimos 12 meses observados
    previsao = y.iloc[-12:].values.copy()
    # IC ±10 % (mesmo critério do script principal)
    conf_int = np.column_stack([previsao * 0.90, previsao * 1.10])
    prev_log = np.log(previsao)  # para manter coluna na aba de previsões

tipo_modelo = melhor_nome

if modelo is not None:
    print(f"Tipo: {tipo_modelo} | exógena vencedora: {exog_vencedora or '—'}")
    print(f"Order: {modelo.order}  |  Seasonal: {modelo.seasonal_order}")
    print(f"Parâmetros: {params}")
else:
    print(f"Tipo: {tipo_modelo} (sem parâmetros ARIMA)")

# =============================================================================
# CONSTRUÇÃO DAS ABAS
# =============================================================================

# ── 1. SÉRIE HISTÓRICA ────────────────────────────────────────────────────────
if res is not None:
    fitted_log = res.fittedvalues.reindex(y_log.index)
    fitted_orig = np.exp(fitted_log.values)
else:
    # Naïve: "fitted" = y_{t-12}
    fitted_orig = pd.Series(y.values).shift(12).values
    fitted_log  = pd.Series(np.where(fitted_orig > 0, np.log(fitted_orig), np.nan),
                            index=y_log.index)

df_serie = pd.DataFrame({
    "data"                  : y.index,
    "icms_original"         : y.values,
    "log_icms"              : y_log.values,
    "fitted_log"            : fitted_log.values,
    "fitted_original"       : fitted_orig,
    "residuo_log"           : y_log.values - fitted_log.values,
    "residuo_original"      : y.values - fitted_orig,
    "residuo_percentual_%"  : (y.values - fitted_orig) / y.values * 100,
})
for nome, (X_uf, _) in exogs.items():
    df_serie[nome] = X_uf[nome].values
df_serie["data"] = df_serie["data"].dt.strftime("%Y-%m-%d")

# ── 2. DIFERENCIAÇÕES ─────────────────────────────────────────────────────────
if modelo is not None:
    d_order  = modelo.order[1]
    D_order  = modelo.seasonal_order[1]
    m_season = modelo.seasonal_order[3]
else:
    d_order, D_order, m_season = 0, 0, 12  # naïve: sem diferenciação formal

log_y_series = pd.Series(y_log.values, index=y_log.index, name="log_icms")

diff_saz = log_y_series.diff(m_season) if D_order >= 1 else log_y_series.copy()
diff_saz.name = f"diff_sazonal_D{D_order}_m{m_season}  [=log(y_t) - log(y_{{t-{m_season}}})]"

diff_reg = diff_saz.diff(1) if d_order >= 1 else diff_saz.copy()
diff_reg.name = f"diff_regular_d{d_order}  [=diff_saz_t - diff_saz_{{t-1}}]"

df_diff = pd.DataFrame({
    "data"                          : y.index.strftime("%Y-%m-%d"),
    "log_icms"                      : y_log.values,
    f"diff_sazonal (D={D_order},m={m_season})" : diff_saz.values,
    f"diff_regular (d={d_order})"             : diff_reg.values,
})

# ── 3. PARÂMETROS DO MODELO ───────────────────────────────────────────────────
if res is not None:
    param_values = list(params.values)
    param_stderr = list(res.bse.values)
    param_tval   = list(res.tvalues.values)
    param_pval   = list(res.pvalues.values)

    df_params = pd.DataFrame({
        "parametro"         : param_names,
        "coeficiente"       : param_values,
        "erro_padrao"       : param_stderr,
        "t_statistic"       : param_tval,
        "p_value"           : param_pval,
        "significativo_5%"  : ["Sim" if p < 0.05 else "Não" for p in param_pval],
    })
    info_geral = pd.DataFrame({
        "parametro"        : ["UF", "tipo_modelo", "order (p,d,q)", "seasonal_order (P,D,Q,m)",
                              "AIC", "AICc", "BIC", "variavel_exogena", "n_obs",
                              "inicio_serie", "fim_serie"],
        "coeficiente"      : [UF_AUDITORIA, tipo_modelo,
                              str(modelo.order), str(modelo.seasonal_order),
                              round(res.aic, 4), round(res.aicc, 4) if hasattr(res, "aicc") else "—",
                              round(res.bic, 4),
                              exog_vencedora if exog_vencedora else "—",
                              len(y), str(y.index.min().date()), str(y.index.max().date())],
        "erro_padrao"      : [""] * 11,
        "t_statistic"      : [""] * 11,
        "p_value"          : [""] * 11,
        "significativo_5%" : [""] * 11,
    })
    df_params = pd.concat([info_geral, pd.DataFrame([{}]), df_params], ignore_index=True)
else:
    # Naïve: apenas informações gerais
    param_values = []
    df_params = pd.DataFrame({
        "parametro"        : ["UF", "tipo_modelo", "descricao", "variavel_exogena",
                              "n_obs", "inicio_serie", "fim_serie"],
        "coeficiente"      : [UF_AUDITORIA, tipo_modelo,
                              "Previsão = y_{t-12} (último valor do mesmo mês no ano anterior)",
                              "—", len(y),
                              str(y.index.min().date()), str(y.index.max().date())],
        "erro_padrao"      : [""] * 7,
        "t_statistic"      : [""] * 7,
        "p_value"          : [""] * 7,
        "significativo_5%" : [""] * 7,
    })

# ── 4. BACKTEST ────────────────────────────────────────────────────────────────
backtest_prev = candidatos[melhor_nome][1]
df_backtest = pd.DataFrame({
    "data"              : y_teste.index.strftime("%Y-%m-%d"),
    "icms_real"         : y_teste.values,
    "icms_previsto"     : backtest_prev,
    "erro_absoluto"     : np.abs(y_teste.values - backtest_prev),
    "erro_%"            : np.abs(y_teste.values - backtest_prev) / y_teste.values * 100,
    "sinal_erro"        : y_teste.values - backtest_prev,
})
rmse_bt, mae_bt, mape_bt = calcular_metricas(y_teste.values, backtest_prev)
df_backtest.loc[len(df_backtest)] = ["RMSE", rmse_bt, "", "", "", ""]
df_backtest.loc[len(df_backtest)] = ["MAE",  mae_bt,  "", "", "", ""]
df_backtest.loc[len(df_backtest)] = ["MAPE", mape_bt, "", "", "", ""]

# Comparação dos 3 candidatos
df_metricas = pd.DataFrame([
    {
        "modelo": k,
        "RMSE"  : v["rmse"],
        "MAE"   : v["mae"],
        "MAPE_%": v["mape"],
        "escolhido": "SIM" if k == melhor_nome else "não",
    }
    for k, v in metricas_uf.items()
])

# ── 5. PREVISÕES FUTURAS ──────────────────────────────────────────────────────
df_prev = pd.DataFrame({
    "data"                : datas_futuras.strftime("%Y-%m-%d"),
    "ano"                 : datas_futuras.year,
    "mes"                 : datas_futuras.month,
    "log_previsto"        : prev_log,
    "icms_previsto"       : previsao,
    "limite_inferior_95"  : conf_int[:, 0],
    "limite_superior_95"  : conf_int[:, 1],
    "amplitude_IC_95"     : conf_int[:, 1] - conf_int[:, 0],
    "exog_vencedora"      : exog_vencedora or "—",
})
for nome, (_, X_fut_uf) in exogs.items():
    df_prev[nome] = X_fut_uf[nome].values

# ── 6. EXÓGENA PIB ─────────────────────────────────────────────────
df_pib_export = df_pib[["data", "pib", "pib_var_mom", "pib_var_12m", "log_pib"]].copy()
df_pib_export["data"] = df_pib_export["data"].dt.strftime("%Y-%m-%d")
if exog_vencedora:
    df_pib_export["exogena_vencedora"] = df_pib_export[exog_vencedora]
else:
    df_pib_export["exogena_vencedora"] = np.nan

# ── 7. EQUAÇÃO DO MODELO ──────────────────────────────────────────────────────
linhas_eq = []

if modelo is not None:
    p, d, q     = modelo.order
    P, D, Q, m  = modelo.seasonal_order
    ar_params  = {k: v for k, v in zip(param_names, param_values) if k.startswith("ar.") and not k.startswith("ar.S.")}
    ma_params  = {k: v for k, v in zip(param_names, param_values) if k.startswith("ma.") and not k.startswith("ma.S.")}
    sar_params = {k: v for k, v in zip(param_names, param_values) if k.startswith("ar.S.")}
    sma_params = {k: v for k, v in zip(param_names, param_values) if k.startswith("ma.S.")}
    intercept_val = params.get("intercept", params.get("const", None))

    linhas_eq.append(f"Modelo: {tipo_modelo}")
    linhas_eq.append(f"Especificação ARIMA: ({p},{d},{q})({P},{D},{Q})[{m}] em log(ICMS)")
    linhas_eq.append("")
    linhas_eq.append("A série estimada é:  Ỹ_t = log(ICMS_t)")
    if D >= 1:
        linhas_eq.append(f"Diferenciação sazonal (D={D}): w_t = Ỹ_t - Ỹ_{{t-{m}}}")
    if d >= 1:
        linhas_eq.append(f"Diferenciação regular (d={d}): z_t = w_t - w_{{t-1}}")
    linhas_eq.append("")
    linhas_eq.append("Equação do processo z_t (série duplamente diferenciada):")

    termos = []
    if intercept_val is not None:
        termos.append(f"{intercept_val:.6f} (intercepto)")
    for k, v in ar_params.items():
        lag = k.replace("ar.L", "")
        termos.append(f"{v:.6f} * z_{{t-{lag}}}")
    for k, v in sar_params.items():
        lag = k.replace("ar.S.L", "")
        termos.append(f"{v:.6f} * z_{{t-{lag}}}")
    for k, v in ma_params.items():
        lag = k.replace("ma.L", "")
        termos.append(f"{v:.6f} * ε_{{t-{lag}}}")
    for k, v in sma_params.items():
        lag = k.replace("ma.S.L", "")
        termos.append(f"{v:.6f} * ε_{{t-{lag}}}")
    if exog_vencedora:
        exog_coef_name = [k for k in param_names if k not in
                          list(ar_params) + list(ma_params) + list(sar_params) +
                          list(sma_params) + ["intercept", "const", "sigma2"]]
        for k in exog_coef_name:
            termos.append(f"{params[k]:.6f} * {k}")
    termos.append("ε_t (ruído branco)")

    eq_str = "z_t = " + "\n      + ".join(termos)
    linhas_eq.append(eq_str)
    linhas_eq.append("")
    linhas_eq.append("Após obter z_t (previsão), reconstrução:")
    if d >= 1 and D >= 1:
        linhas_eq.append(f"  w_t = z_t + w_{{t-1}}")
        linhas_eq.append(f"  Ỹ_t = w_t + Ỹ_{{t-{m}}}")
    elif D >= 1:
        linhas_eq.append(f"  Ỹ_t = z_t + Ỹ_{{t-{m}}}")
    linhas_eq.append("  ICMS_t = exp(Ỹ_t)")
else:
    linhas_eq.append("Modelo: naive_sazonal")
    linhas_eq.append("")
    linhas_eq.append("Sem parâmetros ARIMA estimados.")
    linhas_eq.append("")
    linhas_eq.append("Equação de previsão:")
    linhas_eq.append("  ICMS_t = ICMS_{t-12}")
    linhas_eq.append("")
    linhas_eq.append("Ou seja, a previsão de cada mês é simplesmente o valor")
    linhas_eq.append("observado no mesmo mês do ano anterior.")
    linhas_eq.append("")
    linhas_eq.append("Intervalo de confiança usado: ± 10 % do valor previsto.")
    linhas_eq.append("  Limite inferior = ICMS_{t-12} * 0,90")
    linhas_eq.append("  Limite superior = ICMS_{t-12} * 1,10")

df_equacao = pd.DataFrame({"equacao_modelo": linhas_eq})

# =============================================================================
# ESCRITA DO EXCEL
# =============================================================================
print(f"Salvando {saida_auditoria} ...")

with pd.ExcelWriter(saida_auditoria, engine="openpyxl") as writer:
    df_serie.to_excel(writer,    sheet_name="serie_historica",  index=False)
    df_diff.to_excel(writer,     sheet_name="diferenciacoes",   index=False)
    df_params.to_excel(writer,   sheet_name="parametros_modelo",index=False)
    df_backtest.to_excel(writer, sheet_name="backtest",         index=False)
    df_metricas.to_excel(writer, sheet_name="comparativo_modelos", index=False)
    df_prev.to_excel(writer,     sheet_name="previsoes_futuras",index=False)
    df_pib_export.to_excel(writer, sheet_name="exogena_pib",    index=False)
    df_equacao.to_excel(writer,  sheet_name="equacao_modelo",   index=False)

    # Formatação
    wb = writer.book
    for sheet_name in wb.sheetnames:
        formatar_planilha(wb[sheet_name])

    # Largura especial para equação
    wb["equacao_modelo"].column_dimensions["A"].width = 120

print(f"\nPlanilha de auditoria salva em: {saida_auditoria.resolve()}")
print("\nAbas geradas:")
for nome in ["serie_historica", "diferenciacoes", "parametros_modelo",
             "backtest", "comparativo_modelos", "previsoes_futuras",
             "exogena_pib", "equacao_modelo"]:
    print(f"  - {nome}")
