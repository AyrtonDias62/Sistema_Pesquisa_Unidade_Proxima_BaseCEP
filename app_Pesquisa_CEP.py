import streamlit as st
import pandas as pd
import math
import re
import unicodedata
import requests
import openrouteservice
from openrouteservice import client
import folium
from streamlit_folium import st_folium
from datetime import datetime

# --- 1. CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Tecnolab Logística V9.0", layout="wide", page_icon="🚚")

# --- CSS ADAPTATIVO (SUPORTE A MODO CLARO E ESCURO) ---
st.markdown("""
    <style>
    .block-container { padding-top: 3.5rem; padding-bottom: 0rem; }

    .titulo-v86 {
        color: #2E86C1;
        margin: 0;
        font-size: 28px;
        font-weight: bold;
    }

    [data-testid="stMetric"] {
        background-color: var(--secondary-background-color);
        padding: 15px;
        border-radius: 10px;
        border: 1px solid var(--border-color);
        box-shadow: 0px 2px 4px rgba(0,0,0,0.1);
    }

    .stTextInput label {
        color: var(--text-color) !important;
        font-weight: bold;
    }

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


# --- CORREÇÕES MANUAIS DE GEOCODIFICAÇÃO ---
# Use este dicionário para CEPs em que o geocodificador posiciona o marcador em ponto incorreto.
# Formato: "CEP sem hífen": {"lat": latitude, "lon": longitude, "observacao": "texto opcional"}
COORDENADAS_CEP_CORRIGIDAS = {
    "09666000": {
        "lat": -23.6584497,
        "lon": -46.6063854,
        "observacao": "Correção manual: CEP 09666-000 / Rua Santos / Taboão / São Bernardo do Campo"
    }
}


# --- FUNÇÕES DE APOIO ---
def normalizar_cep(cep: str) -> str:
    """Mantém apenas os dígitos do CEP."""
    return re.sub(r"\D", "", str(cep or ""))


def normalizar_texto(texto: str) -> str:
    """Remove acentos, converte para minúsculas e reduz espaços."""
    texto = str(texto or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def texto_valido(valor) -> bool:
    return bool(valor) and str(valor).strip().upper() not in {"N/A", "NONE", "NULL", "NAN"}


def coordenada_valida(lat, lon) -> bool:
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return False

    # Limite amplo do Brasil. Evita coordenadas invertidas ou resultados fora do país.
    return -34.0 <= lat <= 6.0 and -74.0 <= lon <= -34.0


def extrair_coordenadas_brasilapi_v2(dados: dict):
    """Extrai latitude/longitude quando o endpoint CEP V2 da BrasilAPI retornar geolocalização."""
    if not isinstance(dados, dict):
        return None, None

    location = dados.get("location") or {}
    coordinates = location.get("coordinates") or {}

    lat = None
    lon = None

    if isinstance(coordinates, dict):
        lat = coordinates.get("latitude")
        lon = coordinates.get("longitude")
    elif isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
        # Algumas APIs usam [longitude, latitude].
        lon, lat = coordinates[0], coordinates[1]

    if coordenada_valida(lat, lon):
        return float(lat), float(lon)

    return None, None


def montar_payload_cep(dados: dict, fonte: str, cep_limpo: str):
    """Padroniza o retorno de diferentes APIs de CEP."""
    lat, lon = extrair_coordenadas_brasilapi_v2(dados)

    return {
        "cep": dados.get("cep", cep_limpo),
        "logradouro": dados.get("logradouro") or dados.get("street") or "N/A",
        "bairro": dados.get("bairro") or dados.get("neighborhood") or "N/A",
        "localidade": dados.get("localidade") or dados.get("city") or "N/A",
        "uf": dados.get("uf") or dados.get("state") or "SP",
        "fonte": fonte,
        "lat_api": lat,
        "lon_api": lon,
    }


def consultar_brasilapi_v2(cep_limpo: str):
    url = f"https://brasilapi.com.br/api/cep/v2/{cep_limpo}"
    response = requests.get(url, timeout=10, headers={"User-Agent": "Tecnolab-Streamlit/1.0"})
    response.raise_for_status()
    return response.json()


def consultar_brasilapi_v1(cep_limpo: str):
    url = f"https://brasilapi.com.br/api/cep/v1/{cep_limpo}"
    response = requests.get(url, timeout=10, headers={"User-Agent": "Tecnolab-Streamlit/1.0"})
    response.raise_for_status()
    return response.json()


def consultar_cep(cep: str):
    """
    Consulta o CEP com tolerância a falhas.

    Ordem:
        1. ViaCEP para dados básicos.
        2. BrasilAPI V2 para enriquecer com coordenadas, quando disponível.
        3. BrasilAPI V2/V1 como fallback se o ViaCEP falhar.
    """
    cep_limpo = normalizar_cep(cep)

    if len(cep_limpo) != 8:
        return None, "CEP inválido. Informe um CEP com 8 dígitos."

    tentativas = []

    # --- 1ª tentativa: ViaCEP ---
    try:
        url_viacep = f"https://viacep.com.br/ws/{cep_limpo}/json/"
        response = requests.get(url_viacep, timeout=10, headers={"User-Agent": "Tecnolab-Streamlit/1.0"})
        response.raise_for_status()
        dados = response.json()

        if dados.get("erro"):
            return None, "CEP não encontrado na base do ViaCEP."

        payload = montar_payload_cep(dados, "ViaCEP", cep_limpo)

        # Enriquecimento opcional: tenta obter coordenadas na BrasilAPI V2, sem derrubar o app.
        try:
            dados_v2 = consultar_brasilapi_v2(cep_limpo)
            lat_v2, lon_v2 = extrair_coordenadas_brasilapi_v2(dados_v2)
            if coordenada_valida(lat_v2, lon_v2):
                payload["lat_api"] = lat_v2
                payload["lon_api"] = lon_v2
                payload["fonte_coordenada_api"] = "BrasilAPI V2"
        except Exception as e:
            payload["fonte_coordenada_api"] = None
            payload["observacao_api_coords"] = f"BrasilAPI V2 sem coordenada disponível: {type(e).__name__}"

        return payload, None

    except requests.exceptions.Timeout:
        tentativas.append("ViaCEP: timeout")
    except requests.exceptions.ConnectionError:
        tentativas.append("ViaCEP: falha de conexão")
    except requests.exceptions.HTTPError as e:
        tentativas.append(f"ViaCEP: erro HTTP {e}")
    except requests.exceptions.RequestException as e:
        tentativas.append(f"ViaCEP: {e}")
    except ValueError:
        tentativas.append("ViaCEP: resposta JSON inválida")

    # --- 2ª tentativa: BrasilAPI V2 ---
    try:
        dados = consultar_brasilapi_v2(cep_limpo)
        return montar_payload_cep(dados, "BrasilAPI V2", cep_limpo), None
    except requests.exceptions.Timeout:
        tentativas.append("BrasilAPI V2: timeout")
    except requests.exceptions.ConnectionError:
        tentativas.append("BrasilAPI V2: falha de conexão")
    except requests.exceptions.HTTPError as e:
        if getattr(e.response, "status_code", None) == 404:
            return None, "CEP não encontrado nas bases consultadas."
        tentativas.append(f"BrasilAPI V2: erro HTTP {e}")
    except requests.exceptions.RequestException as e:
        tentativas.append(f"BrasilAPI V2: {e}")
    except ValueError:
        tentativas.append("BrasilAPI V2: resposta JSON inválida")

    # --- 3ª tentativa: BrasilAPI V1 ---
    try:
        dados = consultar_brasilapi_v1(cep_limpo)
        return montar_payload_cep(dados, "BrasilAPI V1", cep_limpo), None
    except requests.exceptions.Timeout:
        tentativas.append("BrasilAPI V1: timeout")
    except requests.exceptions.ConnectionError:
        tentativas.append("BrasilAPI V1: falha de conexão")
    except requests.exceptions.HTTPError as e:
        if getattr(e.response, "status_code", None) == 404:
            return None, "CEP não encontrado nas bases consultadas."
        tentativas.append(f"BrasilAPI V1: erro HTTP {e}")
    except requests.exceptions.RequestException as e:
        tentativas.append(f"BrasilAPI V1: {e}")
    except ValueError:
        tentativas.append("BrasilAPI V1: resposta JSON inválida")

    return None, (
        "Não foi possível consultar o CEP nos serviços externos. "
        "Verifique saída HTTPS, DNS, proxy/firewall do servidor/container do Streamlit. "
        f"Detalhes: {' | '.join(tentativas)}"
    )


def diagnosticar_conectividade_cep(cep: str):
    """Executa testes simples de conectividade HTTP para exibir no Streamlit."""
    cep_limpo = normalizar_cep(cep)
    if len(cep_limpo) != 8:
        return []

    endpoints = [
        ("ViaCEP", f"https://viacep.com.br/ws/{cep_limpo}/json/"),
        ("BrasilAPI V2", f"https://brasilapi.com.br/api/cep/v2/{cep_limpo}"),
        ("BrasilAPI V1", f"https://brasilapi.com.br/api/cep/v1/{cep_limpo}"),
    ]

    resultados = []
    for nome, url in endpoints:
        inicio = datetime.now()
        try:
            response = requests.get(url, timeout=10, headers={"User-Agent": "Tecnolab-Streamlit/1.0"})
            duracao_ms = int((datetime.now() - inicio).total_seconds() * 1000)
            resultados.append({
                "Serviço": nome,
                "Status": response.status_code,
                "Tempo_ms": duracao_ms,
                "Resultado": "OK" if response.ok else "HTTP não OK"
            })
        except Exception as e:
            duracao_ms = int((datetime.now() - inicio).total_seconds() * 1000)
            resultados.append({
                "Serviço": nome,
                "Status": "-",
                "Tempo_ms": duracao_ms,
                "Resultado": f"{type(e).__name__}: {e}"
            })

    return resultados


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


def pontuar_feature_geocodificacao(feature, logradouro, bairro, cidade, uf, cep_limpo):
    props = feature.get("properties", {}) or {}
    geom = feature.get("geometry", {}) or {}
    coords = geom.get("coordinates", [])

    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return -9999

    lon, lat = coords[0], coords[1]
    if not coordenada_valida(lat, lon):
        return -9999

    partes = []
    for chave in ["label", "name", "street", "locality", "localadmin", "neighbourhood", "county", "region", "postalcode", "country"]:
        valor = props.get(chave)
        if valor:
            partes.append(str(valor))
    texto = normalizar_texto(" ".join(partes))

    score = 0

    confidence = props.get("confidence")
    try:
        score += float(confidence) * 100
    except (TypeError, ValueError):
        pass

    logradouro_n = normalizar_texto(logradouro)
    bairro_n = normalizar_texto(bairro)
    cidade_n = normalizar_texto(cidade)
    uf_n = normalizar_texto(uf)

    if logradouro_n and logradouro_n in texto:
        score += 120
    else:
        tokens_relevantes = [t for t in logradouro_n.split() if len(t) >= 4]
        score += sum(12 for t in tokens_relevantes if t in texto)

    if bairro_n and bairro_n in texto:
        score += 45
    if cidade_n and cidade_n in texto:
        score += 65
    if uf_n and uf_n in texto:
        score += 20
    if cep_limpo and cep_limpo in re.sub(r"\D", "", texto):
        score += 100

    # Bônus para a região de atuação do app: Grande São Paulo/ABC.
    try:
        lat_f = float(lat)
        lon_f = float(lon)
        if -24.1 <= lat_f <= -23.3 and -47.1 <= lon_f <= -45.8:
            score += 30
    except (TypeError, ValueError):
        pass

    return score


@st.cache_data(show_spinner=False, ttl=86400)
def buscar_coordenadas_por_geocoder(cep_limpo, logradouro, bairro, cidade, uf):
    """
    Busca coordenadas no ORS/Pelias usando mais contexto do que apenas rua + cidade.
    O retorno é avaliado por pontuação para reduzir escolha de ponto incorreto.
    """
    termos_busca = []

    if texto_valido(logradouro):
        termos_busca.append(
            ", ".join(
                item for item in [logradouro, bairro, cidade, uf, cep_limpo, "Brasil"]
                if texto_valido(item)
            )
        )
        termos_busca.append(
            ", ".join(
                item for item in [logradouro, bairro, cidade, uf, "Brasil"]
                if texto_valido(item)
            )
        )
        termos_busca.append(
            ", ".join(
                item for item in [logradouro, cidade, uf, "Brasil"]
                if texto_valido(item)
            )
        )

    if texto_valido(cidade):
        termos_busca.append(
            ", ".join(
                item for item in [bairro, cidade, uf, cep_limpo, "Brasil"]
                if texto_valido(item)
            )
        )

    termos_busca.append(f"{cep_limpo}, Brasil")

    # Remove duplicidades preservando ordem.
    termos_busca = list(dict.fromkeys(termos_busca))

    melhor = None
    ultimo_erro = None
    candidatos_diag = []

    for termo in termos_busca:
        try:
            try:
                geo_res = ors_client.pelias_search(
                    text=termo,
                    size=10,
                    focus_point=[-46.5594, -23.6912],
                    country="BR"
                )
            except TypeError:
                # Compatibilidade com versões do openrouteservice sem parâmetro country.
                geo_res = ors_client.pelias_search(
                    text=termo,
                    size=10,
                    focus_point=[-46.5594, -23.6912]
                )

            features = geo_res.get("features", [])
            for feature in features:
                coords = feature.get("geometry", {}).get("coordinates", [])
                if len(coords) < 2:
                    continue

                lon_c, lat_c = coords[0], coords[1]
                if not coordenada_valida(lat_c, lon_c):
                    continue

                score = pontuar_feature_geocodificacao(feature, logradouro, bairro, cidade, uf, cep_limpo)
                props = feature.get("properties", {}) or {}
                label = props.get("label") or props.get("name") or termo

                candidatos_diag.append({
                    "termo": termo,
                    "label": label,
                    "lat": float(lat_c),
                    "lon": float(lon_c),
                    "score": round(score, 2)
                })

                if melhor is None or score > melhor["score"]:
                    melhor = {
                        "lat": float(lat_c),
                        "lon": float(lon_c),
                        "score": score,
                        "label": label,
                        "termo": termo
                    }

        except Exception as e:
            ultimo_erro = str(e)

    if melhor:
        candidatos_diag = sorted(candidatos_diag, key=lambda x: x["score"], reverse=True)[:10]
        return melhor["lat"], melhor["lon"], {
            "fonte": "OpenRouteService/Pelias",
            "label": melhor["label"],
            "termo": melhor["termo"],
            "score": round(melhor["score"], 2),
            "candidatos": candidatos_diag
        }, None

    if ultimo_erro:
        return None, None, None, f"Erro ao geolocalizar o endereço pela ORS: {ultimo_erro}"

    return None, None, None, "Não foi possível localizar coordenadas para o endereço retornado pelo CEP."


def obter_coordenadas_cliente(cep_limpo, dados_cep):
    """Define a melhor fonte de coordenadas do cliente."""
    if cep_limpo in COORDENADAS_CEP_CORRIGIDAS:
        item = COORDENADAS_CEP_CORRIGIDAS[cep_limpo]
        lat = item.get("lat")
        lon = item.get("lon")
        if coordenada_valida(lat, lon):
            return float(lat), float(lon), {
                "fonte": "Correção manual por CEP",
                "label": item.get("observacao", "Correção manual"),
                "termo": cep_limpo,
                "score": None,
                "candidatos": []
            }, None

    lat_api = dados_cep.get("lat_api")
    lon_api = dados_cep.get("lon_api")
    if coordenada_valida(lat_api, lon_api):
        return float(lat_api), float(lon_api), {
            "fonte": dados_cep.get("fonte_coordenada_api") or dados_cep.get("fonte") or "API de CEP",
            "label": "Coordenada retornada pela API de CEP",
            "termo": cep_limpo,
            "score": None,
            "candidatos": []
        }, None

    return buscar_coordenadas_por_geocoder(
        cep_limpo=cep_limpo,
        logradouro=dados_cep.get("logradouro") or "N/A",
        bairro=dados_cep.get("bairro") or "N/A",
        cidade=dados_cep.get("localidade") or "N/A",
        uf=dados_cep.get("uf") or "SP"
    )


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
        r, erro_cep = consultar_cep(cep_limpo)

    if erro_cep:
        st.error(erro_cep)

        with st.expander("Diagnóstico técnico da consulta CEP"):
            if st.button("Testar serviços de CEP agora", use_container_width=True):
                resultados_diag = diagnosticar_conectividade_cep(cep_limpo)
                if resultados_diag:
                    st.dataframe(pd.DataFrame(resultados_diag), use_container_width=True, hide_index=True)
                else:
                    st.warning("Informe um CEP válido para executar o diagnóstico.")

    else:
        logra = r.get("logradouro") or "N/A"
        bairro = r.get("bairro") or "N/A"
        cidade = r.get("localidade") or "N/A"
        uf = r.get("uf") or "SP"

        lat_c, lon_c, diag_geo, erro_geo = obter_coordenadas_cliente(cep_limpo, r)

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
            st.info(
                f"📍 **Endereço:** {logra}, {bairro}, {cidade}/{uf}  \n"
                f"Fonte CEP: {r.get('fonte', 'N/A')}  \n"
                f"Fonte coordenada: {diag_geo.get('fonte', 'N/A') if diag_geo else 'N/A'}"
            )

            with st.expander("Diagnóstico de geocodificação"):
                st.write({
                    "CEP": cep_limpo,
                    "Latitude usada": lat_c,
                    "Longitude usada": lon_c,
                    "Fonte": diag_geo.get("fonte") if diag_geo else "N/A",
                    "Resultado selecionado": diag_geo.get("label") if diag_geo else "N/A",
                    "Termo pesquisado": diag_geo.get("termo") if diag_geo else "N/A",
                    "Score": diag_geo.get("score") if diag_geo else "N/A",
                })

                candidatos = diag_geo.get("candidatos") if diag_geo else []
                if candidatos:
                    st.caption("Principais candidatos retornados pelo geocodificador")
                    st.dataframe(pd.DataFrame(candidatos), use_container_width=True, hide_index=True)
                else:
                    st.caption("Coordenada definida por API de CEP ou correção manual. Sem lista de candidatos ORS/Pelias.")

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
                    "UF": uf,
                    "Fonte Coordenada": diag_geo.get("fonte") if diag_geo else "N/A",
                    "Lat Cliente": lat_c,
                    "Lon Cliente": lon_c,
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
                tooltip=f"Cliente - {cep_limpo}",
                popup=f"{logra}, {bairro}, {cidade}/{uf}<br>Fonte: {diag_geo.get('fonte') if diag_geo else 'N/A'}",
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

            st_folium(m, use_container_width=True, height=600, key="mapa_v90")


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

