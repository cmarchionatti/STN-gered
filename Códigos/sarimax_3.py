"""Previsões mensais de ICMS, IPVA e ITCD por UF."""

import warnings
from datetime import datetime
from pathlib import Path
from time import perf_counter

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pmdarima import auto_arima
from pmdarima.arima import ARIMA, StepwiseContext
from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch
from statsmodels.stats.stattools import jarque_bera
from threadpoolctl import threadpool_limits


RAIZ = Path(__file__).resolve().parents[1]
PASTA_PLANILHAS = RAIZ / "Planilhas"
ARQUIVO_RECEITAS = PASTA_PLANILHAS / "rreo_anexo3_todos_anos.xlsx"
ARQUIVO_PIB = PASTA_PLANILHAS / "pib_mensal.csv"
SAIDA_EXCEL = PASTA_PLANILHAS / "previsoes_icms_ipva_itcd_estados_com_pib.xlsx"
PASTA_GRAFICOS = RAIZ / "Gráficos" / "graficos_previsao_sarimax_3"
TRIBUTOS = ("ICMS", "IPVA", "ITCD")
UFS = tuple("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())
COLS_EXOG = ("log_pib", "pib_var_12m", "pib_var_mom")
MESES_PT_BR = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
MAX_PASSOS_BUSCA = 20
SEGUNDOS_POR_BUSCA = 5
MAX_CANDIDATOS_REAJUSTE = 3
MAX_ORIGENS_ROLLING = 3
MIN_TREINO_ROLLING = 60
HORIZONTE_PREVISAO = None
ORDEM_SARIMA_RESERVA = (1, 1, 0)
ORDEM_SAZONAL_RESERVA = (0, 1, 1, 12)
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


def obter_transformacoes(serie):
    nivel = bool((serie < 0).any())
    log1p = bool((serie == 0).any()) and not nivel
    transformar = np.asarray if nivel else np.log1p if log1p else np.log
    inverter = np.asarray if nivel else np.expm1 if log1p else np.exp
    nome = "nível" if nivel else "log1p" if log1p else "log"
    return transformar, inverter, nivel, nome


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
    opcoes = dict(sep=";", decimal=",", thousands=".", header=0,
                  names=["data", "pib", "situacao"])
    try:
        df = pd.read_csv(arquivo, encoding="utf-8-sig", **opcoes)
    except UnicodeDecodeError:
        df = pd.read_csv(arquivo, encoding="cp1252", **opcoes)
    meses = {nome: numero for numero, nome in enumerate(MESES_PT_BR, 1)}
    partes = df["data"].astype(str).str.strip().str.lower().str.extract(r"^([a-z]{3})/(\d{2}|\d{4})$")
    anos = pd.to_numeric(partes[1], errors="coerce")
    anos = anos.where(anos >= 100, anos + np.where(anos >= 69, 1900, 2000))
    df["data"] = pd.to_datetime(dict(year=anos, month=partes[0].map(meses), day=1), errors="coerce")
    df["pib"] = pd.to_numeric(df["pib"], errors="coerce")
    df["situacao"] = df["situacao"].astype(str).str.strip().str.upper()
    invalidas = df["data"].isna() | ~np.isfinite(df["pib"]) | (df["pib"] <= 0) | ~df["situacao"].isin(("R", "P"))
    if df.empty or invalidas.any():
        raise ValueError(f"PIB vazio ou com linhas inválidas: {(df.index[invalidas] + 2).tolist()}")
    if df["data"].duplicated().any():
        raise ValueError("PIB com meses duplicados.")
    df = df.sort_values("data").reset_index(drop=True)
    faltantes = pd.date_range(df["data"].min(), df["data"].max(), freq="MS").difference(df["data"])
    if len(faltantes):
        print(f"AVISO | PIB com meses ausentes: {faltantes.strftime('%Y-%m').tolist()}; não serão preenchidos.")
    realizados = df.loc[df["situacao"] == "R", "data"]
    projetados = df.loc[df["situacao"] == "P", "data"]
    if realizados.empty or (not projetados.empty and projetados.min() <= realizados.max()):
        raise ValueError("PIB deve conter histórico R seguido, se houver, somente por projeções P.")
    df["fonte"] = df["situacao"].map({"R": "BCB", "P": "SPE"})
    df = df.set_index("data").asfreq("MS").reset_index()
    df["pib"] = df["pib"] / 1000
    df["log_pib"] = np.log(df["pib"])
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
    dummies_hist = build_crisis_dummies(indice)
    dummies_fut = build_crisis_dummies(datas_futuras)
    exogs = {"dummies": (dummies_hist, dummies_fut)}
    pib_indexado = df_pib.set_index("data")
    if not pib_indexado["situacao"].reindex(indice).eq("R").all():
        print("AVISO | PIB histórico ausente ou marcado como projeção; candidatos sem PIB serão mantidos.")
        return exogs
    for nome in COLS_EXOG:
        historico = pib_indexado[[nome]].reindex(indice)
        futuro = pib_indexado[[nome]].reindex(datas_futuras)
        if not np.isfinite(historico.to_numpy()).all() or not np.isfinite(futuro.to_numpy()).all():
            print(f"AVISO | {nome} sem cobertura completa; candidato ignorado, sem preencher o PIB.")
            continue
        exogs[nome] = (pd.concat([historico, dummies_hist], axis=1),
                       pd.concat([futuro, dummies_fut], axis=1))
    return exogs


def diagnosticos_residuos(modelo, indice):
    if modelo is None:
        return None
    residuos = np.asarray(modelo.arima_res_.resid, dtype=float)
    burn = modelo.order[1] + modelo.seasonal_order[1] * modelo.seasonal_order[3]
    datas = pd.DatetimeIndex(indice)[burn:]
    residuos = residuos[burn:]
    model_df = (
        modelo.order[0] + modelo.order[2]
        + modelo.seasonal_order[0] + modelo.seasonal_order[2]
    )
    return diagnosticos_erros(residuos, datas, model_df=model_df)


def diagnosticos_erros(residuos, datas, model_df=0):
    residuos = np.asarray(residuos, dtype=float)
    datas = pd.DatetimeIndex(datas)
    finitos = np.isfinite(residuos)
    residuos, datas = residuos[finitos], datas[finitos]
    if len(residuos) < 20:
        return None
    lags_lb = min(12, len(residuos) // 2 - 1)
    lags_arch = min(12, len(residuos) // 5)
    return {
        "lb_p": float(acorr_ljungbox(
            residuos, lags=[lags_lb], model_df=model_df, return_df=True,
        )["lb_pvalue"].iloc[0]),
        "jb_p": float(jarque_bera(residuos)[1]),
        "arch_p": float(het_arch(residuos, nlags=lags_arch)[1]),
        "arch_lags": lags_arch, "resid": residuos, "datas": datas,
    }


def avaliar_alertas_modelo(registro, diag):
    alertas = []
    if registro.get("convergiu") is False:
        alertas.append("Otimização do modelo final não convergiu.")
    if registro.get("modelo_reserva_usado"):
        alertas.append("Foi usado o SARIMA de reserva após falhas nos candidatos selecionados.")
    if not registro.get("teste_avaliado", False):
        alertas.append("Não foi possível avaliar um SARIMA no teste final independente.")
    if not registro["intervalo_95_disponivel"]:
        alertas.append("Intervalo de previsão de 95% indisponível.")
    if diag is None:
        alertas.append("Diagnósticos dos resíduos indisponíveis; adequação não verificada.")
    else:
        for chave, teste, problema in (
            ("lb_p", "Ljung-Box", "Autocorrelação dos resíduos"),
            ("jb_p", "Jarque-Bera", "Não normalidade dos resíduos"),
            ("arch_p", "ARCH-LM", "Heteroscedasticidade condicional"),
        ):
            valor = diag.get(chave, np.nan)
            if not np.isfinite(valor):
                alertas.append(f"{teste}: p-valor indisponível; teste inconclusivo.")
            elif valor < 0.05:
                alertas.append(f"{problema} ({teste}: p={valor:.4g} < 0.05).")
    return alertas


def ajustar_arima_convergente(base, serie, exogenas, identificacao):
    falhas = []
    for metodo in ("lbfgs", "powell"):
        try:
            modelo = ARIMA(
                order=base.order, seasonal_order=base.seasonal_order,
                with_intercept=base.with_intercept, trend=base.trend,
                method=metodo, maxiter=300, suppress_warnings=True,
            )
            modelo.fit(serie, X=exogenas)
            if not modelo.arima_res_.mle_retvals.get("converged", False):
                raise ValueError("otimização não convergiu")
            if not np.isfinite(modelo.params()).all() or not np.isfinite(modelo.arima_res_.llf):
                raise ValueError("parâmetros ou log-verossimilhança não finitos")
            return modelo
        except Exception as erro:
            falhas.append(f"{metodo}: {erro}")
            print(f"AVISO | {identificacao} | ajuste {metodo} falhou: {erro}")
    raise ValueError("Nenhum otimizador produziu ajuste convergente: " + "; ".join(falhas))


def avaliar_especificacao_rolling(
    y, horizonte, origens, exogs, exog_nome, identificacao, base=None,
    buscar_ordem=False, dummies_outlier=None,
):
    exog_historica = exogs[exog_nome][0] if exog_nome is not None else None
    if dummies_outlier is not None:
        exog_historica = (
            pd.concat([exog_historica, dummies_outlier], axis=1)
            if exog_historica is not None else dummies_outlier
        )
    modelo_base = base
    primeiro_modelo = None
    primeiro_treino = None
    observados = []
    previsoes = []
    for numero_origem, origem in enumerate(origens):
        treino = y.iloc[:origem]
        teste = y.iloc[origem:origem + horizonte]
        transformar, inverter, nivel, _ = obter_transformacoes(treino)
        treino_transformado = pd.Series(transformar(treino), index=treino.index)
        x_treino = exog_historica.loc[treino.index] if exog_historica is not None else None
        x_teste = exog_historica.loc[teste.index] if exog_historica is not None else None
        if buscar_ordem and numero_origem == 0:
            with StepwiseContext(max_steps=MAX_PASSOS_BUSCA, max_dur=SEGUNDOS_POR_BUSCA):
                modelo = auto_arima(treino_transformado, X=x_treino, **AUTO_ARIMA_KW)
            if not modelo.arima_res_.mle_retvals.get("converged", False):
                modelo = ajustar_arima_convergente(
                    modelo, treino_transformado, x_treino,
                    f"{identificacao} | origem rolling {numero_origem + 1} | {exog_nome or 'SARIMA_puro'}",
                )
            modelo_base = modelo
        else:
            if modelo_base is None:
                raise ValueError("Especificação base ausente para a origem rolling.")
            modelo = ajustar_arima_convergente(
                modelo_base, treino_transformado, x_treino,
                f"{identificacao} | origem rolling {numero_origem + 1} | {exog_nome or 'SARIMA'}",
            )
        previsao_transformada = modelo.predict(n_periods=horizonte, X=x_teste)
        previsao = np.asarray(inverter(previsao_transformada), dtype=float)
        if not nivel:
            previsao = np.maximum(0, previsao)
        if not np.isfinite(previsao).all():
            raise ValueError("Previsão rolling não finita.")
        observados.append(teste.to_numpy(dtype=float))
        previsoes.append(previsao)
        if primeiro_modelo is None:
            primeiro_modelo = modelo
            primeiro_treino = treino
    metricas = calcular_metricas(np.concatenate(observados), np.concatenate(previsoes))
    return modelo_base, metricas, primeiro_modelo, primeiro_treino


def ajustar_serie(y, df_pib, identificacao):
    if y.isna().any() or not np.isfinite(y.to_numpy()).all():
        raise ValueError("Série com meses faltantes ou valores não finitos.")
    horizonte = (
        12 - y.index[-1].month or 12
        if HORIZONTE_PREVISAO is None else HORIZONTE_PREVISAO
    )
    if not isinstance(horizonte, int) or horizonte < 1:
        raise ValueError("HORIZONTE_PREVISAO deve ser None ou um inteiro positivo.")
    tamanho_teste = horizonte
    fim_desenvolvimento = len(y) - tamanho_teste
    numero_origens_cv = min(
        MAX_ORIGENS_ROLLING,
        (fim_desenvolvimento - MIN_TREINO_ROLLING) // horizonte,
    )
    if numero_origens_cv < 2:
        raise ValueError(
            f"Série curta para horizonte {horizonte}: são necessárias ao menos duas origens "
            f"rolling com {MIN_TREINO_ROLLING} meses iniciais."
        )
    primeira_origem = fim_desenvolvimento - numero_origens_cv * horizonte
    origens_cv = list(range(primeira_origem, fim_desenvolvimento, horizonte))
    y_teste = y.iloc[fim_desenvolvimento:]
    transformar_final, inverter_final, nivel_final, nome_transformacao_final = obter_transformacoes(y)

    def restaurar(valores, inverter, nivel):
        valores = np.asarray(inverter(valores), dtype=float)
        return valores if nivel else np.maximum(0, valores)

    datas_futuras = pd.date_range(y.index[-1] + pd.offsets.MonthBegin(1), periods=horizonte, freq="MS")
    exogs = preparar_exogenas(df_pib, y.index, datas_futuras)
    y_final_transformada = pd.Series(transformar_final(y), index=y.index)
    candidatos = {}
    metricas_cv = {}
    modelos_primeira_origem = {}
    for nome in (*exogs, None):
        try:
            modelo, metricas, primeiro_modelo, primeiro_treino = avaliar_especificacao_rolling(
                y, horizonte, origens_cv, exogs, nome, identificacao, buscar_ordem=True,
            )
            chave = f"SARIMAX_{nome}" if nome is not None else "SARIMA_puro"
            candidatos[chave] = (modelo, None, nome)
            metricas_cv[chave] = metricas
            modelos_primeira_origem[chave] = (primeiro_modelo, primeiro_treino)
        except Exception as erro:
            print(f"{identificacao} | candidato {nome or 'SARIMA_puro'} falhou: {erro}")
    try:
        base_reserva = ARIMA(
            order=ORDEM_SARIMA_RESERVA,
            seasonal_order=ORDEM_SAZONAL_RESERVA,
            with_intercept=False,
            suppress_warnings=True,
        )
        modelo_reserva, metricas_reserva, primeiro_modelo_reserva, primeiro_treino_reserva = avaliar_especificacao_rolling(
            y, horizonte, origens_cv, exogs, None, identificacao, base=base_reserva,
        )
        candidatos["SARIMA_reserva"] = (modelo_reserva, None, None)
        metricas_cv["SARIMA_reserva"] = metricas_reserva
        modelos_primeira_origem["SARIMA_reserva"] = (primeiro_modelo_reserva, primeiro_treino_reserva)
    except Exception as erro:
        print(f"AVISO | {identificacao} | SARIMA de reserva indisponível: {erro}")
    if not candidatos:
        raise ValueError("Nenhum candidato SARIMA/SARIMAX convergiu, incluindo o SARIMA de reserva.")
    vencedor_cv = min(metricas_cv, key=lambda nome: metricas_cv[nome]["rmse"])
    matrizes_outliers = {}
    base_outliers, _, exog_base = candidatos[vencedor_cv]
    if base_outliers is not None:
        try:
            primeiro_modelo, primeiro_treino = modelos_primeira_origem[vencedor_cv]
            diag_treino = diagnosticos_residuos(primeiro_modelo, primeiro_treino.index)
            if diag_treino is not None and diag_treino["arch_p"] < 0.05:
                sigma = float(np.std(diag_treino["resid"]))
                extremos = np.flatnonzero(np.abs(diag_treino["resid"]) > 3 * sigma) if sigma > 0 else np.array([], dtype=int)
                extremos = extremos[np.argsort(np.abs(diag_treino["resid"][extremos]))[-10:]]
                if extremos.size:
                    datas_outliers = diag_treino["datas"][extremos]
                    colunas = [f"out_{data:%Y%m}" for data in datas_outliers]
                    dummies_outlier = pd.DataFrame(0, index=y.index, columns=colunas)
                    for data, coluna in zip(datas_outliers, colunas):
                        dummies_outlier.loc[data, coluna] = 1
                    modelo_out, metricas_out, primeiro_out, treino_out = avaliar_especificacao_rolling(
                        y, horizonte, origens_cv, exogs, exog_base, identificacao,
                        base=base_outliers, dummies_outlier=dummies_outlier,
                    )
                    if metricas_out["rmse"] < metricas_cv[vencedor_cv]["rmse"]:
                        historico_base, futuro_base = exogs[exog_base] if exog_base is not None else (None, None)
                        historico_out = pd.concat([historico_base, dummies_outlier], axis=1) if historico_base is not None else dummies_outlier
                        futuro_out = pd.DataFrame(0, index=datas_futuras, columns=colunas)
                        futuro_out = pd.concat([futuro_base, futuro_out], axis=1) if futuro_base is not None else futuro_out
                        chave_out = f"{vencedor_cv}+out{len(colunas)}"
                        candidatos[chave_out] = (modelo_out, None, exog_base)
                        metricas_cv[chave_out] = metricas_out
                        modelos_primeira_origem[chave_out] = (primeiro_out, treino_out)
                        matrizes_outliers[chave_out] = (historico_out, futuro_out)
                        vencedor_cv = chave_out
                    else:
                        print(f"INFO | {identificacao} | dummies de outliers descartadas: não melhoraram o RMSE rolling.")
        except Exception as erro:
            print(f"AVISO | {identificacao} | variante rolling com outliers descartada: {erro}")

    candidatos_ordenados = [
        nome for nome in sorted(metricas_cv, key=lambda chave: metricas_cv[chave]["rmse"])
        if candidatos[nome][0] is not None
    ][:MAX_CANDIDATOS_REAJUSTE]
    if "SARIMA_reserva" in candidatos and "SARIMA_reserva" not in candidatos_ordenados:
        candidatos_ordenados.append("SARIMA_reserva")

    y_desenvolvimento = y.iloc[:-tamanho_teste]
    transformar_desenvolvimento, inverter_desenvolvimento, nivel_desenvolvimento, nome_transformacao_treino = obter_transformacoes(y_desenvolvimento)
    y_desenvolvimento_transformado = pd.Series(transformar_desenvolvimento(y_desenvolvimento), index=y_desenvolvimento.index)
    candidato_avaliado_teste = None
    metricas_teste = {"rmse": np.nan, "mae": np.nan, "mape": np.nan}
    base_teste, _, exog_teste = candidatos[vencedor_cv]
    historico_teste = matrizes_outliers[vencedor_cv][0] if vencedor_cv in matrizes_outliers else (
        exogs[exog_teste][0] if exog_teste is not None else None
    )
    x_desenvolvimento = historico_teste.loc[y_desenvolvimento.index] if historico_teste is not None else None
    x_teste = historico_teste.loc[y_teste.index] if historico_teste is not None else None
    try:
        modelo_teste = ajustar_arima_convergente(
            base_teste, y_desenvolvimento_transformado, x_desenvolvimento,
            f"{identificacao} | teste final {vencedor_cv}",
        )
        previsao_teste = restaurar(
            modelo_teste.predict(n_periods=tamanho_teste, X=x_teste),
            inverter_desenvolvimento, nivel_desenvolvimento,
        )
        if not np.isfinite(previsao_teste).all():
            raise ValueError("Previsão não finita no teste final.")
        candidato_avaliado_teste = vencedor_cv
        metricas_teste = calcular_metricas(y_teste, previsao_teste)
    except Exception as erro:
        print(f"AVISO | {identificacao} | candidato escolhido não pôde ser avaliado no teste final: {erro}")

    candidatos_finais = [vencedor_cv] + [nome for nome in candidatos_ordenados if nome != vencedor_cv]
    modelo, diag = None, None
    previsao = intervalo = None
    tipo_modelo = ""
    exog_nome, historico, futuro = None, None, None
    for nome_candidato in candidatos_finais:
        base_candidato, _, exog_candidato = candidatos[nome_candidato]
        if nome_candidato in matrizes_outliers:
            historico_candidato, futuro_candidato = matrizes_outliers[nome_candidato]
        else:
            historico_candidato, futuro_candidato = exogs[exog_candidato] if exog_candidato is not None else (None, None)
        try:
            modelo_candidato = ajustar_arima_convergente(
                base_candidato, y_final_transformada, historico_candidato,
                f"{identificacao} | reajuste final {nome_candidato}",
            )
            prev_log, ic_log = modelo_candidato.predict(
                n_periods=horizonte, X=futuro_candidato, return_conf_int=True, alpha=0.05,
            )
            previsao_candidata = restaurar(prev_log, inverter_final, nivel_final)
            intervalo_candidato = restaurar(ic_log, inverter_final, nivel_final)
            if not np.isfinite(previsao_candidata).all() or not np.isfinite(intervalo_candidato).all():
                raise ValueError("Previsão ou intervalo não finito no reajuste final.")
            modelo, previsao, intervalo = modelo_candidato, previsao_candidata, intervalo_candidato
            exog_nome, historico, futuro = exog_candidato, historico_candidato, futuro_candidato
            tipo_modelo = nome_candidato
            break
        except Exception as erro:
            print(f"AVISO | {identificacao} | reajuste final de {nome_candidato} falhou: {erro}")
    if modelo is None:
        raise ValueError("Nenhum SARIMA/SARIMAX elegível convergiu no reajuste final.")
    try:
        diag = diagnosticos_residuos(modelo, y.index)
    except Exception as erro:
        diag = None
        print(f"AVISO | {identificacao} | diagnóstico final falhou: {erro}")
    registro = {
        "tipo_modelo": tipo_modelo,
        "modelo_selecionado_cv": vencedor_cv,
        "modelo_avaliado_teste": candidato_avaliado_teste or "não avaliado",
        "modelo_final": tipo_modelo,
        "teste_avaliado": candidato_avaliado_teste is not None,
        "teste_corresponde_modelo_final": candidato_avaliado_teste == tipo_modelo,
        "modelo_reserva_usado": tipo_modelo.startswith("SARIMA_reserva"),
        "outliers_treino": ";".join(coluna for coluna in historico.columns if coluna.startswith("out_")) if historico is not None else "",
        "exog_utilizada": exog_nome or "",
        "transformacao": nome_transformacao_final,
        "transformacao_treino": nome_transformacao_treino,
        "order": str(modelo.order),
        "seasonal_order": str(modelo.seasonal_order),
        "aic": modelo.aic(),
        "bic": modelo.bic(),
        "n_obs": len(y), "tamanho_desenvolvimento": fim_desenvolvimento,
        "n_origens_cv": numero_origens_cv, "horizonte": horizonte, "tamanho_teste": tamanho_teste,
        "tamanho_treino_cv_inicial": origens_cv[0],
        "tamanho_treino_cv_final": origens_cv[-1],
        "origens_cv": ";".join(y.index[origem].strftime("%Y-%m") for origem in origens_cv),
        "rmse_cv": metricas_cv[vencedor_cv]["rmse"],
        "mae_cv": metricas_cv[vencedor_cv]["mae"],
        "mape_cv": metricas_cv[vencedor_cv]["mape"],
        "inicio_teste": y_teste.index.min(), "fim_teste": y_teste.index.max(),
        "inicio_serie": y.index.min(), "fim_serie": y.index.max(),
        "intervalo_95_disponivel": True,
        "metodo_intervalo": "ARIMA_condicional",
        "avaliacao_teste": "teste_final_nao_usado_para_selecao; condicional_ao_PIB_informado",
        "convergiu": bool(modelo.arima_res_.mle_retvals.get("converged", False)),
        "otimizador_final": modelo.method,
        "iteracoes_final": modelo.arima_res_.mle_retvals.get("iterations", np.nan),
        **{f"{metrica}_teste": valor for metrica, valor in metricas_teste.items()},
        **{f"rmse_cv_{nome}": valores["rmse"] for nome, valores in metricas_cv.items()},
        **{nome: diag[nome] if diag is not None else np.nan for nome in ("lb_p", "jb_p", "arch_p")},
    }
    tabela = pd.DataFrame({
        "data_previsao": datas_futuras, "ano": datas_futuras.year, "mes_num": datas_futuras.month,
        "mes": [MESES_PT_BR[mes - 1] for mes in datas_futuras.month], "valor_previsto": previsao,
        "limite_inferior_95": intervalo[:, 0], "limite_superior_95": intervalo[:, 1],
        "exog_nome": exog_nome or "",
        "exog_valor": futuro[exog_nome].to_numpy() if exog_nome in COLS_EXOG else np.nan,
    })
    pib_futuro = df_pib.set_index("data").reindex(datas_futuras)
    tabela["pib_bilhoes"] = pib_futuro["pib"].to_numpy()
    tabela["pib_situacao"] = pib_futuro["situacao"].to_numpy()
    tabela["pib_fonte"] = pib_futuro["fonte"].to_numpy()
    return tabela, registro, diag


def salvar_graficos(y, tabela, registro, diag, tributo, uf, pasta_base=None):
    pasta = (pasta_base or PASTA_GRAFICOS) / tributo.lower()
    pasta.mkdir(parents=True, exist_ok=True)
    rotulo_modelo = registro["tipo_modelo"].replace("SARIMA_reserva", "SARIMA de reserva")
    fig, eixo = plt.subplots(figsize=(10, 5.5))
    eixo.plot(y.index, y.values, marker="o", label="Observado")
    eixo.plot([y.index[-1], *tabela["data_previsao"]], [y.iloc[-1], *tabela["valor_previsto"]],
              linestyle="--", marker="o", label=f"Previsão ({rotulo_modelo})")
    if registro["intervalo_95_disponivel"]:
        eixo.fill_between(tabela["data_previsao"], tabela["limite_inferior_95"],
                          tabela["limite_superior_95"], alpha=0.2, label="Intervalo 95%")
    eixo.axvline(y.index[-1], linestyle=":", alpha=0.7)
    eixo.set(title=f"{tributo} - {uf} | histórico e previsão de {len(tabela)} meses",
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
        eixos[1].set(xlabel="Data", ylabel="Resíduos ao quadrado")
        fig.tight_layout()
        fig.savefig(pasta / f"residuos_arch_{tributo.lower()}_{uf}.png", dpi=200, bbox_inches="tight")
        plt.close(fig)


def salvar_grafico_sem_previsao(tributo, uf, motivo, pasta_base):
    pasta = pasta_base / tributo.lower()
    pasta.mkdir(parents=True, exist_ok=True)
    fig, eixo = plt.subplots(figsize=(10, 5.5))
    eixo.set_title(f"{tributo} - {uf} | previsão indisponível nesta execução")
    eixo.text(0.5, 0.5, motivo, ha="center", va="center", wrap=True, transform=eixo.transAxes)
    eixo.set_axis_off()
    fig.tight_layout()
    fig.savefig(pasta / f"sem_previsao_{tributo.lower()}_{uf}.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def main():
    warnings.filterwarnings("ignore")
    inicio_total = perf_counter()
    pasta_graficos_execucao = PASTA_GRAFICOS / f"execucao_{datetime.now():%Y%m%d_%H%M%S_%f}"
    receitas = preparar_receitas(pd.read_excel(ARQUIVO_RECEITAS))
    df_pib = ler_pib(ARQUIVO_PIB)
    print(f"PIB em bilhões de reais | histórico BCB até {df_pib.loc[df_pib['situacao'] == 'R', 'data'].max():%Y-%m}")
    print(
        f"Validação rolling-origin: até {MAX_ORIGENS_ROLLING} origens, horizonte de {HORIZONTE_PREVISAO or 'até dezembro'} meses; "
        "o bloco final de mesmo horizonte não participa da seleção. Avaliação condicional ao PIB informado."
    )
    print("Os intervalos são condicionais ao SARIMA ajustado e à projeção de PIB informada.")
    previsoes, modelos, ocorrencias = [], [], []
    for tributo in TRIBUTOS:
        for uf in UFS:
            identificacao = f"{tributo} | {uf}"
            base = receitas[(receitas["tributo"] == tributo) & (receitas["uf_consulta"] == uf)]
            try:
                if base.empty:
                    raise ValueError("Sem dados para este tributo/UF.")
                serie = base.set_index("data")["valor"].sort_index().asfreq("MS")
                print(f"INÍCIO | {identificacao} | {len(modelos) + 1}/{len(TRIBUTOS) * len(UFS)}", flush=True)
                inicio_ajuste = perf_counter()
                with threadpool_limits(limits=1):
                    tabela, registro, diag = ajustar_serie(serie, df_pib, identificacao)
                registro["tempo_ajuste_segundos"] = perf_counter() - inicio_ajuste
            except Exception as erro:
                print(f"Aviso: {identificacao} | {erro}")
                ocorrencias.append({"tributo": tributo, "uf": uf, "status": "sem_previsao", "motivo": str(erro)})
                try:
                    salvar_grafico_sem_previsao(tributo, uf, str(erro), pasta_graficos_execucao)
                except Exception as erro_grafico:
                    print(f"AVISO | {identificacao} | erro ao salvar gráfico de falha: {erro_grafico}")
                continue
            tabela.insert(0, "uf", uf)
            tabela.insert(0, "tributo", tributo)
            previsoes.append(tabela)
            alertas = avaliar_alertas_modelo(registro, diag)
            registro["status_diagnostico"] = "com_alertas" if alertas else "sem_alertas_nos_testes"
            registro["avisos_modelo"] = " | ".join(alertas)
            modelos.append({"tributo": tributo, "uf": uf, **registro})
            ocorrencias.append({
                "tributo": tributo, "uf": uf,
                "status": "alerta_modelo" if alertas else "ok",
                "motivo": registro["avisos_modelo"],
            })
            try:
                salvar_graficos(serie, tabela, registro, diag, tributo, uf, pasta_graficos_execucao)
            except Exception as erro:
                print(f"AVISO | {identificacao} | erro ao salvar gráficos: {erro}")
                ocorrencias.append({"tributo": tributo, "uf": uf, "status": "erro_grafico", "motivo": str(erro)})
            status = "AVISO" if alertas else "OK"
            print(
                f"{status} | {identificacao} | CV RMSE={registro['rmse_cv']:.2f} "
                f"({registro['n_origens_cv']} origens) | teste final RMSE={registro['rmse_teste']:.2f} "
                f"| seleção={registro['modelo_selecionado_cv']} | final={registro['tipo_modelo']} "
                f"| {registro['tempo_ajuste_segundos']:.1f}s",
                flush=True,
            )
            for alerta in alertas:
                print(f"AVISO | {identificacao} | {registro['tipo_modelo']} | {alerta}")
    PASTA_PLANILHAS.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(SAIDA_EXCEL, engine="openpyxl") as writer:
        (pd.concat(previsoes, ignore_index=True) if previsoes else pd.DataFrame()).to_excel(
            writer, sheet_name="previsoes", index=False)
        pd.DataFrame(modelos).to_excel(writer, sheet_name="modelos", index=False)
        df_pib.to_excel(writer, sheet_name="pib_tratado", index=False)
        pd.DataFrame(ocorrencias).to_excel(writer, sheet_name="ocorrencias", index=False)
    print(f"\nExcel: {SAIDA_EXCEL}")
    print(f"Gráficos desta execução: {pasta_graficos_execucao}")
    print(f"Séries previstas: {len(modelos)} de {len(TRIBUTOS) * len(UFS)}")
    print(f"Modelos ARIMA/SARIMAX convergentes: {sum(item['convergiu'] is True for item in modelos)}")
    print(f"Séries com SARIMA de reserva: {sum(item['modelo_reserva_usado'] for item in modelos)}")
    print(f"Tempo total: {(perf_counter() - inicio_total) / 60:.2f} minutos")
    com_alertas = [item for item in ocorrencias if item["status"] == "alerta_modelo"]
    print(f"Séries com alertas no modelo final: {len(com_alertas)}")
    for item in com_alertas:
        print(f"AVISO FINAL | {item['tributo']} | {item['uf']} | {item['motivo']}")


if __name__ == "__main__":
    main()