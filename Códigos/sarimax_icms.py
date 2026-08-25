import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pmdarima import auto_arima
from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch
from statsmodels.stats.stattools import jarque_bera

warnings.filterwarnings("ignore")


# =========================
# MÉTRICAS DE ERRO
# =========================
def calcular_metricas(y_real, y_prev):
    """Retorna RMSE, MAE, MAPE e MASE simplificado."""
    y_real = np.asarray(y_real, dtype=float)
    y_prev = np.asarray(y_prev, dtype=float)
    erro = y_real - y_prev
    rmse = float(np.sqrt(np.mean(erro ** 2)))
    mae = float(np.mean(np.abs(erro)))
    # MAPE evita divisão por zero
    mask = y_real != 0
    mape = float(np.mean(np.abs(erro[mask] / y_real[mask])) * 100) if mask.any() else np.nan
    return rmse, mae, mape


# =========================
# DIAGNÓSTICO DE RESÍDUOS
# =========================
def diagnosticos_residuos(modelo, uf, tipo_modelo, alpha=0.05):
    """Ljung-Box, Jarque-Bera e ARCH-LM nos resíduos do modelo final; imprime veredicto."""
    if modelo is None:
        print(f"DIAG {uf} | {tipo_modelo} | naive: sem resíduos para testar")
        return
    try:
        # descarta o warm-up do filtro de Kalman: primeiros d+D*m resíduos são degenerados
        resid = np.asarray(modelo.arima_res_.resid, dtype=float)
        d = modelo.order[1]
        D = modelo.seasonal_order[1] if len(modelo.seasonal_order) >= 2 else 0
        m = modelo.seasonal_order[3] if len(modelo.seasonal_order) >= 4 else 0
        burn = int(d + D * m)
        resid = resid[burn:]
        resid = resid[np.isfinite(resid)]
        if resid.size < 20:
            print(f"DIAG {uf} | {tipo_modelo} | resíduos insuficientes ({resid.size}) para diagnóstico")
            return
        lags_lb = min(12, resid.size // 2 - 1)
        lb_p = float(acorr_ljungbox(resid, lags=[lags_lb], return_df=True)["lb_pvalue"].iloc[0])
        _, jb_p, _, _ = jarque_bera(resid)
        lags_arch = min(12, resid.size // 5)
        _, arch_p, _, _ = het_arch(resid, nlags=lags_arch)
        # Não-normalidade sozinha não invalida coeficientes/significâncias (validade assintótica);
        # só LB (autocorrelação) e ARCH (heteroscedasticidade) são considerados problemas críticos.
        problemas = []
        if lb_p < alpha:
            problemas.append(f"autocorr(LB p={lb_p:.3f})")
        if arch_p < alpha:
            problemas.append(f"heteroced.(ARCH p={arch_p:.3f})")
        alertas = []
        if jb_p < alpha:
            alertas.append(f"nao-normal(JB p={jb_p:.3f})")
        if problemas:
            veredicto = "MODELO COM PROBLEMAS -> " + "; ".join(problemas)
            if alertas:
                veredicto += " | alerta: " + "; ".join(alertas)
        else:
            veredicto = "MODELO OK" + (" (alerta: " + "; ".join(alertas) + ")" if alertas else "")
        print(
            f"DIAG {uf} | {tipo_modelo} | "
            f"LB({lags_lb}) p={lb_p:.3f} | JB p={jb_p:.3f} | ARCH({lags_arch}) p={arch_p:.3f} | {veredicto}"
        )
    except Exception as e:
        print(f"DIAG {uf} | {tipo_modelo} | diagnóstico falhou: {e}")


# HOLDOUT calculado por UF: 25 % da série, mínimo 12 meses (ver dentro do loop)

# =========================
# ARQUIVOS
# =========================
arquivo_icms = Path(r"C:\Users\carlos.marchionatti\GitHub - STN\STN-gered\Planilhas\rreo_anexo3_todos_anos.xlsx")
arquivo_pib  = Path(r"C:\Users\carlos.marchionatti\GitHub - STN\STN-gered\Planilhas\Grade SPE 05 03 2026.xlsx")

pasta_planilhas = Path(r"C:\Users\carlos.marchionatti\GitHub - STN\STN-gered\Planilhas")
pasta_planilhas.mkdir(parents=True, exist_ok=True)
saida_excel = pasta_planilhas / "previsoes_icms_estados_com_pib.xlsx"

pasta_graficos = Path(r"C:\Users\carlos.marchionatti\GitHub - STN\STN-gered\Gráficos\graficos_previsao_icms_estados_com_pib")
pasta_graficos.mkdir(parents=True, exist_ok=True)

# =========================
# MAPEAMENTO DAS COLUNAS MENSAIS DO PERÍODO 6
# =========================
mapa_p6 = {
    "<MR-11>": 1,  "<MR-10>": 2,  "<MR-9>": 3,  "<MR-8>": 4,
    "<MR-7>":  5,  "<MR-6>":  6,  "<MR-5>": 7,  "<MR-4>": 8,
    "<MR-3>":  9,  "<MR-2>":  10, "<MR-1>": 11, "<MR>":   12,
}

# =========================
# LEITURA E FILTRO DO ICMS
# =========================
df = pd.read_excel(arquivo_icms)
df["conta"] = df["conta"].astype(str).str.upper()
df_icms_all = df[df["conta"] == "ICMS"].copy()

# Linhas dos anos fechados (bimestre 6): 12 meses de janeiro a dezembro.
df_p6 = df_icms_all[
    (df_icms_all["periodo_consulta"] == 6) &
    (df_icms_all["coluna"].isin(mapa_p6))
].copy()
df_p6["mes_num"] = df_p6["coluna"].map(mapa_p6)

# Linhas do bimestre corrente do ano atual (<MR>, <MR-1>, ..., <MR-11>):
# <MR>   = último mês do bimestre  (2 * nr_bimestre)
# <MR-k> = k meses antes de <MR>   (2 * nr_bimestre - k)
df_bim = df_icms_all[
    (df_icms_all["periodo_consulta"] != 6) &
    (df_icms_all["coluna"].str.match(r"^<MR(-\d+)?>$", na=False))
].copy()
_k = df_bim["coluna"].str.extract(r"^<MR(?:-(\d+))?>$")[0].fillna("0").astype(int)
df_bim["mes_num"] = 2 * df_bim["periodo_consulta"].astype(int) - _k

df_icms = pd.concat([df_p6, df_bim], ignore_index=True)
df_icms["valor"] = pd.to_numeric(df_icms["valor"], errors="coerce")
df_icms["ano_consulta"] = pd.to_numeric(df_icms["ano_consulta"], errors="coerce").astype("Int64")
df_icms["mes_num"]      = pd.to_numeric(df_icms["mes_num"], errors="coerce").astype("Int64")

df_icms["data"] = pd.to_datetime(
    dict(year=df_icms["ano_consulta"], month=df_icms["mes_num"], day=1),
    errors="coerce"
)

df_icms = (
    df_icms.dropna(subset=["data", "valor"])
    .groupby(["uf_consulta", "data"], as_index=False)["valor"]
    .sum()
    .sort_values(["uf_consulta", "data"])
)

# =========================
# LEITURA DO PIB MENSAL
# =========================
# Na aba "PIB Mensal", os dados começam após algumas linhas de cabeçalho.
df_pib = pd.read_excel(
    arquivo_pib,
    sheet_name="PIB Mensal",
    header=None,
    skiprows=5,
    names=["data", "pib"]
).copy()

df_pib["data"] = pd.to_datetime(df_pib["data"], errors="coerce")
df_pib["pib"] = pd.to_numeric(df_pib["pib"], errors="coerce")
df_pib = df_pib.dropna(subset=["data", "pib"]).copy()

# Normaliza para início do mês e agrega possíveis duplicidades no mesmo mês.
df_pib["data"] = df_pib["data"].dt.to_period("M").dt.to_timestamp(how="start")
df_pib = (
    df_pib.groupby("data", as_index=False)["pib"]
    .mean()
    .sort_values("data")
    .reset_index(drop=True)
)

# =========================
# TRANSFORMAÇÕES DO PIB
# =========================
df_pib["pib_var_mom"] = df_pib["pib"].pct_change() * 100
df_pib["pib_var_12m"] = df_pib["pib"].pct_change(12) * 100
df_pib["log_pib"] = np.where(df_pib["pib"] > 0, np.log(df_pib["pib"]), np.nan)

# ESCOLHA DA EXÓGENA:
# O script testa as três opções abaixo por UF e escolhe automaticamente a melhor
# pelo RMSE no holdout. "log_pib" é PIB nominal em log (relacionamento de elasticidade
# com log(ICMS)); "pib_var_12m" é a variação interanual; "pib_var_mom" é a mês-a-mês.
cols_exog_candidatas = ["log_pib", "pib_var_12m", "pib_var_mom"]

dfs_exog = {
    nome: df_pib[["data", nome]].dropna().copy()
    for nome in cols_exog_candidatas
}

# =========================
# AJUSTE E PREVISÃO
# =========================
resultados_previsao = []
resultados_modelo = []

ufs = sorted(df_icms["uf_consulta"].dropna().unique())

for uf in ufs:
    base = df_icms[df_icms["uf_consulta"] == uf].copy()

    # frequência mensal da série alvo
    y = (
        base.sort_values("data")
        .set_index("data")["valor"]
        .asfreq("MS")
    )

    # checagem de lacunas na série alvo
    if y.isna().any():
        print(f"Aviso: {uf} tem meses faltantes no ICMS. Pulando.")
        continue

    # HOLDOUT = 25 % da série, mínimo 12 meses
    HOLDOUT = max(12, int(round(len(y) * 0.25)))
    # garante treino mínimo de 24 meses (2 ciclos sazonais)
    if len(y) - HOLDOUT < 24:
        print(f"Aviso: {uf} tem série curta demais ({len(y)} obs) para holdout de {HOLDOUT}. Pulando.")
        continue

    ultima_data = y.index[-1]
    # Horizonte dinâmico: prevê até dezembro do mesmo ano.
    # Se o último mês já for dezembro, prevê 12 meses do ano seguinte.
    meses_ate_dezembro = 12 - ultima_data.month
    horizonte = meses_ate_dezembro if meses_ate_dezembro > 0 else 12
    datas_futuras = pd.date_range(
        ultima_data + pd.offsets.MonthBegin(1),
        periods=horizonte,
        freq="MS"
    )

    # alinha cada candidata exógena ao índice mensal de y e prepara horário futuro
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
            print(f"Aviso: {uf} sem exógena utilizável para {nome}; descartando essa candidata.")
            continue

        exogs[nome] = (X_uf, X_fut_uf)

    # =========================
    # BACKTEST (holdout dos últimos HOLDOUT meses)
    # =========================
    y_treino, y_teste = y.iloc[:-HOLDOUT], y.iloc[-HOLDOUT:]

    # transformação log estabiliza a variância e evita previsões "achatadas"
    y_treino_log = np.log(y_treino)
    y_log = np.log(y)

    candidatos = {}

    # candidatos SARIMAX: um por exógena disponível
    for nome, (X_uf, _) in exogs.items():
        X_treino = X_uf.iloc[:-HOLDOUT]
        X_teste = X_uf.iloc[-HOLDOUT:]
        try:
            m = auto_arima(y_treino_log, X=X_treino, seasonal=True, m=12,
                           stepwise=True, suppress_warnings=True,
                           error_action="ignore", information_criterion="aicc",
                           max_p=3, max_q=3, max_P=2, max_Q=2, max_D=1,
                           with_intercept=True)
            prev = np.exp(m.predict(n_periods=HOLDOUT, X=X_teste))
            candidatos[f"SARIMAX_{nome}"] = (m, prev)
        except Exception as e:
            print(f"{uf} SARIMAX_{nome} falhou: {e}")

    # candidato SARIMA puro (sem exógena)
    try:
        m2 = auto_arima(y_treino_log, seasonal=True, m=12,
                        stepwise=True, suppress_warnings=True,
                        error_action="ignore", information_criterion="aicc",
                        max_p=3, max_q=3, max_P=2, max_Q=2, max_D=1,
                        with_intercept=True)
        prev2 = np.exp(m2.predict(n_periods=HOLDOUT))
        candidatos["SARIMA_puro"] = (m2, prev2)
    except Exception as e:
        print(f"{uf} SARIMA_puro falhou: {e}")

    # candidato naïve sazonal (y_t = y_{t-12})
    if len(y_treino) >= 12:
        ultimos_12_tr = y_treino.iloc[-12:].values
        prev3 = np.array([ultimos_12_tr[i % 12] for i in range(HOLDOUT)])
        candidatos["naive_sazonal"] = (None, prev3)

    # calcula métricas e escolhe o melhor pelo RMSE
    metricas_uf = {}
    for nome, (mod, prev) in candidatos.items():
        rmse, mae, mape = calcular_metricas(y_teste.values, prev)
        metricas_uf[nome] = {"rmse": rmse, "mae": mae, "mape": mape}

    if not metricas_uf:
        print(f"{uf}: nenhum candidato treinou. Pulando.")
        continue

    # Seleciona o melhor modelo pelo RMSE, incluindo o naïve sazonal.
    melhor_nome = min(metricas_uf, key=lambda k: metricas_uf[k]["rmse"])
    print(f"{uf} | melhor = {melhor_nome} | métricas: {metricas_uf}")

    # Identifica a exógena vencedora (se houver)
    if melhor_nome.startswith("SARIMAX_"):
        exog_vencedora = melhor_nome.replace("SARIMAX_", "")
        X_full, X_fut = exogs[exog_vencedora]
    else:
        exog_vencedora = ""
        X_full, X_fut = None, None

    # =========================
    # REAJUSTE FINAL com toda a série usando o tipo vencedor
    # =========================
    try:
        if melhor_nome.startswith("SARIMAX_"):
            modelo = auto_arima(y_log, X=X_full, seasonal=True, m=12,
                                stepwise=True, suppress_warnings=True,
                                error_action="ignore", information_criterion="aicc",
                                max_p=3, max_q=3, max_P=2, max_Q=2, max_D=1,
                                with_intercept=True)
            prev_log, conf_int_log = modelo.predict(n_periods=horizonte, X=X_fut,
                                                    return_conf_int=True, alpha=0.05)
            previsao = np.exp(prev_log)
            conf_int = np.exp(conf_int_log)
            tipo_modelo = melhor_nome
        elif melhor_nome == "SARIMA_puro":
            modelo = auto_arima(y_log, seasonal=True, m=12,
                                stepwise=True, suppress_warnings=True,
                                error_action="ignore", information_criterion="aicc",
                                max_p=3, max_q=3, max_P=2, max_Q=2, max_D=1,
                                with_intercept=True)
            prev_log, conf_int_log = modelo.predict(n_periods=horizonte,
                                                    return_conf_int=True, alpha=0.05)
            previsao = np.exp(prev_log)
            conf_int = np.exp(conf_int_log)
            tipo_modelo = "SARIMA_puro"
        else:  # naive_sazonal
            modelo = None
            # y_{t} = y_{t-12}: para cada passo i (0..horizonte-1) pega o mesmo mês do ano anterior.
            previsao = np.array([y.iloc[-12 + i] for i in range(horizonte)])
            conf_int = np.column_stack([previsao * 0.9, previsao * 1.1])
            tipo_modelo = "naive_sazonal"
    except Exception:
        # Fallback: ARIMAX sem sazonalidade, usando a primeira exógena disponível.
        nome_fb = next(iter(exogs)) if exogs else None
        X_fb, X_fut_fb = exogs[nome_fb] if nome_fb else (None, None)
        modelo = auto_arima(y_log, X=X_fb, seasonal=False, stepwise=True,
                            suppress_warnings=True, error_action="ignore",
                            information_criterion="aicc", max_p=4, max_q=4)
        prev_log, conf_int_log = modelo.predict(n_periods=horizonte, X=X_fut_fb,
                                                return_conf_int=True, alpha=0.05)
        previsao = np.exp(prev_log)
        conf_int = np.exp(conf_int_log)
        tipo_modelo = f"ARIMAX_fallback_{nome_fb}" if nome_fb else "ARIMAX_fallback"
        exog_vencedora = nome_fb or ""
        X_fut = X_fut_fb

    previsao = np.asarray(previsao, dtype=float)
    conf_int = np.asarray(conf_int, dtype=float)

    diagnosticos_residuos(modelo, uf, tipo_modelo)

    for i, data in enumerate(datas_futuras):
        resultados_previsao.append({
            "uf": uf,
            "data_previsao": data,
            "ano": data.year,
            "mes_num": data.month,
            "mes": data.strftime("%b"),
            "valor_previsto": float(previsao[i]),
            "limite_inferior_95": float(conf_int[i, 0]),
            "limite_superior_95": float(conf_int[i, 1]),
            "exog_nome": exog_vencedora,
            "exog_valor": float(X_fut.iloc[i, 0]) if X_fut is not None else np.nan,
        })

    resultados_modelo.append({
        "uf": uf,
        "tipo_modelo": tipo_modelo,
        "exog_utilizada": exog_vencedora,
        "order": str(modelo.order) if modelo is not None else "",
        "seasonal_order": str(modelo.seasonal_order) if modelo is not None else "",
        "aic": modelo.aic() if modelo is not None else np.nan,
        "bic": modelo.bic() if modelo is not None else np.nan,
        "rmse_teste": metricas_uf[melhor_nome]["rmse"],
        "mae_teste": metricas_uf[melhor_nome]["mae"],
        "mape_teste": metricas_uf[melhor_nome]["mape"],
        "rmse_SARIMAX_log_pib": metricas_uf.get("SARIMAX_log_pib", {}).get("rmse", np.nan),
        "rmse_SARIMAX_pib_var_12m": metricas_uf.get("SARIMAX_pib_var_12m", {}).get("rmse", np.nan),
        "rmse_SARIMAX_pib_var_mom": metricas_uf.get("SARIMAX_pib_var_mom", {}).get("rmse", np.nan),
        "rmse_SARIMA_puro": metricas_uf.get("SARIMA_puro", {}).get("rmse", np.nan),
        "rmse_naive_sazonal": metricas_uf.get("naive_sazonal", {}).get("rmse", np.nan),
        "n_obs": len(y),
        "inicio_serie": y.index.min(),
        "fim_serie": y.index.max(),
    })

    # gráfico
    plt.figure(figsize=(10, 5.5))

    plt.plot(y.index, y.values, marker="o", label="Observado")

    x_previsao = [y.index[-1]] + list(datas_futuras)
    y_previsao = [y.iloc[-1]] + list(previsao)

    plt.plot(
        x_previsao,
        y_previsao,
        linestyle="--",
        label=f"Previsão ({exog_vencedora or 'sem exógena'})"
    )

    plt.plot(
        datas_futuras,
        previsao,
        linestyle="None",
        marker="o"
    )

    plt.fill_between(datas_futuras, conf_int[:, 0], conf_int[:, 1], alpha=0.2)
    plt.axvline(y.index[-1], linestyle=":", alpha=0.7)

    plt.title(f"ICMS - {uf} | histórico + previsão {horizonte} meses | exógena: {exog_vencedora or '—'}")
    plt.xlabel("Data")
    plt.ylabel("Valor")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(pasta_graficos / f"previsao_icms_{uf}.png", dpi=200, bbox_inches="tight")
    plt.close()

    if modelo is not None:
        print(
            f"OK | {uf} | {tipo_modelo} | exog={exog_vencedora or '—'} | "
            f"order={modelo.order} | seasonal={modelo.seasonal_order}"
        )
    else:
        print(f"OK | {uf} | {tipo_modelo} (sem ordem ARIMA)")

# =========================
# SALVAR RESULTADOS
# =========================
df_prev = pd.DataFrame(resultados_previsao)
df_mod = pd.DataFrame(resultados_modelo)

with pd.ExcelWriter(saida_excel, engine="openpyxl") as writer:
    df_prev.to_excel(writer, sheet_name="previsoes", index=False)
    df_mod.to_excel(writer, sheet_name="modelos", index=False)
    df_pib.to_excel(writer, sheet_name="pib_tratado", index=False)

print("\nArquivos gerados:")
print(f"- Excel: {saida_excel}")
print(f"- Gráficos PNG: {pasta_graficos.resolve()}")

