import time
import requests
import pandas as pd

BASE = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/rreo"
ANO = 2026
PERIODO = 2
NO_ANEXO = "RREO-Anexo 03"
CONTAS_ALVO = {"ICMS", "IPVA", "ITCD"}

ENTES_ESTADUAIS = {
    "RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17,
    "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25, "PE": 26, "AL": 27, "SE": 28, "BA": 29,
    "MG": 31, "ES": 32, "RJ": 33, "SP": 35,
    "PR": 41, "SC": 42, "RS": 43,
    "MS": 50, "MT": 51, "GO": 52, "DF": 53
}

def get_items(session, params):
    resp = session.get(BASE, params=params, timeout=120)
    resp.raise_for_status()
    payload = resp.json()
    return payload["items"] if isinstance(payload, dict) and "items" in payload else payload

linhas = []

with requests.Session() as session:
        for uf, id_ente in ENTES_ESTADUAIS.items():
            params = {
                "an_exercicio": ANO,
                "nr_periodo": PERIODO,
                "co_tipo_demonstrativo": "RREO",
                "id_ente": id_ente,
                "no_anexo": NO_ANEXO,
                "co_esfera": "E"
            }

            try:
                itens = get_items(session, params)

                for item in itens:
                    conta = str(item.get("conta", "")).strip().upper()
                    if conta in CONTAS_ALVO:
                        item["uf_consulta"] = uf
                        item["id_ente_consulta"] = id_ente
                        item["ano_consulta"] = ANO
                        item["periodo_consulta"] = PERIODO
                        linhas.append(item)

                print(f"OK   | {uf} | bimestre {PERIODO} | filtradas até agora: {len(linhas)}")

            except requests.HTTPError as e:
                detalhe = e.response.text[:300] if e.response is not None else ""
                print(f"ERRO | {uf} | bimestre {PERIODO} | {e} | {detalhe}")

            time.sleep(1.05)

df = pd.DataFrame(linhas)

arquivo_csv = f"dados_{ANO}_{PERIODO}.csv"
arquivo_xlsx = f"dados_{ANO}_{PERIODO}.xlsx"

df.to_csv(arquivo_csv, index=False, encoding="utf-8-sig")
df.to_excel(arquivo_xlsx, index=False, engine="openpyxl")

print("\nTotal de linhas filtradas:", len(df))
print("Arquivos salvos:")
print("-", arquivo_csv)
print("-", arquivo_xlsx)