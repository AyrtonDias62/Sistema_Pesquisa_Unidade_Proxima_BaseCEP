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
st.set_page_config(page_title="Tecnolab Log V7.5", layout="wide")

# --- CSS RECALIBRADO ---
st.markdown("""
    <style>
    /* Reduz o respiro do topo sem sufocar o título */
    .block-container {
        padding-top: 1.5rem; 
        padding-bottom: 0rem;
    }
    /* Estilização das métricas */
    [data-testid="stMetric"] {
        background-color: var(--secondary-background-color);
        padding: 8px 15px;
        border-radius: 10px;
        border: 1px solid rgba(128, 128, 128, 0.2);
    }
    /* Ajuste fino no espaçamento do título */
    .titulo-container {
        margin-bottom: 15px;
        border-bottom: 2px solid #2E86C1;
        padding-bottom: 5px;
    }
    </style>
    """, unsafe_allow_html=True)

try:
    api_key = st.secrets["ORS_KEY"]
    ors_client = client.Client(key=api_key)
except Exception as e:
    st.error("Erro: Configure a ORS_KEY nas Secrets.")
    st.stop()

# --- LOGIN ---
if "autenticado" not in st.session_state:
    st.title("🔐 Acesso Tecnolab")
    senha = st.text_input("Senha:", type="password")
    if st.button("Entrar"):
        if senha == "123456": 
            st.session_state["autenticado"] = True
            st.rerun()
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

PARES_PROXIMOS = [{"U6", "U14"}, {"U11", "U5"}]

def calcular_distancia_reta(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return round(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a))), 2)

@st.cache_data(show_spinner=False)
def definir_unidade_sugerida_cache(lat_c, lon_c, unidades):
    for u in unidades:
        u['dist_reta'] = calcular_distancia_reta(lat_c, lon_c, u['lat'], u['lon'])
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

# --- TÍTULO REVISADO ---
st.markdown('<div class="titulo-container"><h2 style="color: #2E86C1; margin: 0;">📍 Painel Logístico Tecnolab</h2></div>', unsafe_allow_html=True)

if 'historico' not in st.session_state: st.session_state['historico'] = []

# Input de CEP
cep = st.text_input("Digite o CEP do Cliente:", placeholder="00000-000", key="input_cep")

if cep and len(cep.replace("-","")) == 8:
    r = requests.get(f"https://viacep.com.br/ws/{cep.replace('-','')}/json/").json()
    if "erro" not in r:
        logra, bairro, cidade = r.get('logradouro','N/A'), r.get('bairro','N/A'), r.get('localidade','N/A')
        
        try:
            geo_res = ors_client.pelias_search(text=f"{logra}, {cidade}, SP, Brasil", size=1, focus_point=[-46.55, -23.69])
            coords = geo_res['features'][0]['geometry']['coordinates'] if geo_res['features'] else [0,0]
            lat_c, lon_c = coords[1], coords[0]
            
            sugerida_nome = definir_unidade_sugerida_cache(lat_c, lon_c, unidades_base)
            for u in unidades_base: u['Dist. Reta (km)'] = calcular_distancia_reta(lat_c, lon_c, u['lat'], u['lon'])
            
            df_comp = pd.DataFrame(unidades_base).sort_values('Dist. Reta (km)')
            
            # --- COLUNAS ---
            col_left, col_right = st.columns([1, 1.3])

            with col_left:
                st.info(f"**Endereço:** {logra}, {bairro}")
                escolha = st.selectbox("Unidade para Atendimento:", df_comp['nome'].tolist(), index=df_comp['nome'].tolist().index(sugerida_nome))
                unidade_f = next(item for item in unidades_base if item["nome"] == escolha)

                route_res = ors_client.directions(coordinates=((unidade_f['lon'], unidade_f['lat']), (lon_c, lat_c)), profile='driving-car', format='geojson')
                dist_real = round(route_res['features'][0]['properties']['summary']['distance'] / 1000, 2)
                tempo_min = int(route_res['features'][0]['properties']['summary']['duration'] / 60)

                st.metric("Caminho Real (Condução)", f"{dist_real} km", delta=f"{tempo_min} min")
                
                if st.button("✅ Registrar Atendimento", use_container_width=True):
                    st.session_state['historico'].insert(0, {"Hora": datetime.now().strftime("%H:%M"), "CEP": cep, "Unid": escolha, "KM": dist_real})
                    st.success("Dados salvos no histórico.")

                # ALTURA AUMENTADA PARA MOSTRAR TODAS AS LINHAS (12 unidades)
                st.dataframe(df_comp[['nome', 'Dist. Reta (km)']], use_container_width=True, hide_index=True, height=450)

            with col_right:
                m = folium.Map(location=[lat_c, lon_c], zoom_start=12)
                folium.Marker([lat_c, lon_c], icon=folium.Icon(color='red', icon='user')).add_to(m)
                folium.Marker([unidade_f['lat'], unidade_f['lon']], icon=folium.Icon(color='green', icon='plus')).add_to(m)
                folium.PolyLine([[p[1], p[0]] for p in route_res['features'][0]['geometry']['coordinates']], color="#2E86C1", weight=6, opacity=0.8).add_to(m)
                st_folium(m, use_container_width=True, height=650, key="mapa_v75")

        except Exception as e: st.error(f"Erro no cálculo: {e}")
    else: st.error("CEP não localizado.")

if st.session_state.get('historico'):
    st.divider()
    st.subheader("📝 Últimos Registros")
    st.table(pd.DataFrame(st.session_state['historico']).head(5))
