import streamlit as st
import pandas as pd
import math
import requests
import openrouteservice
from openrouteservice import client
import folium
from streamlit_folium import st_folium
from datetime import datetime

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Sistema Logístico Tecnolab - V7.3", layout="wide")

try:
    api_key = st.secrets["ORS_KEY"]
    ors_client = client.Client(key=api_key)
except Exception as e:
    st.error("Erro: Configure a ORS_KEY nas Secrets.")
    st.stop()

# --- LOGIN ---
if "autenticado" not in st.session_state:
    st.title("🔐 Acesso ao Sistema Tecnolab")
    senha = st.text_input("Digite a senha:", type="password")
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

# Definição dos Pares Próximos (Grupos)
PARES_PROXIMOS = [
    {"U6", "U14"},
    {"U11", "U5"}
]

def calcular_distancia_reta(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return round(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a))), 2)

@st.cache_data(show_spinner=False)
def definir_unidade_sugerida_cache(lat_c, lon_c, unidades):
    # 1. Calcula distância reta para todas
    for u in unidades:
        u['dist_reta'] = calcular_distancia_reta(lat_c, lon_c, u['lat'], u['lon'])
    
    ordenadas = sorted(unidades, key=lambda x: x['dist_reta'])
    
    # 2. Lógica de Filtragem de Pares (Deduplicação)
    finalistas = []
    nomes_no_grupo_ja_vistos = set()

    for u in ordenadas:
        if len(finalistas) >= 3:
            break
            
        # Verifica se a unidade pertence a um par "conflitante"
        pertence_a_grupo = False
        for grupo in PARES_PROXIMOS:
            if u['nome'] in grupo:
                pertence_a_grupo = True
                # Se nenhuma outra unidade deste grupo foi adicionada, adicionamos esta
                id_grupo = tuple(sorted(list(grupo)))
                if id_grupo not in nomes_no_grupo_ja_vistos:
                    finalistas.append(u)
                    nomes_no_grupo_ja_vistos.add(id_grupo)
                break
        
        # Se não pertence a nenhum grupo crítico, adiciona normalmente
        if not pertence_a_grupo:
            finalistas.append(u)

    # 3. Avalia Rota Real apenas para as finalistas filtradas
    melhor_u = None
    menor_km_real = float('inf')
    
    for cand in finalistas:
        try:
            route = ors_client.directions(
                coordinates=((cand['lon'], cand['lat']), (lon_c, lat_c)),
                profile='driving-car', format='geojson'
            )
            dist_km = route['features'][0]['properties']['summary']['distance'] / 1000
            if dist_km < menor_km_real:
                menor_km_real = dist_km
                melhor_u = cand['nome']
        except: continue
            
    return melhor_u if melhor_u else finalistas[0]['nome']

# --- INTERFACE ---
st.title("📍 Painel Logístico Tecnolab")

if 'historico' not in st.session_state:
    st.session_state['historico'] = []

cep = st.text_input("CEP do Cliente:", placeholder="Ex: 09134-740", key="input_cep")

if cep and len(cep.replace("-","")) == 8:
    r = requests.get(f"https://viacep.com.br/ws/{cep.replace('-','')}/json/").json()
    
    if "erro" not in r:
        logra, bairro, cidade = r.get('logradouro','N/A'), r.get('bairro','N/A'), r.get('localidade','N/A')
        st.info(f"📍 Endereço: {logra} - {bairro}, {cidade}")

        try:
            # Geocodificação
            geo_res = ors_client.pelias_search(text=f"{logra}, {cidade}, SP, Brasil", size=1, focus_point=[-46.55, -23.69])
            if geo_res and len(geo_res['features']) > 0:
                coords = geo_res['features'][0]['geometry']['coordinates']
                lat_c, lon_c = coords[1], coords[0]
            else:
                geo_cep = ors_client.pelias_search(text=f"{cep}, Brasil", size=1)
                coords = geo_cep['features'][0]['geometry']['coordinates']
                lat_c, lon_c = coords[1], coords[0]
            
            # Sugestão Inteligente (com Deduplicação de Pares)
            sugerida_nome = definir_unidade_sugerida_cache(lat_c, lon_c, unidades_base)

            # Tabela Comparativa
            for u in unidades_base:
                u['Dist. Reta (km)'] = calcular_distancia_reta(lat_c, lon_c, u['lat'], u['lon'])
            
            df_comparativo = pd.DataFrame(unidades_base).sort_values('Dist. Reta (km)')
            lista_nomes = df_comparativo['nome'].tolist()
            idx_sugerida = lista_nomes.index(sugerida_nome) if sugerida_nome in lista_nomes else 0

            col_left, col_right = st.columns([1, 1.5])

            with col_left:
                st.subheader("🏁 Atendimento")
                escolha = st.selectbox("Unidade Selecionada:", lista_nomes, index=idx_sugerida)
                unidade_f = next(item for item in unidades_base if item["nome"] == escolha)

                # Rota Final
                route_res = ors_client.directions(
                    coordinates=((unidade_f['lon'], unidade_f['lat']), (lon_c, lat_c)),
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
                        "Unid. Escolhida": escolha,
                        "Distância": f"{dist_real} km"
                    })
                    st.success("Registrado!")

                st.divider()
                st.dataframe(df_comparativo[['nome', 'Dist. Reta (km)']], use_container_width=True, hide_index=True)

            with col_right:
                m = folium.Map(location=[lat_c, lon_c], zoom_start=12)
                folium.Marker([lat_c, lon_c], icon=folium.Icon(color='red')).add_to(m)
                folium.Marker([unidade_f['lat'], unidade_f['lon']], icon=folium.Icon(color='green')).add_to(m)
                folium.PolyLine(caminho, color="#2E86C1", weight=5).add_to(m)
                st_folium(m, use_container_width=True, height=500, key="mapa_tecnolab_v73")

        except Exception as e:
            st.error(f"Erro: {e}")
