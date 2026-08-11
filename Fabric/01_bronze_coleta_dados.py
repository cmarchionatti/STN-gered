# =============================================================================
#
#   HIATO FISCAL DAS UNIDADES DA FEDERAÇÃO DO BRASIL
#   NOTEBOOK 1 — BRONZE: Coleta de Dados Brutos
#
#   Fontes:
#     - API Siconfi (Tesouro Nacional): receitas e despesas DCA
#     - API Portal da Transparência: FCDF
#     - API Sidra/IBGE: populações totais, IPCA, PIB
#     - API IPEA Data: taxa de homicídios (THOMIC)
#     - FTP IBGE: projeções populacionais por faixa etária
#     - Arquivo Excel (ingestão manual): códigos contábeis
#
#   Outputs (Delta Tables no Lakehouse):
#     - bronze_siconfi_raw
#     - bronze_fcdf_raw
#     - bronze_populacao_total
#     - bronze_ipca
#     - bronze_pib_uf
#     - bronze_pop_faixa_etaria
#     - bronze_homicidios
#     - bronze_codigos_contabeis
#
# =============================================================================

# ── Instalação de dependências extras ────────────────────────────────────────
# Execute esta célula separadamente na primeira vez
# %pip install requests pandas openpyxl xlrd sidrapy

# =============================================================================
# CÉLULA 1 — Configurações do Usuário
# =============================================================================

# Caminho do arquivo Excel com códigos contábeis (carregado no Lakehouse Files)
ARQUIVO_CODIGOS = "abfss://93dbb6c5-e901-426c-bc03-1ee0284a1363@onelake.dfs.fabric.microsoft.com/c83ffa7e-2cc3-42a6-ac17-685b570193cc/Files/Dados - Novo FPE.xlsx"
ABA_CODIGOS     = "Códigos Contábeis - Receitas"

# Período de coleta
ANO_INICIO_COLETA = 2019
ANO_FIM_COLETA    = 2022

# Token da API do Portal da Transparência (não é mais usado — coleta atual usa
# o endpoint público resultadoGrafico, equivalente ao do script R original).
# Mantido apenas por compatibilidade.
FCDF_API_TOKEN = "55adb14c417034cf758c915be67a5eb9"

# Nome do Lakehouse (Delta Tables serão salvas aqui)
LAKEHOUSE_NAME = "hiato_fiscal"   # ajuste conforme o nome do seu Lakehouse

periodo_coleta = list(range(ANO_INICIO_COLETA, ANO_FIM_COLETA + 1))
periodo_str    = f"{ANO_INICIO_COLETA}_{ANO_FIM_COLETA}"

print(f"Período de coleta: {periodo_coleta}")
print(f"Arquivo de códigos: {ARQUIVO_CODIGOS}")


# =============================================================================
# CÉLULA 2 — Imports
# =============================================================================

import requests
import json
import time
import io
import tempfile
import os

import pandas as pd
import numpy as np

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    StructType, StructField, StringType, DoubleType, IntegerType, LongType
)

spark = SparkSession.builder.getOrCreate()

print("SparkSession iniciada.")


# =============================================================================
# CÉLULA 3 — Tabelas Auxiliares
# =============================================================================

tabela_uf = pd.DataFrame({
    "codigo": [
        11, 12, 13, 14, 15, 16, 17,
        21, 22, 23, 24, 25, 26, 27, 28, 29,
        31, 32, 33, 35,
        41, 42, 43,
        50, 51, 52, 53
    ],
    "uf": [
        "RO", "AC", "AM", "RR", "PA", "AP", "TO",
        "MA", "PI", "CE", "RN", "PB", "PE", "AL", "SE", "BA",
        "MG", "ES", "RJ", "SP",
        "PR", "SC", "RS",
        "MS", "MT", "GO", "DF"
    ],
    "nome_uf": [
        "Rondônia", "Acre", "Amazonas", "Roraima", "Pará", "Amapá", "Tocantins",
        "Maranhão", "Piauí", "Ceará", "Rio Grande do Norte", "Paraíba",
        "Pernambuco", "Alagoas", "Sergipe", "Bahia",
        "Minas Gerais", "Espírito Santo", "Rio de Janeiro", "São Paulo",
        "Paraná", "Santa Catarina", "Rio Grande do Sul",
        "Mato Grosso do Sul", "Mato Grosso", "Goiás", "Distrito Federal"
    ]
})

# Mapeamento nome completo → sigla
nome_para_sigla = {
    "Acre": "AC", "Alagoas": "AL", "Amapá": "AP", "Amazonas": "AM",
    "Bahia": "BA", "Ceará": "CE", "Distrito Federal": "DF",
    "Espírito Santo": "ES", "Goiás": "GO", "Maranhão": "MA",
    "Mato Grosso": "MT", "Mato Grosso do Sul": "MS", "Minas Gerais": "MG",
    "Pará": "PA", "Paraíba": "PB", "Paraná": "PR", "Pernambuco": "PE",
    "Piauí": "PI", "Rio de Janeiro": "RJ", "Rio Grande do Norte": "RN",
    "Rio Grande do Sul": "RS", "Rondônia": "RO", "Roraima": "RR",
    "Santa Catarina": "SC", "São Paulo": "SP", "Sergipe": "SE",
    "Tocantins": "TO"
}

codigos_ibge = tabela_uf["codigo"].tolist()
print(f"UFs mapeadas: {len(tabela_uf)}")


# =============================================================================
# CÉLULA 4 — MÓDULO 1: Coleta Siconfi via API
# =============================================================================

print("\n===== MÓDULO 1: Coleta Siconfi =====\n")

BASE_URL_SICONFI = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/dca"

registros = []

for ano in periodo_coleta:
    for cod in codigos_ibge:
        chave = f"{ano}_{cod}"
        print(f"-> Consultando: {chave} ...", end=" ")

        try:
            resp = requests.get(
                BASE_URL_SICONFI,
                params={"an_exercicio": str(ano), "id_ente": str(cod)},
                timeout=30
            )
        except requests.exceptions.RequestException as e:
            print(f"ERRO de conexão: {e}")
            time.sleep(0.12)
            continue

        if resp.status_code != 200:
            print(f"Status {resp.status_code}")
            time.sleep(0.12)
            continue

        try:
            parsed = resp.json()
        except json.JSONDecodeError as e:
            print(f"JSON inválido: {e}")
            time.sleep(0.12)
            continue

        items = parsed.get("items", [])
        if not items:
            print("Sem dados.")
            time.sleep(0.12)
            continue

        df_chunk = pd.DataFrame(items)
        df_chunk["ano"]     = ano
        df_chunk["id_ente"] = cod
        registros.append(df_chunk)
        print(f"OK — linhas: {len(df_chunk)}")

        time.sleep(0.12)

if registros:
    siconfi_raw_pd = pd.concat(registros, ignore_index=True)
    print(f"\nTotal de linhas Siconfi: {len(siconfi_raw_pd)}")
else:
    siconfi_raw_pd = pd.DataFrame()
    print("Nenhum dado coletado do Siconfi.")

# Salva como Delta Table no Lakehouse
siconfi_spark = spark.createDataFrame(siconfi_raw_pd.astype(str))
siconfi_spark.write.format("delta").mode("overwrite").saveAsTable("bronze_siconfi_raw")
print("Tabela bronze_siconfi_raw salva.")


# =============================================================================
# CÉLULA 5 — MÓDULO 2: FCDF via Portal da Transparência (resultadoGrafico)
# =============================================================================

print("\n===== MÓDULO 2: Fundo Constitucional do DF =====\n")

# Mesmo endpoint público usado pelo script R: tipoDespesa = "Pagamentos realizados",
# filtro "próprio órgão" feito na camada Silver para reproduzir o critério do R.
BASE_URL_FCDF = (
    "https://portaldatransparencia.gov.br/orgaos/"
    "distribuicao-execucao-orcamentaria-financeira/resultadoGrafico"
)

registros_fcdf = []
headers_fcdf = {
    "User-Agent": "Mozilla/5.0 (compatible; FabricBronze/1.0)",
    "Accept":     "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

for ano in periodo_coleta:
    try:
        r = requests.get(
            BASE_URL_FCDF,
            params={
                "tipoDespesa":   "Pagamentos realizados",
                "codigoOrgao":   "25915",   # Código SIAFI do FCDF
                "isOrgaoMaximo": "false",
                "ano":           ano,
            },
            headers=headers_fcdf,
            timeout=30,
        )

        if r.status_code != 200 or r.text.strip() in ("", "[]"):
            print(f"  Ano {ano} — HTTP {r.status_code} / vazio")
            continue

        try:
            dados_r = r.json()
        except json.JSONDecodeError as e:
            print(f"  Ano {ano} — JSON inválido: {e}")
            continue

        # Endpoint pode devolver lista ou dict — normaliza para lista de dicts
        if isinstance(dados_r, dict):
            dados_r = [dados_r]

        if not dados_r:
            print(f"  Ano {ano} — sem dados")
            continue

        for item in dados_r:
            if isinstance(item, dict):
                item["ano"] = ano
        registros_fcdf.extend(d for d in dados_r if isinstance(d, dict))
        print(f"  Ano {ano} — {len(dados_r)} registros")

    except Exception as e:
        print(f"  Ano {ano} — Erro: {e}")

    time.sleep(0.1)

if registros_fcdf:
    fcdf_raw_pd = pd.DataFrame(registros_fcdf)
    print(f"\nTotal linhas FCDF: {len(fcdf_raw_pd)}")
else:
    fcdf_raw_pd = pd.DataFrame()
    print("Nenhum dado FCDF coletado.")

if not fcdf_raw_pd.empty:
    fcdf_spark = spark.createDataFrame(fcdf_raw_pd.astype(str))
    (fcdf_spark.write
        .format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")   # schema mudou vs versão anterior (empenhado/liquidado/pago → valores)
        .saveAsTable("bronze_fcdf_raw"))
    print("Tabela bronze_fcdf_raw salva.")
else:
    print("AVISO: Nenhum dado FCDF coletado — tabela bronze_fcdf_raw não criada.")


# =============================================================================
# CÉLULA 6 — MÓDULO 3: Populações Totais por UF via API Sidra (IBGE)
# =============================================================================

print("\n===== MÓDULO 3: Populações Totais (Sidra) =====\n")

# Tabela 6579 — estimativas populacionais (sem 2022)
URL_SIDRA_6579 = "https://apisidra.ibge.gov.br/values/t/6579/n3/all/v/9324/p/{anos}/f/a"
anos_6579 = [str(a) for a in periodo_coleta if a != 2022]

registros_pop = []

if anos_6579:
    url = URL_SIDRA_6579.format(anos=",".join(anos_6579))
    try:
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            dados = r.json()
            registros_pop.extend(dados[1:])  # ignora cabeçalho
            print(f"Tabela 6579 — {len(dados)-1} registros")
        else:
            print(f"Erro tabela 6579: {r.status_code}")
    except Exception as e:
        print(f"Erro tabela 6579: {e}")

# Tabela 4714 — Censo 2022
URL_SIDRA_4714 = "https://apisidra.ibge.gov.br/values/t/4714/n3/all/v/93/p/2022/f/a"
try:
    r = requests.get(URL_SIDRA_4714, timeout=60)
    if r.status_code == 200:
        dados = r.json()
        registros_pop.extend(dados[1:])
        print(f"Tabela 4714 (Censo 2022) — {len(dados)-1} registros")
    else:
        print(f"Erro tabela 4714: {r.status_code}")
except Exception as e:
    print(f"Erro tabela 4714: {e}")

pop_pd = pd.DataFrame(registros_pop)
pop_spark = spark.createDataFrame(pop_pd.astype(str))
pop_spark.write.format("delta").mode("overwrite").saveAsTable("bronze_populacao_total")
print("Tabela bronze_populacao_total salva.")


# =============================================================================
# CÉLULA 7 — MÓDULO 4: IPCA mensal (dezembro) via API Sidra
# =============================================================================

print("\n===== MÓDULO 4: IPCA (Sidra) =====\n")

URL_IPCA = "https://apisidra.ibge.gov.br/values/t/1737/n1/all/v/2266/p/201401-202412"

try:
    r = requests.get(URL_IPCA, timeout=60)
    if r.status_code == 200:
        dados_ipca = r.json()
        ipca_pd = pd.DataFrame(dados_ipca[1:])
        print(f"IPCA — {len(ipca_pd)} registros")
    else:
        ipca_pd = pd.DataFrame()
        print(f"Erro IPCA: {r.status_code}")
except Exception as e:
    ipca_pd = pd.DataFrame()
    print(f"Erro IPCA: {e}")

ipca_spark = spark.createDataFrame(ipca_pd.astype(str))
ipca_spark.write.format("delta").mode("overwrite").saveAsTable("bronze_ipca")
print("Tabela bronze_ipca salva.")


# =============================================================================
# CÉLULA 8 — MÓDULO 5: PIB nominal por UF via API Sidra
# =============================================================================

print("\n===== MÓDULO 5: PIB por UF (Sidra) =====\n")

anos_pib = ",".join([str(a) for a in periodo_coleta])
URL_PIB  = f"https://apisidra.ibge.gov.br/values/t/5938/n3/all/v/37/p/{anos_pib}/f/a"

try:
    r = requests.get(URL_PIB, timeout=120)
    if r.status_code == 200:
        dados_pib = r.json()
        pib_pd = pd.DataFrame(dados_pib[1:])
        print(f"PIB UF — {len(pib_pd)} registros")
    else:
        pib_pd = pd.DataFrame()
        print(f"Erro PIB: {r.status_code}")
except Exception as e:
    pib_pd = pd.DataFrame()
    print(f"Erro PIB: {e}")

pib_spark = spark.createDataFrame(pib_pd.astype(str))
pib_spark.write.format("delta").mode("overwrite").saveAsTable("bronze_pib_uf")
print("Tabela bronze_pib_uf salva.")


# =============================================================================
# CÉLULA 9 — MÓDULO 6: Populações por Faixa Etária (FTP IBGE)
# =============================================================================

print("\n===== MÓDULO 6: Populações por Faixa Etária (FTP IBGE) =====\n")

URL_IBGE_FTP = (
    "https://ftp.ibge.gov.br/Projecao_da_Populacao/"
    "Projecao_da_Populacao_2018/"
    "projecoes_2018_populacao_idade_simples_2010_2060_20201209.xls"
)

# UFs a excluir (agregados regionais)
ABAS_EXCLUIR = {
    "BRASIL", "Brasil", "Norte", "Nordeste", "Sudeste",
    "Sul", "Centro-Oeste", "Centro Oeste", "NORTE",
    "NORDESTE", "SUDESTE", "SUL", "CENTRO-OESTE", "BR"
}

anos_analise_set = set(range(ANO_INICIO_COLETA, ANO_FIM_COLETA + 1))

print("Baixando arquivo do FTP do IBGE (~15 MB)...")
try:
    r = requests.get(URL_IBGE_FTP, timeout=180)
    r.raise_for_status()
    arquivo_bytes = io.BytesIO(r.content)
    print("Download concluído!")
except Exception as e:
    raise RuntimeError(f"Erro no download do arquivo IBGE: {e}")

# Lê todas as abas
xls = pd.ExcelFile(arquivo_bytes, engine="xlrd")
abas_todas = xls.sheet_names
abas_uf    = [a for a in abas_todas if a.strip() not in ABAS_EXCLUIR]
print(f"Abas de UF identificadas: {len(abas_uf)}")


def ler_aba_uf(xls_file, aba, anos_interesse):
    """Lê uma aba do arquivo de projeções IBGE e retorna DataFrame longo."""
    try:
        df_raw = xls_file.parse(aba, header=None, dtype=str)

        # Localiza linhas com "total" (3 blocos: masculino, feminino, total geral)
        linhas_total = [
            i for i, v in enumerate(df_raw.iloc[:, 0])
            if isinstance(v, str) and v.strip().lower() == "total"
        ]

        if len(linhas_total) < 3:
            print(f"  [{aba}] Menos de 3 blocos TOTAL.")
            return None

        linha_total_geral = linhas_total[2]
        linha_anos        = linha_total_geral - 1

        anos_row = [
            str(v).replace(".0", "").strip()
            for v in df_raw.iloc[linha_anos]
        ]
        cols_interesse   = [j for j, a in enumerate(anos_row) if a in {str(x) for x in anos_interesse}]
        anos_encontrados = [anos_row[j] for j in cols_interesse]

        if not cols_interesse:
            print(f"  [{aba}] Anos não encontrados.")
            return None

        linha_inicio = linha_total_geral + 1
        linha_fim    = min(len(df_raw), linha_inicio + 101)

        bloco = df_raw.iloc[linha_inicio:linha_fim, [0] + cols_interesse].copy()
        bloco.columns = ["idade_raw"] + anos_encontrados

        bloco["idade"] = pd.to_numeric(
            bloco["idade_raw"].str.replace(r"\.0$", "", regex=True), errors="coerce"
        )
        bloco = bloco.dropna(subset=["idade"])
        bloco = bloco[(bloco["idade"] >= 0) & (bloco["idade"] <= 100)]
        bloco = bloco.drop(columns=["idade_raw"])

        long_df = bloco.melt(id_vars=["idade"], var_name="Ano", value_name="pop")
        long_df["pop"]   = pd.to_numeric(long_df["pop"].str.replace(r"[\s,]", "", regex=True), errors="coerce")
        long_df["Ano"]   = long_df["Ano"].astype(int)
        long_df["SIGLA"] = aba.strip()
        long_df = long_df.dropna(subset=["pop"])

        return long_df

    except Exception as e:
        print(f"  [{aba}] Erro: {e}")
        return None


print(f"Lendo {len(abas_uf)} abas...")
lista_dfs_pop = []
erros = []

for aba in abas_uf:
    df_aba = ler_aba_uf(xls, aba, anos_analise_set)
    if df_aba is not None:
        lista_dfs_pop.append(df_aba)
    else:
        erros.append(aba.strip())

print(f"Abas lidas com sucesso: {len(lista_dfs_pop)}/{len(abas_uf)}")
if erros:
    print(f"Com problema: {', '.join(erros)}")

pop_faixa_pd = pd.concat(lista_dfs_pop, ignore_index=True)
print(f"Total de linhas (faixa etária): {len(pop_faixa_pd)}")

pop_faixa_spark = spark.createDataFrame(pop_faixa_pd)
pop_faixa_spark.write.format("delta").mode("overwrite").saveAsTable("bronze_pop_faixa_etaria")
print("Tabela bronze_pop_faixa_etaria salva.")


# =============================================================================
# CÉLULA 10 — MÓDULO 7: Taxa de Homicídios via API IPEA Data (OData)
# =============================================================================

print("\n===== MÓDULO 7: Taxa de Homicídios (IPEA Data API) =====\n")

# OData REST oficial do IPEA Data (independe da biblioteca ipeadatapy,
# que está quebrada por causa de mudanças no schema de metadados).
# Código atual da série: AVIOL12_THOMIC (Taxa de homicídios por 100 mil hab.).
# O código antigo 'THOMIC' foi descontinuado.
URL_THOMIC = "http://www.ipeadata.gov.br/api/odata4/ValoresSerie(SERCODIGO='AVIOL12_THOMIC')"

try:
    r = requests.get(URL_THOMIC, timeout=180)
    r.raise_for_status()
    payload = r.json()
except Exception as e:
    raise RuntimeError(f"Erro ao consultar IPEA Data: {e}")

registros_thomic = payload.get("value", [])
if not registros_thomic:
    raise RuntimeError("IPEA Data retornou lista vazia para a série AVIOL12_THOMIC.")

df_thomic = pd.DataFrame(registros_thomic)
print(f"Série AVIOL12_THOMIC baixada: {df_thomic.shape}")

# Filtra somente nível Estado (a série traz Brasil, Regiões, Estados, Municípios etc.)
df_estados = df_thomic[df_thomic["NIVNOME"] == "Estados"].copy()
df_estados["TERCODIGO"] = pd.to_numeric(df_estados["TERCODIGO"], errors="coerce")

# Padroniza para o esquema que a Silver espera (Período, Região_ID, Valor)
homic_raw_pd = df_estados.rename(columns={
    "VALDATA":   "Período",
    "TERCODIGO": "Região_ID",
    "VALVALOR":  "Valor",
})[["Período", "Região_ID", "Valor"]]

print(f"Linhas estaduais: {len(homic_raw_pd)}")

homic_spark = spark.createDataFrame(homic_raw_pd.astype(str))
homic_spark.write.format("delta").mode("overwrite").saveAsTable("bronze_homicidios")
print("Tabela bronze_homicidios salva.")


# =============================================================================
# CÉLULA 11 — MÓDULO 8: Códigos Contábeis (Ingestão Manual via Excel)
# =============================================================================

print("\n===== MÓDULO 8: Códigos Contábeis (Excel) =====\n")

# OBS: Faça o upload do arquivo Excel para o Lakehouse antes de executar.
# Caminho no ABFS: Lakehouse > Files > dados > Dados - Novo FPE.xlsx
# Lê diretamente do OneLake via pandas (suportado no Fabric)

try:
    codigos_pd = pd.read_excel(ARQUIVO_CODIGOS, sheet_name=ABA_CODIGOS, skiprows=1)
    print(f"Códigos contábeis carregados: {codigos_pd.shape}")
except Exception as e:
    raise RuntimeError(
        f"Erro ao ler arquivo de códigos contábeis.\n"
        f"Certifique-se de que o arquivo foi carregado no Lakehouse em:\n"
        f"Files/dados/Dados - Novo FPE.xlsx\n"
        f"Erro original: {e}"
    )

# Remove colunas completamente vazias (colunas "Unnamed" sem dados do Excel)
codigos_pd = codigos_pd.dropna(axis=1, how="all")

# Sanitiza nomes de colunas: remove caracteres inválidos para Delta Tables
_INVALIDOS = str.maketrans(" ,;{}()\n\t=", "__________")
codigos_pd.columns = [
    str(c).strip().translate(_INVALIDOS)
    for c in codigos_pd.columns
]

codigos_spark = spark.createDataFrame(codigos_pd.astype(str))
codigos_spark.write.format("delta").mode("overwrite").saveAsTable("bronze_codigos_contabeis")
print("Tabela bronze_codigos_contabeis salva.")


# =============================================================================
# CÉLULA 12 — Resumo das tabelas criadas
# =============================================================================

tabelas_bronze = [
    "bronze_siconfi_raw",
    "bronze_fcdf_raw",
    "bronze_populacao_total",
    "bronze_ipca",
    "bronze_pib_uf",
    "bronze_pop_faixa_etaria",
    "bronze_homicidios",
    "bronze_codigos_contabeis",
]

print("\n========== RESUMO BRONZE ==========")
for tabela in tabelas_bronze:
    try:
        n = spark.table(tabela).count()
        print(f"  {tabela}: {n:,} linhas")
    except Exception as e:
        print(f"  {tabela}: ERRO ({e})")

print("\nNotebook Bronze concluído.")
