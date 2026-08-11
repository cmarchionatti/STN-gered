import warnings
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
from pmdarima import auto_arima

warnings.filterwarnings("ignore")

# =========================
# ARQUIVOS
# =========================
arquivo_icms = Path(r"C:\Users\march\Desktop\VSCode\STN\Planilhas\rreo_anexo3_icms_ipva_itcd_2022_2025.xlsx")
saida_excel = Path("previsoes_icms_3m_estados.xlsx")
pasta_graficos = Path("graficos_previsao_icms_estados")
pasta_graficos.mkdir(exist_ok=True)

# =========================
# MAPEAMENTO DOS 12 MESES
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
# LEITURA E PREPARAÇÃO
# =========================
df = pd.read_excel(arquivo_icms)

df_icms = df[
    (df["conta"].astype(str).str.upper() == "ICMS") &
    (df["periodo_consulta"] == 6) &
    (df["coluna"].isin(mapa_colunas.keys()))
].copy()

df_icms["mes"] = df_icms["coluna"].map(lambda x: mapa_colunas[x][0])
df_icms["ordem_mes"] = df_icms["coluna"].map(lambda x: mapa_colunas[x][1])
df_icms["valor"] = pd.to_numeric(df_icms["valor"], errors="coerce")

# Se por algum motivo houver duplicidade, soma por UF/mês
df_icms = (
    df_icms.groupby(["uf_consulta", "mes", "ordem_mes"], as_index=False)["valor"]
    .sum()
    .sort_values(["uf_consulta", "ordem_mes"])
)

# =========================
# AJUSTE E PREVISÃO
# =========================
resultados_previsao = []
resultados_modelo = []

ufs = sorted(df_icms["uf_consulta"].dropna().unique())

for uf in ufs:
    base = df_icms[df_icms["uf_consulta"] == uf].sort_values("ordem_mes").copy()

    if len(base) != 12:
        print(f"Aviso: {uf} tem {len(base)} observações em vez de 12. Pulando.")
        continue

    # índice mensal fixo: jan/2024 a dez/2024
    y = pd.Series(
        base["valor"].values,
        index=pd.date_range("2024-01-01", periods=12, freq="MS"),
        name="ICMS"
    )

    try:
        # tentativa sazonal
        modelo = auto_arima(
            y,
            seasonal=True,
            m=12,
            stepwise=True,
            suppress_warnings=True,
            error_action="ignore",
            trace=False,
            information_criterion="aicc",
            max_p=2,
            max_q=2,
            max_d=2,
            max_P=1,
            max_Q=1,
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
            max_p=3,
            max_q=3,
            max_d=2,
            stationary=False
        )
        tipo_modelo = "ARIMA_fallback"

    previsao, conf_int = modelo.predict(n_periods=12, return_conf_int=True, alpha=0.05)

    datas_futuras = pd.date_range("2025-01-01", periods=3, freq="MS")

    # guardar previsão
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

    # guardar modelo escolhido
    resultados_modelo.append({
        "uf": uf,
        "tipo_modelo": tipo_modelo,
        "order": str(modelo.order),
        "seasonal_order": str(modelo.seasonal_order),
        "aic": getattr(modelo, "aic", lambda: None)(),
        "bic": getattr(modelo, "bic", lambda: None)(),
    })

    # gráfico histórico + previsão
    plt.figure(figsize=(10, 5.5))

    plt.plot(y.index, y.values, marker="o", label="Observado")

    x_previsao = [y.index[-1]] + list(datas_futuras)
    y_previsao = [y.iloc[-1]] + list(previsao)

    linha_prev, = plt.plot(
        x_previsao,
        y_previsao,
        marker="o",
        linestyle="--",
        color="tab:orange",
        label="Previsão"
    )

# não desenha marcador no primeiro ponto (dezembro observado)
    linha_prev.set_markevery(range(1, len(x_previsao)))

# intervalo de confiança só para o horizonte futuro
    plt.fill_between(datas_futuras, conf_int[:, 0], conf_int[:, 1], alpha=0.2)

# separador visual
    plt.axvline(y.index[-1], linestyle=":", alpha=0.7)

    plt.title(f"ICMS - {uf} | histórico 2024 + previsão 3 meses")
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