"""Previsoes mensais de ICMS, IPVA e ITCD por UF, com base no sarimax_icms."""

import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pmdarima import auto_arima
from pmdarima.arima import ARIMA
from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch
from statsmodels.stats.stattools import jarque_bera


RAIZ = Path(__file__).resolve().parents[1]
PASTA_PLANILHAS = RAIZ / "Planilhas"
ARQUIVO_RECEITAS = PASTA_PLANILHAS / "rreo_anexo3_todos_anos.xlsx"
ARQUIVO_PIB = PASTA_PLANILHAS / "Grade SPE 05 03 2026.xlsx"
SAIDA_EXCEL = PASTA_PLANILHAS / "previsoes_icms_ipva_itcd_estados_com_pib.xlsx"
PASTA_GRAFICOS = RAIZ / "Gráficos" / "graficos_previsao_sarimax_3"
TRIBUTOS = ("ICMS", "IPVA", "ITCD")
UFS = tuple("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())
COLS_EXOG = ("log_pib", "pib_var_12m", "pib_var_mom")
CRISES = (
    ("d_crise_br", "2015-01-01", "2016-12-31"),
    ("d_pandemia", "2020-03-01", "2020-12-31"),
)
AUTO_ARIMA_KW = dict(
    seasonal=True, m=12, stepwise=True, suppress_warnings=True,
    error_action="ignore", information_criterion="aicc",
    max_p=2, max_q=2, max_P=1, max_Q=1, max_D=1, max_d=1,
    with_intercept=True,
)


def calcular_metricas(y_real, y_prev):
    y_real = np.asarray(y_real, dtype=float)
    erro = y_real - np.asarray(y_prev, dtype=float)
    mask = y_real != 0
    return {
        "rmse": float(np.sqrt(np.mean(erro ** 2))),
        "mae": float(np.mean(np.abs(erro))),
        "mape": float(np.mean(np.abs(erro[mask] / y_real[mask])) * 100) if mask.any() else np.nan,
    }


def preparar_receitas(df):
    df = df.copy()
    df["tributo"] = df["conta"].astype(str).str.strip().str.upper()
    df["tributo"] = df["tributo"].replace({"ITCMD": "ITCD"})
    df["uf_consulta"] = df["uf_consulta"].astype(str).str.strip().str.upper()
    df = df[df["tributo"].isin(TRIBUTOS) & df["uf_consulta"].isin(UFS)].copy()
    for coluna in ("ano_consulta", "periodo_consulta", "valor"):
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce")
    df = df[df["coluna"].str.match(r"^<MR(?:-\d+)?>$", na=False)].copy()
    df = df.dropna(subset=["ano_consulta", "periodo_consulta", "valor"])
    df = df[df["periodo_consulta"].isin(range(1, 7))].copy()
    deslocamento = df["coluna"].str.extract(r"^<MR(?:-(\d+))?>$")[0].fillna("0").astype(int)
    fim_bimestre = pd.to_datetime(dict(
        year=df["ano_consulta"].astype(int),
        month=2 * df["periodo_consulta"].astype(int),
        day=1,
    ))
    df["data"] = (fim_bimestre.dt.to_period("M") - deslocamento).dt.to_timestamp()
    df["referencia_declaracao"] = fim_bimestre
    return (
        df.sort_values("referencia_declaracao", kind="stable")
        .drop_duplicates(["tributo", "uf_consulta", "data"], keep="last")
        .sort_values(["tributo", "uf_consulta", "data"])
        [["tributo", "uf_consulta", "data", "valor"]]
        .reset_index(drop=True)
    )


def ler_pib(arquivo):
    df = pd.read_excel(arquivo, sheet_name="PIB Mensal", header=None,
                       skiprows=5, names=["data", "pib"])
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    df["pib"] = pd.to_numeric(df["pib"], errors="coerce")
    df = df.dropna(subset=["data", "pib"]).copy()
    df["data"] = df["data"].dt.to_period("M").dt.to_timestamp()
    df = df.groupby("data", as_index=False)["pib"].mean().sort_values("data")
    df = df.set_index("data").asfreq("MS").reset_index()
    df["log_pib"] = np.log(df["pib"].where(df["pib"] > 0))
    df["pib_var_12m"] = df["pib"].pct_change(12, fill_method=None) * 100
    df["pib_var_mom"] = df["pib"].pct_change(fill_method=None) * 100
    return df.replace([np.inf, -np.inf], np.nan)


def build_crisis_dummies(datas):
    indice = pd.DatetimeIndex(datas)
    return pd.DataFrame({
        nome: ((indice >= pd.Timestamp(inicio)) & (indice <= pd.Timestamp(fim))).astype(int)
        for nome, inicio, fim in CRISES
    }, index=indice)


def preparar_exogenas(df_pib, indice, datas_futuras):
    exogs = {}
    dummies_hist = build_crisis_dummies(indice)
    dummies_fut = build_crisis_dummies(datas_futuras)
    for nome in COLS_EXOG:
        historico = df_pib.set_index("data")[[nome]].reindex(indice)
        historico[nome] = historico[nome].interpolate(method="time").ffill().bfill()
        futuro = df_pib.set_index("data")[[nome]].reindex(datas_futuras)
        futuro[nome] = futuro[nome].interpolate(method="time").ffill().bfill()
        futuro[nome] = futuro[nome].fillna(historico[nome].iloc[-1])
        if historico.isna().any().any() or futuro.isna().any().any():
            continue
        exogs[nome] = (pd.concat([historico, dummies_hist], axis=1),
                       pd.concat([futuro, dummies_fut], axis=1))
    exogs["dummies"] = (dummies_hist, dummies_fut)
    return exogs


def diagnosticos_residuos(modelo, indice):
    if modelo is None:
        return None
    residuos = np.asarray(modelo.arima_res_.resid, dtype=float)
    burn = modelo.order[1] + modelo.seasonal_order[1] * modelo.seasonal_order[3]
    datas = pd.DatetimeIndex(indice)[burn:]
    residuos = residuos[burn:]
    finitos = np.isfinite(residuos)
    residuos, datas = residuos[finitos], datas[finitos]
    if len(residuos) < 20:
        return None
    lags_lb = min(12, len(residuos) // 2 - 1)
    lags_arch = min(12, len(residuos) // 5)
    return {
        "lb_p": float(acorr_ljungbox(residuos, lags=[lags_lb], return_df=True)["lb_pvalue"].iloc[0]),
        "jb_p": float(jarque_bera(residuos)[1]),
        "arch_p": float(het_arch(residuos, nlags=lags_arch)[1]),
        "arch_lags": lags_arch, "resid": residuos, "datas": datas,
    }


def ajustar_serie(y, df_pib, identificacao):
    if y.isna().any() or not np.isfinite(y.to_numpy()).all():
        raise ValueError("Serie com meses faltantes ou valores nao finitos.")
    usa_nivel = bool((y < 0).any())
    holdout = max(12, int(round(len(y) * 0.25)))
    if len(y) - holdout < 24:
        raise ValueError(f"Serie curta: {len(y)} observacoes; treino minimo de 24 meses.")
    horizonte = 12 - y.index[-1].month or 12
    datas_futuras = pd.date_range(y.index[-1] + pd.offsets.MonthBegin(1), periods=horizonte, freq="MS")
    exogs = preparar_exogenas(df_pib, y.index, datas_futuras)
    usa_log1p = bool((y == 0).any())
    if usa_nivel:
        transformar = inverter = np.asarray
    else:
        transformar, inverter = (np.log1p, np.expm1) if usa_log1p else (np.log, np.exp)
    def restaurar(valores):
        valores = np.asarray(inverter(valores), dtype=float)
        return valores if usa_nivel else np.maximum(0, valores)

    y_log = pd.Series(transformar(y), index=y.index)
    treino_log = y_log.iloc[:-holdout]
    y_teste = y.iloc[-holdout:]
    candidatos = {}
    for nome in (*exogs, None):
        try:
            historico = exogs[nome][0] if nome is not None else None
            treino = historico.iloc[:-holdout] if historico is not None else None
            teste = historico.iloc[-holdout:] if historico is not None else None
            modelo = auto_arima(treino_log, X=treino, **AUTO_ARIMA_KW)
            previsao = restaurar(modelo.predict(n_periods=holdout, X=teste))
            if not np.isfinite(previsao).all():
                raise ValueError("Previsao nao finita no holdout.")
            chave = f"SARIMAX_{nome}" if nome is not None else "SARIMA_puro"
            candidatos[chave] = (modelo, previsao, nome)
        except Exception as erro:
            print(f"{identificacao} | candidato {nome or 'SARIMA_puro'} falhou: {erro}")
    candidatos["naive_sazonal"] = (None, np.resize(y.iloc[:-holdout].values[-12:], holdout), None)
    metricas = {nome: calcular_metricas(y_teste, candidato[1]) for nome, candidato in candidatos.items()}
    vencedor = min(metricas, key=lambda nome: metricas[nome]["rmse"])
    best, _, exog_nome = candidatos[vencedor]
    tipo_modelo = vencedor
    modelo, diag = None, None
    previsao = np.resize(y.values[-12:], horizonte)
    intervalo = np.full((horizonte, 2), np.nan)
    historico, futuro = exogs[exog_nome] if exog_nome is not None else (None, None)
    if best is not None:
        try:
            modelo = ARIMA(order=best.order, seasonal_order=best.seasonal_order,
                           with_intercept=True, suppress_warnings=True)
            modelo.fit(y_log, X=historico)
            prev_log, ic_log = modelo.predict(n_periods=horizonte, X=futuro, return_conf_int=True, alpha=0.05)
            previsao = restaurar(prev_log)
            intervalo = restaurar(ic_log)
            if not np.isfinite(previsao).all() or not np.isfinite(intervalo).all():
                raise ValueError("Previsao ou intervalo nao finito no reajuste.")
        except Exception as erro:
            print(f"{identificacao} | reajuste falhou; usando naive sazonal: {erro}")
            modelo, exog_nome, historico, futuro = None, None, None, None
            tipo_modelo = "naive_sazonal_fallback"
            previsao = np.resize(y.values[-12:], horizonte)
            intervalo = np.full((horizonte, 2), np.nan)
    try:
        diag = diagnosticos_residuos(modelo, y.index)
        if diag is not None and diag["arch_p"] < 0.05:
            sigma = float(np.std(diag["resid"]))
            outliers = np.flatnonzero(np.abs(diag["resid"]) > 3 * sigma) if sigma > 0 else np.array([], dtype=int)
            outliers = outliers[np.argsort(np.abs(diag["resid"][outliers]))[-10:]]
            if outliers.size:
                datas_outliers = diag["datas"][outliers]
                colunas = [f"out_{data:%Y%m}" for data in datas_outliers]
                hist_out = pd.DataFrame(0, index=y.index, columns=colunas)
                fut_out = pd.DataFrame(0, index=datas_futuras, columns=colunas)
                for data, coluna in zip(datas_outliers, colunas):
                    hist_out.loc[data, coluna] = 1
                hist_retry = pd.concat([historico, hist_out], axis=1) if historico is not None else hist_out
                fut_retry = pd.concat([futuro, fut_out], axis=1) if futuro is not None else fut_out
                retry = ARIMA(order=modelo.order, seasonal_order=modelo.seasonal_order,
                              with_intercept=True, suppress_warnings=True)
                retry.fit(y_log, X=hist_retry)
                prev_log, ic_log = retry.predict(n_periods=horizonte, X=fut_retry, return_conf_int=True, alpha=0.05)
                prev_retry = restaurar(prev_log)
                ic_retry = restaurar(ic_log)
                if np.isfinite(prev_retry).all() and np.isfinite(ic_retry).all():
                    modelo, previsao, intervalo = retry, prev_retry, ic_retry
                    tipo_modelo += f"+out{outliers.size}"
                    diag = diagnosticos_residuos(modelo, y.index)
    except Exception as erro:
        print(f"{identificacao} | diagnostico/reajuste de outliers falhou: {erro}")
    registro = {
        "tipo_modelo": tipo_modelo, "modelo_selecionado_holdout": vencedor,
        "exog_utilizada": exog_nome or "",
        "transformacao": "nivel" if usa_nivel else ("log1p" if usa_log1p else "log"),
        "order": str(modelo.order) if modelo is not None else "",
        "seasonal_order": str(modelo.seasonal_order) if modelo is not None else "",
        "aic": modelo.aic() if modelo is not None else np.nan,
        "bic": modelo.bic() if modelo is not None else np.nan,
        "n_obs": len(y), "holdout": holdout, "inicio_serie": y.index.min(), "fim_serie": y.index.max(),
        "intervalo_95_disponivel": modelo is not None,
        **{f"{metrica}_teste": valor for metrica, valor in metricas[vencedor].items()},
        **{f"rmse_{nome}": valores["rmse"] for nome, valores in metricas.items()},
        **{nome: diag[nome] if diag is not None else np.nan for nome in ("lb_p", "jb_p", "arch_p")},
    }
    tabela = pd.DataFrame({
        "data_previsao": datas_futuras, "ano": datas_futuras.year, "mes_num": datas_futuras.month,
        "mes": datas_futuras.strftime("%b"), "valor_previsto": previsao,
        "limite_inferior_95": intervalo[:, 0], "limite_superior_95": intervalo[:, 1],
        "exog_nome": exog_nome or "",
        "exog_valor": futuro[exog_nome].to_numpy() if exog_nome in COLS_EXOG else np.nan,
    })
    return tabela, registro, diag


def salvar_graficos(y, tabela, registro, diag, tributo, uf):
    pasta = PASTA_GRAFICOS / tributo.lower()
    pasta.mkdir(parents=True, exist_ok=True)
    fig, eixo = plt.subplots(figsize=(10, 5.5))
    eixo.plot(y.index, y.values, marker="o", label="Observado")
    eixo.plot([y.index[-1], *tabela["data_previsao"]], [y.iloc[-1], *tabela["valor_previsto"]],
              linestyle="--", marker="o", label=f"Previsao ({registro['tipo_modelo']})")
    if registro["intervalo_95_disponivel"]:
        eixo.fill_between(tabela["data_previsao"], tabela["limite_inferior_95"],
                          tabela["limite_superior_95"], alpha=0.2, label="Intervalo 95%")
    eixo.axvline(y.index[-1], linestyle=":", alpha=0.7)
    eixo.set(title=f"{tributo} - {uf} | historico e previsao de {len(tabela)} meses",
             xlabel="Data", ylabel="Valor (R$)")
    eixo.grid(True, alpha=0.3)
    eixo.legend()
    fig.tight_layout()
    fig.savefig(pasta / f"previsao_{tributo.lower()}_{uf}.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    if diag is not None and diag["arch_p"] < 0.05:
        fig, eixos = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        eixos[0].plot(diag["datas"], diag["resid"], color="steelblue")
        eixos[0].axhline(0, color="black", linewidth=0.8)
        eixos[0].set_title(f"{tributo} - {uf} | ARCH({diag['arch_lags']}) p={diag['arch_p']:.3f}")
        eixos[1].plot(diag["datas"], diag["resid"] ** 2, color="firebrick")
        eixos[1].set(xlabel="Data", ylabel="Residuos ao quadrado")
        fig.tight_layout()
        fig.savefig(pasta / f"residuos_arch_{tributo.lower()}_{uf}.png", dpi=200, bbox_inches="tight")
        plt.close(fig)


def main():
    warnings.filterwarnings("ignore")
    receitas = preparar_receitas(pd.read_excel(ARQUIVO_RECEITAS))
    df_pib = ler_pib(ARQUIVO_PIB)
    previsoes, modelos, ocorrencias = [], [], []
    for tributo in TRIBUTOS:
        for uf in UFS:
            identificacao = f"{tributo} | {uf}"
            base = receitas[(receitas["tributo"] == tributo) & (receitas["uf_consulta"] == uf)]
            try:
                if base.empty:
                    raise ValueError("Sem dados para este tributo/UF.")
                serie = base.set_index("data")["valor"].sort_index().asfreq("MS")
                tabela, registro, diag = ajustar_serie(serie, df_pib, identificacao)
            except Exception as erro:
                print(f"Aviso: {identificacao} | {erro}")
                ocorrencias.append({"tributo": tributo, "uf": uf, "status": "sem_previsao", "motivo": str(erro)})
                continue
            tabela.insert(0, "uf", uf)
            tabela.insert(0, "tributo", tributo)
            previsoes.append(tabela)
            modelos.append({"tributo": tributo, "uf": uf, **registro})
            ocorrencias.append({"tributo": tributo, "uf": uf, "status": "ok", "motivo": ""})
            try:
                salvar_graficos(serie, tabela, registro, diag, tributo, uf)
            except Exception as erro:
                ocorrencias.append({"tributo": tributo, "uf": uf, "status": "erro_grafico", "motivo": str(erro)})
            print(f"OK | {identificacao} | {registro['tipo_modelo']} | RMSE={registro['rmse_teste']:.2f}")
    PASTA_PLANILHAS.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(SAIDA_EXCEL, engine="openpyxl") as writer:
        (pd.concat(previsoes, ignore_index=True) if previsoes else pd.DataFrame()).to_excel(
            writer, sheet_name="previsoes", index=False)
        pd.DataFrame(modelos).to_excel(writer, sheet_name="modelos", index=False)
        df_pib.to_excel(writer, sheet_name="pib_tratado", index=False)
        pd.DataFrame(ocorrencias).to_excel(writer, sheet_name="ocorrencias", index=False)
    print(f"\nExcel: {SAIDA_EXCEL}")
    print(f"Graficos: {PASTA_GRAFICOS}")
    print(f"Series previstas: {len(modelos)} de {len(TRIBUTOS) * len(UFS)}")


if __name__ == "__main__":
    main()