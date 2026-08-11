import warnings
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
from pmdarima import auto_arima

warnings.filterwarnings("ignore")

# =========================
# ARQUIVOS
# =========================
arquivo = Path("rreo_anexo3_icms_ipva_itcd_2022_2025.xlsx")
saida_excel = Path("previsoes_icms_3m_estados_2022_2025.xlsx")
pasta_graficos = Path("graficos_previsao_icms_estados_2022_2025")
pasta_graficos.mkdir(exist_ok=True)

# =========================
# MAPEAMENTO DAS COLUNAS MENSAIS DO PERÍODO 6
# =========================
mapa_colunas = {
    "<MR-11>": ("Jan", 1),
    "<MR-10>": ("Fev", 2),
    "<MR-9>": ("Mar", 3),
    "<MR-8>": ("Abr", 4),
    "<MR-7>": ("Mai", 5),
    "<MR-6>": ("Jun", 6),
    "<MR-5>": ("Jul", 7),
    "<MR-4>": ("Ago", 8),
    "<MR-3>": ("Set", 9),
    "<MR-2>": ("Out", 10),
    "<MR-1>": ("Nov", 11),
    "<MR>": ("Dez", 12),
}

# =========================
# LEITURA E FILTRO
# =========================
df = pd.read_excel(arquivo)

df_icms = df[
    (df["conta"].astype(str).str.upper() == "ICMS") &
    (df["periodo_consulta"] == 6) &
    (df["coluna"].isin(mapa_colunas.keys()))
].copy()

df_icms["mes"] = df_icms["coluna"].map(lambda x: mapa_colunas[x][0])
df_icms["mes_num"] = df_icms["coluna"].map(lambda x: mapa_colunas[x][1])
df_icms["valor"] = pd.to_numeric(df_icms["valor"], errors="coerce")
df_icms["ano_consulta"] = pd.to_numeric(df_icms["ano_consulta"], errors="coerce").astype("Int64")

# data mensal real
df_icms["data"] = pd.to_datetime(
    dict(year=df_icms["ano_consulta"], month=df_icms["mes_num"], day=1),
    errors="coerce"
)

# Se houver duplicidade por UF/data, soma
df_icms = (
    df_icms.groupby(["uf_consulta", "data"], as_index=False)["valor"]
    .sum()
    .sort_values(["uf_consulta", "data"])
)

# =========================
# AJUSTE E PREVISÃO
# =========================
resultados_previsao = []
resultados_modelo = []

ufs = sorted(df_icms["uf_consulta"].dropna().unique())

for uf in ufs:
    base = df_icms[df_icms["uf_consulta"] == uf].copy()
    base = base.sort_values("data").set_index("data")

    # força frequência mensal
    y = base["valor"].asfreq("MS")

    # checagem de lacunas
    if y.isna().any():
        print(f"Aviso: {uf} tem meses faltantes. Pulando.")
        continue

    try:
        # tenta SARIMA sazonal
        modelo = auto_arima(
            y,
            seasonal=True,
            m=12,
            stepwise=True,
            suppress_warnings=True,
            error_action="ignore",
            trace=False,
            information_criterion="aicc",
            max_p=3,
            max_q=3,
            max_d=2,
            max_P=2,
            max_Q=2,
            max_D=1,
            stationary=False
        )
        tipo_modelo = "SARIMA"
    except Exception:
        # fallback não sazonal
        modelo = auto_arima(
            y,
            seasonal=False,
            stepwise=True,
            suppress_warnings=True,
            error_action="ignore",
            trace=False,
            information_criterion="aicc",
            max_p=4,
            max_q=4,
            max_d=2,
            stationary=False
        )
        tipo_modelo = "ARIMA_fallback"

    previsao, conf_int = modelo.predict(n_periods=3, return_conf_int=True, alpha=0.05)

    ultima_data = y.index[-1]
    datas_futuras = pd.date_range(ultima_data + pd.offsets.MonthBegin(1), periods=3, freq="MS")

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
        })

    resultados_modelo.append({
        "uf": uf,
        "tipo_modelo": tipo_modelo,
        "order": str(modelo.order),
        "seasonal_order": str(modelo.seasonal_order),
        "aic": getattr(modelo, "aic", lambda: None)(),
        "bic": getattr(modelo, "bic", lambda: None)(),
        "n_obs": len(y),
        "inicio_serie": y.index.min(),
        "fim_serie": y.index.max(),
    })

    # gráfico
    plt.figure(figsize=(10, 5.5))

    # observado
    plt.plot(y.index, y.values, marker="o", label="Observado")

    # linha prevista emendada ao último observado
    x_previsao = [y.index[-1]] + list(datas_futuras)
    y_previsao = [y.iloc[-1]] + list(previsao)

    plt.plot(
        x_previsao,
        y_previsao,
        linestyle="--",
        label="Previsão"
    )

    # marcadores só nos meses futuros
    plt.plot(
        datas_futuras,
        previsao,
        linestyle="None",
        marker="o"
    )

    # intervalo de confiança
    plt.fill_between(datas_futuras, conf_int[:, 0], conf_int[:, 1], alpha=0.2)

    # linha vertical separando observado e previsto
    plt.axvline(y.index[-1], linestyle=":", alpha=0.7)

    plt.title(f"ICMS - {uf} | histórico + previsão 3 meses")
    plt.xlabel("Data")
    plt.ylabel("Valor")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(pasta_graficos / f"previsao_icms_{uf}.png", dpi=200, bbox_inches="tight")
    plt.close()

    print(f"OK | {uf} | {tipo_modelo} | order={modelo.order} | seasonal={modelo.seasonal_order}")

# =========================
# SALVAR RESULTADOS
# =========================
df_prev = pd.DataFrame(resultados_previsao)
df_mod = pd.DataFrame(resultados_modelo)

with pd.ExcelWriter(saida_excel, engine="openpyxl") as writer:
    df_prev.to_excel(writer, sheet_name="previsoes", index=False)
    df_mod.to_excel(writer, sheet_name="modelos", index=False)

print("\nArquivos gerados:")
print(f"- Excel: {saida_excel}")
print(f"- Gráficos PNG: {pasta_graficos.resolve()}")