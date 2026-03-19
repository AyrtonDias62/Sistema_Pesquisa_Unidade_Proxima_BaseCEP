import streamlit as st
import pandas as pd
import math
import requests
import openrouteservice
from openrouteservice import client
import folium
from streamlit_folium import st_folium
from datetime import datetime

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Tecnolab Log V7.9", layout="wide")

# --- CSS RECALIBRADO ---
st.markdown("""
    <style>
    /* 1. Espaço no topo para evitar cortes */
    .block-container {
        padding-top: 2rem; 
        padding-bottom: 1rem;
    }
    
    /* 2. Cabeçalho alinhado e com linha divisória */
    .header-wrapper {
        display: flex;
        align-items: center;
        border-bottom: 3px solid #2E86C1;
        padding-bottom: 15px;
        margin-bottom: 20px;
    }
    
    .titulo-v79 {
        color: #2E86C1;
        margin: 0;
        font-size: 30px;
        font-weight: bold;
        padding-left: 20px;
    }

    /* 3. Ajuste do campo de CEP (Rótulo vs Linha) */
    .stTextInput {
        margin-top: 5px;
    }

    /* 4. Estilo das Métricas */
    [data-testid="stMetric"] {
        background-color: #f8f9fa;
        padding: 10px;
        border-radius: 8px;
        border: 1px solid #e0e0e0;
    }
    </style>
    """, unsafe_allow_html=True)

# --- INICIALIZAÇÃO API ---
try:
    api_key = st.secrets["ORS_KEY"]
    ors_client = client.Client(key=api_key)
except:
    st.error("⚠️ Erro: ORS_KEY não configurada nas Secrets.")
    st.stop()

# --- LOGIN ---
if "autenticado" not in st.session_state:
    st.title("🔐 Acesso Restrito Tecnolab")
    senha = st.text_input("Senha de acesso:", type="password")
    if st.button("Entrar"):
        if senha == "123456": 
            st.session_state["autenticado"] = True
            st.rerun()
    st.stop()

# --- DADOS E FUNÇÕES ---
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
PARES_PROXIMOS = [{"U6", "U14"}, {"U11", "U5"}]

def calcular_distancia_reta(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return round(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a))), 2)

@st.cache_data(show_spinner=False)
def definir_unidade_sugerida(lat_c, lon_c, unidades):
    for u in unidades: u['dist_reta'] = calcular_distancia_reta(lat_c, lon_c, u['lat'], u['lon'])
    ordenadas = sorted(unidades, key=lambda x: x['dist_reta'])
    
    finalistas, grupos_vistos = [], set()
    for u in ordenadas:
        if len(finalistas) >= 3: break
        is_par = False
        for grupo in PARES_PROXIMOS:
            if u['nome'] in grupo:
                is_par = True
                id_g = tuple(sorted(list(grupo)))
                if id_g not in grupos_vistos:
                    finalistas.append(u); grupos_vistos.add(id_g)
                break
        if not is_par: finalistas.append(u)
    
    melhor_u, menor_km = None, float('inf')
    for cand in finalistas:
        try:
            route = ors_client.directions(coordinates=((cand['lon'], cand['lat']), (lon_c, lat_c)), profile='driving-car', format='geojson')
            d = route['features'][0]['properties']['summary']['distance']
            if d < menor_km: menor_km = d; melhor_u = cand['nome']
        except: continue
    return melhor_u if melhor_u else finalistas[0]['nome']

# --- INTERFACE PRINCIPAL ---
# --- CABEÇALHO COM LOGO LOCAL ---
col_h1, col_h2 = st.columns([1, 4])
with col_h1:
    # O Streamlit busca o arquivo na mesma pasta do script
    st.image("furgao_tecnolab.png", width=150)
with col_h2:
    st.markdown('<h1 class="titulo-v79">Painel Localizador CEP Cliente x Unidade Tecnolab mais próxima</h1>', unsafe_allow_html=True)

if 'historico' not in st.session_state: st.session_state['historico'] = []

# Área de Busca
cep = st.text_input("CEP do Cliente:", placeholder="Ex: 09134-740", key="input_cep")

if cep and len(cep.replace("-","")) == 8:
    r = requests.get(f"https://viacep.com.br/ws/{cep.replace('-','')}/json/").json()
    if "erro" not in r:
        logra, bairro, cidade = r.get('logradouro','N/A'), r.get('bairro','N/A'), r.get('localidade','N/A')
        
        try:
            geo_res = ors_client.pelias_search(text=f"{logra}, {cidade}, SP, Brasil", size=1, focus_point=[-46.55, -23.69])
            coords = geo_res['features'][0]['geometry']['coordinates'] if geo_res['features'] else [0,0]
            lat_c, lon_c = coords[1], coords[0]
            
            sugerida_nome = definir_unidade_sugerida(lat_c, lon_c, unidades_base)
            for u in unidades_base: u['Dist. Reta (km)'] = calcular_distancia_reta(lat_c, lon_c, u['lat'], u['lon'])
            df_comp = pd.DataFrame(unidades_base).sort_values('Dist. Reta (km)')
            
            col_left, col_right = st.columns([1, 1.4])

            with col_left:
                st.info(f"📍 **Endereço:** {logra}, {bairro}")
                escolha = st.selectbox("Selecione a Unidade:", df_comp['nome'].tolist(), index=df_comp['nome'].tolist().index(sugerida_nome))
                unidade_f = next(item for item in unidades_base if item["nome"] == escolha)

                route_res = ors_client.directions(coordinates=((unidade_f['lon'], unidade_f['lat']), (lon_c, lat_c)), profile='driving-car', format='geojson')
                dist_real = round(route_res['features'][0]['properties']['summary']['distance'] / 1000, 2)
                tempo_min = int(route_res['features'][0]['properties']['summary']['duration'] / 60)

                m1, m2 = st.columns(2)
                m1.metric("🚗 Distância Real", f"{dist_real} km")
                m2.metric("⏱️ Tempo Estimado", f"{tempo_min} min")
                
                if st.button("✅ Registrar Atendimento", use_container_width=True):
                    st.session_state['historico'].insert(0, {"Hora": datetime.now().strftime("%H:%M"), "CEP": cep, "Unid": escolha, "KM": dist_real})
                    st.toast("Atendimento registrado com sucesso!")

                st.dataframe(df_comp[['nome', 'Dist. Reta (km)']], use_container_width=True, hide_index=True, height=450)

            with col_right:
                m = folium.Map(location=[lat_c, lon_c], zoom_start=13)
                folium.Marker([lat_c, lon_c], popup="Cliente", icon=folium.Icon(color='red', icon='home')).add_to(m)
                folium.Marker([unidade_f['lat'], unidade_f['lon']], popup=escolha, icon=folium.Icon(color='green', icon='plus')).add_to(m)
                folium.PolyLine([[p[1], p[0]] for p in route_res['features'][0]['geometry']['coordinates']], color="#2E86C1", weight=6).add_to(m)
                st_folium(m, use_container_width=True, height=620, key="mapa_v79")

        except Exception as e: st.error(f"Erro no processamento: {e}")
    else:
        st.error("CEP não encontrado.")
