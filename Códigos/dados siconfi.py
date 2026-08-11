import time
import requests
import pandas as pd

BASE = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt"
ANO = 2024

# Se quiser só o fechamento do ano, troque para: PERIODOS = [6]
PERIODOS = [1, 2, 3, 4, 5, 6]

# Estados + DF (códigos IBGE dos entes estaduais)
ENTES_ESTADUAIS = {
    "RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17,
    "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25, "PE": 26, "AL": 27, "SE": 28, "BA": 29,
    "MG": 31, "ES": 32, "RJ": 33, "SP": 35,
    "PR": 41, "SC": 42, "RS": 43,
    "MS": 50, "MT": 51, "GO": 52, "DF": 53
}

def get_items(endpoint: str, params: dict | None = None):
    """
    Faz GET e devolve a lista de itens, lidando com respostas no formato
    ORDS {"items": [...]} ou lista pura.
    """
    url = f"{BASE}/{endpoint}"
    resp = requests.get(url, params=params, timeout=120)
    resp.raise_for_status()
    payload = resp.json()

    if isinstance(payload, dict) and "items" in payload:
        return payload["items"]
    if isinstance(payload, list):
        return payload

    raise ValueError(f"Formato inesperado de resposta em {endpoint}: {type(payload)}")

def escolher_coluna(df: pd.DataFrame, candidatos: list[str]) -> str | None:
    """
    Tenta encontrar a coluna certa por nome exato ou parcial, ignorando maiúsc./minúsc.
    """
    cols = list(df.columns)
    lower_map = {c.lower(): c for c in cols}

    # exato
    for cand in candidatos:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]

    # parcial
    for cand in candidatos:
        for c in cols:
            if cand.lower() in c.lower():
                return c

    return None

# 1) Descobrir o nome exato do Anexo 03 na tabela de apoio
df_anexos = pd.DataFrame(get_items("anexos-relatorios"))

col_anexo = escolher_coluna(df_anexos, ["no_anexo", "anexo"])
col_relatorio = escolher_coluna(df_anexos, ["relatorio", "demonstrativo", "no_relatorio"])
col_esfera = escolher_coluna(df_anexos, ["co_esfera", "esfera"])

if col_anexo is None:
    raise RuntimeError(
        f"Não consegui localizar a coluna do nome do anexo. Colunas retornadas: {df_anexos.columns.tolist()}"
    )

mask = df_anexos[col_anexo].astype(str).str.contains(r"anexo\s*0?3\b", case=False, regex=True, na=False)

if col_relatorio is not None:
    mask &= df_anexos[col_relatorio].astype(str).str.contains("RREO", case=False, na=False)

if col_esfera is not None:
    mask &= df_anexos[col_esfera].astype(str).str.contains(r"^E$|EST", case=False, regex=True, na=False)

candidatos = df_anexos.loc[mask, col_anexo].dropna().astype(str).unique().tolist()

if not candidatos:
    raise RuntimeError(
        "Não encontrei o Anexo 03 automaticamente. "
        f"Veja manualmente as linhas de df_anexos.head(50) e ajuste o valor de no_anexo."
    )

print(df_anexos.head(50))

NO_ANEXO = "RREO-Anexo 03"
print("Anexo identificado:", NO_ANEXO)

# 2) Baixar RREO Anexo 03 para todos os estados em 2024
linhas = []

for nr_periodo in PERIODOS:
    for uf, id_ente in ENTES_ESTADUAIS.items():
        params = {
            "an_exercicio": ANO,
            "nr_periodo": nr_periodo,
            "co_tipo_demonstrativo": "RREO",
            "id_ente": id_ente,
            "no_anexo": NO_ANEXO,
            "co_esfera": "E",   # E = Estados e DF
        }

        try:
            itens = get_items("rreo", params=params)

            for item in itens:
                item["uf_consulta"] = uf
                item["id_ente_consulta"] = id_ente
                item["ano_consulta"] = ANO
                item["periodo_consulta"] = nr_periodo

            linhas.extend(itens)
            print(f"OK  | {uf} | bimestre {nr_periodo} | {len(itens)} linhas")

        except requests.HTTPError as e:
            texto = ""
            if e.response is not None:
                texto = e.response.text[:300]
            print(f"ERRO | {uf} | bimestre {nr_periodo} | {e} | {texto}")

        # respeitar o limite oficial de 1 req/s
        time.sleep(1.05)

df = pd.DataFrame(linhas)

print("\nTotal de linhas baixadas:", len(df))
print("Colunas:")
print(df.columns.tolist())

print("\nAmostra:")
print(df.head())

# 3) Salvar
arquivo = "rreo_anexo3_estados_2024.csv"
df.to_csv(arquivo, index=False, encoding="utf-8-sig")
print(f"\nArquivo salvo em: {arquivo}")