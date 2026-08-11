# =============================================================================
#
#   HIATO FISCAL DAS UNIDADES DA FEDERAÇÃO DO BRASIL
#   Script unificado — Capacidade Fiscal + Necessidade de Gasto
#
#   Ordem de execução dos módulos:
#     1. Configurações do usuário
#     2. Pacotes e opções globais
#     3. Tabelas auxiliares
#     4. [MÓDULO 1] Coleta de receitas e despesas — API Siconfi
#     5. [MÓDULO 2] Fundo Constitucional do DF — API Portal da Transparência
#     6. [MÓDULO 3] Capacidade fiscal dos estados
#     7. [MÓDULO 4] Populações por faixa etária específica — IBGE
#     8. [MÓDULO 5] Necessidade de gasto
#
# =============================================================================
# Garante uma biblioteca de usuário gravável e a coloca no topo do libPath
user_lib <- Sys.getenv("R_LIBS_USER", unset = file.path("~", "R-libs"))
if (!dir.exists(user_lib)) dir.create(user_lib, recursive = TRUE)
.libPaths(c(user_lib, .libPaths()))

# =============================================================================
# ██████████████████████████████████████████████████████████████████████████
#  SEÇÃO DE CONFIGURAÇÕES DO USUÁRIO — EDITE APENAS AQUI
# ██████████████████████████████████████████████████████████████████████████
# =============================================================================

# ── Diretório de trabalho (onde os outputs serão salvos) ────────────────────
DIR_TRABALHO <- "C:/Users/carlos.marchionatti/OneDrive - Tesouro Nacional/VSCode/STN/FPE - R"

# ── Planilha de códigos contábeis ───────────────────────────────────────────
ARQUIVO_CODIGOS <- file.path(DIR_TRABALHO, "Dados - Novo FPE.xlsx")
ABA_CODIGOS     <- "Códigos Contábeis - Receitas"

# ── Período de coleta dos dados brutos (anos a buscar nas APIs) ─────────────
#    Altere os valores abaixo para expandir ou restringir a coleta.
ANO_INICIO_COLETA <- 2019
ANO_FIM_COLETA    <- 2022

# ── Período de análise (subconjunto do período de coleta usado nos cálculos) ─
#    Deve estar dentro do intervalo [ANO_INICIO_COLETA, ANO_FIM_COLETA].
ANO_INICIO_ANALISE <- 2019
ANO_FIM_ANALISE    <- 2022

# ── Ano-base para deflação (IPCA de dezembro) ────────────────────────────────
#    Deve estar dentro do período de coleta.
ANO_BASE_DEFLACAO <- 2022

# ── Tipo de despesa a utilizar na análise ────────────────────────────────────
#    Opções: "Despesas Empenhadas" | "Despesas Liquidadas" | "Despesas Pagas"
TIPO_DESPESA <- "Despesas Empenhadas"

# =============================================================================
#  FIM DAS CONFIGURAÇÕES DO USUÁRIO
# =============================================================================


# =============================================================================
# Iniciando o timer da rotina completa
tempo_inicio <- proc.time()

# =============================================================================
# 1. PACOTES E OPÇÕES GLOBAIS
# =============================================================================

pacotes_necessarios <- c(
  "httr", "jsonlite", "dplyr", "stringr",
  "readr", "tidyverse", "readxl", "data.table",
  "plm", "writexl", "sidrar",
  "ipeadatar", "lubridate", "purrr",
  "tidyr", "readxl"
)

for (pkg in pacotes_necessarios) {
  if (!requireNamespace(pkg, quietly = TRUE)) install.packages(pkg)
  library(pkg, character.only = TRUE)
}

options(scipen = 999)   # remove notação científica
options(timeout = 300)  # aumenta timeout para downloads pesados

setwd(DIR_TRABALHO)

# Derivados das configurações do usuário
periodo_coleta  <- ANO_INICIO_COLETA:ANO_FIM_COLETA
periodo_analise <- ANO_INICIO_ANALISE:ANO_FIM_ANALISE
periodo_str     <- paste0(ANO_INICIO_COLETA, "_", ANO_FIM_COLETA)

# Coluna do deflator a usar, gerada dinamicamente com base em ANO_BASE_DEFLACAO
col_deflator <- paste0("deflator_dez_", ANO_BASE_DEFLACAO %% 100)


# =============================================================================
# 2. TABELAS AUXILIARES COMUNS
# =============================================================================

tabela_uf <- data.frame(
  codigo = c(
    11, 12, 13, 14, 15, 16, 17,
    21, 22, 23, 24, 25, 26, 27, 28, 29,
    31, 32, 33, 35,
    41, 42, 43,
    50, 51, 52, 53
  ),
  uf = c(
    "RO", "AC", "AM", "RR", "PA", "AP", "TO",
    "MA", "PI", "CE", "RN", "PB", "PE", "AL", "SE", "BA",
    "MG", "ES", "RJ", "SP",
    "PR", "SC", "RS",
    "MS", "MT", "GO", "DF"
  ),
  nome_uf = c(
    "Rondônia", "Acre", "Amazonas", "Roraima", "Pará", "Amapá", "Tocantins",
    "Maranhão", "Piauí", "Ceará", "Rio Grande do Norte", "Paraíba",
    "Pernambuco", "Alagoas", "Sergipe", "Bahia",
    "Minas Gerais", "Espírito Santo", "Rio de Janeiro", "São Paulo",
    "Paraná", "Santa Catarina", "Rio Grande do Sul",
    "Mato Grosso do Sul", "Mato Grosso", "Goiás", "Distrito Federal"
  ),
  stringsAsFactors = FALSE
)

# Mapeamento código IBGE → sigla (usado em scripts de necessidade de gasto)
uf_map <- tibble::tibble(
  tcode = tabela_uf$codigo,
  UF    = tabela_uf$uf
)

# Função auxiliar: converte nome completo da UF em sigla
nome_para_sigla <- function(nome_col) {
  case_when(
    nome_col == "Acre"                 ~ "AC",
    nome_col == "Alagoas"              ~ "AL",
    nome_col == "Amapá"               ~ "AP",
    nome_col == "Amazonas"             ~ "AM",
    nome_col == "Bahia"                ~ "BA",
    nome_col == "Ceará"               ~ "CE",
    nome_col == "Distrito Federal"     ~ "DF",
    nome_col == "Espírito Santo"      ~ "ES",
    nome_col == "Goiás"              ~ "GO",
    nome_col == "Maranhão"            ~ "MA",
    nome_col == "Mato Grosso"          ~ "MT",
    nome_col == "Mato Grosso do Sul"   ~ "MS",
    nome_col == "Minas Gerais"         ~ "MG",
    nome_col == "Pará"               ~ "PA",
    nome_col == "Paraíba"            ~ "PB",
    nome_col == "Paraná"             ~ "PR",
    nome_col == "Pernambuco"           ~ "PE",
    nome_col == "Piauí"              ~ "PI",
    nome_col == "Rio de Janeiro"       ~ "RJ",
    nome_col == "Rio Grande do Norte"  ~ "RN",
    nome_col == "Rio Grande do Sul"    ~ "RS",
    nome_col == "Rondônia"           ~ "RO",
    nome_col == "Roraima"              ~ "RR",
    nome_col == "Santa Catarina"       ~ "SC",
    nome_col == "São Paulo"           ~ "SP",
    nome_col == "Sergipe"              ~ "SE",
    nome_col == "Tocantins"            ~ "TO"
  )
}


# =============================================================================
# ── MÓDULO 1: SICONFI — Receitas e Despesas via API ─────────────────────────
# =============================================================================

message("\n===== MÓDULO 1: Coleta Siconfi =====\n")

codigos_ibge <- tabela_uf$codigo
base_url_siconfi <- "https://apidatalake.tesouro.gov.br/ords/siconfi/tt/dca"

respostas_brutas <- list()
dados_parsed     <- list()

for (ano in periodo_coleta) {
  for (cod in codigos_ibge) {
    chave <- paste0(ano, "_", cod)
    cat("-> Consultando:", chave, "...\n")

    resp <- tryCatch(
      GET(base_url_siconfi,
          query   = list(an_exercicio = as.character(ano),
                         id_ente      = as.character(cod)),
          timeout(30)),
      error = function(e) e
    )

    respostas_brutas[[chave]] <- resp

    if (inherits(resp, "error")) {
      message("  ERRO de conexão para ", chave, ": ", resp$message)
      dados_parsed[[chave]] <- list(ok = FALSE, reason = resp$message, df = data.frame())
      Sys.sleep(0.12); next
    }

    sc <- status_code(resp)
    if (sc != 200) {
      message("  Status ", sc, " para ", chave)
      dados_parsed[[chave]] <- list(ok = FALSE, reason = paste0("status_", sc), df = data.frame())
      Sys.sleep(0.12); next
    }

    txt    <- content(resp, as = "text", encoding = "UTF-8")
    parsed <- tryCatch(fromJSON(txt, flatten = TRUE), error = function(e) e)

    if (inherits(parsed, "error")) {
      message("  JSON inválido para ", chave, ": ", parsed$message)
      dados_parsed[[chave]] <- list(ok = FALSE, reason = parsed$message, df = data.frame())
      Sys.sleep(0.12); next
    }

    if (!("items" %in% names(parsed)) || length(parsed$items) == 0) {
      message("  Sem dados (items vazio) para ", chave)
      dados_parsed[[chave]] <- list(ok = TRUE, reason = "empty", df = data.frame())
      Sys.sleep(0.12); next
    }

    df <- as.data.frame(parsed$items, stringsAsFactors = FALSE)
    if (nrow(df) > 0) { df$ano <- ano; df$id_ente <- cod }

    dados_parsed[[chave]] <- list(ok = TRUE, reason = "ok", df = df)
    cat("  OK — linhas:", nrow(df), "\n")
    Sys.sleep(0.12)
  }
}

# Consolidando em um único data frame
lista_dfs <- Filter(Negate(is.null),
                    lapply(dados_parsed, function(x) {
                      if (is.list(x) && !is.null(x$df) && nrow(x$df) > 0) x$df else NULL
                    }))

if (length(lista_dfs) > 0) {
  todos_df <- do.call(rbind, lista_dfs)
  cat("Total de linhas agregadas (Siconfi):", nrow(todos_df), "\n")
} else {
  todos_df <- data.frame()
  cat("Nenhum dado para agregar (todos vazios/erro).\n")
}

# ── Receitas ─────────────────────────────────────────────────────────────────
receitas_siconfi_api <- todos_df %>%
  filter(anexo == "DCA-Anexo I-C", coluna == "Receitas Brutas Realizadas") %>%
  select(exercicio, uf, conta, coluna, valor) %>%
  rename(AN_EXERCICIO   = exercicio,
         SG_ENTE        = uf,
         ELEMENTLABEL   = conta,
         NO_LABEL_EIXO_X = coluna,
         VALUE          = valor)

# ── Despesas ─────────────────────────────────────────────────────────────────
dados_gastos <- todos_df %>%
  filter(anexo == "DCA-Anexo I-E", coluna == "Despesas Empenhadas") %>%
  select(exercicio, uf, rotulo, coluna, conta, valor, instituicao, id_ente, populacao) %>%
  rename(Ano            = exercicio,
         UF             = uf,
         ELEMENTLABEL   = conta,
         NO_LABEL_EIXO_X = coluna,
         NO_LABEL_EIXO_Y = conta,   # nota: renomeação dupla; mantida do original
         VALUE          = valor,
         NO_ORGAO       = instituicao,
         NO_ENTE        = id_ente,
         QT_HABITANTE   = populacao)

# ── Salvando workspace Siconfi ────────────────────────────────────────────────
save(receitas_siconfi_api, dados_gastos,
     file = paste0("workspace_siconfi_", periodo_str, ".RData"))
message("Workspace Siconfi salvo.")


# =============================================================================
# ── MÓDULO 2: FCDF — Fundo Constitucional do DF via API ─────────────────────
# =============================================================================

message("\n===== MÓDULO 2: Fundo Constitucional do DF =====\n")

resultados_fcdf <- list()

for (ano in periodo_coleta) {
  r <- GET(
    "https://portaldatransparencia.gov.br/orgaos/distribuicao-execucao-orcamentaria-financeira/resultadoGrafico",
    query = list(
      tipoDespesa   = "Pagamentos realizados",
      codigoOrgao   = "25915",
      isOrgaoMaximo = "false",
      ano           = ano
    )
  )

  texto <- content(r, as = "text", encoding = "UTF-8")

  if (status_code(r) == 200 && texto != "[]") {
    dados_r          <- fromJSON(texto)
    dados_r$ano      <- ano
    resultados_fcdf[[as.character(ano)]] <- dados_r
    cat("Ano", ano, "- OK\n")
  } else {
    cat("Ano", ano, "- Sem dados ou erro:", status_code(r), "\n")
  }
}

df_fcdf_raw <- do.call(rbind, resultados_fcdf)

fcdf <- df_fcdf_raw %>%
  filter(str_detect(valores, "próprio órgão")) %>%
  mutate(
    Valor = valores %>%
      str_extract("R\\$[\\s\\d\\.\\,]+") %>%
      str_remove("R\\$") %>%
      str_trim() %>%
      str_remove_all("\\.") %>%
      str_replace(",", ".") %>%
      as.numeric()
  ) %>%
  transmute(
    Ano       = ano,
    Descrição = "FCDF",
    Valor     = Valor
  )

message("FCDF coletado. Linhas: ", nrow(fcdf))


# =============================================================================
# ── MÓDULO 3: CAPACIDADE FISCAL DOS ESTADOS ──────────────────────────────────
# =============================================================================

message("\n===== MÓDULO 3: Capacidade Fiscal =====\n")

# OBS: Usamos receita bruta no lugar de receita líquida.
# OBS: Estimativas populacionais do Siconfi DIFERENTES do IBGE.
# OBS: Não usamos heranças para estimar ITCD.

# ── Códigos contábeis ─────────────────────────────────────────────────────────
codigos_contabeis <- read_excel(ARQUIVO_CODIGOS,
                                sheet = ABA_CODIGOS,
                                skip  = 1)

# Filtra apenas linhas com dados completos e seleciona apenas os anos de análise
codigos_contabeis <- codigos_contabeis[complete.cases(codigos_contabeis[[as.character(ANO_INICIO_ANALISE)]]), ]
codigos_contabeis <- select(codigos_contabeis,
                             Imposto,
                             all_of(as.character(periodo_analise)))

# ── Populações totais por UF — API Sidra ─────────────────────────────────────
# OBS: a série 6579 não tem 2022 e 2023; complementamos com o Censo (tabela 4714).
pop_raw <- get_sidra(
  x        = 6579,
  variable = "9324",
  period   = as.character(periodo_coleta),
  geo      = "State"
)

pop_raw_2022 <- get_sidra(
  x        = 4714,
  variable = "93",
  period   = "2022",
  geo      = "State"
)

pop_raw <- rbind(pop_raw, pop_raw_2022)
rm(pop_raw_2022)

populacao_long <- pop_raw %>%
  select(uf_nome = `Unidade da Federação`, Ano = Ano, populacao_total = Valor) %>%
  mutate(SIGLA = nome_para_sigla(uf_nome)) %>%
  select(SIGLA, Ano, populacao_total) %>%
  arrange(SIGLA, Ano) %>%
  mutate(Ano = as.numeric(Ano))

# ── IPCA mensal (dezembro) — API Sidra ───────────────────────────────────────
ipca_raw <- get_sidra(api = "/t/1737/n1/all/v/2266/p/201401-202412")

ipca_tratado <- ipca_raw %>%
  select(mes_ano = `Mês`, ipca_mensal = Valor) %>%
  mutate(
    Ano        = as.integer(str_extract(mes_ano, "\\d{4}")),
    mes        = str_remove(mes_ano, "\\s\\d{4}$"),
    ipca_mensal = as.numeric(ipca_mensal)
  ) %>%
  filter(mes == "dezembro")

# Gera deflator para o ano-base escolhido pelo usuário
ipca_base <- ipca_tratado %>% filter(Ano == ANO_BASE_DEFLACAO) %>% pull(ipca_mensal)

ipca <- ipca_tratado %>%
  mutate(
    deflator_dez_22 = ipca_base / ipca_mensal,
    deflator_dez_19 = (ipca_tratado %>% filter(Ano == 2019) %>% pull(ipca_mensal)) / ipca_mensal,
    deflator_dez_18 = (ipca_tratado %>% filter(Ano == 2018) %>% pull(ipca_mensal)) / ipca_mensal
  )

# ── PIB nominal por UF — API Sidra ───────────────────────────────────────────
pib_uf_raw <- get_sidra(
  x        = 5938,
  variable = "37",
  period   = as.character(periodo_coleta),
  geo      = "State"
)

pib_long <- pib_uf_raw %>%
  select(Estado = `Unidade da Federação`, ano = Ano, pib = Valor) %>%
  mutate(Sigla = nome_para_sigla(Estado)) %>%
  select(Sigla, Estado, ano, pib) %>%
  arrange(Sigla, ano) %>%
  mutate(ano    = as.numeric(as.character(ano)),
         Estado = as.factor(Estado))

# ── Receitas Siconfi + deflação + população + PIB ────────────────────────────
receitas_siconfi <- receitas_siconfi_api
receitas_siconfi$SG_ENTE <- as.factor(receitas_siconfi$SG_ENTE)

receitas_siconfi <- receitas_siconfi %>%
  left_join(select(ipca, Ano, deflator_dez_22), by = c("AN_EXERCICIO" = "Ano")) %>%
  left_join(populacao_long, by = c("AN_EXERCICIO" = "Ano", "SG_ENTE" = "SIGLA")) %>%
  mutate(VALUE = VALUE * deflator_dez_22)

dados_pib <- receitas_siconfi %>%
  left_join(pib_long, by = c("AN_EXERCICIO" = "ano", "SG_ENTE" = "Sigla")) %>%
  mutate(pib_per_capita = pib * 1000 / populacao_total)

# ── Transferências (calculadas de forma residual) ────────────────────────────
codigos_transferencias <- filter(codigos_contabeis, Imposto == "Transferências Totais")
codigos_transf_vec     <- unlist(codigos_transferencias[1, -1])

codigos_transf_deducao <- filter(codigos_contabeis, Imposto == "Subtração FPE")
codigos_transf_ded_vec <- unlist(codigos_transf_deducao[1, -1])

dados_transferencias <- dados_pib[grepl(paste(codigos_transf_vec, collapse = "|"),
                                        dados_pib$ELEMENTLABEL), ]

dados_transferencias_deducao <- dados_pib[grepl(paste(codigos_transf_ded_vec, collapse = "|"),
                                                 dados_pib$ELEMENTLABEL), ]

dados_transferencias_agregado <- dados_transferencias %>%
  group_by(SG_ENTE) %>%
  summarize(VALUE = mean(VALUE))

dados_transferencias_deducao_agregado <- dados_transferencias_deducao %>%
  group_by(SG_ENTE) %>%
  summarize(VALUE = mean(VALUE))

dados_pib <- dados_pib %>%
  left_join(select(dados_transferencias, AN_EXERCICIO, SG_ENTE, VALUE),
            by = c("SG_ENTE", "AN_EXERCICIO")) %>%
  rename(VALUE = VALUE.x, Transferencias = VALUE.y) %>%
  mutate(tti = (pib * 1000 - Transferencias) / 1000)

# ── IRRF Servidores ───────────────────────────────────────────────────────────
codigos_ir     <- unlist(filter(codigos_contabeis, Imposto == "IRRF servidores")[1, -1])
dados_ir       <- dados_pib[grepl(paste(codigos_ir, collapse = "|"), dados_pib$ELEMENTLABEL), ]
dados_ir$VALUE2 <- dados_ir$VALUE
dados_ir_2022  <- select(filter(dados_ir, AN_EXERCICIO == ANO_FIM_ANALISE), SG_ENTE, VALUE2)

# ── ICMS ──────────────────────────────────────────────────────────────────────
# Desconto: 20% FUNDEB + 25% municípios → fator líquido = 0,60
codigos_icms  <- unlist(filter(codigos_contabeis, Imposto == "ICMS")[1, -1])
dados_icms    <- dados_pib[grepl(paste(codigos_icms, collapse = "|"), dados_pib$ELEMENTLABEL), ]
dados_icms$VALUE2 <- dados_icms$VALUE * 0.6

dados_icms_medio <- dados_icms %>%
  group_by(SG_ENTE) %>%
  summarize(VALUE2 = mean(VALUE2)) %>%
  left_join(select(filter(dados_icms, AN_EXERCICIO == ANO_FIM_ANALISE), SG_ENTE, VALUE2),
            by = "SG_ENTE")

# ── IPVA ──────────────────────────────────────────────────────────────────────
# Desconto: 20% FUNDEB + 50% municípios → fator líquido = 0,40
codigos_ipva  <- unlist(filter(codigos_contabeis, Imposto == "IPVA")[1, -1])
dados_ipva    <- dados_pib[grepl(paste(codigos_ipva, collapse = "|"), dados_pib$ELEMENTLABEL), ]
dados_ipva$VALUE2 <- dados_ipva$VALUE * 0.4

dados_ipva_medio <- dados_ipva %>%
  group_by(SG_ENTE) %>%
  summarize(VALUE2 = mean(VALUE2)) %>%
  left_join(select(filter(dados_ipva, AN_EXERCICIO == ANO_FIM_ANALISE), SG_ENTE, VALUE2),
            by = "SG_ENTE")

# ── ITCD ──────────────────────────────────────────────────────────────────────
# Desconto: 20% FUNDEB → fator líquido = 0,80
codigos_itcd  <- unlist(filter(codigos_contabeis, Imposto == "ITCD")[1, -1])
dados_itcd    <- dados_pib[grepl(paste(codigos_itcd, collapse = "|"), dados_pib$ELEMENTLABEL), ]
dados_itcd$VALUE2 <- dados_itcd$VALUE * 0.8

dados_itcd_medio <- dados_itcd %>%
  group_by(SG_ENTE) %>%
  summarize(VALUE2 = mean(VALUE2)) %>%
  left_join(select(filter(dados_itcd, AN_EXERCICIO == ANO_FIM_ANALISE), SG_ENTE, VALUE2),
            by = "SG_ENTE")

# ── Previdência Estadual ──────────────────────────────────────────────────────
codigos_prev <- unlist(filter(codigos_contabeis,
                               Imposto == "Contribuições para  Regimes Próprios de Previdência")[1, -1])
dados_prev   <- dados_pib[grepl(paste(codigos_prev, collapse = "|"), dados_pib$ELEMENTLABEL), ]

dados_prev_medio <- dados_prev %>%
  group_by(SG_ENTE) %>%
  summarize(VALUE = mean(VALUE)) %>%
  left_join(select(filter(dados_prev, AN_EXERCICIO == ANO_FIM_ANALISE), SG_ENTE, VALUE),
            by = "SG_ENTE")

# ── Outras Receitas ───────────────────────────────────────────────────────────
impostos_outros <- c("Taxas", "Outras Contribuições Sociais", "Receitas Patrimonial",
                     "Receita Agropecuária", "Receita Industrial",
                     "Receita de Serviços", "Outras receitas correntes")

codigos_outros_df <- filter(codigos_contabeis, Imposto %in% impostos_outros)
codigos_outros    <- unlist(codigos_outros_df[, -1])

dados_outros <- dados_pib[grepl(paste(codigos_outros, collapse = "|"), dados_pib$ELEMENTLABEL), ] %>%
  group_by(SG_ENTE, AN_EXERCICIO) %>%
  summarize(VALUE = sum(VALUE), pib = mean(pib),
            populacao_total = mean(populacao_total),
            Transferencias  = mean(Transferencias),
            tti             = mean(tti)) %>%
  mutate(pib_per_capita = pib * 1000 / populacao_total)

dados_outros_medio <- dados_outros %>%
  group_by(SG_ENTE) %>%
  summarize(VALUE = mean(VALUE)) %>%
  left_join(select(filter(dados_outros, AN_EXERCICIO == ANO_FIM_ANALISE), SG_ENTE, VALUE),
            by = "SG_ENTE")

# ── Receitas exclusivas do DF (município + estado) ────────────────────────────
fcdf <- fcdf[complete.cases(fcdf$Valor), ]
fcdf$Ano <- as.numeric(fcdf$Ano)

populacao_long_df <- filter(populacao_long, SIGLA == "DF")
fcdf <- fcdf %>%
  left_join(select(populacao_long_df, Ano, populacao_total), by = "Ano") %>%
  mutate(VALUE = Valor / populacao_total)

impostos_df     <- c("ISSQN", "IPTU", "ITR", "ITBI")
codigos_df_linhas <- filter(codigos_contabeis, Imposto %in% impostos_df)
codigos_df        <- unlist(codigos_df_linhas[, -1])

dados_df <- dados_pib[grepl(paste(codigos_df, collapse = "|"), dados_pib$ELEMENTLABEL), ] %>%
  filter(SG_ENTE == "DF") %>%
  group_by(AN_EXERCICIO, SG_ENTE) %>%
  summarize(VALUE = sum(VALUE))

dados_df_agregado <- dados_df %>%
  left_join(select(fcdf, Ano, Valor), by = c("AN_EXERCICIO" = "Ano")) %>%
  mutate(total = VALUE + Valor)

dados_df_agregado_ano_fim <- select(filter(dados_df_agregado, AN_EXERCICIO == ANO_FIM_ANALISE),
                                    SG_ENTE, total)

# ── Regressões para receita potencial ────────────────────────────────────────

# PIB médio e tti do ano de referência
media_pib <- aggregate(pib ~ SG_ENTE, data = dados_pib, FUN = mean)
media_pib <- media_pib %>%
  left_join(select(filter(dados_icms, AN_EXERCICIO == ANO_FIM_ANALISE), SG_ENTE, pib),
            by = "SG_ENTE") %>%
  rename(pib = pib.x, pib_ano_fim = pib.y) %>%
  left_join(select(filter(dados_icms, AN_EXERCICIO == ANO_FIM_ANALISE), SG_ENTE, tti),
            by = "SG_ENTE") %>%
  rename(tti_ano_fim = tti)

# ICMS
modelo_icms_pooled <- plm(VALUE2 ~ tti, model = "pooling",
                           index = c("SG_ENTE", "AN_EXERCICIO"), data = dados_icms)
summary(modelo_icms_pooled)

fitted_final_icms <- data.frame(
  SG_ENTE           = media_pib$SG_ENTE,
  Fitted_Pooled_icms = coef(modelo_icms_pooled)[1] +
    media_pib$tti_ano_fim * coef(modelo_icms_pooled)[2]
)

# IPVA (sem intercepto — estimativa negativa)
modelo_ipva_pooled <- plm(VALUE2 ~ tti - 1, model = "pooling",
                           index = c("SG_ENTE", "AN_EXERCICIO"), data = dados_ipva)
summary(modelo_ipva_pooled)

fitted_final_ipva <- data.frame(
  SG_ENTE           = media_pib$SG_ENTE,
  Fitted_Pooled_ipva = media_pib$tti_ano_fim * coef(modelo_ipva_pooled)[1]
)

# ITCD (sem intercepto — estimativa negativa)
modelo_itcd_pooled <- plm(VALUE2 ~ tti - 1, model = "pooling",
                           index = c("SG_ENTE", "AN_EXERCICIO"), data = dados_itcd)
summary(modelo_itcd_pooled)

fitted_final_itcd <- data.frame(
  SG_ENTE           = media_pib$SG_ENTE,
  Fitted_Pooled_itcd = media_pib$tti_ano_fim * coef(modelo_itcd_pooled)[1]
)

# Previdência
# OBS: ajuste necessário pois havia códigos duplicados para RR (2020-2021), ES (2019) e RN (2019)
modelo_prev_pooled <- plm(VALUE ~ tti, model = "pooling",
                           index = c("SG_ENTE", "AN_EXERCICIO"), data = dados_prev)
summary(modelo_prev_pooled)

fitted_final_prev <- data.frame(
  SG_ENTE           = media_pib$SG_ENTE,
  Fitted_Pooled_prev = coef(modelo_prev_pooled)[1] +
    media_pib$tti_ano_fim * coef(modelo_prev_pooled)[2]
)

# Outras
modelo_outros_pooled <- plm(VALUE ~ tti, model = "pooling",
                             index = c("SG_ENTE", "AN_EXERCICIO"), data = dados_outros)
summary(modelo_outros_pooled)

fitted_final_outros <- data.frame(
  SG_ENTE             = media_pib$SG_ENTE,
  Fitted_Pooled_outros = coef(modelo_outros_pooled)[1] +
    media_pib$tti_ano_fim * coef(modelo_outros_pooled)[2]
)

# ── Compilando capacidade fiscal ──────────────────────────────────────────────
dados_estimados <- reduce(
  list(fitted_final_icms, fitted_final_ipva, fitted_final_itcd,
       fitted_final_prev, fitted_final_outros),
  left_join, by = "SG_ENTE"
)

tabela_final <- dados_estimados
tabela_final$Fitted_Pooled_Total <-
  rowSums(tabela_final[, grep("Fitted_Pooled", colnames(tabela_final))], na.rm = TRUE)
tabela_final <- tabela_final[, c("SG_ENTE", "Fitted_Pooled_Total")]

tabela_final <- tabela_final %>%
  left_join(dados_transferencias_agregado,       by = "SG_ENTE") %>%
  left_join(dados_df_agregado_ano_fim,           by = "SG_ENTE") %>%
  left_join(dados_transferencias_deducao_agregado, by = "SG_ENTE") %>%
  left_join(dados_ir_2022,                       by = "SG_ENTE")

colnames(tabela_final)[colnames(tabela_final) == "VALUE.x"]  <- "Transferencias"
colnames(tabela_final)[colnames(tabela_final) == "total"]    <- "Receitas_DF"
colnames(tabela_final)[colnames(tabela_final) == "VALUE.y"]  <- "Transferencias_deducao"
colnames(tabela_final)[colnames(tabela_final) == "VALUE2"]   <- "IR_Servidores"

tabela_final[is.na(tabela_final)] <- 0
tabela_final <- select(tabela_final, -any_of("AN_EXERCICIO"))

tabela_final <- mutate(tabela_final,
  total_pooled = Fitted_Pooled_Total + Transferencias + Receitas_DF -
    Transferencias_deducao + IR_Servidores
)

# Compilando estimativas médias por imposto
names(dados_icms_medio)[names(dados_icms_medio) == "VALUE2.x"] <- "ICMS_MEDIO"
names(dados_ipva_medio)[names(dados_ipva_medio) == "VALUE2.x"] <- "IPVA_MEDIO"
names(dados_itcd_medio)[names(dados_itcd_medio) == "VALUE2.x"] <- "ITCD_MEDIO"
names(dados_prev_medio)[names(dados_prev_medio) == "VALUE.x"]  <- "PREV_MEDIO"
names(dados_outros_medio)[names(dados_outros_medio) == "VALUE.x"] <- "OUTROS_MEDIO"

names(dados_icms_medio)[names(dados_icms_medio) == "VALUE2.y"] <- paste0("ICMS_",  ANO_FIM_ANALISE)
names(dados_ipva_medio)[names(dados_ipva_medio) == "VALUE2.y"] <- paste0("IPVA_",  ANO_FIM_ANALISE)
names(dados_itcd_medio)[names(dados_itcd_medio) == "VALUE2.y"] <- paste0("ITCD_",  ANO_FIM_ANALISE)
names(dados_prev_medio)[names(dados_prev_medio) == "VALUE.y"]  <- paste0("PREV_",  ANO_FIM_ANALISE)
names(dados_outros_medio)[names(dados_outros_medio) == "VALUE.y"] <- paste0("OUTROS_", ANO_FIM_ANALISE)

dados_total_medio <- reduce(
  list(dados_icms_medio, dados_ipva_medio, dados_itcd_medio,
       dados_prev_medio, dados_outros_medio),
  left_join, by = "SG_ENTE"
)

receita_estimativas <- left_join(dados_total_medio, dados_estimados, by = "SG_ENTE") %>%
  mutate(Total_ano_fim = rowSums(select(.,
    starts_with("ICMS_") & ends_with(as.character(ANO_FIM_ANALISE)),
    starts_with("IPVA_") & ends_with(as.character(ANO_FIM_ANALISE)),
    starts_with("ITCD_") & ends_with(as.character(ANO_FIM_ANALISE)),
    starts_with("PREV_") & ends_with(as.character(ANO_FIM_ANALISE)),
    starts_with("OUTROS_") & ends_with(as.character(ANO_FIM_ANALISE))
  ), na.rm = TRUE))

# ── Exportando resultados de capacidade fiscal ────────────────────────────────
write_xlsx(tabela_final,       "dados_final_capacidade_fiscal_api.xlsx")
write_xlsx(receita_estimativas,"dados_final_receita_media_api.xlsx")
message("Arquivos de capacidade fiscal exportados.")


# =============================================================================
# ── MÓDULO 4: POPULAÇÕES POR FAIXA ETÁRIA ESPECÍFICA ────────────────────────
# =============================================================================

message("\n===== MÓDULO 4: Populações específicas (IBGE) =====\n")

# Download do arquivo de projeção IBGE (~15 MB)
url_ibge <- paste0(
  "https://ftp.ibge.gov.br/Projecao_da_Populacao/",
  "Projecao_da_Populacao_2018/",
  "projecoes_2018_populacao_idade_simples_2010_2060_20201209.xls"
)

arquivo_local <- tempfile(fileext = ".xls")

message("Baixando arquivo do FTP do IBGE (~15 MB)...")
resp_ibge <- GET(url_ibge, write_disk(arquivo_local, overwrite = TRUE),
                 timeout(120), progress())
if (http_error(resp_ibge)) stop("Erro no download do arquivo IBGE.")
message("Download concluído!")

# Identificando abas de UF
abas          <- excel_sheets(arquivo_local)
abas_excluir  <- c("BRASIL", "Brasil", "Norte", "Nordeste", "Sudeste",
                   "Sul", "Centro-Oeste", "Centro Oeste", "NORTE",
                   "NORDESTE", "SUDESTE", "SUL", "CENTRO-OESTE", "BR")
# Mantemos os nomes originais das abas (com possíveis espaços) para leitura correta
# O str_trim() é aplicado apenas na coluna SIGLA dentro da função ler_aba_uf
abas_uf       <- abas[!abas %in% abas_excluir]
message("Abas de UF identificadas: ", length(abas_uf))

# Função leitora de cada aba
# Os anos lidos são definidos pelo período de análise do usuário
anos_analise_chr <- as.character(periodo_analise)

ler_aba_uf <- function(aba) {
  tryCatch({
    df_raw <- read_xls(arquivo_local, sheet = aba,
                       col_names = FALSE, col_types = "text")

    linhas_total <- which(
      sapply(df_raw[[1]], function(x)
        !is.na(x) && grepl("^total$", x, ignore.case = TRUE))
    )

    if (length(linhas_total) < 3) {
      warning("[", aba, "] Menos de 3 blocos TOTAL encontrados.")
      return(NULL)
    }

    linha_total_geral <- linhas_total[3]
    linha_anos        <- linha_total_geral - 1

    anos_row         <- str_replace(as.character(df_raw[linha_anos, ]), "\\.0$", "")
    cols_interesse   <- which(anos_row %in% anos_analise_chr)
    anos_encontrados <- anos_row[cols_interesse]

    if (length(cols_interesse) == 0) {
      warning("[", aba, "] Anos de análise não encontrados.")
      return(NULL)
    }

    linha_inicio <- linha_total_geral + 1
    linhas_dados <- linha_inicio:min(nrow(df_raw), linha_inicio + 100)

    bloco <- df_raw[linhas_dados, c(1, cols_interesse)]
    names(bloco) <- c("idade_raw", anos_encontrados)

    bloco |>
      mutate(idade = suppressWarnings(
        as.integer(str_replace(idade_raw, "\\.0$", ""))
      )) |>
      filter(!is.na(idade), idade >= 0, idade <= 100) |>
      select(-idade_raw) |>
      pivot_longer(cols = -idade, names_to = "Ano", values_to = "pop") |>
      mutate(
        Ano   = as.integer(Ano),
        pop   = suppressWarnings(as.numeric(str_replace_all(pop, "[\\s,]", ""))),
        SIGLA = str_trim(aba)   # remove espaços extras herdados do nome da aba (ex: "CE ")
      ) |>
      filter(!is.na(pop))

  }, error = function(e) {
    warning("[", aba, "] Erro: ", e$message)
    return(NULL)
  })
}

message("\nLendo dados das ", length(abas_uf), " abas de UF...")
lista_ufs  <- map(abas_uf, ler_aba_uf)
# Aplica str_trim nos nomes para remover espaços (ex: "CE " → "CE")
names(lista_ufs) <- str_trim(abas_uf)
ok <- map_lgl(lista_ufs, ~ !is.null(.))
message("Abas lidas com sucesso: ", sum(ok), "/", length(abas_uf))
if (any(!ok)) message("Com problema: ", paste(names(lista_ufs)[!ok], collapse = ", "))

# Deletando arquivo temporário
if (file.exists(arquivo_local)) {
  file.remove(arquivo_local)
  message("Arquivo temporário deletado.")
}

# Agregando
dados_todos_pop <- bind_rows(lista_ufs[ok])

resultado_final <- dados_todos_pop |>
  group_by(SIGLA, Ano) |>
  summarise(
    pop_0_a_6_anos  = sum(pop[idade <= 6],  na.rm = TRUE),
    pop_60_mais_anos = sum(pop[idade >= 60], na.rm = TRUE),
    .groups = "drop"
  ) |>
  mutate(pop_6_60_anos = pop_0_a_6_anos + pop_60_mais_anos) |>
  arrange(SIGLA, Ano) |>
  as.data.table()

pop_10_19_anos <- dados_todos_pop |>
  filter(idade >= 10, idade <= 19) |>
  group_by(SIGLA, Ano) |>
  summarise(pop_10_19_anos = sum(pop, na.rm = TRUE), .groups = "drop") |>
  arrange(SIGLA, Ano) |>
  as.data.table()

message("\nLinhas por ano (populações 0-6 e 60+, deve ser 27 por ano):")
print(resultado_final[, .N, by = Ano])

message("\nLinhas por ano (população 10-19, deve ser 27 por ano):")
print(pop_10_19_anos[, .N, by = Ano])


# =============================================================================
# ── MÓDULO 5: NECESSIDADE DE GASTO ──────────────────────────────────────────
# =============================================================================

message("\n===== MÓDULO 5: Necessidade de Gasto =====\n")

# OBS: Valores corrigidos a dez/ANO_BASE_DEFLACAO.
# OBS: O motivador para transportes é o PIB.

# Carregando populações por faixa etária (geradas no módulo 4)
populacao_6_60_anos  <- resultado_final
populacao_10_19_anos <- pop_10_19_anos

# Calculando número de deputados com base na população do ano de referência
deputados_uf <- filter(populacao_long, Ano == ANO_FIM_ANALISE)
quociente_populacional <- sum(deputados_uf$populacao_total) / 513

deputados_uf <- deputados_uf %>%
  mutate(
    dep_fed_antes = populacao_total / quociente_populacional,
    dep_federais  = ifelse(dep_fed_antes < 8, 8,
                           ifelse(dep_fed_antes > 70, 70, round(dep_fed_antes))),
    dep_estaduais = ifelse(dep_federais < 13, 3 * dep_federais,
                           36 + (dep_federais - 12)),
    Percentual    = dep_estaduais / sum(dep_estaduais)
  ) %>%
  rename(UF = SIGLA)

# ── Taxa de homicídios — IPEA Data ────────────────────────────────────────────
get_thomic_safe <- function(anos, tentativas = 5, pausa = 5) {
  safe_ipea <- possibly(ipeadata, otherwise = NULL)
  for (i in seq_len(tentativas)) {
    message("Tentativa ", i, " de ", tentativas)
    dados <- safe_ipea("THOMIC")
    if (!is.null(dados)) {
      return(dados %>% filter(uname == "States", year(date) %in% anos))
    }
    Sys.sleep(pausa)
  }
  stop("Falha ao baixar a série THOMIC após ", tentativas, " tentativas.")
}

homic_rate <- get_thomic_safe(periodo_analise)

taxa_homicidios <- homic_rate %>%
  transmute(tcode, Ano = year(date), homicidios = value) %>%
  left_join(uf_map, by = "tcode") %>%
  select(UF, Ano, homicidios) %>%
  arrange(UF, Ano)

# ── Preparando dados de gastos ────────────────────────────────────────────────
# dados_gastos vem do Módulo 1. Estrutura real das colunas:
#   NO_LABEL_EIXO_X = tipo de despesa (ex: "Despesas Empenhadas")
#   NO_LABEL_EIXO_Y = função de gasto (ex: "04 - Administração")
#   VALUE           = valor bruto
#   QT_HABITANTE    = população do ente

dados_gastos <- dados_gastos %>%
  left_join(select(ipca, Ano, deflator_dez_22), by = "Ano") %>%
  mutate(
    Valor = VALUE * deflator_dez_22,
    Conta = NO_LABEL_EIXO_Y       # função de gasto
  ) %>%
  rename(
    Coluna    = NO_LABEL_EIXO_X,  # tipo de despesa
    População = QT_HABITANTE
  ) %>%
  # ── Seleção do tipo de despesa e do período de análise ──────────────────────
  #    Ajuste TIPO_DESPESA e ANO_INICIO/FIM_ANALISE na Seção de Configurações
  filter(Coluna == TIPO_DESPESA,
         Ano >= ANO_INICIO_ANALISE & Ano <= ANO_FIM_ANALISE)

# Populacao anual
populacao_anual <- populacao_long %>%
  group_by(Ano) %>%
  summarize(populacao_total = sum(populacao_total))

# Funções de gasto
funcoes <- c("01 - Legislativa", "02 - Judiciária", "03 - Essencial à Justiça",
             "04 - Administração", "06 - Segurança Pública", "09 - Previdência Social",
             "10 - Saúde", "12 - Educação", "26 - Transporte")

outras <- c("08 - Assistência Social", "11 - Trabalho", "13 - Cultura",
            "14 - Direitos da Cidadania", "15 - Urbanismo", "16 - Habitação",
            "17 - Saneamento", "18 - Gestão Ambiental", "19 - Ciência e Tecnologia",
            "20 - Agricultura", "21 - Organização Agrária", "22 - Indústria",
            "23 - Comércio e Serviços", "24 - Comunicações", "25 - Energia",
            "27 - Desporto e Lazer")

dados_outras <- dados_gastos %>%
  filter(Conta %in% outras) %>%
  select(Ano, UF, População, Conta, Valor, VALUE) %>%
  group_by(UF, Ano) %>%
  summarize(Valor = sum(Valor), População = mean(População),
            Conta = "Outras", VALUE = sum(VALUE))

# Função auxiliar: calcula gasto estimado per capita e médio por UF
calc_per_capita <- function(df_func, var_valor, var_driver, driver_long, join_vars, nome_pc) {
  agg <- df_func %>%
    group_by(Ano) %>%
    summarize(Valor_ano = sum(.data[[var_valor]]))

  driver_ano <- driver_long %>%
    group_by(Ano) %>%
    summarize(driver = sum(.data[[var_driver]]))

  agg <- left_join(agg, driver_ano, by = "Ano") %>%
    mutate(per_capita = Valor_ano / driver)

  names(agg)[names(agg) == "per_capita"] <- nome_pc
  agg
}

# ── Administração ─────────────────────────────────────────────────────────────
dados_adm <- filter(dados_gastos, Conta == "04 - Administração")

dados_adm_agregado <- dados_adm %>%
  group_by(Ano) %>%
  summarize(Valor_adm_ano = sum(Valor)) %>%
  left_join(populacao_anual, by = "Ano") %>%
  mutate(adm_per_capita = Valor_adm_ano / populacao_total)

dados_gastos_estimados <- left_join(
  filter(populacao_long, Ano == ANO_FIM_ANALISE),
  select(dados_adm_agregado, Ano, adm_per_capita), by = "Ano"
) %>% mutate(gasto_estimado_adm = populacao_total * adm_per_capita)

dados_estimado_media <- dados_adm %>%
  group_by(UF) %>%
  summarize(Valor_adm = mean(Valor))

modelo_adm_pooled <- plm(Valor ~ População, model = "pooling",
                          index = c("UF", "Ano"), data = dados_adm)
summary(modelo_adm_pooled)

despesas_estimadas <- filter(populacao_long, Ano == ANO_FIM_ANALISE) %>%
  rename(UF = SIGLA) %>%
  mutate(gastos_pooled_adm = populacao_total * coef(modelo_adm_pooled)[2] +
           coef(modelo_adm_pooled)[1])

# ── Judiciário (DF excluído) ──────────────────────────────────────────────────
dados_jud <- filter(dados_gastos, Conta == "02 - Judiciária", UF != "DF")

dados_jud_agregado <- dados_jud %>%
  group_by(Ano) %>%
  summarize(Valor_jud_ano = sum(Valor)) %>%
  left_join(populacao_anual, by = "Ano") %>%
  mutate(jud_per_capita = Valor_jud_ano / populacao_total)

dados_gastos_estimados <- left_join(dados_gastos_estimados,
                                    select(dados_jud_agregado, Ano, jud_per_capita), by = "Ano") %>%
  mutate(gasto_estimado_jud = populacao_total * jud_per_capita)

dados_jud_media <- dados_jud %>%
  group_by(UF) %>%
  summarize(Valor_jud = mean(Valor))

dados_estimado_media <- left_join(dados_estimado_media, dados_jud_media, by = "UF")

modelo_jud_pooled <- plm(Valor ~ População, model = "pooling",
                          index = c("UF", "Ano"), data = dados_jud)
summary(modelo_jud_pooled)

despesas_estimadas <- mutate(despesas_estimadas,
  gastos_pooled_jud = populacao_total * coef(modelo_jud_pooled)[2] + coef(modelo_jud_pooled)[1]
)

# ── Essencial à Justiça ───────────────────────────────────────────────────────
# OBS: PE não apresenta dados; estimamos com base no driver população.
dados_justica <- filter(dados_gastos, Conta == "03 - Essencial à Justiça")

dados_justica_agregado <- dados_justica %>%
  group_by(Ano) %>%
  summarize(Valor_justica_ano = sum(Valor)) %>%
  left_join(populacao_anual, by = "Ano") %>%
  mutate(justica_per_capita = Valor_justica_ano / populacao_total)

dados_gastos_estimados <- left_join(dados_gastos_estimados,
                                    select(dados_justica_agregado, Ano, justica_per_capita), by = "Ano") %>%
  mutate(gasto_estimado_justica = populacao_total * justica_per_capita)

dados_justica_media <- dados_justica %>%
  group_by(UF) %>%
  summarize(Valor_jus = mean(Valor))

dados_estimado_media <- left_join(dados_estimado_media, dados_justica_media, by = "UF")

modelo_justica_pooled <- plm(Valor ~ População, model = "pooling",
                              index = c("UF", "Ano"), data = dados_justica)
summary(modelo_justica_pooled)

despesas_estimadas <- mutate(despesas_estimadas,
  gastos_pooled_justica = populacao_total * coef(modelo_justica_pooled)[2] + coef(modelo_justica_pooled)[1]
)

# ── Legislativo (alocação proporcional ao número de legisladores) ─────────────
# OBS: não foi rodada uma regressão aqui. Usamos o total de gastos e alocamos
#      cada estado proporcionalmente ao seu percentual de legisladores.
dados_legislativo <- filter(dados_gastos, Conta == "01 - Legislativa")

dados_legislativo_agregado <- dados_legislativo %>%
  group_by(Ano) %>%
  summarize(Valor_total = sum(Valor))

dados_legislativo <- dados_legislativo %>%
  left_join(dados_legislativo_agregado, by = "Ano") %>%
  left_join(select(deputados_uf, UF, Percentual), by = "UF") %>%
  filter(!is.na(Percentual)) %>%
  mutate(estimado = Valor_total * Percentual, diferenca = estimado - Valor)

dados_legislativo_media <- dados_legislativo %>%
  group_by(UF) %>%
  summarize(estimado_legislativo = mean(estimado), Valor_legislativo = mean(Valor))

dados_estimado_media <- left_join(dados_estimado_media, dados_legislativo_media, by = "UF")

despesas_estimadas <- left_join(despesas_estimadas,
                                select(dados_estimado_media, UF, estimado_legislativo),
                                by = "UF")

# ── Outras funções ────────────────────────────────────────────────────────────
dados_outras_agregado <- dados_outras %>%
  group_by(Ano) %>%
  summarize(Valor_outras_ano = sum(Valor)) %>%
  left_join(populacao_anual, by = "Ano") %>%
  mutate(outras_per_capita = Valor_outras_ano / populacao_total)

dados_gastos_estimados <- left_join(dados_gastos_estimados,
                                    select(dados_outras_agregado, Ano, outras_per_capita), by = "Ano") %>%
  mutate(gasto_estimado_outras = populacao_total * outras_per_capita)

dados_outras_media <- dados_outras %>%
  group_by(UF) %>%
  summarize(Valor_outras = mean(Valor))

dados_estimado_media <- left_join(dados_estimado_media, dados_outras_media, by = "UF")

modelo_outros_pooled_gasto <- plm(Valor ~ População, model = "pooling",
                                   index = c("UF", "Ano"), data = dados_outras)
summary(modelo_outros_pooled_gasto)

despesas_estimadas <- mutate(despesas_estimadas,
  gastos_pooled_outros = populacao_total * coef(modelo_outros_pooled_gasto)[2] +
    coef(modelo_outros_pooled_gasto)[1]
)

# ── Educação ──────────────────────────────────────────────────────────────────
# Driver: população de 10 a 19 anos (proxy para ensino fundamental + médio)
dados_educacao <- filter(dados_gastos, Conta == "12 - Educação") %>%
  left_join(populacao_10_19_anos, by = c("UF" = "SIGLA", "Ano"))

dados_educacao_agregado <- dados_educacao %>%
  group_by(Ano) %>%
  summarize(Valor_educacao_ano = sum(Valor))

populacao_10_19_anos_anual <- populacao_10_19_anos %>%
  group_by(Ano) %>%
  summarize(pop_10_19_anos = sum(pop_10_19_anos))

dados_educacao_agregado <- left_join(dados_educacao_agregado, populacao_10_19_anos_anual, by = "Ano") %>%
  mutate(educacao_per_capita = Valor_educacao_ano / pop_10_19_anos)

dados_gastos_estimados <- left_join(dados_gastos_estimados,
                                    select(dados_educacao_agregado, Ano, educacao_per_capita), by = "Ano") %>%
  left_join(populacao_10_19_anos, by = c("Ano", "SIGLA")) %>%
  mutate(gasto_estimado_educacao = pop_10_19_anos * educacao_per_capita)

dados_educacao_media <- dados_educacao %>%
  group_by(UF) %>%
  summarize(Valor_educacao = mean(Valor))

dados_estimado_media <- left_join(dados_estimado_media, dados_educacao_media, by = "UF")

# Sem intercepto (era negativo)
modelo_educacao_pooled <- plm(Valor ~ pop_10_19_anos - 1, model = "pooling",
                               index = c("UF", "Ano"), data = dados_educacao)
summary(modelo_educacao_pooled)

despesas_estimadas <- despesas_estimadas %>%
  left_join(select(filter(populacao_10_19_anos, Ano == ANO_FIM_ANALISE), SIGLA, pop_10_19_anos),
            by = c("UF" = "SIGLA")) %>%
  mutate(gastos_pooled_educacao = pop_10_19_anos * coef(modelo_educacao_pooled)[1])

# ── Saúde ─────────────────────────────────────────────────────────────────────
# Driver: população de 0 a 6 anos + 60 anos ou mais
dados_saude <- filter(dados_gastos, Conta == "10 - Saúde") %>%
  left_join(populacao_6_60_anos, by = c("UF" = "SIGLA", "Ano"))

dados_saude_agregado <- dados_saude %>%
  group_by(Ano) %>%
  summarize(Valor_saude_ano = sum(Valor))

populacao_6_60_anos_anual <- populacao_6_60_anos %>%
  group_by(Ano) %>%
  summarize(pop_6_60_anos = sum(pop_6_60_anos))

dados_saude_agregado <- left_join(dados_saude_agregado, populacao_6_60_anos_anual, by = "Ano") %>%
  mutate(saude_per_capita = Valor_saude_ano / pop_6_60_anos)

dados_gastos_estimados <- left_join(dados_gastos_estimados,
                                    select(dados_saude_agregado, Ano, saude_per_capita), by = "Ano") %>%
  left_join(populacao_6_60_anos, by = c("Ano", "SIGLA")) %>%
  mutate(gasto_estimado_saude = pop_6_60_anos * saude_per_capita)

dados_saude_media <- dados_saude %>%
  group_by(UF) %>%
  summarize(Valor_saude = mean(Valor))

dados_estimado_media <- left_join(dados_estimado_media, dados_saude_media, by = "UF")

modelo_saude_pooled <- plm(Valor ~ pop_6_60_anos, model = "pooling",
                            index = c("UF", "Ano"), data = dados_saude)
summary(modelo_saude_pooled)

despesas_estimadas <- despesas_estimadas %>%
  left_join(select(filter(populacao_6_60_anos, Ano == ANO_FIM_ANALISE), SIGLA, pop_6_60_anos),
            by = c("UF" = "SIGLA")) %>%
  mutate(gastos_pooled_saude = pop_6_60_anos * coef(modelo_saude_pooled)[2] +
           coef(modelo_saude_pooled)[1])

# ── Segurança Pública ─────────────────────────────────────────────────────────
# Drivers: população + taxa de homicídios
dados_seguranca <- filter(dados_gastos, Conta == "06 - Segurança Pública") %>%
  left_join(taxa_homicidios, by = c("UF", "Ano"))

# Modelos: linear e log-log (para definir pesos dos drivers)
lin_seguranca <- plm(Valor ~ População + homicidios - 1, data = dados_seguranca,
                     effect = "individual", model = "random", index = c("UF", "Ano"))

log_seguranca <- plm(log(Valor) ~ log(População) + log(homicidios) - 1, data = dados_seguranca,
                     effect = "individual", model = "random", index = c("UF", "Ano"))

summary(lin_seguranca)
summary(log_seguranca)

peso_pop_seguranca <- coef(log_seguranca)["log(População)"] /
  (coef(log_seguranca)["log(População)"] + coef(log_seguranca)["log(homicidios)"])
peso_hom_seguranca <- coef(log_seguranca)["log(homicidios)"] /
  (coef(log_seguranca)["log(População)"] + coef(log_seguranca)["log(homicidios)"])

dados_seguranca_agregado <- dados_seguranca %>%
  group_by(Ano) %>%
  summarize(Valor_seguranca_ano = sum(Valor))

taxa_homicidios <- taxa_homicidios %>%
  left_join(populacao_long, by = c("UF" = "SIGLA", "Ano")) %>%
  filter(!is.na(populacao_total)) %>%
  mutate(homicidio_total = (homicidios / 100000) * populacao_total)

taxa_homicidios_ano <- taxa_homicidios %>%
  group_by(Ano) %>%
  summarize(homicidio_total = sum(homicidio_total))

dados_seguranca_agregado <- dados_seguranca_agregado %>%
  left_join(taxa_homicidios_ano, by = "Ano") %>%
  left_join(populacao_anual, by = "Ano") %>%
  mutate(seguranca_per_capita    = Valor_seguranca_ano / populacao_total,
         seguranca_per_homicidio = Valor_seguranca_ano / homicidio_total)

dados_gastos_estimados <- dados_gastos_estimados %>%
  left_join(select(dados_seguranca_agregado, Ano, seguranca_per_capita, seguranca_per_homicidio),
            by = "Ano") %>%
  left_join(select(taxa_homicidios, UF, Ano, homicidio_total),
            by = c("Ano", "SIGLA" = "UF")) %>%
  mutate(gasto_estimado_seguranca =
           homicidio_total * seguranca_per_homicidio * peso_hom_seguranca +
           populacao_total * seguranca_per_capita    * peso_pop_seguranca)

dados_seguranca_media <- dados_seguranca %>%
  group_by(UF) %>%
  summarize(Valor_seguranca = mean(Valor))

dados_estimado_media <- left_join(dados_estimado_media, dados_seguranca_media, by = "UF")

modelo_seguranca_pooled <- plm(Valor ~ População + homicidios, model = "pooling",
                                index = c("UF", "Ano"), data = dados_seguranca)
summary(modelo_seguranca_pooled)

despesas_estimadas <- despesas_estimadas %>%
  left_join(select(filter(taxa_homicidios, Ano == ANO_FIM_ANALISE), UF, homicidios),
            by = "UF") %>%
  mutate(gastos_pooled_seguranca =
           coef(modelo_seguranca_pooled)[1] +
           populacao_total * coef(modelo_seguranca_pooled)[2] +
           homicidios      * coef(modelo_seguranca_pooled)[3])

# ── Transporte ────────────────────────────────────────────────────────────────
# Driver: PIB (motivador definido metodologicamente)
dados_transporte <- filter(dados_gastos, Conta == "26 - Transporte") %>%
  left_join(select(pib_long, Sigla, ano, pib), by = c("UF" = "Sigla", "Ano" = "ano"))

dados_transporte_agregado <- dados_transporte %>%
  group_by(Ano) %>%
  summarize(Valor_transporte_ano = sum(Valor))

pib_long_anual <- pib_long %>%
  group_by(ano) %>%
  summarize(pib = sum(pib))

dados_transporte_agregado <- left_join(dados_transporte_agregado, pib_long_anual,
                                       by = c("Ano" = "ano")) %>%
  mutate(transporte_per_pib = Valor_transporte_ano / pib)

dados_gastos_estimados <- left_join(dados_gastos_estimados,
                                    select(dados_transporte_agregado, Ano, transporte_per_pib), by = "Ano") %>%
  left_join(select(pib_long, Sigla, ano, pib), by = c("SIGLA" = "Sigla", "Ano" = "ano")) %>%
  mutate(gasto_estimado_transporte = pib * transporte_per_pib)

dados_transporte_media <- dados_transporte %>%
  group_by(UF) %>%
  summarize(Valor_transporte = mean(Valor))

dados_estimado_media <- left_join(dados_estimado_media, dados_transporte_media, by = "UF")

modelo_transporte_pooled <- plm(Valor ~ pib, model = "pooling",
                                 index = c("UF", "Ano"), data = dados_transporte)
summary(modelo_transporte_pooled)

despesas_estimadas <- despesas_estimadas %>%
  left_join(select(filter(pib_long, ano == ANO_FIM_ANALISE), Sigla, pib),
            by = c("UF" = "Sigla")) %>%
  mutate(gastos_pooled_transporte =
           coef(modelo_transporte_pooled)[1] + pib * coef(modelo_transporte_pooled)[2])

# ── Previdência Estadual ──────────────────────────────────────────────────────
dados_previdencia <- filter(dados_gastos, Conta == "09 - Previdência Social")

dados_previdencia_agregado <- dados_previdencia %>%
  group_by(Ano) %>%
  summarize(Valor_previdencia_ano = sum(Valor)) %>%
  left_join(populacao_anual, by = "Ano") %>%
  mutate(previdencia_per_capita = Valor_previdencia_ano / populacao_total)

dados_gastos_estimados <- left_join(dados_gastos_estimados,
                                    select(dados_previdencia_agregado, Ano, previdencia_per_capita), by = "Ano") %>%
  mutate(gasto_estimado_previdencia = populacao_total * previdencia_per_capita)

dados_previdencia_media <- dados_previdencia %>%
  group_by(UF) %>%
  summarize(Valor_previdencia = mean(Valor))

dados_estimado_media <- left_join(dados_estimado_media, dados_previdencia_media, by = "UF")

# OBS: intercepto negativo foi mantido pois valores de gasto continuam positivos
modelo_previdencia_pooled <- plm(Valor ~ População, model = "pooling",
                                  index = c("UF", "Ano"), data = dados_previdencia)
summary(modelo_previdencia_pooled)

despesas_estimadas <- mutate(despesas_estimadas,
  gastos_pooled_previdencia = coef(modelo_previdencia_pooled)[1] +
    populacao_total * coef(modelo_previdencia_pooled)[2]
)

# ── Encargos Especiais (Juros) ────────────────────────────────────────────────
dados_encargos <- filter(dados_gastos, Conta == "28 - Encargos Especiais")

dados_encargos_agregado <- dados_encargos %>%
  group_by(Ano) %>%
  summarize(Valor_encargos_ano = sum(Valor)) %>%
  left_join(populacao_anual, by = "Ano") %>%
  mutate(encargos_per_capita = Valor_encargos_ano / populacao_total)

dados_gastos_estimados <- left_join(dados_gastos_estimados,
                                    select(dados_encargos_agregado, Ano, encargos_per_capita), by = "Ano") %>%
  mutate(gasto_estimado_encargos = populacao_total * encargos_per_capita)

dados_encargos_media <- dados_encargos %>%
  group_by(UF) %>%
  summarize(Valor_encargos = mean(Valor))

dados_estimado_media <- left_join(dados_estimado_media, dados_encargos_media, by = "UF")

# Sem intercepto (fitted values ficariam negativos)
modelo_encargos_pooled <- plm(Valor ~ População - 1, model = "pooling",
                               index = c("UF", "Ano"), data = dados_encargos)
summary(modelo_encargos_pooled)

despesas_estimadas <- mutate(despesas_estimadas,
  gastos_pooled_encargos = populacao_total * coef(modelo_encargos_pooled)[1]
)

# ── Totalizando necessidade de gasto ──────────────────────────────────────────
despesas_estimadas <- despesas_estimadas %>%
  mutate(
    gastos_pooled_total = gastos_pooled_adm + gastos_pooled_jud + gastos_pooled_justica +
      estimado_legislativo + gastos_pooled_outros + gastos_pooled_educacao +
      gastos_pooled_saude + gastos_pooled_seguranca + gastos_pooled_transporte +
      gastos_pooled_previdencia,
    gastos_pooled_total_com_encargos = gastos_pooled_total + gastos_pooled_encargos
  )

dados_estimado_media[is.na(dados_estimado_media)] <- 0

dados_estimado_media <- dados_estimado_media %>%
  mutate(
    total_valor_medio = Valor_adm + Valor_jud + Valor_jus + Valor_legislativo +
      Valor_outras + Valor_educacao + Valor_saude + Valor_seguranca +
      Valor_transporte + Valor_previdencia,
    total_valor_medio_com_encargos = total_valor_medio + Valor_encargos
  )

gastos_estimados_final <- dados_gastos_estimados %>%
  select(SIGLA, Ano, gasto_estimado_adm, gasto_estimado_jud, gasto_estimado_justica,
         gasto_estimado_outras, gasto_estimado_educacao, gasto_estimado_saude,
         gasto_estimado_seguranca, gasto_estimado_transporte,
         gasto_estimado_previdencia, gasto_estimado_encargos) %>%
  filter(!is.na(gasto_estimado_adm)) %>%
  mutate(
    gasto_total = gasto_estimado_adm + gasto_estimado_jud + gasto_estimado_justica +
      gasto_estimado_outras + gasto_estimado_educacao + gasto_estimado_saude +
      gasto_estimado_seguranca + gasto_estimado_transporte + gasto_estimado_previdencia,
    gasto_total_com_encargos = gasto_total + gasto_estimado_encargos
  )

# ── Exportando resultados de necessidade de gasto ─────────────────────────────
write_xlsx(
  select(despesas_estimadas, UF, gastos_pooled_adm, gastos_pooled_jud,
         gastos_pooled_justica, estimado_legislativo, gastos_pooled_outros,
         gastos_pooled_educacao, gastos_pooled_saude, gastos_pooled_seguranca,
         gastos_pooled_transporte, gastos_pooled_previdencia,
         gastos_pooled_encargos, gastos_pooled_total, gastos_pooled_total_com_encargos),
  "despesa_pooled_estimada_api.xlsx"
)

write_xlsx(dados_estimado_media, "despesa_media_api.xlsx")

write_xlsx(
  filter(gastos_estimados_final, Ano == ANO_FIM_ANALISE),
  "despesa_proporcional_estimada_api.xlsx"
)

message("\n===== ROTINA CONCLUÍDA =====")
message("Arquivos gerados em: ", DIR_TRABALHO)
message("  - workspace_siconfi_", periodo_str, ".RData")
message("  - dados_final_capacidade_fiscal_api.xlsx")
message("  - dados_final_receita_media_api.xlsx")
message("  - despesa_pooled_estimada_api.xlsx")
message("  - despesa_media_api.xlsx")
message("  - despesa_proporcional_estimada_api.xlsx")

# FIM

# Calculando e exibindo o tempo total de execução
tempo_total <- proc.time() - tempo_inicio
tempo_seg   <- round(tempo_total["elapsed"])
tempo_min   <- floor(tempo_seg / 60)
tempo_s     <- tempo_seg %% 60
message(sprintf("\nTempo total de execução: %d min %d s", tempo_min, tempo_s))
