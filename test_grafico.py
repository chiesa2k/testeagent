# test_grafico.py (CORRIGIDO)
import plotly # <<< Adicionado import do módulo principal plotly
import plotly.express as px
import plotly.io as pio
import pandas as pd
import traceback
import os # Para verificar se o arquivo foi criado

print(f"--- Iniciando teste de geração de imagem Plotly/Kaleido ---")

try:
    print(f"Versão do Pandas: {pd.__version__}")
except AttributeError:
    print("Não foi possível obter a versão do Pandas de pd.__version__")

try:
    print(f"Versão do Plotly: {plotly.__version__}") # <<< CORRIGIDO AQUI
except AttributeError:
    print("Não foi possível obter a versão do Plotly de plotly.__version__.")


print(f"Configurando pio.kaleido.scope.plotlyjs para CDN...")
try:
    if pio: # Verifica se pio foi importado corretamente
        pio.kaleido.scope.plotlyjs = "https://cdn.plot.ly/plotly-latest.min.js"
        print(f"pio.kaleido.scope.plotlyjs configurado como: {pio.kaleido.scope.plotlyjs}")
    else:
        print("AVISO: plotly.io (pio) não foi importado, não foi possível configurar plotlyjs para Kaleido.")
except Exception as e_config:
    print(f"ERRO ao configurar pio.kaleido.scope.plotlyjs: {e_config}")

# Dados de exemplo simples
df = pd.DataFrame({
    'Mes': ['JAN', 'FEV', 'MAR'],
    'Valores': [100, 250, 180]
})

fig = px.bar(df, x="Mes", y="Valores", title="Teste de Gráfico Simples")

print("\n--- Tentativa 1: Usando engine='kaleido' explicitamente ---")
try:
    print("Gerando imagem 'teste_grafico_kaleido.png'...")
    if px and pio: # Garante que plotly.express e plotly.io foram importados
        fig.write_image("teste_grafico_kaleido.png", engine="kaleido", scale=1.5, width=500, height=250)
        if os.path.exists("teste_grafico_kaleido.png"):
            print("SUCESSO: Imagem 'teste_grafico_kaleido.png' salva com engine='kaleido'.")
        else:
            print("AVISO: Imagem 'teste_grafico_kaleido.png' não foi criada, apesar de não haver erro explícito.")
    else:
        print("AVISO: Plotly.express ou Plotly.io não importados, pulando tentativa com engine='kaleido'.")
except Exception as e_kaleido:
    print(f"ERRO com engine='kaleido': {e_kaleido}")
    traceback.print_exc()

print("\n--- Tentativa 2: Deixando Plotly escolher o engine (sem especificar 'kaleido') ---")
try:
    print("Gerando imagem 'teste_grafico_default.png'...")
    if px: # Garante que plotly.express foi importado
        fig.write_image("teste_grafico_default.png", scale=1.5, width=500, height=250)
        if os.path.exists("teste_grafico_default.png"):
            print("SUCESSO: Imagem 'teste_grafico_default.png' salva com engine padrão.")
        else:
            print("AVISO: Imagem 'teste_grafico_default.png' não foi criada, apesar de não haver erro explícito.")
    else:
        print("AVISO: Plotly.express não importado, pulando tentativa com engine padrão.")
except Exception as e_default:
    print(f"ERRO com engine padrão: {e_default}")
    traceback.print_exc()

print("\n--- Teste concluído ---")