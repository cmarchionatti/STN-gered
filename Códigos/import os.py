import os
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from matplotlib.backends.backend_pdf import PdfPages

arquivo = Path("rreo_anexo3_icms_ipva_itcd_2024.xlsx")
saida_dir = Path("graficos_icms_estados")
saida_dir.mkdir(exist_ok=True)
pdf_path = Path("graficos_icms_estados_2024.pdf")

df = pd.read_excel(arquivo)

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

df_icms = df[
    (df["conta"].astype(str).str.upper() == "ICMS") &
    (df["periodo_consulta"] == 6) &
    (df["coluna"].isin(mapa_colunas.keys()))
].copy()

df_icms["mes"] = df_icms["coluna"].map(lambda x: mapa_colunas[x][0])
df_icms["ordem_mes"] = df_icms["coluna"].map(lambda x: mapa_colunas[x][1])
df_icms["valor_milhoes"] = pd.to_numeric(df_icms["valor"], errors="coerce") / 1_000_000

def formato_milhoes(x, pos):
    return f"{x:,.0f}".replace(",", ".")

ufs = sorted(df_icms["uf_consulta"].dropna().unique())

with PdfPages(pdf_path) as pdf:
    for uf in ufs:
        base = (
            df_icms[df_icms["uf_consulta"] == uf]
            .sort_values("ordem_mes")
        )

        plt.figure(figsize=(10, 5.5))
        plt.plot(base["mes"], base["valor_milhoes"], marker="o")
        plt.title(f"ICMS mensal em 2024 - {uf}")
        plt.xlabel("Mês")
        plt.ylabel("R$ milhões")
        plt.grid(True, alpha=0.3)
        plt.gca().yaxis.set_major_formatter(FuncFormatter(formato_milhoes))
        plt.tight_layout()

        png_path = saida_dir / f"icms_2024_{uf}.png"
        plt.savefig(png_path, dpi=200, bbox_inches="tight")
        pdf.savefig()
        plt.close()

print(f"Gráficos salvos em: {saida_dir}")
print(f"PDF consolidado salvo em: {pdf_path}")