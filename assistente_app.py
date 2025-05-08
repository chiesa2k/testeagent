# assistente_app.py (Versão Completa - Correção Duplicação)

import streamlit as st
import pandas as pd
import plotly.express as px
import io
import traceback
import os
import re

from langchain_community.chat_message_histories import StreamlitChatMessageHistory

# <<< Importa a função de inicialização do agente.py >>>
agent_module_imported = False
inicializar_agent_executor = None
try:
    # Garante que agente.py está completo e sem erros de sintaxe antes de importar
    print("--- DEBUG APP: Tentando importar 'inicializar_agent_executor' de 'agente.py'... ---")
    import sys
    # Adiciona o diretório atual ao path para garantir a importação correta
    sys.path.insert(0, os.path.dirname(__file__)) 
    from agente import inicializar_agent_executor
    print("--- DEBUG APP: Função 'inicializar_agent_executor' importada com sucesso. ---")
    agent_module_imported = True
except ModuleNotFoundError:
    st.error("Erro Crítico: O arquivo 'agente.py' não foi encontrado.")
    st.info("Certifique-se de que 'agente.py' está no mesmo diretório que 'assistente_app.py'.")
    st.stop()
except ImportError as e:
    st.error(f"Erro Crítico ao importar ou durante a inicialização do 'agente.py': {e}")
    st.info("Isso pode ser um erro de sintaxe DENTRO do 'agente.py' ou uma dependência faltando. Verifique o terminal.")
    traceback.print_exc(); st.stop()
except Exception as e: # Pega outros erros que podem ocorrer durante a carga do agente.py
    st.error(f"Erro inesperado ao importar/configurar 'agente.py': {e}")
    st.info("Verifique o console/terminal para detalhes do erro em 'agente.py'.")
    traceback.print_exc(); st.stop()

# <<< Bloco para tratar pergunta sobre capacidades diretamente >>>
CAPABILITIES_TEXT = """
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
CAPABILITIES_TRIGGERS = [
    "o que você pode fazer", "o que voce pode fazer", "quais suas funções",
    "quais sao suas funcoes", "qual sua função", "qual sua funcao", "suas funções",
    "suas funcoes", "como pode me ajudar", "como voce me ajuda", "no que você é útil",
    "no que voce e util", "suas habilidades", "suas capacidades", "listar funções",
    "listar funcoes", "o que você faz", "o que voce faz"
]
def check_for_capabilities_question(prompt: str) -> bool:
    if not prompt: return False
    prompt_lower = prompt.lower().strip('?.,! ').replace('-', ' ')
    for trigger in CAPABILITIES_TRIGGERS:
        if trigger in prompt_lower:
            # Verifica se o prompt é curto e contém o gatilho (evita pegar em frases longas)
            if len(prompt_lower) < len(trigger) + 15: return True 
    return False

# --- Configuração da Página ---
st.set_page_config(
    page_title="Marina Supply", 
    page_icon="marina.png", # Certifique-se que 'marina.png' está na mesma pasta
    layout="wide"
)

# --- Inicialização do Session State ---
if 'last_table_markdown' not in st.session_state: st.session_state.last_table_markdown = None
if 'plot_fig' not in st.session_state: st.session_state.plot_fig = None
if 'user_input_trigger' not in st.session_state: st.session_state.user_input_trigger = False
if 'clicked_suggestion' not in st.session_state: st.session_state.clicked_suggestion = None


# --- Título e Interface ---
col_left, col_center, col_right = st.columns([1, 3, 1], gap="small")
with col_center:
    image_path = "marina.png"
    try:
        if os.path.exists(image_path): st.image(image_path, width=450)
        else: st.warning(f"Imagem '{image_path}' não encontrada.")
    except Exception as img_err: st.error(f"Erro ao carregar imagem: {img_err}")

# --- Sidebar ---
st.sidebar.info("""
**Marina - Assistente de Dados**
Ferramenta experimental para consulta de dados da Supply Marine.

**Funcionalidades:**
- Vendas (Geral, Ano, Mês, Resumo Mensal)
- Faturamento Bruto/Líquido (Geral, Ano, Mês, Resumo Mensal)
- BMs Pendentes (Total, Ano, Resumo Mensal)
- Relatórios Pendentes (Total, Ano, Mês, Resumo Mensal)
- Relatório Gerencial Diário (YTD)

*Use linguagem natural para suas perguntas.*
""")
st.sidebar.markdown("---") 
if st.sidebar.button("🗑️ Limpar Histórico", key="clear_history_button"):
    # Limpa o histórico da Langchain/Streamlit e outros estados relacionados
    if "langchain_chat_history_supply_final_v2" in st.session_state:
        del st.session_state["langchain_chat_history_supply_final_v2"] # Remove a chave do histórico
    if 'agent_executor_initialized' in st.session_state:
        del st.session_state['agent_executor_initialized'] # Força reinicialização do agente
    st.session_state.plot_fig = None 
    st.session_state.last_table_markdown = None 
    st.session_state.user_input_trigger = False
    st.session_state.clicked_suggestion = None
    print("--- DEBUG APP: Histórico e estados relacionados limpos pelo botão. ---")
    st.rerun() # Recarrega a página para refletir a limpeza


# <<< SUGESTÕES DE PERGUNTAS >>>
if agent_module_imported:
    st.markdown("---")
    st.markdown("<small>Tente perguntar algo como:</small>", unsafe_allow_html=True)
    suggestions = [
        "Qual o total de vendas?", "BMs pendentes por mes", "Faturamento bruto 2024",
        "Relatórios pendentes total", "Faturamento líquido maio 2024", "O que você pode fazer?",
        "Relatorio gerencial"
    ]
    num_cols = 3
    cols = st.columns(num_cols)
    for i, suggestion in enumerate(suggestions):
        with cols[i % num_cols]:
            if st.button(suggestion, key=f"suggestion_{i}_final_v3", help=f"Perguntar: {suggestion}", use_container_width=True): # Chave atualizada
                st.session_state.clicked_suggestion = suggestion
                st.session_state.user_input_trigger = True
                st.rerun()
    st.markdown("---")
else:
    st.warning("Módulo do agente não foi carregado corretamente, sugestões desabilitadas.")

# --- Gerenciamento da Memória e Histórico de Chat ---
# A chave agora é usada para buscar/criar o histórico
msgs = StreamlitChatMessageHistory(key="langchain_chat_history_supply_final_v2") 

# --- Inicialização do Agente Executor (apenas uma vez por sessão) ---
agent_executor = None
if inicializar_agent_executor:
    if 'agent_executor_initialized' not in st.session_state:
        print("--- DEBUG APP: Tentando inicializar Agent Executor pela primeira vez... ---")
        with st.spinner("Inicializando a Marina... 🚀"):
            # Passa o objeto de histórico 'msgs' para o inicializador
            st.session_state.agent_executor_initialized = inicializar_agent_executor(chat_message_history=msgs) 
        if not st.session_state.agent_executor_initialized:
            st.error("Falha Crítica: Não foi possível inicializar o Agente Executor. Verifique a chave API no .env e os logs do terminal para erros detalhados em agente.py.")
            print("--- ERRO APP: inicializar_agent_executor retornou None. Verifique erros em agente.py ou no terminal. ---")
            st.stop()
        else:
             print("--- DEBUG APP: Agent Executor inicializado com sucesso e armazenado na sessão. ---")
    # Sempre pega o executor da sessão depois de inicializado ou se já existia
    agent_executor = st.session_state.get('agent_executor_initialized') 
else:
     # Este caso não deve ocorrer se o try/except na importação funcionar
     if agent_module_imported: 
        st.error("Erro: A função de inicialização do agente não pôde ser carregada.")
        st.stop()

# --- Mensagem Inicial e Exibição do Histórico ---
# Adiciona a mensagem inicial APENAS se o histórico estiver vazio E o agente estiver pronto
if agent_executor and len(msgs.messages) == 0: 
    msgs.add_ai_message("Olá! Eu sou a Marina, sua assistente de dados da Supply Marine. Como posso te ajudar hoje?")

chat_display_area = st.container()
with chat_display_area:
    # Limpa plot/tabela anterior se houve novo input do usuário (antes de processar e exibir o novo)
    if st.session_state.user_input_trigger:
        st.session_state.last_table_markdown = None
        st.session_state.plot_fig = None

    # Exibe mensagens do histórico
    for msg_idx, msg in enumerate(msgs.messages):
        with st.chat_message(msg.type):
            if msg.type == "ai" and isinstance(msg.content, str) and msg.content.strip().startswith("<!DOCTYPE html>"):
                print(f"--- DEBUG APP: Renderizando mensagem AI (índice {msg_idx}) como HTML. ---")
                st.markdown(msg.content, unsafe_allow_html=True)
            else:
                st.write(msg.content) # Renderiza como texto/markdown padrão

    # Lógica do Botão Gerar Gráfico (só aparece se houver tabela na última resposta AI)
    if st.session_state.last_table_markdown:
        st.markdown("---")
        if st.button("📊 Gerar Gráfico", key="plot_button_final_v3"): # Nova chave
            print(f"--- DEBUG APP: Botão Gerar Gráfico clicado. Markdown guardado: {st.session_state.last_table_markdown[:200]}...")
            markdown_content = st.session_state.last_table_markdown
            # Regex para extrair a tabela markdown (simplificada)
            table_match = re.search(r"(\s*\|.*\|\s*\n\s*\|(?: *\:?-+?\:? *\|)+?\s*\n(?: *\|.*\|\s*\n?)+)", markdown_content, re.MULTILINE)
            if table_match:
                table_md = table_match.group(1).strip()
                print(f"--- DEBUG APP: Markdown da tabela extraído para plotagem:\n{table_md}")
                try:
                    # Usa StringIO para ler o markdown como se fosse um CSV com separador |
                    lines = table_md.split('\n')
                    # Pega o cabeçalho removendo pipes extras e espaços
                    header_line = lines[0]
                    header = [h.strip() for h in re.sub(r"(^ *\||\| *$)", "", header_line).split('|')]
                    # Pega linhas de dados, remove pipes extras
                    data_lines = [re.sub(r"(^ *\||\| *$)", "", line.strip()) for line in lines[2:] if line.strip()]
                    if not data_lines: raise ValueError("Nenhuma linha de dados encontrada.")
                    
                    data_io = io.StringIO("\n".join(data_lines))
                    df = pd.read_csv(data_io, sep='|', names=header, skipinitialspace=True)
                    
                    # Limpa espaços extras em todas as células
                    df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
                    print(f"--- DEBUG APP: DataFrame parseado para plotagem:\n{df.head()}")

                    if df.empty or len(df.columns) < 2:
                        st.warning("Não foi possível extrair dados válidos da tabela para o gráfico.")
                    else:
                        x_col = df.columns[0] # Assume primeira coluna como X
                        y_col = df.columns[1] # Assume segunda coluna como Y
                        
                        df_plot = df[[x_col, y_col]].copy()

                        # Tenta limpar a coluna Y para ser numérica (remove R$, ., troca , por .)
                        def clean_numeric_column(series):
                            if series.dtype == 'object':
                                series_cleaned = series.astype(str).str.replace('R$', '', regex=False).str.strip()
                                series_cleaned = series_cleaned.str.replace('.', '', regex=False) 
                                series_cleaned = series_cleaned.str.replace(',', '.', regex=False) 
                                return pd.to_numeric(series_cleaned, errors='coerce')
                            return pd.to_numeric(series, errors='coerce') 

                        df_plot[y_col] = clean_numeric_column(df_plot[y_col])
                        df_plot.dropna(subset=[y_col], inplace=True) # Remove linhas onde Y não pôde ser convertido
                        
                        print(f"--- DEBUG APP: DataFrame para plotar (Y limpo):\n{df_plot.head()}")

                        if not df_plot.empty:
                            title = f"Gráfico: {y_col.replace('_', ' ').title()} por {x_col.title()}"
                            try:
                                fig = px.bar(df_plot, x=x_col, y=y_col, title=title, text_auto='.2s')
                                fig.update_traces(textposition='outside')
                                fig.update_layout(xaxis_title=x_col.title(), yaxis_title=y_col.replace('_', ' ').title())
                                st.session_state.plot_fig = fig # Armazena a figura na sessão
                                print(f"--- DEBUG APP: Gráfico Plotly gerado e armazenado na sessão. ---")
                            except Exception as plot_err:
                                st.error(f"Erro ao gerar o gráfico com Plotly: {plot_err}")
                                print(f"--- ERRO APP: Plotly falhou: {plot_err} ---")
                                traceback.print_exc()
                        else:
                            st.warning("Não há dados numéricos válidos para plotar na coluna Y após a limpeza.")
                except Exception as parse_err:
                    st.error(f"Erro ao processar a tabela Markdown para o gráfico: {parse_err}")
                    print(f"--- ERRO APP: Parsing da tabela para gráfico falhou: {parse_err} ---")
                    traceback.print_exc()
            else:
                st.warning("Não encontrei uma tabela formatada na última resposta para gerar o gráfico.")
                print(f"--- AVISO APP: Regex (plotagem) não encontrou tabela no markdown guardado. ---")
            st.session_state.last_table_markdown = None # Limpa para o botão sumir após tentativa
            st.rerun() # Roda novamente para exibir o gráfico (ou erro) e remover o botão


    # Área para Exibição do Gráfico Gerado pelo Botão
    if st.session_state.plot_fig:
        st.markdown("---")
        st.plotly_chart(st.session_state.plot_fig, use_container_width=True)

    # --- Área de Input do Usuário ---
    user_prompt = None
    # Reset user_input_trigger *before* checking for new input
    st.session_state.user_input_trigger = False 

    # Input via botão de sugestão
    if 'clicked_suggestion' in st.session_state and st.session_state.clicked_suggestion:
        user_prompt = st.session_state.clicked_suggestion
        st.session_state.clicked_suggestion = None # Limpa a sugestão clicada
        st.session_state.user_input_trigger = True # Indica que houve um input
        print(f"--- DEBUG APP: Input via SUGESTÃO: {user_prompt} ---")

    # Input via campo de chat
    if prompt_from_field := st.chat_input("Faça sua pergunta sobre os dados...", key="user_text_input_final_v3"): # Nova chave
        user_prompt = prompt_from_field
        st.session_state.user_input_trigger = True # Indica que houve um input
        print(f"--- DEBUG APP: Input via CAMPO DE TEXTO: {user_prompt} ---")

    # --- Processamento do Input e Interação com Agente ---
    if user_prompt:
        # Limpa plot/tabela anterior se usuário iniciou nova interação
        if st.session_state.user_input_trigger:
            print(f"--- DEBUG APP: Novo prompt '{user_prompt[:50]}...', limpando plot_fig e last_table_markdown ANTES do processamento do agente. ---")
            st.session_state.plot_fig = None
            st.session_state.last_table_markdown = None

        # Adiciona mensagem do usuário ao histórico e exibe
        st.chat_message("user").write(user_prompt)
        # Adiciona ao histórico da Langchain se for nova ou diferente da última
        if not msgs.messages or msgs.messages[-1].type != "user" or msgs.messages[-1].content != user_prompt:
            msgs.add_user_message(user_prompt)

        # Verifica se é pergunta sobre capacidades
        if check_for_capabilities_question(user_prompt):
            print(f"--- DEBUG APP: Pergunta sobre capacidades ('{user_prompt}'). Respondendo direto. ---")
            st.chat_message("ai").write(CAPABILITIES_TEXT)
            msgs.add_ai_message(CAPABILITIES_TEXT)
            st.session_state.user_input_trigger = False # Reseta o trigger aqui
            st.rerun() # Re-renderiza para mostrar a resposta

        # Se não for pergunta sobre capacidades e o agente estiver pronto, invoca o agente
        elif agent_executor:
            with st.spinner("Marina está pensando... 🧠"):
                try:
                    print(f"--- DEBUG APP: Invocando agente com input: '{user_prompt[:100]}...' ---")
                    agent_input = {"input": user_prompt} 
                    response = agent_executor.invoke(agent_input) # <<< CHAMADA REAL AO AGENTE >>>

                    ai_response_content = "Desculpe, não obtive uma resposta válida." 
                    if response and isinstance(response, dict) and 'output' in response:
                        ai_response_content = response['output']
                        print(f"--- DEBUG APP: Resposta recebida do agente (tipo: {type(ai_response_content)}). Trecho: {str(ai_response_content)[:200]}... ---")
                        
                        is_html_report = isinstance(ai_response_content, str) and ai_response_content.strip().startswith("<!DOCTYPE html>")
                        has_multiple_pipes = isinstance(ai_response_content, str) and ai_response_content.count('|') > 4 
                        has_separator_line = isinstance(ai_response_content, str) and any(sep in ai_response_content for sep in ["\n|---", "\n|:---", "\n| ---", "\n| :---"])

                        if not is_html_report and has_multiple_pipes and has_separator_line:
                             print(f"--- DEBUG APP: TABELA DETECTADA (genérico) na resposta do AGENTE. Armazenando markdown para botão de gráfico. ---")
                             st.session_state.last_table_markdown = ai_response_content
                             st.session_state.plot_fig = None 
                    
                    # A LINHA ABAIXO FOI REMOVIDA/COMENTADA PARA EVITAR DUPLICAÇÃO
                    # msgs.add_ai_message(ai_response_content) 

                except Exception as e:
                    error_type_str = type(e).__name__
                    error_details = str(e)
                    st.error(f"Ocorreu um erro técnico ({error_type_str}) ao processar sua pergunta.")
                    print(f"--- ERRO APP: agent_executor.invoke falhou: ---"); traceback.print_exc(); print(f"---")
                    # Adiciona mensagem de erro ao histórico também
                    msgs.add_ai_message(f"Desculpe, encontrei um erro técnico ({error_type_str}) ao tentar responder. Detalhes: {error_details}")

            # Após processar (ou falhar), reseta o trigger e re-renderiza
            st.session_state.user_input_trigger = False 
            print(f"--- DEBUG APP: Solicitando rerun após invoke/erro do agente. ---")
            st.rerun()

        elif not agent_executor: # Caso o agente não tenha inicializado corretamente
            st.error("O agente não está pronto. Verifique os logs do terminal.")
            st.session_state.user_input_trigger = False # Reseta mesmo se falhar

    print("--- DEBUG APP: Fim do script principal assistente_app.py ---")