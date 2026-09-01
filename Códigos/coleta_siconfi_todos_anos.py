"""
coleta_siconfi_todos_anos.py
============================
Baixa o RREO Anexo 03 (receitas tributárias) de todos os estados + DF
para o máximo de anos disponíveis na API do SICONFI (a partir de 2013).

Saída: rreo_anexo3_todos_anos.xlsx  (e .csv de backup)

Estratégia:
- Itera do ano mais recente (ano atual) até 2013.
- Usa apenas o período 6 (fechamento do exercício) para garantir
  os valores anuais definitivos de cada mês.
- Ignora anos/bimestres sem dados (HTTP 4xx ou listas vazias).
- Respeita o limite de 1 req/s da API.
"""

import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================
BASE          = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt"
NO_ANEXO      = "RREO-Anexo 03"
ANO_INICIAL          = 2015                       # primeiro ano com dados consolidados no SICONFI
ANO_FINAL            = datetime.today().year      # ano atual (detectado automaticamente)
BIMESTRE_ANO_ATUAL   = 3                          # bimestre a coletar no ANO ATUAL (1..6)
BIMESTRE_ANOS_PASSADOS = 6                        # anos anteriores: sempre bimestre 6 (fechamento anual)
FORCAR_COLETA_COMPLETA = False                    # True = refaz do zero; False = se já houver CSV, apenas anexa o novo bimestre
CONTAS_INTERESSE     = ["ICMS", "IPVA", "ITCD"]   # apenas essas 3 contas são mantidas
DELAY_SEG            = 1.1                        # respeita limite de 1 req/s

if BIMESTRE_ANO_ATUAL not in (1, 2, 3, 4, 5, 6):
    raise SystemExit("BIMESTRE_ANO_ATUAL deve ser um inteiro entre 1 e 6.")

SAIDA_DIR     = Path(r"C:\Users\carlos.marchionatti\GitHub - STN\STN-gered\Planilhas")
SAIDA_XLSX    = SAIDA_DIR / "rreo_anexo3_todos_anos.xlsx"
SAIDA_CSV     = SAIDA_DIR / "rreo_anexo3_todos_anos.csv"

# Códigos IBGE dos entes estaduais (estados + DF)
ENTES_ESTADUAIS = {
    "RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17,
    "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25, "PE": 26,
    "AL": 27, "SE": 28, "BA": 29,
    "MG": 31, "ES": 32, "RJ": 33, "SP": 35,
    "PR": 41, "SC": 42, "RS": 43,
    "MS": 50, "MT": 51, "GO": 52, "DF": 53,
}

# =============================================================================
# FUNÇÕES
# =============================================================================
def get_items(endpoint: str, params: dict | None = None) -> list:
    url  = f"{BASE}/{endpoint}"
    resp = requests.get(url, params=params, timeout=120)
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict) and "items" in payload:
        return payload["items"]
    if isinstance(payload, list):
        return payload
    raise ValueError(f"Formato inesperado em {endpoint}: {type(payload)}")


# =============================================================================
# COLETA
# =============================================================================
linhas      = []
erros       = []
total_req   = 0
anos        = list(range(ANO_FINAL, ANO_INICIAL - 1, -1))  # mais recente → mais antigo

modo_incremental = SAIDA_CSV.exists() and not FORCAR_COLETA_COMPLETA

if modo_incremental:
    # Checa qual bimestre do ano atual já está no CSV.
    df_existente_check = pd.read_csv(SAIDA_CSV, usecols=["ano_consulta", "periodo_consulta"])
    mask_ano_atual_exist = df_existente_check["ano_consulta"] == ANO_FINAL
    bim_existente = (
        int(df_existente_check.loc[mask_ano_atual_exist, "periodo_consulta"].max())
        if mask_ano_atual_exist.any() else 0
    )
    if BIMESTRE_ANO_ATUAL <= bim_existente:
        print(f"Modo INCREMENTAL: CSV já contém {ANO_FINAL} até o bimestre {bim_existente}."
              f" Nada a fazer para o bimestre {BIMESTRE_ANO_ATUAL}.")
        raise SystemExit(0)

    anos = [ANO_FINAL]
    print(f"Modo INCREMENTAL: arquivo existente detectado (último bim. de {ANO_FINAL} no CSV = {bim_existente}).\n"
          f"  Baixando {ANO_FINAL} bimestre {BIMESTRE_ANO_ATUAL} e substituindo os dados do ano atual.\n")
else:
    print(f"Modo COMPLETO: coletando anos {ANO_INICIAL}–{ANO_FINAL}  "
          f"({len(anos)} anos × {len(ENTES_ESTADUAIS)} UFs)\n"
          f"  • anos anteriores: bimestre {BIMESTRE_ANOS_PASSADOS}\n"
          f"  • ano atual ({ANO_FINAL}): bimestre {BIMESTRE_ANO_ATUAL}\n")

for ano in anos:
    ok_ano = 0
    nr_periodo = BIMESTRE_ANO_ATUAL if ano == ANO_FINAL else BIMESTRE_ANOS_PASSADOS
    for uf, id_ente in ENTES_ESTADUAIS.items():
        params = {
            "an_exercicio"          : ano,
            "nr_periodo"            : nr_periodo,
            "co_tipo_demonstrativo" : "RREO",
            "id_ente"               : id_ente,
            "no_anexo"              : NO_ANEXO,
            "co_esfera"             : "E",
        }
        try:
            itens = get_items("rreo", params=params)
            itens = [it for it in itens if it.get("conta") in CONTAS_INTERESSE]
            for item in itens:
                item["uf_consulta"]      = uf
                item["id_ente_consulta"] = id_ente
                item["ano_consulta"]     = ano
                item["periodo_consulta"] = nr_periodo
            linhas.extend(itens)
            ok_ano += len(itens)
            print(f"OK   | {ano} | p{nr_periodo} | {uf} | {len(itens):>4} linhas")
        except requests.HTTPError as e:
            codigo = e.response.status_code if e.response is not None else "?"
            msg    = (e.response.text[:120] if e.response is not None else str(e))
            print(f"ERRO | {ano} | p{nr_periodo} | {uf} | HTTP {codigo} | {msg}")
            erros.append({"ano": ano, "periodo": nr_periodo, "uf": uf,
                           "http": codigo, "msg": msg})
        except Exception as e:
            print(f"ERRO | {ano} | p{nr_periodo} | {uf} | {e}")
            erros.append({"ano": ano, "periodo": nr_periodo, "uf": uf,
                           "http": "—", "msg": str(e)})

        total_req += 1
        time.sleep(DELAY_SEG)

    print(f"  → {ano}: {ok_ano} linhas coletadas\n")

# =============================================================================
# SALVAR
# =============================================================================
df_novo = pd.DataFrame(linhas)

# Cada request retorna janela móvel de 12 meses (colunas <MR>, <MR-1>, ... <MR-11>).
# Do ano atual, mantemos os meses de jan até o último do bimestre baixado (YTD):
#   - bimestre 1 → <MR>, <MR-1>            (jan-fev)
#   - bimestre 2 → <MR> até <MR-3>          (jan-abr)
#   - ...
#   - bimestre 6 → <MR> até <MR-11>         (jan-dez)
colunas_ano_atual = ["<MR>"] + [f"<MR-{k}>" for k in range(1, 2 * BIMESTRE_ANO_ATUAL)]

if not df_novo.empty and "coluna" in df_novo.columns:
    mask_ano_atual = df_novo["ano_consulta"] == ANO_FINAL
    mask_ytd       = df_novo["coluna"].isin(colunas_ano_atual)
    antes = len(df_novo)
    df_novo = df_novo[~mask_ano_atual | mask_ytd].copy()
    print(f"Filtro janela móvel: {antes} → {len(df_novo)} linhas "
          f"(mantém {len(colunas_ano_atual)} meses do ano atual: {colunas_ano_atual[-1]}…<MR>).")

if modo_incremental and not df_novo.empty:
    df_existente = pd.read_csv(SAIDA_CSV)
    # Remove qualquer dado do ano atual já existente e substitui pelo novo download YTD.
    linhas_ano_atual_antes = int((df_existente["ano_consulta"] == ANO_FINAL).sum())
    df_existente = df_existente[df_existente["ano_consulta"] != ANO_FINAL].copy()
    df = pd.concat([df_existente, df_novo], ignore_index=True)
    print(f"Incremental: {len(df_existente)} linhas anteriores (removidas {linhas_ano_atual_antes} do ano atual) "
          f"+ {len(df_novo)} novas = {len(df)} totais.")
else:
    df = df_novo

print(f"\nTotal de linhas: {len(df)}")
if df.empty:
    print("Nenhum dado retornado. Verifique a conectividade com a API do SICONFI.")
else:
    print(f"Colunas: {df.columns.tolist()}")
    print(f"Anos disponíveis: {sorted(df['ano_consulta'].unique())}")

    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(SAIDA_CSV,  index=False, encoding="utf-8-sig")
    df.to_excel(SAIDA_XLSX, index=False, engine="openpyxl")
    print(f"\nArquivos salvos em:")
    print(f"  {SAIDA_XLSX}")
    print(f"  {SAIDA_CSV}")

if erros:
    df_erros = pd.DataFrame(erros)
    saida_erros = SAIDA_DIR / "rreo_coleta_erros.csv"
    df_erros.to_csv(saida_erros, index=False, encoding="utf-8-sig")
    print(f"\n{len(erros)} requisições com erro salvas em: {saida_erros}")

print(f"\nTotal de requisições: {total_req}")
