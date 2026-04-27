"""
Ayo Venue Checker — Streamlit Web App
Run: streamlit run app.py
"""

import json
import math
import time
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from scraper import (
    JAKARTA_AREAS, SPORT_NAMES, DAY_NAMES,
    avg_dist, haversine, next_weekday, day_index,
    make_session, get_all_venues, fetch_coords,
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
  div[data-testid="stMetric"] { background: #f8f9fa; padding: 12px; border-radius: 8px; }
  .stDataFrame { font-size: 13px; }
</style>
""", unsafe_allow_html=True)

# ─── Session state init ────────────────────────────────────────────────────────

for key, default in {
    "venues_all":  [],
    "available":   [],
    "fetched":     False,
    "ref_lat":     -6.2896,
    "ref_lon":     106.8400,
    "ref_name":    "Pasar Minggu",
    "last_params": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ─── Header + Form ─────────────────────────────────────────────────────────────

st.title("🎾 Cari Lapangan Kosong — Seluruh Jakarta")
st.caption("Cek ketersediaan lapangan olahraga di ayo.co.id secara real-time")

with st.container(border=True):
    col1, col2, col3, col4 = st.columns([2, 2, 2, 2])

    with col1:
        hari_idx = DAY_NAMES.index("Rabu")
        hari = st.selectbox("📅 Hari", DAY_NAMES, index=hari_idx)
        target_date = next_weekday(DAY_NAMES.index(hari))
        st.caption(f"→ {target_date.strftime('%d %b %Y')}")

    with col2:
        jam_main = st.slider("🕖 Sekitar jam berapa", 6, 22, 19)
        st.caption(f"Cari slot mulai antara {jam_main-1}:00–{jam_main+1}:00")

    with col3:
        durasi = st.select_slider(
            "⏱️ Durasi main", options=[1.0, 1.5, 2.0, 2.5, 3.0],
            value=2.0, format_func=lambda x: f"{x:g} jam"
        )

    with col4:
        sport_options = {7: "🎾 Tennis", 1: "⚽ Futsal", 4: "🏸 Badminton",
                        8: "🏀 Basket", 12: "⚽ Mini Soccer"}
        cabor = st.selectbox(
            "🏅 Olahraga",
            list(sport_options.keys()),
            format_func=lambda k: sport_options[k],
        )

    cari_btn = st.button("🔍 Cari Lapangan", type="primary", width="stretch")

# ─── Titik referensi ──────────────────────────────────────────────────────────

with st.expander("📍 Titik referensi untuk sort jarak", expanded=True):
    rc1, rc2, rc3, rc4 = st.columns([3, 2, 2, 2])
    ref_name = rc1.text_input("Nama lokasi", value=st.session_state.ref_name)
    ref_lat  = rc2.number_input("Lat", value=st.session_state.ref_lat, format="%.6f", step=0.001)
    ref_lon  = rc3.number_input("Lon", value=st.session_state.ref_lon, format="%.6f", step=0.001)
    if rc4.button("Perbarui Sort", width="stretch"):
        st.session_state.ref_lat  = ref_lat
        st.session_state.ref_lon  = ref_lon
        st.session_state.ref_name = ref_name
        st.rerun()

    st.caption(
        "💡 Klik marker hijau di peta → otomatis set titik referensi dari sana. "
        "Atau klik kanan di Google Maps → salin koordinat → paste di atas."
    )

st.divider()

# ─── Fetch ────────────────────────────────────────────────────────────────────

params_now = (hari, jam_main, durasi, cabor)

if cari_btn or (st.session_state.last_params == params_now and not st.session_state.fetched):
    if cari_btn or not st.session_state.fetched:
        st.session_state.fetched     = False
        st.session_state.last_params = params_now

        session = make_session()
        date_str = target_date.strftime("%Y-%m-%d")
        sport_name = SPORT_NAMES.get(cabor, f"Cabor {cabor}")

        # Step 1: Venue list
        st.subheader("⏳ Mencari venue...")
        prog1 = st.progress(0.0, "Memulai...")
        status1 = st.empty()
        all_venues: list[dict] = []
        seen_ids: set[int] = set()

        def on_area(lokasi, new_count, total):
            idx = JAKARTA_AREAS.index(lokasi)
            prog1.progress((idx + 1) / len(JAKARTA_AREAS), f"Scraping {lokasi}...")
            status1.caption(f"✓ {lokasi}: {new_count} venue baru | total {total}")

        all_venues = get_all_venues(session, cabor, on_area=on_area)
        prog1.progress(1.0, f"✅ {len(all_venues)} venue ditemukan")

        # Step 2: Availability + coords
        prog2 = st.progress(0.0, "Cek ketersediaan...")
        status2 = st.empty()
        available_raw: list[dict] = []

        for i, v in enumerate(all_venues):
            # Coords
            v = fetch_coords(session, v)
            all_venues[i] = v

            # Availability
            fields = check_fields_flexible(
                session, v["id"], date_str,
                jam_main=jam_main, durasi_jam=durasi, sport_id=cabor
            )
            for f in fields:
                dist = avg_dist(v["lat"], v["lon"],
                                [{"lat": st.session_state.ref_lat, "lon": st.session_state.ref_lon}])
                url = (f"https://ayo.co.id/v/{v['slug']}?date={date_str}&field_id={f['field_id']}"
                       if v.get("slug") else f"https://ayo.co.id/venue/{v['id']}")
                available_raw.append({
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
                    "dist":       dist,
                    "url":        url,
                })

            pct = (i + 1) / len(all_venues)
            prog2.progress(pct, f"[{i+1}/{len(all_venues)}] {v['name'][:45]}")
            time.sleep(0.3)

        prog2.progress(1.0, "✅ Selesai!")

        st.session_state.venues_all = all_venues
        st.session_state.available  = available_raw
        st.session_state.fetched    = True
        st.rerun()

# ─── Results ──────────────────────────────────────────────────────────────────

if st.session_state.fetched and st.session_state.available is not None:
    ref = {"lat": st.session_state.ref_lat, "lon": st.session_state.ref_lon}
    all_venues = st.session_state.venues_all
    available  = st.session_state.available

    # Re-compute dist with current ref point
    for row in available:
        if row.get("lat") and row.get("lon"):
            row["dist"] = haversine(row["lat"], row["lon"], ref["lat"], ref["lon"])
        else:
            row["dist"] = None

    available_sorted = sorted(available, key=lambda r: r["dist"] if r["dist"] is not None else 9999)

    # Deduplicate (same venue + field_name)
    seen_keys: set = set()
    deduped: list[dict] = []
    for r in available_sorted:
        key = (r["venue_id"], r["field"].lower())
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(r)

    # Metrics
    n_venues = len({r["venue_id"] for r in deduped})
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("Lapangan tersedia", len(deduped))
    mc2.metric("Venue tersedia", n_venues)
    mc3.metric(
        "Terdekat",
        f"{deduped[0]['venue'][:28]}" if deduped else "–",
        f"{deduped[0]['dist']:.1f} km" if deduped and deduped[0]['dist'] else "",
    )

    st.divider()

    # ── Peta ──────────────────────────────────────────────────────────────────
    st.subheader("🗺️ Peta")
    st.caption("Klik marker **hijau** untuk menjadikannya titik referensi sort.")

    # Build plotly map
    # Available venues
    av_lats  = [r["lat"] for r in deduped if r.get("lat")]
    av_lons  = [r["lon"] for r in deduped if r.get("lon")]
    av_names = [r["venue"] for r in deduped if r.get("lat")]
    av_texts = [
        f"<b>{r['venue']}</b><br>{r['area']}<br>"
        f"🎾 {r['field']}<br>🕖 {r['slot']}<br>"
        f"💰 Rp{r['price']:,}<br>📍 {r['dist']:.1f} km"
        for r in deduped if r.get("lat")
    ]
    av_urls  = [r["url"] for r in deduped if r.get("lat")]

    # All venues (not available)
    avail_ids = {r["venue_id"] for r in deduped}
    na_venues = [v for v in all_venues if v.get("lat") and v["id"] not in avail_ids]

    fig = go.Figure()

    # Not-available (gray, small)
    if na_venues:
        fig.add_trace(go.Scattermap(
            lat=[v["lat"] for v in na_venues],
            lon=[v["lon"] for v in na_venues],
            mode="markers",
            marker=dict(size=7, color="#aaaaaa", opacity=0.5),
            text=[v["name"] for v in na_venues],
            hovertemplate="<b>%{text}</b><br>Tidak tersedia<extra></extra>",
            name="Tidak tersedia",
        ))

    # Available (green, larger) — one trace per venue so index maps to deduped list
    fig.add_trace(go.Scattermap(
        lat=av_lats,
        lon=av_lons,
        mode="markers+text",
        marker=dict(size=16, color="#00b86b", opacity=0.9),
        text=av_names,
        textposition="top right",
        textfont=dict(size=10, color="#006b3f"),
        customdata=list(range(len(av_lats))),
        hovertemplate="%{hovertext}<extra></extra>",
        hovertext=av_texts,
        name="Tersedia ✅",
    ))

    # Reference point (blue star)
    fig.add_trace(go.Scattermap(
        lat=[ref["lat"]], lon=[ref["lon"]],
        mode="markers",
        marker=dict(size=18, color="#1a73e8", symbol="star"),
        text=[st.session_state.ref_name],
        hovertemplate="<b>📍 %{text}</b><extra></extra>",
        name=st.session_state.ref_name,
    ))

    fig.update_layout(
        map=dict(
            style="carto-positron",
            center=dict(lat=ref["lat"], lon=ref["lon"]),
            zoom=11,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        height=480,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.01,
            xanchor="left", x=0,
        ),
        showlegend=True,
    )

    # Render peta + tangkap click
    event = st.plotly_chart(
        fig,
        width="stretch",
        on_select="rerun",
        selection_mode="points",
        key="main_map",
    )

    # Handle klik marker available → set as ref point
    if event and event.selection and event.selection.points:
        pt = event.selection.points[0]
        # trace index 1 = available trace
        if hasattr(pt, "trace_index") and pt.trace_index == 1:
            idx = pt.point_index if hasattr(pt, "point_index") else 0
            if 0 <= idx < len(deduped):
                clicked = deduped[idx]
                if clicked.get("lat") and clicked.get("lon"):
                    st.session_state.ref_lat  = clicked["lat"]
                    st.session_state.ref_lon  = clicked["lon"]
                    st.session_state.ref_name = clicked["venue"]
                    st.rerun()

    st.divider()

    # ── Tabel ─────────────────────────────────────────────────────────────────
    st.subheader(f"📋 Hasil — {len(deduped)} lapangan dari {n_venues} venue")

    if not deduped:
        st.info("Tidak ada lapangan tersedia di jam dan olahraga yang dipilih.")
    else:
        df = pd.DataFrame([{
            "Jarak": f"{r['dist']:.1f} km" if r["dist"] else "–",
            "Venue": r["venue"],
            "Area": r["area"].replace("Kota ", ""),
            "Lapangan": r["field"],
            "Slot": r["slot"],
            "Harga": f"Rp{r['price']:,}" if r["price"] else "?",
            "Booking": r["url"],
        } for r in deduped])

        st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            column_config={
                "Booking": st.column_config.LinkColumn("Booking", display_text="Buka →"),
                "Jarak":   st.column_config.TextColumn("Jarak", width="small"),
                "Slot":    st.column_config.TextColumn("Slot", width="small"),
                "Harga":   st.column_config.TextColumn("Harga", width="small"),
            },
        )

elif not st.session_state.fetched:
    st.info("Pilih hari, jam, dan olahraga di atas, lalu klik **Cari Lapangan**.")
