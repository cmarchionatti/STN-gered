"""Inspeção focada — contagens e lógica da planilha de Consolidação."""
from pathlib import Path
import pandas as pd

BASE = Path(r"c:\Users\carlos.marchionatti\GitHub - STN\STN-gered\VAAT")
cons = BASE / "Planilha Consolidação 18agosto2026.xlsx"
vaat = BASE / "VAAT_18-08-2026 1.xlsx"

# ---------- Aba "Compilação STN e FNDE" — totais no fim ----------
print("=" * 80)
print("Compilação STN e FNDE — últimas linhas (onde ficam os totais)")
print("=" * 80)
df_comp = pd.read_excel(cons, sheet_name="Compilação STN e FNDE")
print(f"Total linhas: {len(df_comp)}")
print("\nÚltimas 5 linhas:")
print(df_comp.tail(5).to_string())

print("\n\nContagens (das linhas com dados de ente, ignorando totais):")
# Only rows with UF (excluding possible totals rows at the end)
df_ok = df_comp.dropna(subset=["UF"])
print(f"  Linhas com UF preenchido: {len(df_ok)}")
print(f"  Habilitação 163-A? = NÃO: {(df_ok['Habilitação 163-A?'] == 'NÃO').sum()}")
print(f"  Habilitação art. 38? = NÃO: {(df_ok['Habilitação art. 38?'] == 'NÃO').sum()}")
inab_163 = df_ok['Habilitação 163-A?'] == 'NÃO'
inab_38 = df_ok['Habilitação art. 38?'] == 'NÃO'
print(f"  Inabilitado nos dois (163 E 38): {(inab_163 & inab_38).sum()}")
print(f"  Inabilitado em pelo menos um (163 OU 38): {(inab_163 | inab_38).sum()}")

print("\n\nSoma das colunas de contagem já existentes:")
for col in ["Total inab 163", "Total inab 38", "Total inab 163 e 38", "Total inab 163 ou 38"]:
    if col in df_comp.columns:
        print(f"  {col}: {df_comp[col].sum()}")

# ---------- Aba "Comunicado formulas" — para ver textos únicos da coluna Unnamed:8 ----------
print("\n" + "=" * 80)
print("Comunicado formulas — textos únicos da coluna 'Unnamed: 8' (motivos)")
print("=" * 80)
df_com = pd.read_excel(cons, sheet_name="Comunicado formulas")
print("Colunas:", list(df_com.columns))
print("\nValores únicos em 'Unnamed: 8' (motivos):")
if "Unnamed: 8" in df_com.columns:
    print(df_com["Unnamed: 8"].value_counts(dropna=False).to_string())
print("\nValores únicos em 'Verificação preliminar...':")
col_ver = [c for c in df_com.columns if "Vefica" in c or "Verif" in c][0]
print(df_com[col_ver].value_counts(dropna=False).to_string())
print("\nValores únicos em 'Pendência identificada':")
print(df_com["Pendência identificada"].value_counts(dropna=False).to_string())

# ---------- Aba "Final 4agosto2026" — para ver contagem de inabilitados no comunicado ----------
print("\n" + "=" * 80)
print("Final 4agosto2026 — contagem de inabilitados (esta é a lista para o PDF)")
print("=" * 80)
df_final = pd.read_excel(cons, sheet_name="Final 4agosto2026")
print(f"Total linhas: {len(df_final)}")
col_ver = [c for c in df_final.columns if "Vefica" in c or "Verif" in c][0]
print(f"\nDistribuição por '{col_ver[:60]}...':")
print(df_final[col_ver].value_counts(dropna=False).to_string())
print("\nDistribuição por 'Pendência identificada':")
print(df_final["Pendência identificada"].value_counts(dropna=False).to_string())

print("\nAmostra dos INABILITADOS (não-habilitados):")
mask_inab = ~df_final[col_ver].str.startswith("Habilitado", na=False)
print(f"Total inabilitados: {mask_inab.sum()}")
print(df_final[mask_inab].head(30).to_string())

# ---------- VAAT xlsx — aba Inabilitados_18-08-2026 ----------
print("\n" + "=" * 80)
print("VAAT_18-08-2026 — aba Inabilitados_18-08-2026 (COMPLETA)")
print("=" * 80)
df_vaat = pd.read_excel(vaat, sheet_name="Inabilitados_18-08-2026")
print(df_vaat.to_string())
print(f"\nTotal: {len(df_vaat)}")
print("\nContagem por MOTIVO:")
print(df_vaat["MOTIVO"].value_counts().to_string())
