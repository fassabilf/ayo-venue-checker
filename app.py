"""
Ayo Venue Checker — Streamlit Web App
Run: streamlit run app.py
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from scraper import (
    JAKARTA_AREAS, SPORT_NAMES, DAY_NAMES,
    haversine, next_weekday,
    make_session, make_session_bare, geocode,
    get_venues_for_area, fetch_coords,
    check_fields_flexible,
)

# ─── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Cari Lapangan — Jakarta",
    page_icon="🎾",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
  .stApp { max-width: 1400px; margin: auto; }
  .badge { display:inline-block; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:500; }
  .badge-green  { background:#e8f5e9; color:#2e7d32; }
  .badge-yellow { background:#fff3e0; color:#e65100; }
</style>
""", unsafe_allow_html=True)

# ─── Cached: venue catalog (list + koordinat, TTL 6 jam) ──────────────────────
# Dipanggil hanya saat user klik "Cari Lapangan", bukan saat page load.

@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_venue_catalog(cabor: int) -> dict:
    """Fetch semua venue list Jakarta + koordinat. Di-cache 6 jam."""
    session = make_session()
    all_venues: list[dict] = []
    seen: set[int] = set()
    for lokasi in JAKARTA_AREAS:
        for v in get_venues_for_area(session, cabor, lokasi):
            if v["id"] not in seen:
                seen.add(v["id"])
                all_venues.append(v)
        time.sleep(0.2)
    for i, v in enumerate(all_venues):
        all_venues[i] = fetch_coords(session, v)
        time.sleep(0.2)
    return {
        "venues":     all_venues,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "total":      len(all_venues),
    }


# ─── Cached: availability check (concurrent, TTL 15 menit) ───────────────────

@st.cache_data(ttl=15 * 60, show_spinner=False)
def fetch_availability(venues_json: str, date_str: str,
                       jam_main: int, durasi_x2: int, cabor: int) -> list[dict]:
    """Cek availability secara concurrent (5 worker). Cached 15 menit."""
    venues = json.loads(venues_json)
    durasi = durasi_x2 / 2
    results: list[dict] = []
    lock = threading.Lock()

    def check_one(v: dict):
        s = make_session_bare()
        fields = check_fields_flexible(s, v["id"], date_str, jam_main, durasi, cabor)
        rows = []
        for f in fields:
            url = (f"https://ayo.co.id/v/{v['slug']}?date={date_str}&field_id={f['field_id']}"
                   if v.get("slug") else f"https://ayo.co.id/venue/{v['id']}")
            alt_str = ", ".join(
                f"{a['slot_start']:02d}:00–{a['slot_end']:02d}:00"
                for a in f.get("alt_slots", [])
            )
            rows.append({
                "venue_id":   v["id"],
                "venue":      v["name"],
                "area":       v["area"],
                "lat":        v["lat"],
                "lon":        v["lon"],
                "field_id":   f["field_id"],
                "field":      f["field_name"],
                "slot":       f"{f['slot_start']:02d}:00–{f['slot_end']:02d}:00",
                "slot_start": f["slot_start"],
                "price":      f["price_total"],
                "alt_slots":  alt_str,
                "url":        url,
            })
        return rows

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(check_one, v) for v in venues]
        for fut in as_completed(futures):
            with lock:
                results.extend(fut.result())

    return results


# ─── Session state ────────────────────────────────────────────────────────────

for k, v in {
    "ref_lat":     -6.2896,
    "ref_lon":     106.8400,
    "ref_name":    "Pasar Minggu",
    "available":   None,
    "all_venues":  [],
    "search_done": False,
    "last_search": {},
    "geo_results": [],
    "catalog_info": None,   # {"fetched_at", "total"}
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─── Header ───────────────────────────────────────────────────────────────────

st.title("🎾 Cari Lapangan Kosong — Jakarta")
st.caption("Data dari ayo.co.id · Pilih parameter lalu klik **Cari Lapangan**")

# ─── Form ─────────────────────────────────────────────────────────────────────

sport_opts = {7: "🎾 Tennis", 1: "⚽ Futsal", 4: "🏸 Badminton",
              8: "🏀 Basket", 12: "⚽ Mini Soccer"}

qp = st.query_params
def qp_get(key, default, cast=str):
    try: return cast(qp[key]) if key in qp else default
    except Exception: return default

with st.container(border=True):
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    with c1:
        hari = st.selectbox("📅 Hari", DAY_NAMES,
                            index=DAY_NAMES.index(qp_get("hari", "Rabu"))
                                  if qp_get("hari", "Rabu") in DAY_NAMES else 2)
        target_date = next_weekday(DAY_NAMES.index(hari))
        st.caption(f"→ {target_date.strftime('%d %b %Y')}")
    with c2:
        jam_main = st.slider("🕖 Sekitar jam berapa", 6, 22, qp_get("jam", 19, int))
        st.caption(f"Cari slot mulai antara {jam_main-1}:00–{jam_main+1}:00")
    with c3:
        dur_opts = [1.0, 1.5, 2.0, 2.5, 3.0]
        default_dur = qp_get("dur", 2.0, float)
        durasi = st.select_slider("⏱️ Durasi", options=dur_opts,
                                  value=default_dur if default_dur in dur_opts else 2.0,
                                  format_func=lambda x: f"{x:g} jam")
    with c4:
        default_cabor = qp_get("cabor", 7, int)
        cabor = st.selectbox("🏅 Olahraga", list(sport_opts),
                             format_func=lambda k: sport_opts[k],
                             index=list(sport_opts).index(default_cabor)
                                   if default_cabor in sport_opts else 0)

    cari_btn = st.button("🔍 Cari Lapangan", type="primary", width="stretch")

# ─── Titik referensi ──────────────────────────────────────────────────────────

with st.expander("📍 Titik referensi & radius", expanded=not st.session_state.search_done):
    # Geocoding
    geo_col, _ = st.columns([3, 3])
    with geo_col:
        geo_query = st.text_input("🔍 Cari nama lokasi",
                                  placeholder="Cth: Blok M, Gandaria City, Lebak Bulus…")
        if st.button("Cari lokasi", key="geo_btn") and geo_query.strip():
            with st.spinner("Mencari…"):
                st.session_state.geo_results = geocode(geo_query.strip())

    if st.session_state.geo_results:
        sel = st.selectbox("Pilih hasil", st.session_state.geo_results,
                           format_func=lambda r: r["display_name"][:80])
        if st.button("✔ Gunakan lokasi ini"):
            st.session_state.ref_lat  = sel["lat"]
            st.session_state.ref_lon  = sel["lon"]
            st.session_state.ref_name = sel["name"]
            st.session_state.geo_results = []
            st.rerun()

    st.divider()
    rc1, rc2, rc3, rc4, rc5 = st.columns([2, 1.5, 1.5, 1.5, 1.5])
    ref_name = rc1.text_input("Nama", value=st.session_state.ref_name)
    ref_lat  = rc2.number_input("Lat", value=st.session_state.ref_lat,
                                format="%.5f", step=0.001)
    ref_lon  = rc3.number_input("Lon", value=st.session_state.ref_lon,
                                format="%.5f", step=0.001)
    max_km   = rc4.selectbox("Radius", [5, 10, 15, 20, 999], index=1,
                             format_func=lambda x: "Semua Jkt" if x == 999 else f"{x} km")
    if rc5.button("✔ Terapkan", width="stretch"):
        st.session_state.ref_lat  = ref_lat
        st.session_state.ref_lon  = ref_lon
        st.session_state.ref_name = ref_name
        st.rerun()

# ─── Opsi tampilan ────────────────────────────────────────────────────────────

with st.expander("⚙️ Opsi tampilan", expanded=False):
    sort_by = st.radio("Urutkan hasil", ["📍 Jarak", "💰 Harga"], horizontal=True)

max_price = 1_500_000  # no filter

st.divider()

# ─── Proses: catalog + availability (hanya saat klik Cari) ───────────────────

if cari_btn:
    ref = {"lat": st.session_state.ref_lat, "lon": st.session_state.ref_lon}
    date_str = target_date.strftime("%Y-%m-%d")

    # Update URL params untuk share link
    st.query_params.update({
        "hari": hari, "jam": str(jam_main), "dur": str(durasi),
        "cabor": str(cabor),
        "lat": str(round(ref["lat"], 5)), "lon": str(round(ref["lon"], 5)),
        "km": str(max_km),
    })

    # Step 1: Catalog (cache hit = instan, miss = ~2 menit)
    info = st.session_state.catalog_info
    if info:
        age_min = int((datetime.now() - datetime.fromisoformat(info["fetched_at"])).total_seconds() / 60)
        age_str = f"{age_min} mnt lalu" if age_min < 60 else f"{age_min//60} jam lalu"
        st.info(f"📦 Pakai cache venue ({age_str} · {info['total']} venue). Klik 🔄 untuk refresh.", icon=None)
        catalog = load_venue_catalog(cabor)
    else:
        prog_cat = st.progress(0.0)
        status_cat = st.empty()
        status_cat.markdown("**Langkah 1/2 — Memuat daftar venue Jakarta…**  \n"
                            "_(pertama kali ~2 menit, selanjutnya instan dari cache)_")
        catalog = load_venue_catalog(cabor)
        prog_cat.progress(1.0)
        status_cat.markdown(f"✅ **{catalog['total']} venue dimuat**")

    st.session_state.catalog_info = {
        "fetched_at": catalog["fetched_at"],
        "total":      catalog["total"],
    }
    st.session_state.all_venues = catalog["venues"]

    # Step 2: Filter radius
    venues_in_radius = [
        v for v in catalog["venues"]
        if v.get("lat") and v.get("lon") and (
            max_km == 999 or
            haversine(v["lat"], v["lon"], ref["lat"], ref["lon"]) <= max_km
        )
    ]
    n_filt    = len(venues_in_radius)
    est_secs  = max(2, (n_filt + 4) // 5)
    radius_lbl = "semua Jakarta" if max_km == 999 else f"dalam {max_km} km"

    # Step 3: Availability check
    prog_av = st.progress(0.0)
    status_av = st.empty()
    status_av.markdown(
        f"**Langkah 2/2 — Cek ketersediaan {n_filt} venue** {radius_lbl} "
        f"dari **{st.session_state.ref_name}** (~{est_secs}s)…"
    )

    venues_json = json.dumps(venues_in_radius, ensure_ascii=False)
    raw = fetch_availability(venues_json, date_str, int(jam_main),
                             int(durasi * 2), cabor)

    prog_av.progress(1.0)
    status_av.markdown(f"✅ **Selesai!** {len(raw)} lapangan tersedia.")

    st.session_state.available   = raw
    st.session_state.search_done = True
    st.session_state.last_search = {
        "hari": hari, "jam": jam_main, "durasi": durasi,
        "cabor": cabor, "sport": sport_opts[cabor],
        "ref_name": st.session_state.ref_name,
        "ref_lat": round(ref["lat"], 4), "ref_lon": round(ref["lon"], 4),
        "radius": max_km, "date_str": date_str,
        "n_venues_checked": n_filt,
    }
    st.rerun()

# ─── Hasil ────────────────────────────────────────────────────────────────────

if st.session_state.search_done and st.session_state.available is not None:
    ls   = st.session_state.last_search
    ref  = {"lat": st.session_state.ref_lat, "lon": st.session_state.ref_lon}
    avail = list(st.session_state.available)

    # Re-hitung jarak dari ref terkini
    for r in avail:
        if r.get("lat") and r.get("lon"):
            r["dist"] = haversine(r["lat"], r["lon"], ref["lat"], ref["lon"])

    # Filter harga
    if max_price < 1_500_000:
        avail = [r for r in avail if r["price"] <= max_price]

    # Dedup
    seen: set = set()
    deduped: list[dict] = []
    for r in sorted(avail, key=lambda r: r.get("dist") or 9999):
        key = (r["venue_id"], r["field"].lower())
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    if sort_by == "💰 Harga":
        deduped.sort(key=lambda r: r["price"] or 9_999_999)

    n_venues  = len({r["venue_id"] for r in deduped})
    avail_ids = {r["venue_id"] for r in deduped}
    all_venues = st.session_state.all_venues
    na_in_radius = [
        v for v in all_venues
        if v.get("lat") and v["id"] not in avail_ids
        and (ls["radius"] == 999 or
             haversine(v["lat"], v["lon"], ref["lat"], ref["lon"]) <= ls["radius"])
    ]

    # Info pencarian
    radius_info = "semua Jakarta" if ls["radius"] == 999 else f"{ls['radius']} km"
    price_info  = "" if max_price >= 1_500_000 else f" · budget ≤ Rp{max_price:,}"
    ci = st.session_state.catalog_info
    if ci:
        age_min = int((datetime.now() - datetime.fromisoformat(ci["fetched_at"])).total_seconds() / 60)
        age_str = f"{age_min} mnt lalu" if age_min < 60 else f"{age_min//60} jam lalu"
        badge_cls = "badge badge-yellow" if age_min > 300 else "badge badge-green"
        c_badge, c_refresh = st.columns([5, 1])
        with c_badge:
            st.markdown(
                f'<span class="{badge_cls}">📦 Cache {age_str} · {ci["total"]} venue</span>'
                f'&nbsp;&nbsp;<span style="font-size:12px;color:#666">'
                f'{ls["hari"]} · jam ~{ls["jam"]}:00 · {ls["durasi"]:g}j · {ls["sport"]} · '
                f'{radius_info} dari <b>{ls["ref_name"]}</b>{price_info}</span>',
                unsafe_allow_html=True,
            )
        with c_refresh:
            if st.button("🔄 Refresh Cache"):
                load_venue_catalog.clear()
                fetch_availability.clear()
                st.session_state.catalog_info = None
                st.rerun()

    st.caption(
        f"🔗 Share: `?hari={ls['hari']}&jam={ls['jam']}&dur={ls['durasi']}"
        f"&cabor={ls['cabor']}&lat={ls['ref_lat']}&lon={ls['ref_lon']}&km={ls['radius']}`"
    )

    # Metrics
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Lapangan tersedia", len(deduped))
    mc2.metric("Venue tersedia",    n_venues)
    mc3.metric("Venue dicek",       ls["n_venues_checked"])
    mc4.metric("Terdekat",
               deduped[0]["venue"][:22] if deduped else "–",
               f"{deduped[0]['dist']:.1f} km" if deduped and deduped[0].get("dist") else "")

    st.divider()

    # ── Peta ──────────────────────────────────────────────────────────────────
    st.subheader("🗺️ Peta")
    st.caption("Klik marker **hijau** → jadikan titik referensi baru (tabel re-sort).")

    av_lats  = [r["lat"] for r in deduped if r.get("lat")]
    av_lons  = [r["lon"] for r in deduped if r.get("lon")]
    av_hover = [
        f"<b>{r['venue']}</b><br>{r['area']}<br>"
        f"🏅 {r['field']}<br>🕖 {r['slot']}"
        + (f"<br><span style='color:#888'>juga: {r['alt_slots']}</span>" if r.get("alt_slots") else "")
        + f"<br>💰 Rp{r['price']:,}<br>📍 {r.get('dist', 0):.1f} km"
        for r in deduped if r.get("lat")
    ]

    fig = go.Figure()
    if na_in_radius:
        fig.add_trace(go.Scattermap(
            lat=[v["lat"] for v in na_in_radius],
            lon=[v["lon"] for v in na_in_radius],
            mode="markers", marker=dict(size=7, color="#bbb", opacity=0.4),
            text=[v["name"] for v in na_in_radius],
            hovertemplate="<b>%{text}</b><br>Tidak tersedia<extra></extra>",
            name="Tidak tersedia",
        ))
    fig.add_trace(go.Scattermap(
        lat=av_lats, lon=av_lons, mode="markers",
        marker=dict(size=15, color="#00b86b", opacity=0.9),
        hovertext=av_hover, hovertemplate="%{hovertext}<extra></extra>",
        name="Tersedia ✅",
    ))
    fig.add_trace(go.Scattermap(
        lat=[ref["lat"]], lon=[ref["lon"]], mode="markers",
        marker=dict(size=18, color="#1a73e8", symbol="star"),
        text=[st.session_state.ref_name],
        hovertemplate="<b>📍 %{text}</b><extra></extra>",
        name=st.session_state.ref_name,
    ))
    fig.update_layout(
        map=dict(style="carto-positron",
                 center=dict(lat=ref["lat"], lon=ref["lon"]), zoom=11),
        margin=dict(l=0, r=0, t=0, b=0), height=460,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
    )

    event = st.plotly_chart(fig, on_select="rerun", selection_mode="points", key="map")
    if event and event.selection and event.selection.points:
        pt = event.selection.points[0]
        if getattr(pt, "trace_index", None) == 1:
            idx = getattr(pt, "point_index", 0)
            if 0 <= idx < len(deduped) and deduped[idx].get("lat"):
                c = deduped[idx]
                st.session_state.ref_lat  = c["lat"]
                st.session_state.ref_lon  = c["lon"]
                st.session_state.ref_name = c["venue"]
                st.rerun()

    st.divider()

    # ── Tabel ─────────────────────────────────────────────────────────────────
    st.subheader(f"📋 {len(deduped)} lapangan dari {n_venues} venue")

    if not deduped:
        st.info("Tidak ada lapangan tersedia. Coba perluas radius, ganti jam, atau naikkan budget.")
    else:
        df = pd.DataFrame([{
            "Jarak":     f"{r['dist']:.1f} km" if r.get("dist") else "–",
            "Venue":     r["venue"],
            "Area":      r["area"].replace("Kota ", ""),
            "Lapangan":  r["field"],
            "Slot":      r["slot"],
            "Slot lain": r.get("alt_slots") or "–",
            "Harga":     f"Rp{r['price']:,}" if r["price"] else "?",
            "Booking":   r["url"],
        } for r in deduped])

        st.dataframe(
            df, hide_index=True,
            column_config={
                "Booking":   st.column_config.LinkColumn("Booking", display_text="Buka →"),
                "Jarak":     st.column_config.TextColumn(width="small"),
                "Slot":      st.column_config.TextColumn(width="small"),
                "Slot lain": st.column_config.TextColumn("Slot lain", width="medium",
                                                         help="Slot alternatif yang juga kosong"),
                "Harga":     st.column_config.TextColumn(width="small"),
                "Area":      st.column_config.TextColumn(width="medium"),
            },
        )

else:
    # Belum ada search — tampilkan onboarding
    st.markdown("""
    ### Cara pakai
    1. **Pilih hari** dan **jam** yang diinginkan di atas
    2. **Set titik referensi** (default: Pasar Minggu) di bagian 📍 — bisa ketik nama atau isi koordinat
    3. Pilih **radius** pencarian (default 10 km)
    4. Klik **🔍 Cari Lapangan**

    > Pertama kali klik Cari: ~2 menit (ambil data semua venue Jakarta).
    > Pencarian berikutnya: langsung dari cache, cek availability ~3–6 detik.
    """)
