# =============================================================================
#
#   HIATO FISCAL DAS UNIDADES DA FEDERAÇÃO DO BRASIL
#   NOTEBOOK 4 — APRESENTAÇÃO: Gráficos, Tabelas e Resultados
#
#   Inputs (Delta Tables Gold):
#     - gold_hiato_fiscal
#     - gold_capacidade_fiscal
#     - gold_necessidade_gasto
#     - gold_receita_media_impostos
#
#   Outputs:
#     - Gráficos inline (matplotlib/seaborn)
#     - Tabelas formatadas (pandas Styler)
#     - Arquivo Excel consolidado no Lakehouse
#
# =============================================================================

# =============================================================================
# CÉLULA 1 — Configurações
# =============================================================================

ANO_FIM_ANALISE   = 2022
ANO_INICIO_ANALISE = 2019

# No Fabric, o Lakehouse padrão é montado em /lakehouse/default/.
# Files/ guarda arquivos brutos; Tables/ guarda Delta Tables.
OUTPUT_DIR = "/lakehouse/default/Files/resultados"

print(f"Ano de referência: {ANO_FIM_ANALISE}")


# =============================================================================
# CÉLULA 2 — Imports
# =============================================================================

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.patches as mpatches
import seaborn as sns
import io

from pyspark.sql import SparkSession
from IPython.display import display

spark = SparkSession.builder.getOrCreate()

# Configuração global de estilo
plt.rcParams.update({
    "figure.dpi":      150,
    "figure.facecolor": "white",
    "font.family":     "sans-serif",
    "font.size":       10,
    "axes.spines.top":    False,
    "axes.spines.right":  False,
})
sns.set_palette("muted")

# Paleta de regiões brasileiras
CORES_REGIAO = {
    "Norte":          "#2196F3",
    "Nordeste":       "#FF9800",
    "Centro-Oeste":   "#9C27B0",
    "Sudeste":        "#F44336",
    "Sul":            "#4CAF50",
}

REGIAO_UF = {
    "RO": "Norte", "AC": "Norte", "AM": "Norte", "RR": "Norte",
    "PA": "Norte", "AP": "Norte", "TO": "Norte",
    "MA": "Nordeste", "PI": "Nordeste", "CE": "Nordeste", "RN": "Nordeste",
    "PB": "Nordeste", "PE": "Nordeste", "AL": "Nordeste", "SE": "Nordeste",
    "BA": "Nordeste",
    "MG": "Sudeste", "ES": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "SC": "Sul", "RS": "Sul",
    "MS": "Centro-Oeste", "MT": "Centro-Oeste",
    "GO": "Centro-Oeste", "DF": "Centro-Oeste",
}

def formatar_bilhoes(x, pos=None):
    """Formata eixo em R$ bilhões."""
    return f"R$ {x/1e9:.1f} bi"

def formatar_mil_per_capita(x, pos=None):
    return f"R$ {x/1e3:.1f} mil"

import os

GRAFICOS_DIR = os.path.join(OUTPUT_DIR, "graficos")

def salvar_grafico(nome):
    """Salva a figura atual como PNG no Lakehouse (ou no cwd como fallback)."""
    try:
        os.makedirs(GRAFICOS_DIR, exist_ok=True)
        caminho = os.path.join(GRAFICOS_DIR, f"{nome}.png")
    except (FileNotFoundError, PermissionError, OSError):
        caminho = f"{nome}.png"
    plt.savefig(caminho, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"  PNG: {caminho}")

print("Imports e configurações concluídos.")


# =============================================================================
# CÉLULA 3 — Carregamento dos dados Gold
# =============================================================================

hiato    = spark.table("gold_hiato_fiscal").toPandas()
cap      = spark.table("gold_capacidade_fiscal").toPandas()
nec      = spark.table("gold_necessidade_gasto").toPandas()
rec_med  = spark.table("gold_receita_media_impostos").toPandas()

# Normaliza tipos
for df in [hiato, cap, nec]:
    for c in df.select_dtypes(include="object").columns:
        try:
            df[c] = pd.to_numeric(df[c])
        except (ValueError, TypeError):
            pass

hiato["Regiao"] = hiato["UF"].map(REGIAO_UF).fillna("Outro")
nec["Regiao"]   = nec["UF"].map(REGIAO_UF).fillna("Outro")

# Ordena por hiato fiscal
hiato_sorted = hiato.sort_values("hiato_fiscal", ascending=False).reset_index(drop=True)

print("Dados carregados:")
print(hiato_sorted[["UF", "capacidade_fiscal", "gastos_pooled_total", "hiato_fiscal"]].to_string(index=False))


# =============================================================================
# CÉLULA 4 — GRÁFICO 1: Hiato Fiscal por UF (barras horizontais)
# =============================================================================

fig, ax = plt.subplots(figsize=(12, 9))

cores = [CORES_REGIAO.get(REGIAO_UF.get(uf, ""), "#78909C") for uf in hiato_sorted["UF"]]
barras = ax.barh(
    hiato_sorted["UF"],
    hiato_sorted["hiato_fiscal"] / 1e9,
    color=cores,
    edgecolor="white",
    linewidth=0.5
)

ax.axvline(0, color="black", linewidth=0.8, linestyle="--")
ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"R$ {x:.0f} bi"))
ax.set_xlabel("Hiato Fiscal (R$ bilhões, preços dez/" + str(ANO_FIM_ANALISE) + ")")
ax.set_title(
    f"Hiato Fiscal das UFs — Capacidade Fiscal menos Necessidade de Gasto\n"
    f"(Ano de referência: {ANO_FIM_ANALISE})",
    fontweight="bold", pad=12
)
ax.set_ylabel("")

# Legenda de regiões
patches = [mpatches.Patch(color=v, label=k) for k, v in CORES_REGIAO.items()]
ax.legend(handles=patches, loc="lower right", framealpha=0.8, fontsize=8)

plt.tight_layout()
salvar_grafico("01_hiato_fiscal_por_uf")
plt.show()


# =============================================================================
# CÉLULA 5 — GRÁFICO 2: Capacidade Fiscal vs. Necessidade de Gasto (scatter)
# =============================================================================

fig, ax = plt.subplots(figsize=(10, 8))

cores_scatter = [CORES_REGIAO.get(REGIAO_UF.get(uf, ""), "#78909C") for uf in hiato["UF"]]

sc = ax.scatter(
    hiato["cap_fiscal_per_capita"] / 1e3,
    hiato["nec_gasto_per_capita"]  / 1e3,
    c=cores_scatter, s=80, alpha=0.85, edgecolors="white", linewidth=0.8
)

# Linha de 45 graus (hiato = 0)
lim_max = max(hiato["cap_fiscal_per_capita"].max(), hiato["nec_gasto_per_capita"].max()) / 1e3 * 1.05
ax.plot([0, lim_max], [0, lim_max], "k--", linewidth=0.8, alpha=0.5, label="Hiato = 0")

# Rótulos das UFs
for _, row in hiato.iterrows():
    ax.annotate(
        row["UF"],
        xy=(row["cap_fiscal_per_capita"] / 1e3, row["nec_gasto_per_capita"] / 1e3),
        xytext=(3, 3), textcoords="offset points", fontsize=7, alpha=0.8
    )

ax.set_xlabel(f"Capacidade Fiscal per capita (R$ mil, dez/{ANO_FIM_ANALISE})")
ax.set_ylabel(f"Necessidade de Gasto per capita (R$ mil, dez/{ANO_FIM_ANALISE})")
ax.set_title(
    "Capacidade Fiscal vs. Necessidade de Gasto por Habitante",
    fontweight="bold", pad=12
)
patches = [mpatches.Patch(color=v, label=k) for k, v in CORES_REGIAO.items()]
ax.legend(handles=patches + [plt.Line2D([0], [0], color="k", linestyle="--", label="Hiato = 0")],
          fontsize=8, framealpha=0.8)

plt.tight_layout()
salvar_grafico("02_capacidade_vs_necessidade")
plt.show()


# =============================================================================
# CÉLULA 6 — GRÁFICO 3: Decomposição da Necessidade de Gasto (barras empilhadas)
# =============================================================================

funcoes_gasto = {
    "gastos_pooled_adm":          "Administração",
    "gastos_pooled_jud":          "Judiciário",
    "gastos_pooled_justica":      "Essencial à Justiça",
    "estimado_legislativo":       "Legislativo",
    "gastos_pooled_outros":       "Outras Funções",
    "gastos_pooled_educacao":     "Educação",
    "gastos_pooled_saude":        "Saúde",
    "gastos_pooled_seguranca":    "Segurança Pública",
    "gastos_pooled_transporte":   "Transporte",
    "gastos_pooled_previdencia":  "Previdência",
}

nec_plot = nec.set_index("UF")
nec_plot = nec_plot[[c for c in funcoes_gasto.keys() if c in nec_plot.columns]]
nec_plot = nec_plot.rename(columns=funcoes_gasto)
nec_plot = nec_plot.div(1e9)  # converte para bilhões

# Ordena pelas UFs do gráfico 1
uf_order = hiato_sorted["UF"].tolist()
nec_plot = nec_plot.reindex([u for u in uf_order if u in nec_plot.index])

palette = sns.color_palette("tab10", n_colors=len(nec_plot.columns))

fig, ax = plt.subplots(figsize=(14, 8))
nec_plot.plot(kind="barh", stacked=True, ax=ax, color=palette,
              edgecolor="white", linewidth=0.3)

ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"R$ {x:.0f} bi"))
ax.set_xlabel(f"Necessidade de Gasto (R$ bilhões, dez/{ANO_FIM_ANALISE})")
ax.set_title(
    f"Decomposição da Necessidade de Gasto por Função\n(Ano de referência: {ANO_FIM_ANALISE})",
    fontweight="bold", pad=12
)
ax.legend(loc="lower right", fontsize=7, framealpha=0.8, ncol=2)
plt.tight_layout()
salvar_grafico("03_decomposicao_necessidade_gasto")
plt.show()


# =============================================================================
# CÉLULA 7 — GRÁFICO 4: Decomposição da Capacidade Fiscal (barras empilhadas)
# =============================================================================

cap_plot = cap.copy()
cap_colunas = {
    "Fitted_Pooled_icms":   "ICMS (pot.)",
    "Fitted_Pooled_ipva":   "IPVA (pot.)",
    "Fitted_Pooled_itcd":   "ITCD (pot.)",
    "Fitted_Pooled_prev":   "Previdência (pot.)",
    "Fitted_Pooled_outros": "Outros (pot.)",
    "Transferencias":        "Transferências",
    "total":                 "Rec. Excl. DF",
    "IR_Servidores":         "IRRF Serv.",
}

cap_plot = cap_plot.set_index("SG_ENTE")
cap_plot = cap_plot[[c for c in cap_colunas if c in cap_plot.columns]].rename(columns=cap_colunas)
cap_plot = cap_plot.div(1e9)
cap_plot = cap_plot.reindex([u for u in uf_order if u in cap_plot.index])

palette_cap = sns.color_palette("Set2", n_colors=len(cap_plot.columns))

fig, ax = plt.subplots(figsize=(14, 8))
cap_plot.plot(kind="barh", stacked=True, ax=ax, color=palette_cap,
              edgecolor="white", linewidth=0.3)

ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"R$ {x:.0f} bi"))
ax.set_xlabel(f"Capacidade Fiscal (R$ bilhões, dez/{ANO_FIM_ANALISE})")
ax.set_title(
    f"Decomposição da Capacidade Fiscal por Componente\n(Ano de referência: {ANO_FIM_ANALISE})",
    fontweight="bold", pad=12
)
ax.legend(loc="lower right", fontsize=7, framealpha=0.8, ncol=2)
plt.tight_layout()
salvar_grafico("04_decomposicao_capacidade_fiscal")
plt.show()


# =============================================================================
# CÉLULA 8 — GRÁFICO 5: Mapa de Calor — Hiato Fiscal per capita por Região
# =============================================================================

hiato_regiao = (
    hiato.groupby("Regiao")
    .agg(
        cap_media    = ("cap_fiscal_per_capita",  "mean"),
        nec_media    = ("nec_gasto_per_capita",   "mean"),
        hiato_medio  = ("hiato_per_capita",       "mean"),
        n_ufs        = ("UF", "count")
    )
    .reset_index()
    .sort_values("hiato_medio", ascending=False)
)

fig, ax = plt.subplots(figsize=(9, 4))
pivot = hiato_regiao.set_index("Regiao")[["cap_media", "nec_media", "hiato_medio"]] / 1e3
pivot.columns = ["Cap. Fiscal\n(R$ mil/hab)", "Nec. Gasto\n(R$ mil/hab)", "Hiato\n(R$ mil/hab)"]

sns.heatmap(
    pivot, annot=False, cmap="RdYlGn", center=0,
    linewidths=0.5, ax=ax, cbar=True
)

# Anotação manual: garante que TODAS as células mostrem o valor.
for i in range(pivot.shape[0]):
    for j in range(pivot.shape[1]):
        val = pivot.iloc[i, j]
        texto = f"{val:.1f}" if pd.notna(val) else "—"
        ax.text(
            j + 0.5, i + 0.5, texto,
            ha="center", va="center",
            color="#212121", fontsize=11, fontweight="bold"
        )

ax.set_title(
    f"Médias Regionais — Hiato Fiscal per capita (R$ mil, dez/{ANO_FIM_ANALISE})",
    fontweight="bold", pad=10
)
ax.set_ylabel("")
plt.tight_layout()
salvar_grafico("05_heatmap_regional")
plt.show()


# =============================================================================
# CÉLULA 9 — TABELA 1: Resumo por UF (exibição formatada)
# =============================================================================

tabela_resumo = hiato[[
    "UF", "Regiao", "populacao_total",
    "capacidade_fiscal", "gastos_pooled_total",
    "hiato_fiscal", "hiato_per_capita"
]].copy()

tabela_resumo = tabela_resumo.rename(columns={
    "UF":                  "UF",
    "Regiao":              "Região",
    "populacao_total":     "População",
    "capacidade_fiscal":   "Cap. Fiscal (R$ bi)",
    "gastos_pooled_total": "Nec. Gasto (R$ bi)",
    "hiato_fiscal":        "Hiato (R$ bi)",
    "hiato_per_capita":    "Hiato p/ hab (R$)",
})

tabela_resumo["Cap. Fiscal (R$ bi)"] = (tabela_resumo["Cap. Fiscal (R$ bi)"] / 1e9).round(2)
tabela_resumo["Nec. Gasto (R$ bi)"]  = (tabela_resumo["Nec. Gasto (R$ bi)"]  / 1e9).round(2)
tabela_resumo["Hiato (R$ bi)"]       = (tabela_resumo["Hiato (R$ bi)"]        / 1e9).round(2)
tabela_resumo["Hiato p/ hab (R$)"]   = tabela_resumo["Hiato p/ hab (R$)"].round(0).astype(int)
tabela_resumo["População"]           = tabela_resumo["População"].astype(int)

tabela_resumo = tabela_resumo.sort_values("Hiato (R$ bi)", ascending=False).reset_index(drop=True)

def colorir_hiato(val):
    if isinstance(val, (int, float)):
        if val > 0:   return "background-color: #C8E6C9; color: #1B5E20"
        elif val < 0: return "background-color: #FFCDD2; color: #B71C1C"
    return ""

styled = (
    tabela_resumo.style
    .map(colorir_hiato, subset=["Hiato (R$ bi)", "Hiato p/ hab (R$)"])
    .format({
        "Cap. Fiscal (R$ bi)": "{:.2f}",
        "Nec. Gasto (R$ bi)":  "{:.2f}",
        "Hiato (R$ bi)":       "{:.2f}",
        "Hiato p/ hab (R$)":   "{:,.0f}",
        "População":           "{:,.0f}",
    })
    .set_caption(f"Tabela 1 — Hiato Fiscal das UFs (preços dez/{ANO_FIM_ANALISE})")
    .set_table_styles([
        {"selector": "caption", "props": [("font-size", "13px"), ("font-weight", "bold"), ("text-align", "left")]},
        {"selector": "th", "props": [("background-color", "#37474F"), ("color", "white"), ("font-size", "10px")]},
    ])
)
display(styled)


# =============================================================================
# CÉLULA 10 — TABELA 2: Decomposição da Necessidade de Gasto por UF
# =============================================================================

colunas_gasto = {
    "UF":                       "UF",
    "gastos_pooled_adm":        "Admin.",
    "gastos_pooled_jud":        "Judic.",
    "gastos_pooled_justica":    "Ess.Just.",
    "estimado_legislativo":     "Legis.",
    "gastos_pooled_educacao":   "Educ.",
    "gastos_pooled_saude":      "Saúde",
    "gastos_pooled_seguranca":  "Segur.",
    "gastos_pooled_transporte": "Transp.",
    "gastos_pooled_previdencia":"Previd.",
    "gastos_pooled_outros":     "Outros",
    "gastos_pooled_total":      "TOTAL",
}

tab_nec = nec[[c for c in colunas_gasto if c in nec.columns]].rename(columns=colunas_gasto)
for c in tab_nec.columns[1:]:
    tab_nec[c] = (tab_nec[c] / 1e9).round(2)

tab_nec = tab_nec.sort_values("TOTAL", ascending=False).reset_index(drop=True)

display(
    tab_nec.style
    .format({c: "{:.2f}" for c in tab_nec.columns if c != "UF"})
    .highlight_max(subset=tab_nec.columns[1:], color="#FFF9C4", axis=0)
    .set_caption(f"Tabela 2 — Necessidade de Gasto por Função (R$ bilhões, dez/{ANO_FIM_ANALISE})")
    .set_table_styles([
        {"selector": "caption", "props": [("font-size", "13px"), ("font-weight", "bold"), ("text-align", "left")]},
        {"selector": "th", "props": [("background-color", "#37474F"), ("color", "white"), ("font-size", "9px")]},
    ])
)


# =============================================================================
# CÉLULA 11 — TABELA 3: Ranking de hiato per capita
# =============================================================================

ranking = hiato[["UF", "Regiao", "hiato_per_capita"]].copy()
ranking["hiato_per_capita"] = ranking["hiato_per_capita"].round(0).astype(int)
ranking = ranking.sort_values("hiato_per_capita", ascending=False).reset_index(drop=True)
ranking.index += 1
ranking.columns = ["UF", "Região", "Hiato per capita (R$)"]

display(
    ranking.style
    .map(colorir_hiato, subset=["Hiato per capita (R$)"])
    .format({"Hiato per capita (R$)": "{:,.0f}"})
    .set_caption(f"Tabela 3 — Ranking: Hiato Fiscal per capita (dez/{ANO_FIM_ANALISE})")
    .bar(subset=["Hiato per capita (R$)"], align="mid",
         color=["#FFCDD2", "#C8E6C9"])
    .set_table_styles([
        {"selector": "caption", "props": [("font-size", "13px"), ("font-weight", "bold")]},
        {"selector": "th", "props": [("background-color", "#37474F"), ("color", "white")]},
    ])
)


# =============================================================================
# CÉLULA 12 — GRÁFICO 6: Ranking de Hiato per capita (lollipop chart)
# =============================================================================

fig, ax = plt.subplots(figsize=(10, 9))

cores_l = [
    "#4CAF50" if h >= 0 else "#F44336"
    for h in hiato_sorted["hiato_per_capita"]
]

ax.hlines(
    y=hiato_sorted["UF"],
    xmin=0,
    xmax=hiato_sorted["hiato_per_capita"] / 1e3,
    colors=cores_l, linewidth=2, alpha=0.7
)
ax.scatter(
    hiato_sorted["hiato_per_capita"] / 1e3,
    hiato_sorted["UF"],
    color=cores_l, s=60, zorder=5
)

ax.axvline(0, color="black", linewidth=0.8, linestyle="--")
ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"R$ {x:.0f} mil"))
ax.set_xlabel(f"Hiato Fiscal per capita (R$ mil, dez/{ANO_FIM_ANALISE})")
ax.set_title(
    "Ranking: Hiato Fiscal per capita por UF\n(Verde = superávit de capacidade | Vermelho = déficit)",
    fontweight="bold", pad=12
)

# Anotações de valor
for _, row in hiato_sorted.iterrows():
    ax.annotate(
        f"R$ {row['hiato_per_capita']/1e3:.1f}k",
        xy=(row["hiato_per_capita"] / 1e3, row["UF"]),
        xytext=(5, 0), textcoords="offset points",
        fontsize=7, va="center"
    )

plt.tight_layout()
salvar_grafico("06_ranking_hiato_per_capita")
plt.show()


# =============================================================================
# CÉLULA 13 — Exportação dos resultados para Excel no Lakehouse
# =============================================================================

from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.drawing.image import Image as XLImage

# Estilos compartilhados
CABECALHO_FILL  = PatternFill("solid", fgColor="37474F")
CABECALHO_FONTE = Font(color="FFFFFF", bold=True, size=10)
TITULO_FONTE    = Font(bold=True, size=14, color="263238")
SUBTITULO_FONTE = Font(bold=True, size=11, color="455A64")
SUP_FILL        = PatternFill("solid", fgColor="C8E6C9")
DEF_FILL        = PatternFill("solid", fgColor="FFCDD2")
BORDA_FINA      = Border(
    bottom=Side(style="thin", color="CCCCCC"),
    right=Side(style="thin", color="CCCCCC")
)


def df_para_aba(wb, df, nome_aba, titulo=None, color_hiato_cols=None,
                num_format=None):
    """Escreve um DataFrame em uma aba do workbook com formatação básica.

    color_hiato_cols: lista de nomes de coluna que devem ser pintadas conforme sinal
    num_format: dict {nome_coluna: format_str openpyxl} (ex.: "#,##0.00")
    """
    ws = wb.create_sheet(title=nome_aba)
    color_hiato_cols = color_hiato_cols or []
    num_format       = num_format or {}

    linha_inicio = 1
    if titulo:
        cell = ws.cell(row=1, column=1, value=titulo)
        cell.font = TITULO_FONTE
        linha_inicio = 3

    # Cabeçalho
    for j, col in enumerate(df.columns, start=1):
        cell = ws.cell(row=linha_inicio, column=j, value=str(col))
        cell.fill = CABECALHO_FILL
        cell.font = CABECALHO_FONTE
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Dados
    col_names = list(df.columns)
    for i, row_data in enumerate(df.itertuples(index=False), start=linha_inicio + 1):
        for j, val in enumerate(row_data, start=1):
            cell = ws.cell(row=i, column=j, value=val)
            cell.border = BORDA_FINA
            cell.alignment = Alignment(horizontal="right" if isinstance(val, (int, float)) else "left")
            col_name = col_names[j - 1]
            if col_name in num_format and isinstance(val, (int, float)):
                cell.number_format = num_format[col_name]
            if col_name in color_hiato_cols and isinstance(val, (int, float)):
                if val > 0:
                    cell.fill = SUP_FILL
                elif val < 0:
                    cell.fill = DEF_FILL

    # Congela cabeçalho
    ws.freeze_panes = ws.cell(row=linha_inicio + 1, column=2)

    # Largura das colunas
    for col_cells in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col_cells), default=10)
        ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 35)


# ─── Computação de métricas para o Sumário Executivo ────────────────────────
total_superavit = int((hiato["hiato_fiscal"] > 0).sum())
total_deficit   = int((hiato["hiato_fiscal"] < 0).sum())

uf_maior_sup  = hiato.loc[hiato["hiato_fiscal"].idxmax(), "UF"]
uf_maior_def  = hiato.loc[hiato["hiato_fiscal"].idxmin(), "UF"]
maior_sup_bi  = float(hiato["hiato_fiscal"].max()) / 1e9
maior_def_bi  = float(hiato["hiato_fiscal"].min()) / 1e9

uf_maior_sup_pc = hiato.loc[hiato["hiato_per_capita"].idxmax(), "UF"]
uf_maior_def_pc = hiato.loc[hiato["hiato_per_capita"].idxmin(), "UF"]
maior_sup_pc    = float(hiato["hiato_per_capita"].max())
maior_def_pc    = float(hiato["hiato_per_capita"].min())

media_cap   = float(hiato["cap_fiscal_per_capita"].mean())
media_nec   = float(hiato["nec_gasto_per_capita"].mean())
media_hiato = float(hiato["hiato_per_capita"].mean())

total_cap_nacional   = float(hiato["capacidade_fiscal"].sum())
total_nec_nacional   = float(hiato["gastos_pooled_total"].sum())
total_hiato_nacional = float(hiato["hiato_fiscal"].sum())


# ─── Constrói workbook ──────────────────────────────────────────────────────
wb = Workbook()
wb.remove(wb.active)  # remove a aba default vazia

# ───────────────────────── Aba 1: Sumário Executivo ─────────────────────────
ws = wb.create_sheet(title="Sumário Executivo")

ws.cell(row=1, column=1,
        value=f"Hiato Fiscal das UFs — Sumário Executivo (dez/{ANO_FIM_ANALISE})").font = TITULO_FONTE
ws.cell(row=2, column=1,
        value="Capacidade Fiscal menos Necessidade de Gasto").font = SUBTITULO_FONTE

linhas_sumario = [
    ("Visão Geral", None),
    ("UFs com superávit de capacidade",  f"{total_superavit} / 27"),
    ("UFs com déficit de capacidade",    f"{total_deficit} / 27"),
    ("Hiato fiscal nacional agregado",   total_hiato_nacional),
    ("Capacidade fiscal nacional total", total_cap_nacional),
    ("Necessidade de gasto nacional",    total_nec_nacional),
    ("", ""),
    ("Extremos absolutos (R$ bilhões)", None),
    (f"Maior superávit: {uf_maior_sup}", maior_sup_bi * 1e9),
    (f"Maior déficit:  {uf_maior_def}",  maior_def_bi * 1e9),
    ("", ""),
    ("Extremos per capita (R$/hab)", None),
    (f"Maior superávit per capita: {uf_maior_sup_pc}", maior_sup_pc),
    (f"Maior déficit per capita:  {uf_maior_def_pc}",  maior_def_pc),
    ("", ""),
    ("Médias por habitante (R$)", None),
    ("Capacidade fiscal média p/ hab.", media_cap),
    ("Necessidade de gasto média p/ hab.", media_nec),
    ("Hiato fiscal médio p/ hab.", media_hiato),
]

linha = 4
for rotulo, valor in linhas_sumario:
    c_rot = ws.cell(row=linha, column=1, value=rotulo)
    if valor is None:
        # Seção
        c_rot.font = SUBTITULO_FONTE
        c_rot.fill = PatternFill("solid", fgColor="ECEFF1")
    else:
        c_rot.font = Font(size=10)
        c_val = ws.cell(row=linha, column=2, value=valor)
        c_val.alignment = Alignment(horizontal="right")
        if isinstance(valor, (int, float)):
            c_val.number_format = "#,##0.00"
            if valor > 0:
                c_val.fill = SUP_FILL
            elif valor < 0:
                c_val.fill = DEF_FILL
    linha += 1

ws.column_dimensions["A"].width = 48
ws.column_dimensions["B"].width = 22


# ─────────────────── Aba 2: Hiato Fiscal por UF (Tabela 1) ──────────────────
df_para_aba(
    wb,
    tabela_resumo.reset_index(drop=True),
    "Hiato Fiscal",
    f"Hiato Fiscal das UFs — preços dez/{ANO_FIM_ANALISE}",
    color_hiato_cols=["Hiato (R$ bi)", "Hiato p/ hab (R$)"],
    num_format={
        "População":           "#,##0",
        "Cap. Fiscal (R$ bi)": "#,##0.00",
        "Nec. Gasto (R$ bi)":  "#,##0.00",
        "Hiato (R$ bi)":       "#,##0.00",
        "Hiato p/ hab (R$)":   "#,##0",
    },
)


# ─────────── Aba 3: Necessidade de Gasto por Função (Tabela 2) ──────────────
df_para_aba(
    wb,
    tab_nec.reset_index(drop=True),
    "Nec. de Gasto",
    f"Necessidade de Gasto por Função (R$ bilhões, dez/{ANO_FIM_ANALISE})",
    num_format={c: "#,##0.00" for c in tab_nec.columns if c != "UF"},
)


# ─────────── Aba 4: Capacidade Fiscal por Componente ────────────────────────
df_cap_export = cap_plot.reset_index().rename(columns={"SG_ENTE": "UF"})
df_para_aba(
    wb,
    df_cap_export,
    "Cap. Fiscal",
    f"Capacidade Fiscal por Componente (R$ bilhões, dez/{ANO_FIM_ANALISE})",
    num_format={c: "#,##0.00" for c in df_cap_export.columns if c != "UF"},
)


# ─────────── Aba 5: Ranking de Hiato per Capita (Tabela 3) ──────────────────
df_rank = ranking.reset_index().rename(columns={"index": "Rank"})
df_para_aba(
    wb,
    df_rank,
    "Ranking per capita",
    f"Ranking — Hiato Fiscal per capita (dez/{ANO_FIM_ANALISE})",
    color_hiato_cols=["Hiato per capita (R$)"],
    num_format={"Hiato per capita (R$)": "#,##0"},
)


# ─────────── Aba 6: Médias Regionais (do heatmap) ───────────────────────────
df_reg_export = hiato_regiao.copy()
df_reg_export["cap_media"]   = (df_reg_export["cap_media"]   / 1e3).round(2)
df_reg_export["nec_media"]   = (df_reg_export["nec_media"]   / 1e3).round(2)
df_reg_export["hiato_medio"] = (df_reg_export["hiato_medio"] / 1e3).round(2)
df_reg_export = df_reg_export.rename(columns={
    "Regiao":      "Região",
    "cap_media":   "Cap. Fiscal (R$ mil/hab)",
    "nec_media":   "Nec. Gasto (R$ mil/hab)",
    "hiato_medio": "Hiato (R$ mil/hab)",
    "n_ufs":       "Nº de UFs",
})
df_para_aba(
    wb,
    df_reg_export,
    "Médias Regionais",
    f"Médias Regionais — Hiato Fiscal per capita (dez/{ANO_FIM_ANALISE})",
    color_hiato_cols=["Hiato (R$ mil/hab)"],
    num_format={
        "Cap. Fiscal (R$ mil/hab)": "#,##0.00",
        "Nec. Gasto (R$ mil/hab)":  "#,##0.00",
        "Hiato (R$ mil/hab)":       "#,##0.00",
        "Nº de UFs":                "0",
    },
)


# ─────────── Aba 7: Receita Média de Impostos (gold) ────────────────────────
try:
    df_rec_export = rec_med.copy()
    for c in df_rec_export.select_dtypes(include="object").columns:
        try:
            df_rec_export[c] = pd.to_numeric(df_rec_export[c])
        except (ValueError, TypeError):
            pass
    df_para_aba(
        wb,
        df_rec_export,
        "Receita Média Impostos",
        f"Receita Média de Impostos por UF (preços dez/{ANO_FIM_ANALISE})",
        num_format={c: "#,##0.00"
                    for c in df_rec_export.columns
                    if pd.api.types.is_numeric_dtype(df_rec_export[c])},
    )
except NameError:
    print("Aviso: rec_med não disponível — aba 'Receita Média Impostos' não criada.")


# ─────────── Aba 8: Gráficos (PNGs incorporados) ────────────────────────────
graficos_info = [
    ("01_hiato_fiscal_por_uf",          "Gráfico 1 — Hiato Fiscal por UF"),
    ("02_capacidade_vs_necessidade",    "Gráfico 2 — Capacidade vs. Necessidade per capita"),
    ("03_decomposicao_necessidade_gasto","Gráfico 3 — Decomposição da Necessidade de Gasto"),
    ("04_decomposicao_capacidade_fiscal","Gráfico 4 — Decomposição da Capacidade Fiscal"),
    ("05_heatmap_regional",              "Gráfico 5 — Médias Regionais (heatmap)"),
    ("06_ranking_hiato_per_capita",      "Gráfico 6 — Ranking de Hiato per Capita"),
]

ws_g = wb.create_sheet(title="Gráficos")
ws_g.cell(row=1, column=1,
          value=f"Gráficos — Hiato Fiscal das UFs (dez/{ANO_FIM_ANALISE})").font = TITULO_FONTE
ws_g.column_dimensions["A"].width = 4

linha_atual = 3
LARGURA_PX = 720          # ~12 cm
ALTURA_PX  = 480          # ~8 cm
LINHAS_POR_IMG = 26       # avanço entre imagens

for nome_png, titulo_g in graficos_info:
    caminho = os.path.join(GRAFICOS_DIR, f"{nome_png}.png")
    cell_titulo = ws_g.cell(row=linha_atual, column=2, value=titulo_g)
    cell_titulo.font = SUBTITULO_FONTE
    linha_atual += 1

    if os.path.exists(caminho):
        try:
            img = XLImage(caminho)
            img.width  = LARGURA_PX
            img.height = ALTURA_PX
            ws_g.add_image(img, f"B{linha_atual}")
        except Exception as e:
            ws_g.cell(row=linha_atual, column=2,
                      value=f"[Erro ao inserir imagem: {e}]")
    else:
        ws_g.cell(row=linha_atual, column=2,
                  value=f"[PNG não encontrado: {caminho}]")
    linha_atual += LINHAS_POR_IMG


# ─────────── Aba 9: Dados Gold (hiato completo, para auditoria) ─────────────
df_para_aba(
    wb,
    hiato.copy(),
    "Dados Gold (hiato)",
    "Gold Hiato Fiscal — todas as colunas (auditoria)",
)


# ─── Grava o .xlsx direto no mount local do Lakehouse padrão ────────────────
# mssparkutils.fs.put() só aceita strings — para binários, usar o caminho local.
import os

nome_arquivo = f"resultados_hiato_fiscal_{ANO_FIM_ANALISE}.xlsx"

try:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    caminho_final = os.path.join(OUTPUT_DIR, nome_arquivo)
    wb.save(caminho_final)
    print(f"Arquivo salvo em: {caminho_final}")
    print(f"  Abas: {wb.sheetnames}")
except (FileNotFoundError, PermissionError, OSError):
    # Fallback: sem Lakehouse padrão anexado ou fora do Fabric
    wb.save(nome_arquivo)
    print(f"Arquivo salvo localmente (cwd): {nome_arquivo}")
    print(f"  Abas: {wb.sheetnames}")


# =============================================================================
# CÉLULA 14 — Sumário Executivo (texto gerado programaticamente)
# =============================================================================

total_superavit = (hiato["hiato_fiscal"] > 0).sum()
total_deficit   = (hiato["hiato_fiscal"] < 0).sum()

uf_maior_sup  = hiato.loc[hiato["hiato_fiscal"].idxmax(), "UF"]
uf_maior_def  = hiato.loc[hiato["hiato_fiscal"].idxmin(), "UF"]
maior_sup_bi  = hiato["hiato_fiscal"].max() / 1e9
maior_def_bi  = hiato["hiato_fiscal"].min() / 1e9

media_cap  = hiato["cap_fiscal_per_capita"].mean() / 1e3
media_nec  = hiato["nec_gasto_per_capita"].mean()  / 1e3

print("=" * 60)
print(f"  SUMÁRIO EXECUTIVO — HIATO FISCAL DAS UFs ({ANO_FIM_ANALISE})")
print("=" * 60)
print(f"\n  UFs com superávit de capacidade: {total_superavit}/27")
print(f"  UFs com déficit de capacidade  : {total_deficit}/27")
print(f"\n  Maior superávit: {uf_maior_sup} (R$ {maior_sup_bi:.2f} bi)")
print(f"  Maior déficit  : {uf_maior_def} (R$ {abs(maior_def_bi):.2f} bi)")
print(f"\n  Capacidade fiscal média p/ hab.: R$ {media_cap:.2f} mil")
print(f"  Nec. de gasto média p/ hab.    : R$ {media_nec:.2f} mil")
print("=" * 60)

print("\nNotebook de Apresentação concluído.")
