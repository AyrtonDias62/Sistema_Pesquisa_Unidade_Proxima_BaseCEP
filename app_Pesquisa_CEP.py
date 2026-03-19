import streamlit as st
import pd as pd
import math
import requests
import openrouteservice
from openrouteservice import client
import folium
from streamlit_folium import st_folium
from datetime import datetime

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Sistema Logístico Tecnolab - V7.1", layout="wide")

# Inicialização do Cliente de Mapas (ORS)
try:
    api_key = st.secrets["ORS_KEY"]
    ors_client = client.Client(key=api_key)
except Exception as e:
    st.error("Erro: Configure a ORS_KEY nas Secrets.")

# --- LOGIN SIMPLES ---
if "autenticado" not in st.session_state:
    st.title("🔐 Acesso ao Sistema Tecnolab")
    senha = st.text_input("Digite a senha de acesso:", type="password")
    if st.button("Entrar"):
        if senha == "123456": 
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.stop()

# --- BASE DE UNIDADES ---
unidades_base = [
    {"nome": "Matriz", "lat": -23.6912, "lon": -46.5594},
    {"nome": "U2", "lat": -23.70601, "lon": -46.54946},
    {"nome": "U4", "lat": -23.709069, "lon": -46.413002},
    {"nome": "U5", "lat": -23.65458, "lon": -46.53554},
    {"nome": "U6", "lat": -23.66669, "lon": -46.45455},
    {"nome": "U7", "lat": -23.66117, "lon": -46.56506},
    {"nome": "U8", "lat": -23.72231, "lon": -46.56675},
    {"nome": "U9", "lat": -23.61659, "lon": -46.56845},
    {"nome": "U10", "lat": -23.6326784, "lon": -46.5021218},
    {"nome": "U11", "lat": -23.65379, "lon": -46.53542},
    {"nome": "U13", "lat": -23.68791, "lon": -46.62192},
    {"nome": "U14", "lat": -23.66884, "lon": -46.45567},
]

def calcular_distancia_reta(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return round(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a))), 2)

# CACHE PARA EVITAR LOOPS E CONSUMO EXCESSIVO DE API
@st.cache_data(show_spinner=False)
def definir_unidade_sugerida_cache(lat_c, lon_c, unidades_json):
    # Converte de volta para lista de dicts (cache do streamlit prefere tipos básicos)
    unidades = unidades_json 
    
    # 1. Filtra as 3 mais próximas em linha reta
    for u in unidades:
        u['temp_reta'] = calcular_distancia_reta(lat_c, lon_c, u['lat'], u['lon'])
    
    candidatas = sorted(unidades, key=lambda x: x['temp_reta'])[:3]
    
    melhor_u = None
    menor_km_real = float('inf')
    
    # 2. Testa a rota real apenas para as 3 finalistas
    for cand in candidatas:
        try:
            route = ors_client.directions(
                coordinates=((lon_c, lat_c), (cand['lon'], cand['lat'])),
                profile='driving-car', format='geojson'
            )
            dist_km = route['features'][0]['properties']['summary']['distance'] / 1000
            if dist_km < menor_km_real:
                menor_km_real = dist_km
                melhor_u = cand['nome']
        except:
            continue
            
    return melhor_u if melhor_u else candidatas[0]['nome']

# --- INTERFACE ---
st.title("📍 Painel Logístico Atendimento")

if 'historico' not in st.session_state:
    st.session_state['historico'] = []

# Uso do on_change ou botão para evitar o loop constante de processamento
cep = st.text_input("CEP do Cliente:", placeholder="Ex: 09010-000", key="input_cep")

if cep and len(cep.replace("-","")) == 8:
    r = requests.get(f"https://viacep.com.br/ws/{cep.replace('-','')}/json/").json()
    
    if "erro" not in r:
        logra, bairro, cidade = r.get('logradouro','N/A'), r.get('bairro','N/A'), r.get('localidade','N/A')
        st.info(f"📍 Endereço: {logra} - {bairro}, {cidade}")

        try:
            # Busca Coordenadas
            geo_res = ors_client.pelias_search(text=f"{logra}, {cidade}, SP, Brasil", size=1, focus_point=[-46.55, -23.69])
            if geo_res and len(geo_res['features']) > 0:
                coords = geo_res['features'][0]['geometry']['coordinates']
                lat_c, lon_c = coords[1], coords[0]
            else:
                geo_cep = ors_client.pelias_search(text=f"{cep}, Brasil", size=1)
                coords = geo_cep['features'][0]['geometry']['coordinates']
                lat_c, lon_c = coords[1], coords[0]
            
            # SUGESTÃO COM CACHE (Evita o loop de atualização)
            sugerida_nome = definir_unidade_sugerida_cache(lat_c, lon_c, unidades_base)

            # Atualiza distâncias de reta para a tabela
            for u in unidades_base:
                u['Dist. Reta (km)'] = calcular_distancia_reta(lat_c, lon_c, u['lat'], u['lon'])
            
            df_comparativo = pd.DataFrame(unidades_base).sort_values('Dist. Reta (km)')

            col_left, col_right = st.columns([1, 1.5])

            with col_left:
                st.subheader("🏁 Atendimento")
                # Pré-seleção baseada na rota real
                lista_nomes = df_comparativo['nome'].tolist()
                idx_sugerida = lista_nomes.index(sugerida_nome) if sugerida_nome in lista_nomes else 0
                
                escolha = st.selectbox(
                    "Unidade Selecionada:", 
                    lista_nomes,
                    index=idx_sugerida
                )
                
                unidade_f = next(item for item in unidades_base if item["nome"] == escolha)

                # Rota Real Final
                route_res = ors_client.directions(
                    coordinates=((lon_c, lat_c), (unidade_f['lon'], unidade_f['lat'])),
                    profile='driving-car', format='geojson'
                )
                dist_real = round(route_res['features'][0]['properties']['summary']['distance'] / 1000, 2)
                tempo_min = round(route_res['features'][0]['properties']['summary']['duration'] / 60, 0)
                caminho = [[p[1], p[0]] for p in route_res['features'][0]['geometry']['coordinates']]

                st.metric("Distância Real", f"{dist_real} km", delta=f"{int(tempo_min)} min", delta_color="normal")
                
                if st.button("✅ Registrar Atendimento", use_container_width=True):
                    st.session_state['historico'].insert(0, {
                        "Horário": datetime.now().strftime("%H:%M"),
                        "CEP": cep,
                        "Unid. Sugerida": sugerida_nome,
                        "Unid. Escolhida": escolha,
                        "Distância": f"{dist_real} km"
                    })
                    st.success("Registrado!")
                    st.rerun()

                st.divider()
                st.dataframe(df_comparativo[['nome', 'Dist. Reta (km)']], use_container_width=True, hide_index=True)

            with col_right:
                m = folium.Map(location=[lat_c, lon_c], zoom_start=12)
                folium.Marker([lat_c, lon_c], icon=folium.Icon(color='red')).add_to(m)
                folium.Marker([unidade_f['lat'], unidade_f['lon']], icon=folium.Icon(color='green')).add_to(m)
                folium.PolyLine(caminho, color="#2E86C1", weight=5).add_to(m)
                st_folium(m, use_container_width=True, height=500, key="mapa_v71")

        except Exception as e:
            st.error(f"Erro no processamento: {e}")
    else:
        st.error("CEP não encontrado.")

if st.session_state.get('historico'):
    st.divider()
    st.write("📝 **Histórico Recente**")
    st.table(pd.DataFrame(st.session_state['historico']).head(5))
