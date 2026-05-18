import streamlit as st
import pandas as pd
import math
import re
import requests
import openrouteservice
from openrouteservice import client
import folium
from streamlit_folium import st_folium
from datetime import datetime

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Tecnolab Logística V8.8", layout="wide", page_icon="🚚")

# --- CSS ADAPTATIVO (SUPORTE A MODO CLARO E ESCURO) ---
st.markdown("""
    <style>
    .block-container { padding-top: 3.5rem; padding-bottom: 0rem; }

    /* Título Adaptativo */
    .titulo-v86 {
        color: #2E86C1;
        margin: 0;
        font-size: 28px;
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
except Exception as e:
    st.error(f"Erro ao carregar a ORS_KEY: {e}")
    st.stop()


# --- FUNÇÕES DE APOIO ---
def normalizar_cep(cep: str) -> str:
    """Mantém apenas os dígitos do CEP."""
    return re.sub(r"\D", "", str(cep or ""))


@st.cache_data(show_spinner=False, ttl=86400)
def consultar_viacep(cep: str):
    """
    Consulta o ViaCEP com tratamento de falhas de conexão, timeout, HTTP e JSON.

    Retorno:
        tuple(dict|None, str|None): dados do CEP ou mensagem de erro.
    """
    cep_limpo = normalizar_cep(cep)

    if len(cep_limpo) != 8:
        return None, "CEP inválido. Informe um CEP com 8 dígitos."

    url = f"https://viacep.com.br/ws/{cep_limpo}/json/"

    try:
        response = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": "Tecnolab-Streamlit/1.0"}
        )
        response.raise_for_status()

        dados = response.json()

        if dados.get("erro"):
            return None, "CEP não encontrado na base do ViaCEP."

        return dados, None

    except requests.exceptions.Timeout:
        return None, "Tempo esgotado ao consultar o ViaCEP. Tente novamente."

    except requests.exceptions.ConnectionError:
        return None, (
            "Não foi possível conectar ao ViaCEP. "
            "Verifique internet, DNS, proxy ou firewall do servidor/container do Streamlit."
        )

    except requests.exceptions.HTTPError as e:
        return None, f"Erro HTTP ao consultar o ViaCEP: {e}"

    except requests.exceptions.RequestException as e:
        return None, f"Falha ao consultar o ViaCEP: {e}"

    except ValueError:
        return None, "O ViaCEP retornou uma resposta inválida ou fora do formato JSON esperado."


def calcular_distancia_reta(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return round(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))), 2)


@st.cache_data(show_spinner=False, ttl=86400)
def obter_distancia_real(lon1, lat1, lon2, lat2):
    """Calcula a distância real pela OpenRouteService."""
    try:
        route = ors_client.directions(
            coordinates=((lon1, lat1), (lon2, lat2)),
            profile="driving-car",
            format="geojson"
        )

        features = route.get("features", [])
        if not features:
            return None, None, None

        summary = features[0].get("properties", {}).get("summary", {})
        dist = summary.get("distance")
        dur = summary.get("duration")

        if dist is None or dur is None:
            return None, None, None

        return round(dist / 1000, 2), int(dur / 60), route

    except Exception:
        return None, None, None


@st.cache_data(show_spinner=False, ttl=86400)
def buscar_coordenadas_endereco(logradouro, bairro, cidade):
    """Busca coordenadas do endereço no OpenRouteService/Pelias."""
    termos_busca = []

    if logradouro and logradouro != "N/A":
        termos_busca.append(
            ", ".join(
                item for item in [logradouro, bairro, cidade, "SP", "Brasil"]
                if item and item != "N/A"
            )
        )

    if cidade and cidade != "N/A":
        termos_busca.append(f"{cidade}, SP, Brasil")

    if not termos_busca:
        return None, None, "Endereço insuficiente para geolocalização."

    ultimo_erro = None

    for termo in termos_busca:
        try:
            geo_res = ors_client.pelias_search(
                text=termo,
                size=1,
                focus_point=[-46.5594, -23.6912]
            )

            features = geo_res.get("features", [])
            if features:
                coords = features[0].get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    lon_c, lat_c = coords[0], coords[1]
                    return lat_c, lon_c, None

        except Exception as e:
            ultimo_erro = str(e)

    if ultimo_erro:
        return None, None, f"Erro ao geolocalizar o endereço pela ORS: {ultimo_erro}"

    return None, None, "Não foi possível localizar coordenadas para o endereço retornado pelo CEP."


# --- LOGIN ---
if "autenticado" not in st.session_state:
    st.title("🔐 Acesso Tecnolab")
    senha = st.text_input("Senha:", type="password")
    if st.button("Entrar"):
        senha_app = st.secrets.get("APP_PASSWORD", "123456")
        if senha == senha_app:
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Senha inválida.")
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

# Corrigido: no cadastro das unidades consta U5 - SAD e U11 - SAD, não U5 - SAC / U11 - SAC.
PARES_PROXIMOS = [{"U6 - MAU", "U14 - MAU"}, {"U11 - SAD", "U5 - SAD"}]


# --- CABEÇALHO ---
c_logo, c_tit = st.columns([1.2, 4])
with c_logo:
    try:
        st.image("furgao_tecnolab.png", width=220)
    except Exception:
        st.warning("🚚 Imagem não encontrada")

with c_tit:
    st.markdown(
        '<div class="header-container"><h1 class="titulo-v86">'
        'Localizador CEP Cliente x Un. Tecnolab'
        '</h1></div>',
        unsafe_allow_html=True
    )

if "historico" not in st.session_state:
    st.session_state["historico"] = []


# --- ENTRADA DO CEP ---
cep = st.text_input("CEP do Cliente:", placeholder="Ex: 09134-740", key="input_cep")
cep_limpo = normalizar_cep(cep)

if cep and len(cep_limpo) != 8:
    st.warning("Informe um CEP válido com 8 dígitos.")

elif cep_limpo:
    with st.spinner("Consultando CEP e calculando unidade mais próxima..."):
        r, erro_cep = consultar_viacep(cep_limpo)

    if erro_cep:
        st.error(erro_cep)

    else:
        logra = r.get("logradouro") or "N/A"
        bairro = r.get("bairro") or "N/A"
        cidade = r.get("localidade") or "N/A"

        lat_c, lon_c, erro_geo = buscar_coordenadas_endereco(logra, bairro, cidade)

        if erro_geo:
            st.error(erro_geo)
            st.stop()

        for u in unidades_base:
            u["dist_reta"] = calcular_distancia_reta(lat_c, lon_c, u["lat"], u["lon"])

        ordenadas = sorted(unidades_base, key=lambda x: x["dist_reta"])

        finalistas, vistos = [], set()
        for u in ordenadas:
            if len(finalistas) >= 3:
                break

            par = next((g for g in PARES_PROXIMOS if u["nome"] in g), None)
            if par:
                id_g = tuple(sorted(list(par)))
                if id_g not in vistos:
                    finalistas.append(u)
                    vistos.add(id_g)
            else:
                finalistas.append(u)

        melhor_u_nome = finalistas[0]["nome"]
        menor_km_real = None

        for f in finalistas:
            d, _, _ = obter_distancia_real(f["lon"], f["lat"], lon_c, lat_c)
            if d is not None and (menor_km_real is None or d < menor_km_real):
                menor_km_real = d
                melhor_u_nome = f["nome"]

        df_comp = pd.DataFrame(unidades_base).sort_values("dist_reta")

        cl, cr = st.columns([1, 1.4])

        with cl:
            st.info(f"📍 **Endereço:** {logra}, {bairro}, {cidade}")

            lista_unidades = df_comp["nome"].tolist()
            index_sugerido = lista_unidades.index(melhor_u_nome) if melhor_u_nome in lista_unidades else 0

            escolha = st.selectbox(
                "Selecione a Unidade:",
                lista_unidades,
                index=index_sugerido
            )

            u_sel = next(u for u in unidades_base if u["nome"] == escolha)
            u_sug = next(u for u in unidades_base if u["nome"] == melhor_u_nome)

            dist_escolhida, tempo_escolhido, rota_final = obter_distancia_real(
                u_sel["lon"], u_sel["lat"], lon_c, lat_c
            )
            dist_sugerida, _, _ = obter_distancia_real(
                u_sug["lon"], u_sug["lat"], lon_c, lat_c
            )

            m1, m2 = st.columns(2)
            m1.metric(
                "Distância Real",
                f"{dist_escolhida} km" if dist_escolhida is not None else "Indisponível"
            )
            m2.metric(
                "Tempo Est.",
                f"{tempo_escolhido} min" if tempo_escolhido is not None else "Indisponível"
            )

            rota_disponivel = (
                dist_escolhida is not None
                and tempo_escolhido is not None
                and dist_sugerida is not None
            )

            if not rota_disponivel:
                st.warning(
                    "Não foi possível calcular a rota real pela ORS neste momento. "
                    "A comparação por linha reta permanece disponível."
                )

            if st.button("✅ Registrar Atendimento", use_container_width=True, disabled=not rota_disponivel):
                desvio = round(dist_escolhida - dist_sugerida, 2)

                st.session_state["historico"].insert(0, {
                    "Data/Hora": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "CEP Cliente": cep_limpo,
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

            st.dataframe(
                df_comp[["nome", "dist_reta"]].rename(columns={"dist_reta": "Km Reta"}),
                use_container_width=True,
                hide_index=True,
                height=400
            )

        with cr:
            m = folium.Map(location=[lat_c, lon_c], zoom_start=13)

            folium.Marker(
                [lat_c, lon_c],
                tooltip="Cliente",
                icon=folium.Icon(color="red", icon="home")
            ).add_to(m)

            folium.Marker(
                [u_sel["lat"], u_sel["lon"]],
                tooltip=escolha,
                icon=folium.Icon(color="green", icon="plus")
            ).add_to(m)

            if rota_final:
                coords_rota = rota_final["features"][0]["geometry"]["coordinates"]
                folium.PolyLine(
                    [[p[1], p[0]] for p in coords_rota],
                    color="#2E86C1",
                    weight=6
                ).add_to(m)

            st_folium(m, use_container_width=True, height=600, key="mapa_v88")


# --- HISTÓRICO ---
if st.session_state["historico"]:
    st.divider()
    df_h = pd.DataFrame(st.session_state["historico"])

    h1, h2 = st.columns([3, 1])
    with h1:
        st.subheader("📝 Histórico Operacional")

    with h2:
        csv = df_h.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 Exportar CSV",
            csv,
            "relatorio_tecnolab.csv",
            "text/csv",
            use_container_width=True
        )

    st.dataframe(df_h, use_container_width=True, hide_index=True)
