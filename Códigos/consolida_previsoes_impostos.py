# =============================================================================
# Consolida as previsões de ICMS, IPVA e ITCD em um único arquivo.
#
# Lê os três Excel produzidos pelos scripts sarimax_icms.py / sarimax_ipva.py /
# sarimax_itcd.py e gera:
#
#   Planilhas/previsoes_consolidadas.xlsx
#       - previsoes_long  : todas as previsões empilhadas, com coluna "imposto"
#       - previsoes_wide  : uma linha por (uf, data) e uma coluna por imposto,
#                           mais coluna TOTAL (ICMS + IPVA + ITCD)
#       - modelos         : diagnósticos de modelo dos 3 impostos empilhados
#
#   Planilhas/previsoes_consolidadas_long.csv  (mesmo conteúdo da aba long)
# =============================================================================

from pathlib import Path
import pandas as pd

# =========================
# ARQUIVOS DE ENTRADA
# =========================
pasta_planilhas = Path(
    r"C:\Users\carlos.marchionatti\OneDrive - Tesouro Nacional\VSCode\STN\Planilhas"
)

arquivos = {
    "ICMS": pasta_planilhas / "previsoes_icms_estados_com_pib.xlsx",
    "IPVA": pasta_planilhas / "previsoes_ipva_estados_com_pib.xlsx",
    "ITCD": pasta_planilhas / "previsoes_itcd_estados_com_pib.xlsx",
}

saida_excel = pasta_planilhas / "previsoes_consolidadas.xlsx"
saida_csv   = pasta_planilhas / "previsoes_consolidadas_long.csv"


# =========================
# LEITURA
# =========================
previsoes_partes = []
modelos_partes   = []

for imposto, caminho in arquivos.items():
    if not caminho.exists():
        print(f"[AVISO] arquivo não encontrado, pulando: {caminho}")
        continue

    df_prev = pd.read_excel(caminho, sheet_name="previsoes")
    df_mod  = pd.read_excel(caminho, sheet_name="modelos")

    df_prev.insert(0, "imposto", imposto)
    df_mod.insert(0, "imposto", imposto)

    previsoes_partes.append(df_prev)
    modelos_partes.append(df_mod)

if not previsoes_partes:
    raise SystemExit("Nenhum arquivo de previsão encontrado. Rode os scripts sarimax antes.")

# =========================
# LONG (empilhado)
# =========================
prev_long = pd.concat(previsoes_partes, ignore_index=True)
mod_long  = pd.concat(modelos_partes,   ignore_index=True)

prev_long["data_previsao"] = pd.to_datetime(prev_long["data_previsao"])
prev_long = prev_long.sort_values(["uf", "data_previsao", "imposto"]).reset_index(drop=True)

# =========================
# WIDE (uma coluna por imposto + TOTAL)
# =========================
prev_wide = (
    prev_long
    .pivot_table(
        index=["uf", "data_previsao", "ano", "mes_num", "mes"],
        columns="imposto",
        values="valor_previsto",
        aggfunc="sum",
    )
    .reset_index()
)

for col in ("ICMS", "IPVA", "ITCD"):
    if col not in prev_wide.columns:
        prev_wide[col] = pd.NA

prev_wide["TOTAL"] = prev_wide[["ICMS", "IPVA", "ITCD"]].sum(axis=1, min_count=1)

prev_wide = prev_wide[
    ["uf", "data_previsao", "ano", "mes_num", "mes", "ICMS", "IPVA", "ITCD", "TOTAL"]
].sort_values(["uf", "data_previsao"]).reset_index(drop=True)

prev_wide.columns.name = None

# =========================
# GRAVAÇÃO
# =========================
with pd.ExcelWriter(saida_excel, engine="openpyxl") as writer:
    prev_long.to_excel(writer, sheet_name="previsoes_long", index=False)
    prev_wide.to_excel(writer, sheet_name="previsoes_wide", index=False)
    mod_long.to_excel(writer,  sheet_name="modelos",        index=False)

prev_long.to_csv(saida_csv, index=False, encoding="utf-8-sig")

print("Consolidação concluída.")
print(f"- Excel: {saida_excel}")
print(f"- CSV  : {saida_csv}")
print(f"- Linhas (long): {len(prev_long):,}")
print(f"- Linhas (wide): {len(prev_wide):,}")
print(f"- UFs          : {prev_long['uf'].nunique()}")
print(f"- Impostos     : {sorted(prev_long['imposto'].unique())}")
