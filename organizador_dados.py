import pandas as pd
import sqlite3
import chromadb
import openpyxl # Mesmo que não use diretamente, precisa estar instalado
import re # Importado para limpeza de dados no Chroma se necessário
import io # Importado para possível parse de markdown (não usado na versão final, mas pode deixar)

# --- Constantes ---
NOME_ARQUIVO_EXCEL = 'zeroteste.xlsx' # Verifique se é o nome correto da sua NOVA planilha
NOME_BANCO_SQLITE = 'meus_dados.db'
NOME_COLECAO_CHROMA = 'minha_colecao_textos'
# Escolha uma coluna de texto importante para o ChromaDB. 'servico_descricao' é geralmente melhor que 'atendimento_num'.
COLUNA_TEXTO_IMPORTANTE = 'servico_descricao' # <-- SUGIRO MUDAR PARA ESTA! Mas pode manter 'atendimento_num' se preferir.

print(f"Lendo o arquivo Excel: {NOME_ARQUIVO_EXCEL}...")
try:
    # Lê a aba/planilha chamada 'Base'
    df = pd.read_excel(NOME_ARQUIVO_EXCEL, sheet_name='Base')
    print("--- DEBUG: Colunas encontradas pelo script ---")
    print(df.columns.tolist())
    print("--- FIM DEBUG ---")
    print("Excel lido com sucesso!")

    # --- FORMATAÇÃO DE DATA 'data_faturamento' ---
    coluna_data = 'data_faturamento'
    print(f"Formatando a coluna '{coluna_data}' para texto YYYY-MM-DD...")
    if coluna_data in df.columns:
        try:
            # Converte para datetime, erros viram NaT
            df[coluna_data] = pd.to_datetime(df[coluna_data], errors='coerce')
            # Formata como texto YYYY-MM-DD, NaT vira None (NULL no banco)
            df[coluna_data] = df[coluna_data].dt.strftime('%Y-%m-%d')
            print(f"Coluna '{coluna_data}' formatada com sucesso.")
        except Exception as e_date:
            print(f"Aviso: Ocorreu um erro ao tentar formatar a coluna '{coluna_data}': {e_date}")
            # Continua mesmo se der erro na formatação da data
    else:
        print(f"Aviso: Coluna '{coluna_data}' não encontrada. Pulando formatação de data.")
    # --- FIM DA FORMATAÇÃO DE DATA ---

    # 1. Salvar no Banco de Dados Estruturado (SQLite)
    print(f"Conectando ao banco de dados SQLite: {NOME_BANCO_SQLITE}...")
    conn = sqlite3.connect(NOME_BANCO_SQLITE)
    # Salva a tabela, substituindo se já existir. A coluna de data irá como TEXT.
    df.to_sql('minha_tabela_principal', conn, if_exists='replace', index=False)
    conn.close()
    print("Dados salvos no SQLite com sucesso! (Coluna de data como TEXT)")

    # 2. Salvar no Banco de Dados Vetorial (ChromaDB)
    print("Preparando dados para o ChromaDB...")
    # Pega os textos da coluna escolhida, remove vazios e converte para string
    if COLUNA_TEXTO_IMPORTANTE in df.columns:
      textos = df[COLUNA_TEXTO_IMPORTANTE].dropna().astype(str).tolist()
      # Cria IDs únicos baseados no índice do DataFrame original
      ids = df[COLUNA_TEXTO_IMPORTANTE].dropna().index.astype(str).tolist()
      # Garante que IDs sejam únicos (caso haja índices duplicados por algum motivo)
      if len(ids) != len(set(ids)):
          print("Aviso: IDs gerados para ChromaDB não são únicos, usando renumeração simples.")
          ids = [str(i) for i in range(len(textos))]

    else:
        print(f"Erro Crítico: A coluna '{COLUNA_TEXTO_IMPORTANTE}' definida para ChromaDB não existe na planilha!")
        textos = [] # Garante que a lista esteja vazia para não prosseguir
        ids = []

    # Verifica se a lista 'textos' não está vazia
    if textos:
        print(f"Conectando ao ChromaDB (local)...")
        client = chromadb.PersistentClient(path="./chroma_db_storage")
        collection = client.get_or_create_collection(NOME_COLECAO_CHROMA)

        # --- Bloco de Batching CORRIGIDO ---
        total_items = len(textos)
        batch_size = 4000 # Tamanho seguro para cada lote
        print(f"Adicionando {total_items} textos ao ChromaDB em lotes de {batch_size}...")

        # Loop para processar em lotes
        for i in range(0, total_items, batch_size):
            # Pega o lote atual
            batch_texts = textos[i:i + batch_size]
            batch_ids = ids[i:i + batch_size] # Usa os IDs correspondentes ao lote

            if not batch_texts:
                continue

            print(f"  - Adicionando lote de {len(batch_texts)} itens (começando do item {i})...")
            try:
                # Adiciona o lote ao ChromaDB
                collection.add(
                    documents=batch_texts,
                    ids=batch_ids # Corrigido para usar batch_ids
                )
            except Exception as e_chroma_batch:
                print(f"    * Erro ao adicionar lote iniciado em {i}: {e_chroma_batch}")
                # Continua para o próximo lote

        # Mensagem final DEPOIS do loop
        print("Textos adicionados/atualizados no ChromaDB!")
        # --- Fim do Bloco de Batching CORRIGIDO ---

    else:
        print("Nenhum texto válido encontrado na coluna especificada para adicionar ao ChromaDB.")

    print("\nOrganização dos dados concluída!")

# Blocos de tratamento de erro principal
except FileNotFoundError:
    print(f"Erro CRÍTICO: Arquivo Excel '{NOME_ARQUIVO_EXCEL}' não encontrado!")
    print("Verifique o nome e o local do arquivo.")
except ImportError as e_import:
     print(f"Erro CRÍTICO de importação: {e_import}")
     print("Verifique se todas as bibliotecas (pandas, openpyxl, sqlite3, chromadb) estão instaladas no venv com 'pip install ...'")
except KeyError as e_key:
     print(f"Erro CRÍTICO: A coluna '{COLUNA_TEXTO_IMPORTANTE}' não foi encontrada no Excel durante a preparação para o ChromaDB!")
     print("Verifique o nome da coluna na constante COLUNA_TEXTO_IMPORTANTE e na sua planilha Excel.")
except Exception as e:
    print(f"Ocorreu um erro inesperado CRÍTICO durante a execução: {e}")
    import traceback
    traceback.print_exc() # Imprime mais detalhes do erro