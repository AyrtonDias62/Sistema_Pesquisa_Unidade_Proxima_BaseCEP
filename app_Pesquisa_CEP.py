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
st.set_page_config(page_title="Tecnolab Logística V8.6", layout="wide", page_icon="🚚")

# --- CSS ADAPTATIVO (SUPORTE A MODO CLARO E ESCURO) ---
st.markdown("""
    <style>
    .block-container { padding-top: 3.5rem; padding-bottom: 0rem; }
    
    /* Título Adaptativo */
    .titulo-v86 { 
        color: #2E86C1; 
        margin: 0; 
        font-size: 10px; 
        font-weight: bold; 
    }

    /* Quadros de Métricas Adaptativos */
    [data-testid="stMetric"] {
        background-color: var(--secondary-background-color);
        padding: 15px;
        border-radius: 10px;
        border: 1px solid var(--border-color);
        box-shadow: 0px 2px 4px rgba(0,0,0,0.1);
    }

    /* Estilização extra para garantir visibilidade do rótulo do CEP */
    .stTextInput label {
        color: var(--text-color) !important;
        font-weight: bold;
    }

    /* Linha divisória que respeita o tema */
    .header-container {
        border-bottom: 3px solid #2E86C1;
        padding-bottom: 15px;
        margin-bottom: 25px;
    }
    </style>
    """, unsafe_allow_html=True)

# --- CLIENTE ORS ---
try:
    api_key = st.secrets["ORS_KEY"]
    ors_client = client.Client(key=api_key)
except:
    st.error("Erro na ORS_KEY.")
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

# --- DADOS ---
unidades_base = [
    {"nome": "Matriz SBC", "lat": -23.6912, "lon": -46.5594},
    {"nome": "U2 - SBC", "lat": -23.70601, "lon": -46.54946},
    {"nome": "U4 - RIB", "lat": -23.709069, "lon": -46.413002},
    {"nome": "U5 - SAD", "lat": -23.65458, "lon": -46.53554},
    {"nome": "U6 - MAU", "lat": -23.66669, "lon": -46.45455},
    {"nome": "U7 - SBC", "lat": -23.66117, "lon": -46.56506},
    {"nome": "U8 - SBC", "lat": -23.72231, "lon": -46.56675},
    {"nome": "U9 - SAC", "lat": -23.61659, "lon": -46.56845},
    {"nome": "U10 - SAD", "lat": -23.6326784, "lon": -46.5021218},
    {"nome": "U11 - SAD", "lat": -23.65379, "lon": -46.53542},
    {"nome": "U13 - DIA", "lat": -23.68791, "lon": -46.62192},
    {"nome": "U14 - MAU", "lat": -23.66884, "lon": -46.45567},
]
PARES_PROXIMOS = [{"U6 - MAU", "U14 - MAU"}, {"U11 - SAC", "U5 - SAC"}]

def calcular_distancia_reta(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return round(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a))), 2)

@st.cache_data(show_spinner=False)
def obter_distancia_real(lon1, lat1, lon2, lat2):
    try:
        route = ors_client.directions(coordinates=((lon1, lat1), (lon2, lat2)), profile='driving-car', format='geojson')
        dist = route['features'][0]['properties']['summary']['distance'] / 1000
        dur = route['features'][0]['properties']['summary']['duration'] / 60
        return round(dist, 2), int(dur), route
    except: return None, None, None

# --- CABEÇALHO ---
c_logo, c_tit = st.columns([1.2, 4])
with c_logo:
    try: st.image("furgao_tecnolab.png", width=220)
    except: st.warning("🚚 Imagem não encontrada")
with c_tit:
    st.markdown('<div class="header-container"><h1 class="titulo-v86">Painel Localizador CEP Cliente x Unidade Tecnolab mais próxima</h1></div>', unsafe_allow_html=True)

if 'historico' not in st.session_state: st.session_state['historico'] = []

# Entrada
cep = st.text_input("CEP do Cliente:", placeholder="Ex: 09134-740", key="input_cep")

if cep and len(cep.replace("-","")) == 8:
    r = requests.get(f"https://viacep.com.br/ws/{cep.replace('-','')}/json/").json()
    if "erro" not in r:
        logra, bairro, cidade = r.get('logradouro','N/A'), r.get('bairro','N/A'), r.get('localidade','N/A')
        
        try:
            geo_res = ors_client.pelias_search(text=f"{logra}, {cidade}, SP, Brasil", size=1, focus_point=[-46.5594, -23.6912])
            coords = geo_res['features'][0]['geometry']['coordinates']
            lat_c, lon_c = coords[1], coords[0]
            
            for u in unidades_base: u['dist_reta'] = calcular_distancia_reta(lat_c, lon_c, u['lat'], u['lon'])
            ordenadas = sorted(unidades_base, key=lambda x: x['dist_reta'])
            
            finalistas, vistos = [], set()
            for u in ordenadas:
                if len(finalistas) >= 3: break
                par = next((g for g in PARES_PROXIMOS if u['nome'] in g), None)
                if par:
                    id_g = tuple(sorted(list(par)))
                    if id_g not in vistos: finalistas.append(u); vistos.add(id_g)
                else: finalistas.append(u)
            
            melhor_u_nome = finalistas[0]['nome']
            menor_km_real = 999
            for f in finalistas:
                d, _, _ = obter_distancia_real(f['lon'], f['lat'], lon_c, lat_c)
                if d and d < menor_km_real: menor_km_real = d; melhor_u_nome = f['nome']
            
            df_comp = pd.DataFrame(unidades_base).sort_values('dist_reta')
            
            cl, cr = st.columns([1, 1.4])
            with cl:
                st.info(f"📍 **Endereço:** {logra}, {bairro}")
                escolha = st.selectbox("Selecione a Unidade:", df_comp['nome'].tolist(), index=df_comp['nome'].tolist().index(melhor_u_nome))
                
                u_sel = next(u for u in unidades_base if u["nome"] == escolha)
                u_sug = next(u for u in unidades_base if u["nome"] == melhor_u_nome)
                
                dist_escolhida, tempo_escolhido, rota_final = obter_distancia_real(u_sel['lon'], u_sel['lat'], lon_c, lat_c)
                dist_sugerida, _, _ = obter_distancia_real(u_sug['lon'], u_sug['lat'], lon_c, lat_c)

                m1, m2 = st.columns(2)
                m1.metric("Distância Real", f"{dist_escolhida} km")
                m2.metric("Tempo Est.", f"{tempo_escolhido} min")
                
                if st.button("✅ Registrar Atendimento", use_container_width=True):
                    desvio = round(dist_escolhida - dist_sugerida, 2)
                    st.session_state['historico'].insert(0, {
                        "Data/Hora": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "CEP Cliente": cep,
                        "Endereço": logra,
                        "Bairro": bairro,
                        "Cidade": cidade,
                        "Unid. Sugerida": melhor_u_nome,
                        "Unid. Escolhida": escolha,
                        "KM Real": dist_escolhida,
                        "Dif.(KM)": desvio,
                        "Tempo (Min)": tempo_escolhido
                    })
                    st.balloons()
                    st.toast("Gravado!")

                st.dataframe(df_comp[['nome', 'dist_reta']].rename(columns={'dist_reta': 'Km Reta'}), use_container_width=True, hide_index=True, height=400)

            with cr:
                m = folium.Map(location=[lat_c, lon_c], zoom_start=13)
                folium.Marker([lat_c, lon_c], icon=folium.Icon(color='red', icon='home')).add_to(m)
                folium.Marker([u_sel['lat'], u_sel['lon']], icon=folium.Icon(color='green', icon='plus')).add_to(m)
                if rota_final:
                    folium.PolyLine([[p[1], p[0]] for p in rota_final['features'][0]['geometry']['coordinates']], color="#2E86C1", weight=6).add_to(m)
                st_folium(m, use_container_width=True, height=600, key="mapa_v86")

        except Exception as e: st.error(f"Erro: {e}")

# --- HISTÓRICO ---
if st.session_state['historico']:
    st.divider()
    df_h = pd.DataFrame(st.session_state['historico'])
    h1, h2 = st.columns([3, 1])
    with h1: st.subheader("📝 Histórico Operacional")
    with h2:
        csv = df_h.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 Exportar CSV", csv, "relatorio_tecnolab.csv", "text/csv", use_container_width=True)
    st.dataframe(df_h, use_container_width=True, hide_index=True)
