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
st.set_page_config(page_title="Tecnolab Log V8.0", layout="wide")

# --- CSS PARA LAYOUT COMPACTO E TÍTULO VISÍVEL ---
st.markdown("""
    <style>
    .block-container { padding-top: 2rem; padding-bottom: 0rem; }
    .titulo-v8 { color: #2E86C1; margin: 0; font-size: 28px; font-weight: bold; }
    [data-testid="stMetric"] { background-color: #f8f9fa; padding: 8px; border-radius: 8px; border: 1px solid #e0e0e0; }
    .stButton>button { background-color: #2E86C1; color: white; border-radius: 5px; }
    </style>
    """, unsafe_allow_html=True)

# --- INICIALIZAÇÃO API ---
try:
    api_key = st.secrets["ORS_KEY"]
    ors_client = client.Client(key=api_key)
except:
    st.error("Erro: Verifique a ORS_KEY nas Secrets.")
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

# --- ESTADO DO HISTÓRICO ---
if 'historico' not in st.session_state:
    st.session_state['historico'] = []

# --- DADOS DAS UNIDADES ---
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

# --- FUNÇÕES DE CÁLCULO ---
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

# --- INTERFACE ---
col_logo, col_titulo = st.columns([1, 4])
with col_logo:
    try: st.image("ImagemCarroTecno.png", width=140)
    except: st.write("🚗")
with col_titulo:
    st.markdown('<h1 class="titulo-v8">Painel Logístico Tecnolab</h1>', unsafe_allow_html=True)

cep = st.text_input("CEP do Cliente:", placeholder="00000-000", key="input_cep")

if cep and len(cep.replace("-","")) == 8:
    r = requests.get(f"https://viacep.com.br/ws/{cep.replace('-','')}/json/").json()
    if "erro" not in r:
        logra, bairro = r.get('logradouro','N/A'), r.get('bairro','N/A')
        
        try:
            geo_res = ors_client.pelias_search(text=f"{logra}, São Paulo, Brasil", size=1)
            coords = geo_res['features'][0]['geometry']['coordinates']
            lat_c, lon_c = coords[1], coords[0]
            
            sugerida = definir_unidade_sugerida(lat_c, lon_c, unidades_base)
            for u in unidades_base: u['Dist. Reta (km)'] = calcular_distancia_reta(lat_c, lon_c, u['lat'], u['lon'])
            df_comp = pd.DataFrame(unidades_base).sort_values('Dist. Reta (km)')
            
            c1, c2 = st.columns([1, 1.4])
            with c1:
                st.info(f"📍 {logra}, {bairro}")
                escolha = st.selectbox("Unidade:", df_comp['nome'].tolist(), index=df_comp['nome'].tolist().index(sugerida))
                unidade_f = next(u for u in unidades_base if u["nome"] == escolha)
                
                route = ors_client.directions(coordinates=((unidade_f['lon'], unidade_f['lat']), (lon_c, lat_c)), profile='driving-car', format='geojson')
                km_r = round(route['features'][0]['properties']['summary']['distance'] / 1000, 2)
                min_r = int(route['features'][0]['properties']['summary']['duration'] / 60)
                
                m1, m2 = st.columns(2)
                m1.metric("Distância", f"{km_r} km")
                m2.metric("Tempo", f"{min_r} min")
                
                if st.button("✅ Registrar Atendimento", use_container_width=True):
                    st.session_state['historico'].insert(0, {
                        "Data/Hora": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "CEP": cep,
                        "Unidade": escolha,
                        "KM": km_r,
                        "Tempo (min)": min_r
                    })
                    st.toast("Registrado!")

                st.dataframe(df_comp[['nome', 'Dist. Reta (km)']], use_container_width=True, hide_index=True, height=430)

            with c2:
                m = folium.Map(location=[lat_c, lon_c], zoom_start=13)
                folium.Marker([lat_c, lon_c], icon=folium.Icon(color='red')).add_to(m)
                folium.Marker([unidade_f['lat'], unidade_f['lon']], icon=folium.Icon(color='green')).add_to(m)
                folium.PolyLine([[p[1], p[0]] for p in route['features'][0]['geometry']['coordinates']], color="#2E86C1", weight=6).add_to(m)
                st_folium(m, use_container_width=True, height=580, key="mapa_v8")

        except Exception as e: st.error(f"Erro: {e}")

# --- SEÇÃO DE HISTÓRICO E CSV ---
if st.session_state['historico']:
    st.divider()
    h_col1, h_col2 = st.columns([3, 1])
    with h_col1:
        st.subheader("📝 Histórico de Consultas")
    with h_col2:
        df_hist = pd.DataFrame(st.session_state['historico'])
        csv = df_hist.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 Baixar Histórico (CSV)",
            data=csv,
            file_name=f"logistica_tecnolab_{datetime.now().strftime('%Y%m%d')}.csv",
            mime='text/csv',
            use_container_width=True
        )
    st.dataframe(df_hist, use_container_width=True, hide_index=True)
