# agente.py (Com fix para Kaleido - LEMBRE-SE DE PREENCHER OS PLACEHOLDERS)

# --- Imports ---
import sqlite3 # Usado para conexão local
import pandas as pd
import traceback
import re
import os # Para getenv e paths locais
from langchain.tools import tool
from datetime import datetime, date # Adicionado date
import base64 # Para embutir imagens no HTML
import io # Para gerar imagens em memória

# Imports Langchain Core / OpenAI / Community
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.utilities import SQLDatabase
from langchain_community.tools.sql_database.tool import QuerySQLDataBaseTool
from langchain_community.vectorstores import Chroma
from langchain.tools.retriever import create_retriever_tool
import chromadb
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.memory import ConversationBufferMemory

# Import para gráficos (precisa instalar: pip install plotly kaleido)
try:
    import plotly.express as px
    import plotly.io as pio # <<< ADICIONADO IMPORT DE pio
except ImportError:
    print("--- AVISO: Biblioteca 'plotly' não encontrada. Gráficos não funcionarão. Instale com 'pip install plotly kaleido' ---")
    px = None
    pio = None # Define pio como None se o import falhar

# Para variáveis de ambiente (MELHOR PRÁTICA LOCAL)
from dotenv import load_dotenv

# --- Constantes Locais ---
NOME_BANCO_SQLITE = 'meus_dados.db' # Caminho relativo para o arquivo local
NOME_TABELA_PRINCIPAL_SQL = 'minha_tabela_principal'
NOME_COLECAO_CHROMA = 'minha_colecao_textos'
CHROMA_DB_PATH_LOCAL = "./chroma_db_storage" # Caminho relativo local

# --- Constantes das Colunas ---
REGIME_COL = 'servico_regime' # <<< NOVA CONSTANTE PARA O FILTRO >>>
SALES_VALUE_COL = 'valor_venda_total'; SALES_DATE_COL = 'data_recebimento_po'
BM_LIBERACAO_COL = 'data_liberacao_bm'; BM_DATE_COL = 'data_envio_relatorios'
REPORT_ENVIO_COL = 'data_envio_relatorios'; REPORT_DATE_COL = 'data_final_atendimento'
FAT_DATE_COL = 'data_faturamento'; FAT_STATUS_COL = 'atendimento_andamento'
FAT_GROSS_VALUE_COL = 'valor_venda_total'; FAT_NET_VALUE_COL = 'valor_venda_servico_desc'
FAT_VALID_STATUSES = "'Falta Recebimento', 'Finalizado Com Faturamento'"
# Condições base (serão combinadas com o filtro de regime)
FAT_BASE_CONDITIONS_LIST = [f"{FAT_DATE_COL} IS NOT NULL", f"{FAT_STATUS_COL} IN ({FAT_VALID_STATUSES})"]
REPORT_PENDING_CONDITION_LIST = [f"{REPORT_ENVIO_COL} IS NULL"]
BM_PENDING_CONDITION_LIST = [f"{BM_LIBERACAO_COL} IS NULL", f"{BM_DATE_COL} IS NOT NULL"]


# --- Carregamento da Chave API (Local via .env) ---
load_dotenv() # Carrega variáveis do arquivo .env local
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    print("--- ERRO CRÍTICO: Chave API OpenAI não encontrada no arquivo .env! ---")
    print("Certifique-se de que existe um arquivo '.env' na pasta Zero com a linha:")
    print('OPENAI_API_KEY="sua_chave_api_aqui"')


# --- Funções de Execução SQL (Usando sqlite3 Local) ---
def execute_direct_sql(query: str) -> float | int | str | None:
    """ Executa SQL local que retorna uma única célula (SUM, COUNT). """
    conn = None
    try:
        conn = sqlite3.connect(NOME_BANCO_SQLITE) # Conecta ao DB local
        cursor = conn.cursor()
        cursor.execute(query)
        result = cursor.fetchone()
        conn.close() # Fecha conexão após uso
        if result and result[0] is not None:
            try:
                if isinstance(result[0], str) and '.' in result[0]: return float(result[0])
                elif isinstance(result[0], str) and result[0].isdigit(): return int(result[0])
                elif isinstance(result[0], (int, float)): return result[0]
                else: return str(result[0])
            except (ValueError, TypeError):
                 if isinstance(result[0], (int, float)): return result[0]
                 else: return str(result[0])
        else:
            return 0 if "COUNT" in query.upper() else 0.0
    except sqlite3.Error as e:
        error_msg = f"Erro SQL Local: {e}"; print(f"DEBUG LOCAL SQL ERROR (direct): {error_msg}\nQuery: {query}"); return error_msg
    except Exception as e:
        error_msg = f"Erro inesperado (direct_sql local): {e}"; print(f"DEBUG LOCAL UNEXPECTED ERROR (direct): {error_msg}\nQuery: {query}"); traceback.print_exc(); return error_msg
    finally: # Garante fechamento mesmo se try falhar antes
        if conn:
            try: conn.close()
            except Exception: pass

def execute_query_fetch_all(query: str) -> pd.DataFrame | str:
    """ Executa SQL local e retorna todos os resultados como DataFrame. """
    conn = None
    try:
        conn = sqlite3.connect(NOME_BANCO_SQLITE) # Conecta ao DB local
        df = pd.read_sql_query(query, conn)
        conn.close() # Fecha conexão após uso
        return df
    except sqlite3.Error as e:
        error_msg = f"Erro SQL Local (fetch all): {e}"; print(f"DEBUG LOCAL SQL ERROR (fetch all): {error_msg}\nQuery: {query}"); return error_msg
    except Exception as e:
        error_msg = f"Erro inesperado (fetch all local): {e}"; print(f"DEBUG LOCAL UNEXPECTED ERROR (fetch all): {error_msg}\nQuery: {query}"); traceback.print_exc(); return error_msg
    finally: # Garante fechamento
        if conn:
            try: conn.close()
            except Exception: pass

# --- Helper Function for WHERE clause ---
def build_where_clause(base_conditions: list[str], regime: str | None = None) -> tuple[str, str]:
    """Constrói a cláusula WHERE combinando condições base e filtro de regime opcional."""
    conditions = list(base_conditions) # Cria cópia
    regime_label = ""
    safe_regime = None
    if regime:
        test_regime = regime.strip().capitalize()
        if test_regime in ['Naval', 'Offshore']:
            safe_regime = test_regime
            conditions.append(f"{REGIME_COL} = '{safe_regime}'")
            regime_label = f"{safe_regime} "
    if not conditions: return "", regime_label
    else: return "WHERE " + " AND ".join(conditions), regime_label

# --- Helper Function for Currency Formatting ---
def format_currency_brl(value) -> str:
    """Formata um valor numérico como moeda BRL ou retorna N/D."""
    if value is None or not isinstance(value, (int, float)):
        return "N/D"
    try:
        formatted_us = f"{value:,.2f}"
        formatted_br = formatted_us.replace(',', '#').replace('.', ',').replace('#', '.')
        return f"R$ {formatted_br}"
    except (ValueError, TypeError):
        return "N/D"

# --- Ferramentas (Tools) ---

@tool
def get_agent_capabilities() -> str:
    """
    OBRIGATÓRIO usar esta ferramenta para responder perguntas diretas sobre as
    funções/capacidades/habilidades do agente. Gatilhos EXATOS ou MUITO similares a:
    'o que você pode fazer', 'quais suas funções', 'como pode me ajudar',
    'no que você é útil', 'suas habilidades', 'listar funções', 'capacidades'.
    NÃO usar para nenhum outro tipo de pergunta ou saudação. Apenas descreve capacidades.
    """
    print("--- DEBUG: [Tool Called] get_agent_capabilities ---")
    # ❗❗❗ COLE AQUI O TEXTO COMPLETO DA VARIÁVEL 'capabilities' DA VERSÃO ANTERIOR CORRETA ❗❗❗
    # DEVE COMEÇAR COM: Olá! Eu sou a Marina, sua assistente de dados da Supply Marine...
    # E TERMINAR COM: Em que posso te ajudar com essas funções hoje?
    capabilities = """
        Olá! Eu sou a Marina, sua assistente de dados da Supply Marine. Minhas principais funções são:

    * **Consultar Vendas:** Posso calcular totais (geral, anual, mensal) e resumos mensais, baseados na data de recebimento da PO, opcionalmente filtrados por regime Naval/Offshore. (Ex: `vendas totais`, `vendas naval 2023`, `vendas offshore maio 2024`, `vendas por mes`).
    * **Consultar Faturamento:** Calcular Faturamento Bruto e Líquido (geral, anual, mensal) e resumos mensais, baseados na data de faturamento e status específicos, opcionalmente filtrados por regime Naval/Offshore. (Ex: `faturamento bruto total`, `faturamento líquido offshore 2024`, `faturamento naval por mes`).
    * **Verificar BMs Pendentes:** Contar o total (geral, anual) e resumos mensais de BMs pendentes (liberação nula e relatório enviado), baseados na data de envio do relatório, opcionalmente filtrados por regime Naval/Offshore. (Ex: `BMs pendentes total`, `bms offshore 2024`, `bms naval por mes`).
    * **Verificar Relatórios Pendentes:** Contar o total (geral, anual, mensal) e resumos mensais de relatórios pendentes (envio nulo), baseados na data final do atendimento, opcionalmente filtrados por regime Naval/Offshore. (Ex: `relatórios pendentes`, `relatórios naval 2023`, `relatórios offshore por mes`).
    * **Gerar Relatório Gerencial:** Criar um resumo diário (YTD) com os principais indicadores e gráficos. (Use: 'relatório gerencial', 'relatório do dia').
    * **Executar SQL:** Tentar responder perguntas mais complexas com consultas SQL SELECT diretas (se habilitado).
    * **Buscar em Documentos:** Procurar informações contextuais em documentos da base de conhecimento (se habilitado).

    Em que posso te ajudar com essas funções hoje?
    """
    return capabilities.strip()


# --- Ferramentas de Vendas (Com regime) ---
@tool
def get_total_sales_overall(regime: str | None = None) -> str:
    """Calcula o valor total GERAL de vendas, opcionalmente filtrado por regime (Naval/Offshore). Args: regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_total_sales_overall (Regime: {regime}) ---")
    base_conditions = [f"{SALES_DATE_COL} IS NOT NULL"]
    where_clause, regime_label = build_where_clause(base_conditions, regime)
    sql = f"SELECT SUM({SALES_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
    result = execute_direct_sql(sql)
    value = result if isinstance(result, (int, float)) else 0.0
    if isinstance(result, str): return f"Erro ao calcular total geral de vendas {regime_label}: {result}"
    else: return f"O total geral de vendas {regime_label}(baseado na data de recebimento da PO) é {format_currency_brl(value)}"

@tool
def get_total_sales_for_year(year: int, regime: str | None = None) -> str:
    """Calcula o valor total de vendas para um ANO específico, opcionalmente filtrado por regime (Naval/Offshore). Args: year (int): O ano. regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_total_sales_for_year (Ano: {year}, Regime: {regime}) ---")
    try:
        year = int(year); start_date = f"{year}-01-01"; end_date = f"{year+1}-01-01"
        base_conditions = [f"{SALES_DATE_COL} >= '{start_date}'", f"{SALES_DATE_COL} < '{end_date}'", f"{SALES_DATE_COL} IS NOT NULL"]
        where_clause, regime_label = build_where_clause(base_conditions, regime)
        sql = f"SELECT SUM({SALES_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
        result = execute_direct_sql(sql)
        value = result if isinstance(result, (int, float)) else 0.0
        if isinstance(result, str): return f"Erro ao calcular vendas {regime_label}para {year}: {result}"
        else: return f"O total de vendas {regime_label}para {year} foi {format_currency_brl(value)}"
    except ValueError: return f"Ano inválido fornecido: {year}."
    except Exception as e: return f"Erro inesperado ao processar vendas anuais {regime_label}para {year}: {e}"

# COLE ESTE BLOCO DE CÓDIGO NO LUGAR DO COMENTÁRIO PLACEHOLDER ACIMA:

@tool
def get_total_sales_for_month_year(month_input: str, year: int, regime: str | None = None) -> str:
    """Calcula o valor total de vendas para um MÊS e ANO específicos, opcionalmente filtrado por regime (Naval/Offshore). Args: month_input (str): Mês (nome/número). year (int): Ano. regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_total_sales_for_month_year (Mês: {month_input}, Ano: {year}, Regime: {regime}) ---")
    try:
        year = int(year); months_map = {'janeiro': '01', 'fevereiro': '02', 'março': '03', 'marco': '03', 'abril': '04', 'maio': '05', 'junho': '06', 'julho': '07', 'agosto': '08', 'setembro': '09', 'outubro': '10', 'novembro': '11', 'dezembro': '12'}
        month_num_str_input = str(month_input).lower().strip(); month_num = months_map.get(month_num_str_input) or (month_num_str_input if month_num_str_input.isdigit() else None)
        if month_num and 1 <= int(month_num) <= 12:
            month_num_str = f"{int(month_num):02d}"; next_month_year = year; next_month_num = int(month_num_str) + 1
            if next_month_num > 12: next_month_num = 1; next_month_year += 1
            start_date = f"{year}-{month_num_str}-01"; end_date = f"{next_month_year:04d}-{next_month_num:02d}-01"
            base_conditions = [f"{SALES_DATE_COL} >= '{start_date}'", f"{SALES_DATE_COL} < '{end_date}'", f"{SALES_DATE_COL} IS NOT NULL"]
            where_clause, regime_label = build_where_clause(base_conditions, regime)
            sql = f"SELECT SUM({SALES_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
            result = execute_direct_sql(sql)
            value = result if isinstance(result, (int, float)) else 0.0
            display_month = next((k for k, v in months_map.items() if v == month_num_str), month_num_str)
            if isinstance(result, str): return f"Erro ao calcular vendas {regime_label}para {display_month.capitalize()}/{year}: {result}"
            else: return f"O total de vendas {regime_label}para {display_month.capitalize()} de {year} foi {format_currency_brl(value)}"
        else: return f"Mês inválido fornecido: '{month_input}'."
    except ValueError: return f"Ano inválido fornecido: {year}."
    except Exception as e: return f"Erro inesperado ao processar vendas mensais {regime_label}para {month_input}/{year}: {e}"

@tool
def get_sales_per_month_dataframe(regime: str | None = None) -> str:
    """Busca o total de vendas AGRUPADO POR MÊS, opcionalmente filtrado por regime (Naval/Offshore). Retorna tabela markdown. Args: regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_sales_per_month_dataframe (Regime: {regime}) ---")
    try:
        grouping_date_col = SALES_DATE_COL; date_format_func = "strftime('%Y-%m', {date_col})"
        base_conditions = [f"{grouping_date_col} IS NOT NULL"]
        where_clause, regime_label = build_where_clause(base_conditions, regime)
        sql = f"SELECT {date_format_func.format(date_col=grouping_date_col)} AS Mes, SUM({SALES_VALUE_COL}) AS Total_Vendas_Mes FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause} GROUP BY Mes ORDER BY Mes;"
        df_result = execute_query_fetch_all(sql)
        if isinstance(df_result, pd.DataFrame):
            if not df_result.empty:
                if 'Total_Vendas_Mes' in df_result.columns:
                    try: df_result['Total_Vendas_Mes_fmt'] = df_result['Total_Vendas_Mes'].apply(format_currency_brl)
                    except Exception: df_result['Total_Vendas_Mes_fmt'] = 'Erro fmt'
                else: df_result['Total_Vendas_Mes_fmt'] = 'N/A'
                df_display = df_result[['Mes', 'Total_Vendas_Mes_fmt']].rename(columns={'Total_Vendas_Mes_fmt': 'Vendas_no_Mês'})
                markdown_table = df_display.to_markdown(index=False)
                return f"Aqui está o resumo das vendas {regime_label}por mês:\n{markdown_table}"
            else: return f"Não encontrei dados de vendas {regime_label}para agrupar por mês."
        else: return f"Erro ao buscar vendas {regime_label}por mês: {df_result}"
    except Exception as e:
        error_type = type(e).__name__; error_details = str(e); print(f"--- ERRO DETALHADO (LOCAL) [get_sales_per_month_dataframe]: {error_type}: {error_details} ---"); traceback.print_exc(); print(f"---")
        return f"Desculpe, ocorreu um erro interno ({error_type}) ao processar 'Vendas {regime_label}por mês'. Verifique os logs."

# --- Ferramentas de BMs Pendentes (Com regime) ---
@tool
def get_pending_bms_total(regime: str | None = None) -> str:
    """Calcula a quantidade TOTAL GERAL de BMs 'pendentes', opcionalmente filtrado por regime (Naval/Offshore). Args: regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_pending_bms_total (Regime: {regime}) ---")
    where_clause, regime_label = build_where_clause(BM_PENDING_CONDITION_LIST, regime)
    sql = f"SELECT COUNT(*) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
    result = execute_direct_sql(sql)
    value = result if isinstance(result, int) else 0
    if isinstance(result, str): return f"Não foi possível calcular o total de BMs pendentes {regime_label}. Erro: {result}"
    else: return f"O número total de BMs pendentes {regime_label}é: {value}"

@tool
def get_pending_bms_for_year(year: int, regime: str | None = None) -> str:
    """Calcula a quantidade de BMs 'pendentes' para um ANO específico, opcionalmente filtrado por regime (Naval/Offshore). Args: year (int): O ano. regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_pending_bms_for_year (Ano: {year}, Regime: {regime}) ---")
    try:
        year = int(year); start_date = f"{year}-01-01"; end_date = f"{year+1}-01-01"; filter_date_col = BM_DATE_COL
        base_conditions = BM_PENDING_CONDITION_LIST + [f"{filter_date_col} >= '{start_date}'", f"{filter_date_col} < '{end_date}'"]
        where_clause, regime_label = build_where_clause(base_conditions, regime)
        sql = f"SELECT COUNT(*) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
        result = execute_direct_sql(sql)
        value = result if isinstance(result, int) else 0
        if isinstance(result, str): return f"Não foi possível calcular BMs pendentes {regime_label}para {year}. Erro: {result}"
        else: return f"O número de BMs pendentes {regime_label}para o ano {year} é: {value}"
    except ValueError: return f"Ano inválido fornecido: {year}."
    except Exception as e: return f"Erro inesperado ao processar BMs pendentes {regime_label}para {year}: {e}"

@tool
def get_pending_bms_per_month(regime: str | None = None) -> str:
    """Busca a quantidade de BMs 'pendentes' AGRUPADOS POR MÊS, opcionalmente filtrado por regime (Naval/Offshore). Retorna tabela markdown. Args: regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_pending_bms_per_month (Regime: {regime}) ---")
    try:
        grouping_date_col = BM_DATE_COL; date_format_func = "strftime('%Y-%m', {date_col})"
        base_conditions = BM_PENDING_CONDITION_LIST + [f"{grouping_date_col} IS NOT NULL"]
        where_clause, regime_label = build_where_clause(base_conditions, regime)
        sql = f"SELECT {date_format_func.format(date_col=grouping_date_col)} AS Mes, COUNT(*) AS Total_Pendentes_No_Mes FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause} GROUP BY Mes ORDER BY Mes;"
        df_result = execute_query_fetch_all(sql)
        if isinstance(df_result, pd.DataFrame):
            if not df_result.empty:
                df_result = df_result.rename(columns={'Total_Pendentes_No_Mes': 'Qtd_Pendentes'})
                markdown_table = df_result.to_markdown(index=False)
                return f"Aqui está o resumo de BMs pendentes {regime_label}por mês:\n{markdown_table}"
            else: return f"Não encontrei dados de BMs pendentes {regime_label}para agrupar por mês."
        else: return f"Erro ao buscar BMs pendentes {regime_label}por mês: {df_result}"
    except Exception as e:
        error_type = type(e).__name__; error_details = str(e); print(f"--- ERRO DETALHADO (LOCAL) [get_pending_bms_per_month]: {error_type}: {error_details} ---"); traceback.print_exc(); print(f"---")
        return f"Desculpe, ocorreu um erro interno ({error_type}) ao processar 'BMs pendentes {regime_label}por mês'. Verifique os logs."

# --- Ferramentas de Relatórios Pendentes (Com regime) ---
@tool
def get_pending_reports_total(regime: str | None = None) -> str:
    """Calcula a quantidade TOTAL GERAL de relatórios 'pendentes de envio', opcionalmente filtrado por regime (Naval/Offshore). Args: regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_pending_reports_total (Regime: {regime}) ---")
    where_clause, regime_label = build_where_clause(REPORT_PENDING_CONDITION_LIST, regime)
    sql = f"SELECT COUNT(*) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
    result = execute_direct_sql(sql)
    value = result if isinstance(result, int) else 0
    if isinstance(result, str): return f"Não foi possível calcular o total de relatórios pendentes {regime_label}. Erro: {result}"
    else: return f"O número total de relatórios pendentes {regime_label}de envio é: {value}"

@tool
def get_pending_reports_for_year(year: int, regime: str | None = None) -> str:
    """Calcula a quantidade de relatórios 'pendentes de envio' para um ANO específico, opcionalmente filtrado por regime (Naval/Offshore). Args: year (int): O ano. regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_pending_reports_for_year (Ano: {year}, Regime: {regime}) ---")
    try:
        year = int(year); start_date = f"{year}-01-01"; end_date = f"{year+1}-01-01"; filter_date_col = REPORT_DATE_COL
        base_conditions = REPORT_PENDING_CONDITION_LIST + [f"{filter_date_col} IS NOT NULL", f"{filter_date_col} >= '{start_date}'", f"{filter_date_col} < '{end_date}'"]
        where_clause, regime_label = build_where_clause(base_conditions, regime)
        sql = f"SELECT COUNT(*) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
        result = execute_direct_sql(sql)
        value = result if isinstance(result, int) else 0
        if isinstance(result, str): return f"Não foi possível calcular relatórios pendentes {regime_label}para {year}. Erro: {result}"
        else: return f"O número de relatórios pendentes {regime_label}para o ano {year} é: {value}"
    except ValueError: return f"Ano inválido fornecido: {year}."
    except Exception as e: return f"Erro inesperado ao processar relatórios pendentes {regime_label}para {year}: {e}"

@tool
def get_pending_reports_for_month_year(month_input: str, year: int, regime: str | None = None) -> str:
    """Calcula a quantidade de relatórios 'pendentes de envio' para um MÊS e ANO específicos, opcionalmente filtrado por regime (Naval/Offshore). Args: month_input (str): Mês (nome/número). year (int): Ano. regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_pending_reports_for_month_year (Mês: {month_input}, Ano: {year}, Regime: {regime}) ---")
    try:
        year = int(year); months_map = {'janeiro': '01', 'fevereiro': '02', 'março': '03', 'marco': '03', 'abril': '04', 'maio': '05', 'junho': '06', 'julho': '07', 'agosto': '08', 'setembro': '09', 'outubro': '10', 'novembro': '11', 'dezembro': '12'}
        month_num_str_input = str(month_input).lower().strip(); month_num = months_map.get(month_num_str_input) or (month_num_str_input if month_num_str_input.isdigit() else None)
        if month_num and 1 <= int(month_num) <= 12:
            month_num_str = f"{int(month_num):02d}"; next_month_year = year; next_month_num = int(month_num_str) + 1
            if next_month_num > 12: next_month_num = 1; next_month_year += 1
            start_date = f"{year}-{month_num_str}-01"; end_date = f"{next_month_year:04d}-{next_month_num:02d}-01"; filter_date_col = REPORT_DATE_COL
            base_conditions = REPORT_PENDING_CONDITION_LIST + [f"{filter_date_col} IS NOT NULL", f"{filter_date_col} >= '{start_date}'", f"{filter_date_col} < '{end_date}'"]
            where_clause, regime_label = build_where_clause(base_conditions, regime)
            sql = f"SELECT COUNT(*) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
            result = execute_direct_sql(sql)
            value = result if isinstance(result, int) else 0
            display_month = next((k for k, v in months_map.items() if v == month_num_str), month_num_str)
            if isinstance(result, str): return f"Não foi possível calcular relatórios pendentes {regime_label}para {display_month.capitalize()}/{year}. Erro: {result}"
            else: return f"O número de relatórios pendentes {regime_label}para {display_month.capitalize()} de {year} é: {value}"
        else: return f"Mês inválido fornecido: '{month_input}'."
    except ValueError: return f"Ano inválido fornecido: {year}."
    except Exception as e: return f"Erro inesperado ao processar relatórios pendentes {regime_label}para {month_input}/{year}: {e}"

@tool
def get_pending_reports_per_month(regime: str | None = None) -> str:
    """Busca a quantidade de relatórios 'pendentes de envio' AGRUPADOS POR MÊS, opcionalmente filtrado por regime (Naval/Offshore). Retorna tabela markdown. Args: regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_pending_reports_per_month (Regime: {regime}) ---")
    try:
        grouping_date_col = REPORT_DATE_COL; date_format_func = "strftime('%Y-%m', {date_col})"
        base_conditions = REPORT_PENDING_CONDITION_LIST + [f"{grouping_date_col} IS NOT NULL"]
        where_clause, regime_label = build_where_clause(base_conditions, regime)
        sql = f"SELECT {date_format_func.format(date_col=grouping_date_col)} AS Mes, COUNT(*) AS Total_RP_No_Mes FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause} GROUP BY Mes ORDER BY Mes;"
        df_result = execute_query_fetch_all(sql)
        if isinstance(df_result, pd.DataFrame):
            if not df_result.empty:
                df_result = df_result.rename(columns={'Total_RP_No_Mes': 'Qtd_Pendentes'})
                markdown_table = df_result.to_markdown(index=False)
                return f"Aqui está o resumo de relatórios pendentes {regime_label}por mês:\n{markdown_table}"
            else: return f"Não encontrei dados de relatórios pendentes {regime_label}para agrupar por mês."
        else: return f"Erro ao buscar relatórios pendentes {regime_label}por mês: {df_result}"
    except Exception as e:
        error_type = type(e).__name__; error_details = str(e); print(f"--- ERRO DETALHADO (LOCAL) [get_pending_reports_per_month]: {error_type}: {error_details} ---"); traceback.print_exc(); print(f"---")
        return f"Desculpe, ocorreu um erro interno ({error_type}) ao processar 'Relatórios pendentes {regime_label}por mês'. Verifique os logs."

# --- Ferramentas de Faturamento (Com regime) ---
@tool
def get_gross_revenue_total(regime: str | None = None) -> str:
    """Calcula o Faturamento BRUTO total geral, opcionalmente filtrado por regime (Naval/Offshore). Args: regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_gross_revenue_total (Regime: {regime}) ---")
    where_clause, regime_label = build_where_clause(FAT_BASE_CONDITIONS_LIST, regime)
    sql = f"SELECT SUM({FAT_GROSS_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
    result = execute_direct_sql(sql)
    value = result if isinstance(result, (int, float)) else 0.0
    if isinstance(result, str): return f"Erro ao calcular faturamento bruto total {regime_label}: {result}"
    else: return f"O faturamento bruto total {regime_label}é {format_currency_brl(value)}"

@tool
def get_gross_revenue_for_year(year: int, regime: str | None = None) -> str:
    """Calcula o Faturamento BRUTO para um ANO específico, opcionalmente filtrado por regime (Naval/Offshore). Args: year (int): O ano. regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_gross_revenue_for_year (Ano: {year}, Regime: {regime}) ---")
    try:
        year = int(year); start_date = f"{year}-01-01"; end_date = f"{year+1}-01-01"
        base_conditions = FAT_BASE_CONDITIONS_LIST + [f"{FAT_DATE_COL} >= '{start_date}'", f"{FAT_DATE_COL} < '{end_date}'"]
        where_clause, regime_label = build_where_clause(base_conditions, regime)
        sql = f"SELECT SUM({FAT_GROSS_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
        result = execute_direct_sql(sql)
        value = result if isinstance(result, (int, float)) else 0.0
        if isinstance(result, str): return f"Erro ao calcular faturamento bruto {regime_label}para {year}: {result}"
        else: return f"O faturamento bruto {regime_label}para {year} foi {format_currency_brl(value)}"
    except ValueError: return f"Ano inválido fornecido: {year}."
    except Exception as e: return f"Erro inesperado ao processar faturamento bruto anual {regime_label}para {year}: {e}"

@tool
def get_gross_revenue_for_month_year(month_input: str, year: int, regime: str | None = None) -> str:
    """Calcula o Faturamento BRUTO para um MÊS e ANO específicos, opcionalmente filtrado por regime (Naval/Offshore). Args: month_input (str): Mês (nome/número). year (int): Ano. regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_gross_revenue_for_month_year (Mês: {month_input}, Ano: {year}, Regime: {regime}) ---")
    try:
        year = int(year); months_map = {'janeiro': '01', 'fevereiro': '02', 'março': '03', 'marco': '03', 'abril': '04', 'maio': '05', 'junho': '06', 'julho': '07', 'agosto': '08', 'setembro': '09', 'outubro': '10', 'novembro': '11', 'dezembro': '12'}
        month_num_str_input = str(month_input).lower().strip(); month_num = months_map.get(month_num_str_input) or (month_num_str_input if month_num_str_input.isdigit() else None)
        if month_num and 1 <= int(month_num) <= 12:
            month_num_str = f"{int(month_num):02d}"; next_month_year = year; next_month_num = int(month_num_str) + 1
            if next_month_num > 12: next_month_num = 1; next_month_year += 1
            start_date = f"{year}-{month_num_str}-01"; end_date = f"{next_month_year:04d}-{next_month_num:02d}-01"
            base_conditions = FAT_BASE_CONDITIONS_LIST + [f"{FAT_DATE_COL} >= '{start_date}'", f"{FAT_DATE_COL} < '{end_date}'"]
            where_clause, regime_label = build_where_clause(base_conditions, regime)
            sql = f"SELECT SUM({FAT_GROSS_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
            result = execute_direct_sql(sql)
            value = result if isinstance(result, (int, float)) else 0.0
            display_month = next((k for k, v in months_map.items() if v == month_num_str), month_num_str)
            if isinstance(result, str): return f"Erro ao calcular faturamento bruto {regime_label}para {display_month.capitalize()}/{year}: {result}"
            else: return f"O faturamento bruto {regime_label}para {display_month.capitalize()} de {year} foi {format_currency_brl(value)}"
        else: return f"Mês inválido fornecido: '{month_input}'."
    except ValueError: return f"Ano inválido fornecido: {year}."
    except Exception as e: return f"Erro inesperado ao processar faturamento bruto mensal {regime_label}para {month_input}/{year}: {e}"

@tool
def get_gross_revenue_per_month(regime: str | None = None) -> str:
    """Busca o Faturamento BRUTO AGRUPADO POR MÊS, opcionalmente filtrado por regime (Naval/Offshore). Retorna tabela markdown. Args: regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_gross_revenue_per_month (Regime: {regime}) ---")
    try:
        grouping_date_col = FAT_DATE_COL; date_format_func = "strftime('%Y-%m', {date_col})"
        base_conditions = FAT_BASE_CONDITIONS_LIST + [f"{grouping_date_col} IS NOT NULL"]
        where_clause, regime_label = build_where_clause(base_conditions, regime)
        sql = f"SELECT {date_format_func.format(date_col=grouping_date_col)} AS Mes, SUM({FAT_GROSS_VALUE_COL}) AS Total_FB_Mes FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause} GROUP BY Mes ORDER BY Mes;"
        df_result = execute_query_fetch_all(sql)
        if isinstance(df_result, pd.DataFrame):
            if not df_result.empty:
                if 'Total_FB_Mes' in df_result.columns:
                    try: df_result['Total_FB_Mes_fmt'] = df_result['Total_FB_Mes'].apply(format_currency_brl)
                    except Exception: df_result['Total_FB_Mes_fmt'] = 'Erro fmt'
                else: df_result['Total_FB_Mes_fmt'] = 'N/A'
                df_display = df_result[['Mes', 'Total_FB_Mes_fmt']].rename(columns={'Total_FB_Mes_fmt': 'Faturamento_Bruto'})
                markdown_table = df_display.to_markdown(index=False)
                return f"Aqui está o resumo do faturamento bruto {regime_label}por mês:\n{markdown_table}"
            else: return f"Não encontrei dados de faturamento bruto {regime_label}para agrupar por mês."
        else: return f"Erro ao buscar faturamento bruto {regime_label}por mês: {df_result}"
    except Exception as e:
        error_type = type(e).__name__; error_details = str(e); print(f"--- ERRO DETALHADO (LOCAL) [get_gross_revenue_per_month]: {error_type}: {error_details} ---"); traceback.print_exc(); print(f"---")
        return f"Desculpe, ocorreu um erro interno ({error_type}) ao processar 'Faturamento bruto {regime_label}por mês'. Verifique os logs."

@tool
def get_net_revenue_total(regime: str | None = None) -> str:
    """Calcula o Faturamento LÍQUIDO total geral, opcionalmente filtrado por regime (Naval/Offshore). Args: regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_net_revenue_total (Regime: {regime}) ---")
    where_clause, regime_label = build_where_clause(FAT_BASE_CONDITIONS_LIST, regime)
    sql = f"SELECT SUM({FAT_NET_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
    result = execute_direct_sql(sql)
    value = result if isinstance(result, (int, float)) else 0.0
    if isinstance(result, str): return f"Erro ao calcular faturamento líquido total {regime_label}: {result}"
    else: return f"O faturamento líquido total {regime_label}é {format_currency_brl(value)}"

@tool
def get_net_revenue_for_year(year: int, regime: str | None = None) -> str:
    """Calcula o Faturamento LÍQUIDO para um ANO específico, opcionalmente filtrado por regime (Naval/Offshore). Args: year (int): O ano. regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_net_revenue_for_year (Ano: {year}, Regime: {regime}) ---")
    try:
        year = int(year); start_date = f"{year}-01-01"; end_date = f"{year+1}-01-01"
        base_conditions = FAT_BASE_CONDITIONS_LIST + [f"{FAT_DATE_COL} >= '{start_date}'", f"{FAT_DATE_COL} < '{end_date}'"]
        where_clause, regime_label = build_where_clause(base_conditions, regime)
        sql = f"SELECT SUM({FAT_NET_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
        result = execute_direct_sql(sql)
        value = result if isinstance(result, (int, float)) else 0.0
        if isinstance(result, str): return f"Erro ao calcular faturamento líquido {regime_label}para {year}: {result}"
        else: return f"O faturamento líquido {regime_label}para {year} foi {format_currency_brl(value)}"
    except ValueError: return f"Ano inválido fornecido: {year}."
    except Exception as e: return f"Erro inesperado ao processar faturamento líquido anual {regime_label}para {year}: {e}"

@tool
def get_net_revenue_for_month_year(month_input: str, year: int, regime: str | None = None) -> str:
    """Calcula o Faturamento LÍQUIDO para um MÊS e ANO específicos, opcionalmente filtrado por regime (Naval/Offshore). Args: month_input (str): Mês (nome/número). year (int): Ano. regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_net_revenue_for_month_year (Mês: {month_input}, Ano: {year}, Regime: {regime}) ---")
    try:
        year = int(year); months_map = {'janeiro': '01', 'fevereiro': '02', 'março': '03', 'marco': '03', 'abril': '04', 'maio': '05', 'junho': '06', 'julho': '07', 'agosto': '08', 'setembro': '09', 'outubro': '10', 'novembro': '11', 'dezembro': '12'}
        month_num_str_input = str(month_input).lower().strip(); month_num = months_map.get(month_num_str_input) or (month_num_str_input if month_num_str_input.isdigit() else None)
        if month_num and 1 <= int(month_num) <= 12:
            month_num_str = f"{int(month_num):02d}"; next_month_year = year; next_month_num = int(month_num_str) + 1
            if next_month_num > 12: next_month_num = 1; next_month_year += 1
            start_date = f"{year}-{month_num_str}-01"; end_date = f"{next_month_year:04d}-{next_month_num:02d}-01"
            base_conditions = FAT_BASE_CONDITIONS_LIST + [f"{FAT_DATE_COL} >= '{start_date}'", f"{FAT_DATE_COL} < '{end_date}'"]
            where_clause, regime_label = build_where_clause(base_conditions, regime)
            sql = f"SELECT SUM({FAT_NET_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
            result = execute_direct_sql(sql)
            value = result if isinstance(result, (int, float)) else 0.0
            display_month = next((k for k, v in months_map.items() if v == month_num_str), month_num_str)
            if isinstance(result, str): return f"Erro ao calcular faturamento líquido {regime_label}para {display_month.capitalize()}/{year}: {result}"
            else: return f"O faturamento líquido {regime_label}para {display_month.capitalize()} de {year} foi {format_currency_brl(value)}"
        else: return f"Mês inválido fornecido: '{month_input}'."
    except ValueError: return f"Ano inválido fornecido: {year}."
    except Exception as e: return f"Erro inesperado ao processar faturamento líquido mensal {regime_label}para {month_input}/{year}: {e}"

@tool
def get_net_revenue_per_month(regime: str | None = None) -> str:
    """Busca o Faturamento LÍQUIDO AGRUPADO POR MÊS, opcionalmente filtrado por regime (Naval/Offshore). Retorna tabela markdown. Args: regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_net_revenue_per_month (Regime: {regime}) ---")
    try:
        grouping_date_col = FAT_DATE_COL; date_format_func = "strftime('%Y-%m', {date_col})"
        base_conditions = FAT_BASE_CONDITIONS_LIST + [f"{grouping_date_col} IS NOT NULL"]
        where_clause, regime_label = build_where_clause(base_conditions, regime)
        sql = f"SELECT {date_format_func.format(date_col=grouping_date_col)} AS Mes, SUM({FAT_NET_VALUE_COL}) AS Total_FL_Mes FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause} GROUP BY Mes ORDER BY Mes;"
        df_result = execute_query_fetch_all(sql)
        if isinstance(df_result, pd.DataFrame):
            if not df_result.empty:
                if 'Total_FL_Mes' in df_result.columns:
                    try: df_result['Total_FL_Mes_fmt'] = df_result['Total_FL_Mes'].apply(format_currency_brl)
                    except Exception: df_result['Total_FL_Mes_fmt'] = 'Erro fmt'
                else: df_result['Total_FL_Mes_fmt'] = 'N/A'
                df_display = df_result[['Mes', 'Total_FL_Mes_fmt']].rename(columns={'Total_FL_Mes_fmt': 'Faturamento_Liquido'})
                markdown_table = df_display.to_markdown(index=False)
                return f"Aqui está o resumo do faturamento líquido {regime_label}por mês:\n{markdown_table}"
            else: return f"Não encontrei dados de faturamento líquido {regime_label}para agrupar por mês."
        else: return f"Erro ao buscar faturamento líquido {regime_label}por mês: {df_result}"
    except Exception as e:
        error_type = type(e).__name__; error_details = str(e); print(f"--- ERRO DETALHADO (LOCAL) [get_net_revenue_per_month]: {error_type}: {error_details} ---"); traceback.print_exc(); print(f"---")
        return f"Desculpe, ocorreu um erro interno ({error_type}) ao processar 'Faturamento líquido {regime_label}por mês'. Verifique os logs."

# --- NOVA FERRAMENTA: Relatório Gerencial ---
@tool # Esta é a linha ~524 ou próxima a ela
def generate_daily_management_report() -> str:
    """ 
    Gera um relatório gerencial consolidado com os principais indicadores do ano corrente até a data atual (YTD).
    Use esta ferramenta quando o usuário pedir explicitamente o 'relatório gerencial', 'relatório do dia',
    'consolidado diário', 'resumo gerencial do dia', ou solicitações muito similares.
    Não use para perguntas sobre um único indicador ou com filtro Naval/Offshore (use as ferramentas específicas).
    """ # <--- ESTA É A DOCSTRING. ELA PRECISA ESTAR AQUI!
    print("--- DEBUG: [Tool Called] generate_daily_management_report ---")

    # if px is None or pio is None: # Verifica se plotly e pio foram importados
    #     return "Erro: A biblioteca Plotly é necessária para gerar os gráficos..."
    # ... resto do código da função ...

# --- FIM DA NOVA FERRAMENTA ---


# --- Configuração das Ferramentas Gerais (LOCAL) ---
# ... (Configuração do sql_query_tool e vector_search_tool como antes) ...

# --- Lista Final de Ferramentas ---
custom_tools = [
    get_agent_capabilities,
    get_total_sales_overall,
    get_total_sales_for_year,
    get_total_sales_for_month_year,
    get_sales_per_month_dataframe,
    get_pending_bms_total,
    get_pending_bms_for_year,
    get_pending_bms_per_month,
    get_pending_reports_total,
    get_pending_reports_for_year,
    get_pending_reports_for_month_year,
    get_pending_reports_per_month,
    get_gross_revenue_total,
    get_gross_revenue_for_year,
    get_gross_revenue_for_month_year,
    get_gross_revenue_per_month,
    get_net_revenue_total,
    get_net_revenue_for_year,
    get_net_revenue_for_month_year,
    get_net_revenue_per_month,
    generate_daily_management_report
]
tools = list(custom_tools)
if sql_query_tool: tools.append(sql_query_tool)
if vector_search_tool: tools.append(vector_search_tool)
print(f"--- DEBUG: Total de ferramentas carregadas para teste LOCAL: {len(tools)} ---")

# --- Configuração da Memória, Prompt, LLM e Agente Executor (LOCAL) ---
llm = None; agent = None
# ... (Configuração do LLM, MEMORY_KEY como antes) ...

# ❗❗❗ COLE AQUI O TEXTO COMPLETO DA VARIÁVEL 'SYSTEM_PROMPT' DA VERSÃO ANTERIOR CORRETA ❗❗❗
SYSTEM_PROMPT = """
COLE O SYSTEM_PROMPT COMPLETO AQUI (com a instrução para o relatório HTML)
"""
prompt = ChatPromptTemplate.from_messages(
    [("system", SYSTEM_PROMPT), MessagesPlaceholder(variable_name=MEMORY_KEY),
     ("user", "{input}"), MessagesPlaceholder(variable_name="agent_scratchpad")]
)
# ... (Restante da configuração do agent e inicializar_agent_executor como antes) ...

if llm and tools:
    try:
        agent = create_openai_tools_agent(llm, tools, prompt)
        print(f"--- DEBUG: Agente (LOCAL) criado com {len(tools)} ferramentas. ---")
    except Exception as e: print(f"--- ERRO CRÍTICO (LOCAL): Falha ao criar o agente: {e} ---"); traceback.print_exc(); agent = None
else: print("--- ERRO (LOCAL): Agente não criado (LLM ou Tools falhou). ---")

def inicializar_agent_executor(chat_message_history):
    if not agent or not llm: print("--- ERRO FATAL AO INICIALIZAR EXECUTOR (LOCAL): Componentes não prontos! ---"); return None
    try:
        memory_for_executor = ConversationBufferMemory(chat_memory=chat_message_history, memory_key=MEMORY_KEY, return_messages=True)
        agent_executor_instance = AgentExecutor(
            agent=agent, tools=tools, memory=memory_for_executor, verbose=True,
            handle_parsing_errors="Desculpe, tive um problema ao processar sua solicitação. Poderia reformular?",
            max_iterations=10, max_execution_time=120
        )
        print("--- DEBUG: Instância AgentExecutor (LOCAL) criada. ---")
        return agent_executor_instance
    except Exception as e: print(f"--- ERRO CRÍTICO (LOCAL): Falha ao criar instância AgentExecutor: {e} ---"); traceback.print_exc(); return None


print(f"--- DEBUG: Arquivo {__name__} (config. LOCAL com filtro regime e relatório) carregado. ---")

# --- NOVA FERRAMENTA: Relatório Gerencial ---
@tool
def generate_daily_management_report() -> str:
    """
    Gera um relatório gerencial consolidado com os principais indicadores do ano corrente até a data atual (YTD).
    Use esta ferramenta quando o usuário pedir explicitamente o 'relatório gerencial', 'relatório do dia',
    'consolidado diário', 'resumo gerencial do dia', ou solicitações muito similares.
    Não use para perguntas sobre um único indicador ou com filtro Naval/Offshore (use as ferramentas específicas).
    """
    print("--- DEBUG: [Tool Called] generate_daily_management_report ---")

    if px is None or pio is None: # Verifica se plotly e pio foram importados
        return "Erro: A biblioteca Plotly é necessária para gerar os gráficos deste relatório, mas não foi encontrada. Por favor, instale com 'pip install plotly kaleido'."
    try:
        # --- 1. Calcular Datas ---
        today = date.today()
        current_year = today.year
        start_of_year = date(current_year, 1, 1).strftime('%Y-%m-%d')
        end_of_period = today.strftime('%Y-%m-%d') # YTD
        ytd_months_num = list(range(1, today.month + 1))
        month_map_br = {1: 'JAN', 2: 'FEV', 3: 'MAR', 4: 'ABR', 5: 'MAI', 6: 'JUN', 7: 'JUL', 8: 'AGO', 9: 'SET', 10: 'OUT', 11: 'NOV', 12: 'DEZ'}
        print(f"--- DEBUG [Report]: Período YTD: {start_of_year} a {end_of_period} ---")

        # --- 2. Inicializar Dicionário de Dados ---
        report_data = {f'{cat}_{month_map_br[m].lower()}': (0.0 if cat in ['faturamento', 'vendas'] else 0) for cat in ['faturamento', 'vendas', 'bm_pendente', 'relatorios_pendentes'] for m in range(1, 13)}
        report_data.update({
            'faturamento_total_periodo': 0.0, 'vendas_total_periodo': 0.0,
            'bm_pendente_itens_total_periodo': 0, 'bm_pendente_valor_total_periodo': 0.0, 'bm_pendente_valor_total_historico': 0.0,
            'relatorios_pendentes_itens_total_periodo': 0, 'relatorios_pendentes_valor_total_periodo': 0.0, 'relatorios_pendentes_valor_total_historico': 0.0,
            'data_atualizacao': today.strftime('%d/%m/%Y'),
            'faturamento_chart_base64': '', 'vendas_chart_base64': ''
        })
        month_map_num_to_key = {m: month_map_br[m].lower() for m in range(1, 13)}

        # --- 3. Buscar Dados do Banco ---
        print(f"--- DEBUG [Report]: Buscando dados... ---")
        try:
            # Faturamento YTD Total e Mensal
            fat_ytd_conditions = FAT_BASE_CONDITIONS_LIST + [f"{FAT_DATE_COL} >= '{start_of_year}'", f"{FAT_DATE_COL} <= '{end_of_period}'"]
            where_fat_ytd, _ = build_where_clause(fat_ytd_conditions)
            sql_fat_total = f"SELECT SUM({FAT_GROSS_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_fat_ytd};"
            fat_total = execute_direct_sql(sql_fat_total)
            report_data['faturamento_total_periodo'] = fat_total if isinstance(fat_total, (int, float)) else 0.0

            sql_fat_mensal = f"SELECT strftime('%m', {FAT_DATE_COL}) as mes_num, SUM({FAT_GROSS_VALUE_COL}) as total FROM {NOME_TABELA_PRINCIPAL_SQL} {where_fat_ytd} GROUP BY mes_num;"
            df_fat_mensal = execute_query_fetch_all(sql_fat_mensal)
            if isinstance(df_fat_mensal, pd.DataFrame) and not df_fat_mensal.empty:
                for _, row in df_fat_mensal.iterrows():
                    mes_num = int(row['mes_num'])
                    if mes_num in month_map_num_to_key: report_data[f"faturamento_{month_map_num_to_key[mes_num]}"] = row['total'] or 0.0

            # Vendas YTD Total e Mensal
            sales_ytd_conditions = [f"{SALES_DATE_COL} >= '{start_of_year}'", f"{SALES_DATE_COL} <= '{end_of_period}'", f"{SALES_DATE_COL} IS NOT NULL"]
            where_sales_ytd, _ = build_where_clause(sales_ytd_conditions)
            sql_sales_total = f"SELECT SUM({SALES_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_sales_ytd};"
            sales_total = execute_direct_sql(sql_sales_total)
            report_data['vendas_total_periodo'] = sales_total if isinstance(sales_total, (int, float)) else 0.0

            sql_sales_mensal = f"SELECT strftime('%m', {SALES_DATE_COL}) as mes_num, SUM({SALES_VALUE_COL}) as total FROM {NOME_TABELA_PRINCIPAL_SQL} {where_sales_ytd} GROUP BY mes_num;"
            df_sales_mensal = execute_query_fetch_all(sql_sales_mensal)
            if isinstance(df_sales_mensal, pd.DataFrame) and not df_sales_mensal.empty:
                 for _, row in df_sales_mensal.iterrows():
                    mes_num = int(row['mes_num'])
                    if mes_num in month_map_num_to_key: report_data[f"vendas_{month_map_num_to_key[mes_num]}"] = row['total'] or 0.0

            # BM Pendente YTD e Histórico
            bm_ytd_conditions = BM_PENDING_CONDITION_LIST + [f"{BM_DATE_COL} >= '{start_of_year}'", f"{BM_DATE_COL} <= '{end_of_period}'"]
            where_bm_ytd, _ = build_where_clause(bm_ytd_conditions)
            sql_bm_count_total_ytd = f"SELECT COUNT(*) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_bm_ytd};"
            sql_bm_value_total_ytd = f"SELECT SUM({SALES_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_bm_ytd};"
            report_data['bm_pendente_itens_total_periodo'] = execute_direct_sql(sql_bm_count_total_ytd) or 0
            report_data['bm_pendente_valor_total_periodo'] = execute_direct_sql(sql_bm_value_total_ytd) or 0.0
            sql_bm_mensal = f"SELECT strftime('%m', {BM_DATE_COL}) as mes_num, COUNT(*) as total FROM {NOME_TABELA_PRINCIPAL_SQL} {where_bm_ytd} GROUP BY mes_num;"
            df_bm_mensal = execute_query_fetch_all(sql_bm_mensal)
            if isinstance(df_bm_mensal, pd.DataFrame) and not df_bm_mensal.empty:
                 for _, row in df_bm_mensal.iterrows():
                    mes_num = int(row['mes_num'])
                    if mes_num in month_map_num_to_key: report_data[f"bm_pendente_{month_map_num_to_key[mes_num]}"] = row['total'] or 0
            bm_hist_conditions = BM_PENDING_CONDITION_LIST + [f"{BM_DATE_COL} >= '2019-01-01'"]
            where_bm_hist, _ = build_where_clause(bm_hist_conditions)
            sql_bm_value_hist = f"SELECT SUM({SALES_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_bm_hist};"
            report_data['bm_pendente_valor_total_historico'] = execute_direct_sql(sql_bm_value_hist) or 0.0

            # Relatórios Pendentes YTD e Histórico
            rp_ytd_conditions = REPORT_PENDING_CONDITION_LIST + [f"{REPORT_DATE_COL} >= '{start_of_year}'", f"{REPORT_DATE_COL} <= '{end_of_period}'", f"{REPORT_DATE_COL} IS NOT NULL"]
            where_rp_ytd, _ = build_where_clause(rp_ytd_conditions)
            sql_rp_count_total_ytd = f"SELECT COUNT(*) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_rp_ytd};"
            sql_rp_value_total_ytd = f"SELECT SUM({SALES_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_rp_ytd};"
            report_data['relatorios_pendentes_itens_total_periodo'] = execute_direct_sql(sql_rp_count_total_ytd) or 0
            report_data['relatorios_pendentes_valor_total_periodo'] = execute_direct_sql(sql_rp_value_total_ytd) or 0.0
            sql_rp_mensal = f"SELECT strftime('%m', {REPORT_DATE_COL}) as mes_num, COUNT(*) as total FROM {NOME_TABELA_PRINCIPAL_SQL} {where_rp_ytd} GROUP BY mes_num;"
            df_rp_mensal = execute_query_fetch_all(sql_rp_mensal)
            if isinstance(df_rp_mensal, pd.DataFrame) and not df_rp_mensal.empty:
                 for _, row in df_rp_mensal.iterrows():
                    mes_num = int(row['mes_num'])
                    if mes_num in month_map_num_to_key: report_data[f"relatorios_pendentes_{month_map_num_to_key[mes_num]}"] = row['total'] or 0
            rp_hist_conditions = REPORT_PENDING_CONDITION_LIST + [f"{REPORT_DATE_COL} >= '2019-01-01'", f"{REPORT_DATE_COL} IS NOT NULL"]
            where_rp_hist, _ = build_where_clause(rp_hist_conditions)
            sql_rp_value_hist = f"SELECT SUM({SALES_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_rp_hist};"
            report_data['relatorios_pendentes_valor_total_historico'] = execute_direct_sql(sql_rp_value_hist) or 0.0

            print(f"--- DEBUG [Report]: Dados buscados. ---")
        except Exception as fetch_err:
            print(f"--- ERRO [Report]: Falha ao buscar dados: {fetch_err} ---"); traceback.print_exc()

        # --- 4. Formatar Dados para o Template ---
        report_data_str = {'current_year': current_year}
        for k, v in report_data.items():
             if k.endswith('_chart_base64'): continue
             if isinstance(v, (int, float)) and ('valor' in k or 'faturamento' in k or 'vendas' in k):
                 report_data_str[f"{k}_str"] = format_currency_brl(v)
             else:
                 report_data_str[f"{k}_str"] = str(v) if v is not None else '0'

        # --- 5. Gerar Gráficos ---
        print(f"--- DEBUG [Report]: Gerando gráficos... ---")
        try:
            if pio: # Garante que pio (plotly.io) foi importado com sucesso
                pio.kaleido.scope.plotlyjs = "https://cdn.plot.ly/plotly-latest.min.js"
                print(f"--- DEBUG [Report]: Kaleido plotlyjs scope DENTRO DA FUNÇÃO configurado para CDN. ---")

            mes_labels_ytd = [month_map_br[m] for m in ytd_months_num]
            fat_chart_data = {'Mes': mes_labels_ytd, 'Faturamento': [report_data[f'faturamento_{month_map_num_to_key[m]}'] for m in ytd_months_num]}
            ven_chart_data = {'Mes': mes_labels_ytd, 'Vendas': [report_data[f'vendas_{month_map_num_to_key[m]}'] for m in ytd_months_num]}
            df_fat_chart = pd.DataFrame(fat_chart_data)
            df_ven_chart = pd.DataFrame(ven_chart_data)
            chart_args = {"engine": "kaleido", "scale": 1.5, "width": 500, "height": 250}

            if not df_fat_chart.empty and df_fat_chart['Faturamento'].sum() > 0:
                fig_fat = px.bar(df_fat_chart, x='Mes', y='Faturamento', text_auto=True, title="Faturamento Mensal YTD")
                fig_fat.update_traces(texttemplate='%{text:.2s}', textposition='outside')
                fig_fat.update_layout(yaxis_title="Valor (R$)", yaxis_tickprefix="R$ ", xaxis_title=None, title_x=0.5, height=chart_args["height"])
                img_bytes_fat = fig_fat.to_image(format="png", **chart_args)
                report_data_str['faturamento_chart_base64'] = "data:image/png;base64," + base64.b64encode(img_bytes_fat).decode('utf-8')
                print(f"--- DEBUG [Report]: Gráfico Faturamento gerado. ---")
            else: print(f"--- DEBUG [Report]: Sem dados de Faturamento para plotar. ---"); report_data_str['faturamento_chart_base64'] = ""

            if not df_ven_chart.empty and df_ven_chart['Vendas'].sum() > 0:
                fig_ven = px.bar(df_ven_chart, x='Mes', y='Vendas', text_auto=True, title="Vendas Mensais YTD")
                fig_ven.update_traces(texttemplate='%{text:.2s}', textposition='outside', marker_color='rgba(22, 163, 74, 0.8)')
                fig_ven.update_layout(yaxis_title="Valor (R$)", yaxis_tickprefix="R$ ", xaxis_title=None, title_x=0.5, height=chart_args["height"])
                img_bytes_ven = fig_ven.to_image(format="png", **chart_args)
                report_data_str['vendas_chart_base64'] = "data:image/png;base64," + base64.b64encode(img_bytes_ven).decode('utf-8')
                print(f"--- DEBUG [Report]: Gráfico Vendas gerado. ---")
            else: print(f"--- DEBUG [Report]: Sem dados de Vendas para plotar. ---"); report_data_str['vendas_chart_base64'] = ""
        except ImportError:
             print("--- ERRO [Report]: Plotly ou Kaleido não instalados? ---")
             report_data_str['faturamento_chart_base64'] = "data:text/plain;base64," + base64.b64encode(b"Erro: Plotly/Kaleido nao instalado").decode('utf-8')
             report_data_str['vendas_chart_base64'] = report_data_str['faturamento_chart_base64']
        except Exception as chart_err:
            print(f"--- ERRO [Report]: Falha ao gerar gráficos: {chart_err} ---"); traceback.print_exc()
            report_data_str['faturamento_chart_base64'] = "" # Garante que fica vazio em caso de erro
            report_data_str['vendas_chart_base64'] = ""     # Garante que fica vazio em caso de erro

        # A DEFINIÇÃO DA STRING HTML_TEMPLATE VEM DEPOIS DESTE BLOCO DE CÓDIGO
        # E ANTES DA LINHA 'expected_keys = ...'

        # COLE ESTE BLOCO NO LUGAR DO PLACEHOLDER DO HTML_TEMPLATE:

        HTML_TEMPLATE = """
<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Dashboard Financeiro (YTD {current_year})</title><script src="https://cdn.tailwindcss.com?plugins=forms,typography,aspect-ratio,line-clamp,container-queries"></script><style>@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap'); body {{ font-family: 'Inter', sans-serif; background-color: #f3f4f6; }} .data-card {{ background-color: white; border-radius: 0.5rem; padding: 1.5rem; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1); display: flex; flex-direction: column; height: 100%; }} .card-title {{ display: flex; align-items: center; font-size: 1.125rem; font-weight: 600; color: #1f2937; margin-bottom: 1rem; flex-shrink: 0; }} .card-title span {{ margin-right: 0.5rem; /*color: #4f46e5;*/ font-size: 1.2em;}} .total-value {{ font-size: 1.5rem; font-weight: 700; color: #16a34a; margin-bottom: 1rem; flex-shrink: 0; }} .pending-value {{ color: #dc2626; }} .chart-container {{ position: relative; margin-bottom: 1rem; min-height: 150px; text-align: center; }} .chart-container img {{ max-width: 100%; height: auto; border: 1px solid #eee; margin-top: 0.5rem; }} .monthly-data {{ flex-shrink: 0; }} .monthly-data p {{ margin-bottom: 0.5rem; color: #4b5563; display: flex; justify-content: space-between; font-size: 0.875rem; }} .monthly-data span {{ font-weight: 500; }} .historical-total {{ font-size: 0.875rem; color: #6b7280; margin-top: 1rem; border-top: 1px solid #e5e7eb; padding-top: 0.75rem; }} .historical-total span {{ font-weight: 600; color: #4b5563; }} </style></head><body class="p-4 md:p-8"><header class="mb-6 flex items-center space-x-3"><div><h1 class="text-2xl md:text-3xl font-bold text-gray-800">Dashboard de Resultados – {current_year} (Até {data_atualizacao_str})</h1><p class="text-gray-600">Resumo dos principais indicadores financeiros e operacionais YTD.</p></div></header><main class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
<section class="data-card"><h2 class="card-title"><span>💰</span> Faturamento (Receita Bruta)</h2><div class="total-value">{faturamento_total_periodo_str}</div><div class="chart-container"><img src="{faturamento_chart_base64}" alt="Gráfico Faturamento Mensal YTD"></div>
<div class="monthly-data text-sm border-t pt-4 mt-4"><p>JAN: <span>{faturamento_jan_str}</span></p> <p>FEV: <span>{faturamento_fev_str}</span></p><p>MAR: <span>{faturamento_mar_str}</span></p> <p>ABR: <span>{faturamento_abr_str}</span></p><p>MAI: <span>{faturamento_mai_str}</span></p> <p>JUN: <span>{faturamento_jun_str}</span></p><p>JUL: <span>{faturamento_jul_str}</span></p> <p>AGO: <span>{faturamento_ago_str}</span></p><p>SET: <span>{faturamento_set_str}</span></p> <p>OUT: <span>{faturamento_out_str}</span></p><p>NOV: <span>{faturamento_nov_str}</span></p> <p>DEZ: <span>{faturamento_dez_str}</span></p></div></section>
<section class="data-card"><h2 class="card-title"><span>🛒</span> Vendas</h2><div class="total-value">{vendas_total_periodo_str}</div><div class="chart-container"><img src="{vendas_chart_base64}" alt="Gráfico Vendas Mensal YTD"></div>
<div class="monthly-data text-sm border-t pt-4 mt-4"><p>JAN: <span>{vendas_jan_str}</span></p> <p>FEV: <span>{vendas_fev_str}</span></p><p>MAR: <span>{vendas_mar_str}</span></p> <p>ABR: <span>{vendas_abr_str}</span></p><p>MAI: <span>{vendas_mai_str}</span></p> <p>JUN: <span>{vendas_jun_str}</span></p><p>JUL: <span>{vendas_jul_str}</span></p> <p>AGO: <span>{vendas_ago_str}</span></p><p>SET: <span>{vendas_set_str}</span></p> <p>OUT: <span>{vendas_out_str}</span></p><p>NOV: <span>{vendas_nov_str}</span></p> <p>DEZ: <span>{vendas_dez_str}</span></p></div></section>
<section class="data-card"><h2 class="card-title"><span>⚠️</span> BM Pendente</h2><div class="total-value pending-value">{bm_pendente_valor_total_periodo_str}</div><p class="text-gray-600 mb-2 text-sm">Valor Pendente (YTD {current_year})</p><div class="border-t pt-4 mt-4"><p class="text-lg font-semibold text-gray-700 mb-2">Total de Itens (YTD {current_year}): <span>{bm_pendente_itens_total_periodo_str}</span></p>
<div class="monthly-data text-sm"><p>JAN: <span>{bm_pendente_jan_str}</span> itens</p> <p>FEV: <span>{bm_pendente_fev_str}</span> itens</p><p>MAR: <span>{bm_pendente_mar_str}</span> itens</p> <p>ABR: <span>{bm_pendente_abr_str}</span> itens</p><p>MAI: <span>{bm_pendente_mai_str}</span> itens</p> <p>JUN: <span>{bm_pendente_jun_str}</span> itens</p><p>JUL: <span>{bm_pendente_jul_str}</span> itens</p> <p>AGO: <span>{bm_pendente_ago_str}</span> itens</p><p>SET: <span>{bm_pendente_set_str}</span> itens</p> <p>OUT: <span>{bm_pendente_out_str}</span> itens</p><p>NOV: <span>{bm_pendente_nov_str}</span> itens</p> <p>DEZ: <span>{bm_pendente_dez_str}</span> itens</p></div><p class="historical-total">Valor Total Geral (desde 2019): <span>{bm_pendente_valor_total_historico_str}</span></p></div></section>
<section class="data-card"><h2 class="card-title"><span>📄</span> Relatórios Pendentes</h2><div class="total-value pending-value">{relatorios_pendentes_valor_total_periodo_str}</div><p class="text-gray-600 mb-2 text-sm">Valor Pendente (YTD {current_year})</p><div class="border-t pt-4 mt-4"><p class="text-lg font-semibold text-gray-700 mb-2">Total de Itens (YTD {current_year}): <span>{relatorios_pendentes_itens_total_periodo_str}</span></p>
<div class="monthly-data text-sm"><p>JAN: <span>{relatorios_pendentes_jan_str}</span> itens</p> <p>FEV: <span>{relatorios_pendentes_fev_str}</span> itens</p><p>MAR: <span>{relatorios_pendentes_mar_str}</span> itens</p> <p>ABR: <span>{relatorios_pendentes_abr_str}</span> itens</p><p>MAI: <span>{relatorios_pendentes_mai_str}</span> itens</p> <p>JUN: <span>{relatorios_pendentes_jun_str}</span> itens</p><p>JUL: <span>{relatorios_pendentes_jul_str}</span> itens</p> <p>AGO: <span>{relatorios_pendentes_ago_str}</span> itens</p><p>SET: <span>{relatorios_pendentes_set_str}</span> itens</p> <p>OUT: <span>{relatorios_pendentes_out_str}</span> itens</p><p>NOV: <span>{relatorios_pendentes_nov_str}</span> itens</p> <p>DEZ: <span>{relatorios_pendentes_dez_str}</span> itens</p></div><p class="historical-total">Valor Total Geral (desde 2019): <span>{relatorios_pendentes_valor_total_historico_str}</span></p></div></section>
</main><footer class="mt-10 text-center text-sm text-gray-500">Dados referentes ao período de 01/01/{current_year} a {data_atualizacao_str}.</footer></body></html>
        """
        
        expected_keys = re.findall(r'\{([\w_]+)\}', HTML_TEMPLATE)
        final_data_for_template = {k: report_data_str.get(k, "Erro") for k in expected_keys}
        final_data_for_template['faturamento_chart_base64'] = report_data_str.get('faturamento_chart_base64', '')
        final_data_for_template['vendas_chart_base64'] = report_data_str.get('vendas_chart_base64', '')

        final_html = HTML_TEMPLATE.format(**final_data_for_template)
        print(f"--- DEBUG [Report]: Template HTML preenchido. ---")
        return final_html
    except ImportError:
         print("--- ERRO [Report]: Plotly ou Kaleido não instalados? ---")
         return "Erro: Bibliotecas Plotly/Kaleido não instaladas..."
    except Exception as report_err:
        print(f"--- ERRO GERAL [Report]: Falha ao gerar relatório: {report_err} ---"); traceback.print_exc()
        return f"Desculpe, ocorreu um erro inesperado ao gerar o relatório: {report_err}"

# --- FIM DA NOVA FERRAMENTA ---

# --- Configuração das Ferramentas Gerais (LOCAL) ---
sql_query_tool = None # <<< ESTA LINHA (e a próxima) RESOLVE O NameError
db = None
try:
    db_uri_local = f"sqlite:///{NOME_BANCO_SQLITE}"
    if os.path.exists(NOME_BANCO_SQLITE):
        db = SQLDatabase.from_uri(db_uri_local)
        sql_query_tool = QuerySQLDataBaseTool(db=db) 
        sql_query_tool.name = "sql_database_query_tool"
        sql_query_tool.description = (f"Use APENAS para SQL SELECT complexo no banco local '{NOME_BANCO_SQLITE}'. Priorize ferramentas específicas.")
        print(f"--- DEBUG: SQL Tool (LOCAL) configurada para '{NOME_BANCO_SQLITE}'. ---")
    else:
        print(f"--- AVISO: DB local '{NOME_BANCO_SQLITE}' não encontrado. SQL Tool DESABILITADA. ---")
        # sql_query_tool permanece None se o DB não existe
except Exception as e:
    print(f"--- ERRO: Configurar SQL Tool (LOCAL): {e} ---")
    traceback.print_exc()
    sql_query_tool = None # Garante que é None em caso de erro

vector_search_tool = None # <<< ESTA LINHA (e a próxima para Chroma) SÃO IMPORTANTES
try:
    if os.path.exists(CHROMA_DB_PATH_LOCAL):
        chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH_LOCAL)
        print(f"--- DEBUG: Cliente ChromaDB (LOCAL) conectado a '{CHROMA_DB_PATH_LOCAL}'. ---")
        embedding_function = None # Defina sua função de embedding aqui se usar
        collection = chroma_client.get_collection(NOME_COLECAO_CHROMA, embedding_function=embedding_function)
        vector_store = Chroma(client=chroma_client, collection_name=NOME_COLECAO_CHROMA, embedding_function=embedding_function)
        retriever_chroma = vector_store.as_retriever(search_kwargs={"k": 3})
        vector_search_tool = create_retriever_tool(
            retriever_chroma,
            "busca_documentos_supply_marine", # Nome da ferramenta
            "Use para buscar informações contextuais em documentos locais sobre processos, produtos ou informações gerais da Supply Marine. NÃO use para cálculos ou dados SQL." # Descrição
        )
        print(f"--- DEBUG: Vector Tool (LOCAL) configurada para coleção '{NOME_COLECAO_CHROMA}'. ---")
    else:
        print(f"--- AVISO: ChromaDB local '{CHROMA_DB_PATH_LOCAL}' não encontrado. Vector tool DESABILITADA. ---")
        # vector_search_tool permanece None
except ImportError:
    print(f"--- AVISO: Biblioteca 'chromadb' não encontrada. Vector tool DESABILITADA. Instale com 'pip install chromadb'. ---")
    vector_search_tool = None
except Exception as e_chroma:
    print(f"--- AVISO GERAL: Falha ao configurar ChromaDB/Ferramenta Vetorial (LOCAL): {e_chroma}. ---")
    traceback.print_exc()
    vector_search_tool = None

# --- Lista Final de Ferramentas ---
# COLE ESTE BLOCO NO LUGAR DA DEFINIÇÃO ATUAL DA LISTA custom_tools:

custom_tools = [
    get_agent_capabilities,
    get_total_sales_overall,
    get_total_sales_for_year,
    get_total_sales_for_month_year,
    get_sales_per_month_dataframe,
    get_pending_bms_total,
    get_pending_bms_for_year,
    get_pending_bms_per_month,
    get_pending_reports_total,
    get_pending_reports_for_year,
    get_pending_reports_for_month_year,
    get_pending_reports_per_month,
    get_gross_revenue_total,
    get_gross_revenue_for_year,
    get_gross_revenue_for_month_year,
    get_gross_revenue_per_month,
    get_net_revenue_total,
    get_net_revenue_for_year,
    get_net_revenue_for_month_year,
    get_net_revenue_per_month,
    generate_daily_management_report
]

@tool
def get_total_sales_for_month_year(month_input: str, year: int, regime: str | None = None) -> str:
    """Calcula o valor total de vendas para um MÊS e ANO específicos, opcionalmente filtrado por regime (Naval/Offshore). Args: month_input (str): Mês (nome/número). year (int): Ano. regime (str | None): Opcional. Filtra por 'Naval' ou 'Offshore'."""
    print(f"--- DEBUG: [Tool Called] get_total_sales_for_month_year (Mês: {month_input}, Ano: {year}, Regime: {regime}) ---")
    try:
        year = int(year); months_map = {'janeiro': '01', 'fevereiro': '02', 'março': '03', 'marco': '03', 'abril': '04', 'maio': '05', 'junho': '06', 'julho': '07', 'agosto': '08', 'setembro': '09', 'outubro': '10', 'novembro': '11', 'dezembro': '12'}
        month_num_str_input = str(month_input).lower().strip(); month_num = months_map.get(month_num_str_input) or (month_num_str_input if month_num_str_input.isdigit() else None)
        if month_num and 1 <= int(month_num) <= 12:
            month_num_str = f"{int(month_num):02d}"; next_month_year = year; next_month_num = int(month_num_str) + 1
            if next_month_num > 12: next_month_num = 1; next_month_year += 1
            start_date = f"{year}-{month_num_str}-01"; end_date = f"{next_month_year:04d}-{next_month_num:02d}-01"
            base_conditions = [f"{SALES_DATE_COL} >= '{start_date}'", f"{SALES_DATE_COL} < '{end_date}'", f"{SALES_DATE_COL} IS NOT NULL"]
            where_clause, regime_label = build_where_clause(base_conditions, regime)
            sql = f"SELECT SUM({SALES_VALUE_COL}) FROM {NOME_TABELA_PRINCIPAL_SQL} {where_clause};"
            result = execute_direct_sql(sql)
            value = result if isinstance(result, (int, float)) else 0.0
            display_month = next((k for k, v in months_map.items() if v == month_num_str), month_num_str)
            if isinstance(result, str): return f"Erro ao calcular vendas {regime_label}para {display_month.capitalize()}/{year}: {result}"
            else: return f"O total de vendas {regime_label}para {display_month.capitalize()} de {year} foi {format_currency_brl(value)}"
        else: return f"Mês inválido fornecido: '{month_input}'."
    except ValueError: return f"Ano inválido fornecido: {year}."
    except Exception as e: return f"Erro inesperado ao processar vendas mensais {regime_label}para {month_input}/{year}: {e}"

