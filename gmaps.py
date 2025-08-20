import streamlit as st
import pandas as pd
import numpy as np
import requests
import pydeck as pdk
from urllib.parse import urlparse

st.set_page_config(page_title="Local Prospector", layout="wide")
st.title("📍 Local Prospector — File Outscraper → Azioni")
st.caption("Carica l'Excel di Outscraper, filtra, mappa e calcola un punteggio di opportunità per interventi su sito e Google Business Profile.")

# -----------------------------
# Sidebar: upload + help
# -----------------------------
with st.sidebar:
    st.header("⚙️ Importa dati")
    up = st.file_uploader("Excel da Outscraper (.xlsx) o CSV", type=["xlsx","xls","csv"])  
    st.markdown("**Colonne attese** (se presenti): name, site, phone, full_address, city, latitude, longitude, rating, reviews, photos_count, reviews_per_score_1..5")
    st.divider()
    st.header("🔧 Opzioni punteggio")
    w1 = st.slider("Peso Volume recensioni", 0.0, 1.0, 0.45, 0.05)
    w2 = st.slider("Peso Quota recensioni negative (1-3★)", 0.0, 1.0, 0.35, 0.05)
    w3 = st.slider("Peso Gap da 4.6★", 0.0, 1.0, 0.20, 0.05)
    w4 = st.slider("Peso poche foto (per recensione)", 0.0, 1.0, 0.10, 0.05)
    total_w = w1 + w2 + w3 + w4
    if total_w == 0:
        st.error("I pesi non possono essere tutti zero.")

# -----------------------------
# Helpers
# -----------------------------
@st.cache_data(show_spinner=False)
def read_excel(file):
    """Supporta sia Excel (openpyxl) sia CSV."""
    from pathlib import Path
    name = getattr(file, "name", "uploaded")
    ext = Path(name).suffix.lower()
    if ext in [".csv", ".txt"]:
        try:
            return pd.read_csv(file)
        except Exception:
            file.seek(0)
            return pd.read_csv(file, sep=";")
    try:
        return pd.read_excel(file, engine="openpyxl")
    except ImportError:
        st.error("Manca `openpyxl`. Aggiungilo a requirements.txt: openpyxl>=3.1.3")
        st.stop()
    except Exception as e:
        st.exception(e)
        st.stop()

@st.cache_data(show_spinner=False)
def guess_cms(url: str, timeout=5):
    """Rileva solo se è WordPress. Restituisce 'WordPress', 'Altro', 'No site'."""
    sess = requests.Session()
    sess.headers.update({"User-Agent":"Mozilla/5.0"})

    def norm_variants(u: str):
        if not u or u == "nan":
            return []
        u = u.strip()
        bases = []
        if not u.startswith("http"):
            bases = [f"https://{u}", f"http://{u}"]
        else:
            bases = [u]
        out = set()
        for b in bases:
            out.add(b)
            if "://www." in b:
                out.add(b.replace("://www.", "://"))
            else:
                parts = b.split("://", 1)
                out.add(parts[0]+"://www."+parts[1])
        return list(out)

    def hit(url: str):
        try:
            return sess.get(url, timeout=timeout, allow_redirects=True)
        except Exception:
            return None

    def looks_wp(r) -> bool:
        if r is None:
            return False
        text = r.text.lower() if isinstance(r.text, str) else ""
        return any(k in text for k in ["wp-content","wp-includes","wp-json","wordpress"])

    variants = norm_variants(url)
    if not variants:
        return "No site"

    for base in variants:
        base = base.rstrip("/")
        r = hit(base)
        if looks_wp(r):
            return "WordPress"
        for ep in ["/wp-login.php", "/wp-admin", "/wp-json"]:
            rr = hit(base + ep)
            if rr and rr.status_code in (200,301,302,401,403):
                if ep == "/wp-json" and "application/json" in rr.headers.get("content-type"," "):
                    return "WordPress"
                return "WordPress"
    return "Altro"

@st.cache_data(show_spinner=False)
def process(df: pd.DataFrame, weights):
    needed = ['name','site','phone','full_address','city','latitude','longitude',
              'rating','reviews','photos_count',
              'reviews_per_score_1','reviews_per_score_2','reviews_per_score_3',
              'reviews_per_score_4','reviews_per_score_5']
    for c in needed:
        if c not in df.columns:
            df[c] = np.nan
    for c in ['reviews','photos_count','reviews_per_score_1','reviews_per_score_2','reviews_per_score_3','reviews_per_score_4','reviews_per_score_5']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    df['rating'] = pd.to_numeric(df['rating'], errors='coerce')

    total_reviews = df['reviews'].replace(0, np.nan)
    bad_share = (df['reviews_per_score_1']+df['reviews_per_score_2']+df['reviews_per_score_3'])/total_reviews
    bad_share = bad_share.fillna(0).clip(0,1)

    volume_norm = np.log10(df['reviews']+1)
    volume_norm = volume_norm/volume_norm.max() if volume_norm.max()>0 else volume_norm*0

    rating_gap = (4.6 - df['rating']).clip(lower=0)/1.6

    photos_per_review = (df['photos_count']/df['reviews']).replace([np.inf,-np.inf],np.nan).fillna(0)
    photos_norm = (photos_per_review/photos_per_review.max()).clip(0,1) if photos_per_review.max()>0 else photos_per_review*0

    w1,w2,w3,w4 = weights
    denom = max(w1+w2+w3+w4, 1e-9)
    raw = w1*volume_norm + w2*bad_share + w3*rating_gap + w4*(1-photos_norm)
    score = (raw/denom)*100

    out = df.copy()
    out['GMB_Opportunity_Score'] = score.round(1)
    out['has_website'] = out['site'].astype(str).str.len().fillna(0)>5
    def maps_link(row):
        lat,lon=row.get('latitude'),row.get('longitude')
        if pd.notnull(lat) and pd.notnull(lon):
            return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        return None
    out['maps_link']=out.apply(maps_link,axis=1)
    return out

# -----------------------------
# Main
# -----------------------------
if up is None:
    st.info("Carica l'Excel per iniziare.")
    st.stop()

raw = read_excel(up)
weights = (w1,w2,w3,w4)
processed = process(raw, weights)

with st.expander("🔎 Rileva WordPress — esegue richieste web, usa con parsimonia"):
    run_cms = st.checkbox("Esegui rilevazione ora", value=False)
    if run_cms:
        processed['cms_guess'] = processed['site'].astype(str).apply(guess_cms)
    else:
        processed['cms_guess'] = np.where(
            processed['site'].astype(str).str.contains('wp-content|wp-json|wp-includes|/wp-', case=False, na=False),
            'WordPress (pattern URL)',
            np.where(processed['has_website'],'Altro','No site')
        )

cities = sorted([c for c in processed['city'].dropna().unique().tolist() if str(c).strip()])
col1,col2,col3,col4=st.columns([1,1,1,1])
with col1:
    city_sel = st.multiselect("Filtra città", cities)
with col2:
    min_reviews = st.number_input("Min. recensioni",0,100000,0,10)
with col3:
    site_filter = st.selectbox("Sito web",["Tutti","Con sito","Senza sito"])
with col4:
    cms_filter = st.selectbox("CMS",["Tutti","WordPress","Altro","No site"])

f=processed.copy()
if city_sel:
    f=f[f['city'].isin(city_sel)]
if min_reviews>0:
    f=f[f['reviews']>=min_reviews]
if site_filter=="Con sito":
    f=f[f['has_website']]
elif site_filter=="Senza sito":
    f=f[~f['has_website']]
if cms_filter!="Tutti":
    if cms_filter=="WordPress":
        f=f[f['cms_guess'].str.contains("WordPress",na=False)]
    else:
        f=f[f['cms_guess']==cms_filter]

sort_col=st.selectbox("Ordina per",["GMB_Opportunity_Score","reviews","rating","city","name"])
sort_dir=st.radio("Direzione",["Desc","Asc"],horizontal=True)
f=f.sort_values(sort_col,ascending=(sort_dir=="Asc"))

# Map
st.subheader("🗺️ Mappa")
map_df=f[['latitude','longitude','name','GMB_Opportunity_Score','rating','reviews']].dropna(subset=['latitude','longitude'])
if len(map_df)>0:
    tile_layer=pdk.Layer(
        "TileLayer",
        data="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        min_zoom=0,
        max_zoom=19,
        tile_size=256,
        pickable=False
    )
    points=pdk.Layer(
        "ScatterplotLayer",
        data=map_df,
        get_position='[longitude, latitude]',
        get_radius=70,
        get_fill_color=[230,57,70],
        get_line_color=[255,255,255],
        line_width_min_pixels=1,
        pickable=True
    )
    view_state=pdk.ViewState(
        latitude=float(map_df['latitude'].mean()),
        longitude=float(map_df['longitude'].mean()),
        zoom=11
    )
    tooltip={"html":"<b>{name}</b><br/>Score: {GMB_Opportunity_Score}<br/>⭐ {rating} • {reviews} rec.","style":{"backgroundColor":"white","color":"black"}}
    st.pydeck_chart(pdk.Deck(layers=[tile_layer,points],initial_view_state=view_state,tooltip=tooltip,map_style=None))
else:
    st.info("Nessun punto mappabile con i filtri correnti.")

# Table
st.subheader("📊 Elenco")
show_cols=['name','city','full_address','phone','site','cms_guess','rating','reviews',
           'reviews_per_score_1','reviews_per_score_2','reviews_per_score_3','reviews_per_score_4','reviews_per_score_5','photos_count','GMB_Opportunity_Score','maps_link']
for c in show_cols:
    if c not in f.columns:
        f[c]=np.nan
st.dataframe(f[show_cols],use_container_width=True)

csv=f[show_cols].to_csv(index=False).encode('utf-8')
st.download_button("⬇️ Esporta CSV filtrato",data=csv,file_name="prospect_filtrato.csv",mime="text/csv")

st.subheader("🔗 Azioni rapide")
sel_name=st.selectbox("Seleziona attività",["—"]+f['name'].astype(str).tolist())
if sel_name!="—":
    row=f[f['name'].astype(str)==sel_name].iloc[0]
    c1,c2,c3=st.columns(3)
    with c1:
        if pd.notnull(row.get('site')) and str(row['site']).strip():
            st.link_button("Apri sito",str(row['site']))
        else:
            st.button("Apri sito",disabled=True)
    with c2:
        if pd.notnull(row.get('maps_link')) and str(row['maps_link']).strip():
            st.link_button("Apri Maps",str(row['maps_link']))
        else:
            st.button("Apri Maps",disabled=True)
    with c3:
        colname='location_reviews_link' if 'location_reviews_link' in f.columns else None
        link=row.get(colname) if colname else None
        if link and str(link).strip():
            st.link_button("Apri recensioni",str(link))
        else:
            st.button("Apri recensioni",disabled=True)

st.divider()
st.caption("Filtra per città, sito e CMS. Il punteggio considera volume, quota negative, gap da 4.6★ e scarsità di foto. CMS: solo WordPress/Altro/No site.")
