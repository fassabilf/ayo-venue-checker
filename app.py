"""
Ayo Venue Checker — Streamlit Web App
Run: streamlit run app.py
"""

import time
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from scraper import (
    JAKARTA_AREAS, SPORT_NAMES, DAY_NAMES,
    haversine, next_weekday,
    make_session, get_venues_for_area, fetch_coords,
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
  .cache-badge {
    display: inline-block; padding: 4px 10px; border-radius: 12px;
    font-size: 12px; font-weight: 500; background: #e8f5e9; color: #2e7d32;
  }
  .cache-badge.stale { background: #fff3e0; color: #e65100; }
</style>
""", unsafe_allow_html=True)

# ─── Cached venue loader (slow part — runs ~2 menit, lalu cache 6 jam) ────────

@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_venue_catalog(cabor: int) -> dict:
    """
    Fetch seluruh venue list Jakarta + koordinat untuk satu cabor.
    Di-cache 6 jam — tidak perlu re-fetch tiap pencarian.
    Returns {"venues": [...], "fetched_at": "...", "total": N}
    """
    session = make_session()

    # Step A: Venue list dari 8 area
    all_venues: list[dict] = []
    seen_ids: set[int] = set()
    for lokasi in JAKARTA_AREAS:
        area_venues = get_venues_for_area(session, cabor, lokasi)
        new = [v for v in area_venues if v["id"] not in seen_ids]
        seen_ids.update(v["id"] for v in new)
        all_venues.extend(new)
        time.sleep(0.2)

    # Step B: Koordinat tiap venue (dari slug page)
    for i, v in enumerate(all_venues):
        all_venues[i] = fetch_coords(session, v)
        time.sleep(0.22)

    return {
        "venues":     all_venues,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "total":      len(all_venues),
    }

# ─── Session state init ────────────────────────────────────────────────────────

_defaults = {
    "available":    None,
    "search_done":  False,
    "last_search":  None,   # (hari, jam_main, durasi, cabor, ref_lat, ref_lon, max_km)
    "ref_lat":      -6.2896,
    "ref_lon":      106.8400,
    "ref_name":     "Pasar Minggu",
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─── UI ───────────────────────────────────────────────────────────────────────

st.title("🎾 Cari Lapangan Kosong — Jakarta")

# ── Form ──
with st.container(border=True):
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])

    with c1:
        hari = st.selectbox("📅 Hari", DAY_NAMES, index=DAY_NAMES.index("Rabu"))
        target_date = next_weekday(DAY_NAMES.index(hari))
        st.caption(f"→ {target_date.strftime('%d %b %Y')}")

    with c2:
        jam_main = st.slider("🕖 Sekitar jam berapa", 6, 22, 19)
        st.caption(f"Slot mulai antara {jam_main-1}:00–{jam_main+1}:00")

    with c3:
        durasi = st.select_slider(
            "⏱️ Durasi", options=[1.0, 1.5, 2.0, 2.5, 3.0],
            value=2.0, format_func=lambda x: f"{x:g} jam",
        )

    with c4:
        sport_opts = {7: "🎾 Tennis", 1: "⚽ Futsal", 4: "🏸 Badminton",
                     8: "🏀 Basket", 12: "⚽ Mini Soccer"}
        cabor = st.selectbox("🏅 Olahraga", list(sport_opts), format_func=lambda k: sport_opts[k])

    cari_btn = st.button("🔍 Cari Lapangan", type="primary", width="stretch")

# ── Titik referensi + radius ──
with st.expander("📍 Titik referensi & radius pencarian", expanded=True):
    rc1, rc2, rc3, rc4, rc5 = st.columns([2, 1.5, 1.5, 1.5, 1.5])
    ref_name = rc1.text_input("Nama lokasi", value=st.session_state.ref_name, label_visibility="visible")
    ref_lat  = rc2.number_input("Lat",  value=st.session_state.ref_lat,  format="%.5f", step=0.001)
    ref_lon  = rc3.number_input("Lon",  value=st.session_state.ref_lon,  format="%.5f", step=0.001)
    max_km   = rc4.selectbox("Radius", [5, 10, 15, 20, 999], index=1,
                             format_func=lambda x: "Semua Jakarta" if x == 999 else f"{x} km")
    if rc5.button("✔ Terapkan", width="stretch"):
        st.session_state.ref_lat  = ref_lat
        st.session_state.ref_lon  = ref_lon
        st.session_state.ref_name = ref_name
        st.rerun()
    st.caption("💡 Klik marker hijau di peta → set sebagai titik referensi. "
               "Atau klik kanan Google Maps → salin koordinat.")

st.divider()

# ─── Cache status ─────────────────────────────────────────────────────────────

# Load catalog dari cache (atau fetch kalau belum ada)
catalog_placeholder = st.empty()

with catalog_placeholder.container():
    col_c1, col_c2 = st.columns([5, 1])
    with col_c1:
        with st.spinner(f"Memuat katalog venue {sport_opts[cabor]}…"):
            catalog = load_venue_catalog(cabor)

    fetched_dt  = datetime.fromisoformat(catalog["fetched_at"])
    age_minutes = int((datetime.now() - fetched_dt).total_seconds() / 60)
    age_str     = f"{age_minutes} menit lalu" if age_minutes < 60 else f"{age_minutes//60} jam lalu"
    is_stale    = age_minutes > 300  # > 5 jam = warnai kuning

    # Filter venue by radius
    ref = {"lat": st.session_state.ref_lat, "lon": st.session_state.ref_lon}
    venues_in_radius = [
        v for v in catalog["venues"]
        if v.get("lat") and v.get("lon")
        and (max_km == 999 or haversine(v["lat"], v["lon"], ref["lat"], ref["lon"]) <= max_km)
    ]
    n_filtered = len(venues_in_radius)
    n_total    = catalog["total"]

    with col_c1:
        badge_class = "cache-badge stale" if is_stale else "cache-badge"
        radius_label = "semua Jakarta" if max_km == 999 else f"dalam {max_km} km"
        st.markdown(
            f'<span class="{badge_class}">📦 Cache: {age_str} · {n_total} venue tersimpan</span>'
            f'&nbsp;&nbsp;'
            f'<span style="font-size:13px;color:#555">Akan cek <b>{n_filtered}</b> venue {radius_label} dari <b>{st.session_state.ref_name}</b></span>',
            unsafe_allow_html=True,
        )

    with col_c2:
        if st.button("🔄 Refresh Cache", help="Hapus cache dan ambil ulang data venue"):
            load_venue_catalog.clear()
            st.rerun()

# ─── Search (availability check — hanya untuk venue dalam radius) ─────────────

search_key = (hari, jam_main, durasi, cabor,
              round(st.session_state.ref_lat, 4), round(st.session_state.ref_lon, 4), max_km)

if cari_btn:
    st.session_state.search_done = False
    st.session_state.last_search = search_key

    session     = make_session()
    date_str    = target_date.strftime("%Y-%m-%d")
    sport_name  = SPORT_NAMES.get(cabor, f"Cabor {cabor}")

    st.subheader(f"⏳ Cek ketersediaan {n_filtered} venue…")
    prog = st.progress(0.0, "Memulai…")

    available_raw: list[dict] = []

    for i, v in enumerate(venues_in_radius):
        dist = haversine(v["lat"], v["lon"], ref["lat"], ref["lon"])
        fields = check_fields_flexible(
            session, v["id"], date_str,
            jam_main=jam_main, durasi_jam=durasi, sport_id=cabor,
        )
        for f in fields:
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

        prog.progress((i + 1) / n_filtered,
                      f"[{i+1}/{n_filtered}] {v['name'][:45]}")
        time.sleep(0.3)

    prog.progress(1.0, f"✅ Selesai! {len(available_raw)} lapangan tersedia.")
    st.session_state.available   = available_raw
    st.session_state.search_done = True
    st.session_state.last_search = search_key
    st.rerun()

# ─── Results ──────────────────────────────────────────────────────────────────

if st.session_state.search_done and st.session_state.available is not None:
    available = st.session_state.available

    # Re-sort dengan ref point terkini
    for row in available:
        if row.get("lat") and row.get("lon"):
            row["dist"] = haversine(row["lat"], row["lon"], ref["lat"], ref["lon"])

    available_sorted = sorted(available, key=lambda r: r.get("dist") or 9999)

    # Deduplicate (venue + field_name)
    seen_keys: set = set()
    deduped: list[dict] = []
    for r in available_sorted:
        key = (r["venue_id"], r["field"].lower())
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(r)

    n_venues = len({r["venue_id"] for r in deduped})
    avail_ids = {r["venue_id"] for r in deduped}
    na_in_radius = [v for v in venues_in_radius
                    if v.get("lat") and v["id"] not in avail_ids]

    # Metrics
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Lapangan tersedia", len(deduped))
    mc2.metric("Venue tersedia", n_venues)
    mc3.metric("Venue dicek", n_filtered)
    mc4.metric(
        "Terdekat",
        deduped[0]["venue"][:22] if deduped else "–",
        f"{deduped[0]['dist']:.1f} km" if deduped and deduped[0].get("dist") else "",
    )

    # Info pencarian terakhir
    lp = st.session_state.last_search
    if lp:
        radius_lbl = "semua Jakarta" if lp[6] == 999 else f"{lp[6]} km"
        st.caption(
            f"🔍 Filter terakhir: **{lp[0]}**, jam ~{lp[1]}:00, "
            f"durasi {lp[2]:g} jam, {sport_opts[lp[3]]}, "
            f"radius **{radius_lbl}** dari ({lp[4]:.4f}, {lp[5]:.4f})"
        )

    st.divider()

    # ── Peta ──────────────────────────────────────────────────────────────────
    st.subheader("🗺️ Peta")
    st.caption("Klik marker **hijau** → set sebagai titik referensi (tabel re-sort otomatis).")

    av_lats  = [r["lat"] for r in deduped if r.get("lat")]
    av_lons  = [r["lon"] for r in deduped if r.get("lon")]
    av_names = [r["venue"] for r in deduped if r.get("lat")]
    av_hover = [
        f"<b>{r['venue']}</b><br>{r['area']}<br>"
        f"🏅 {r['field']}<br>🕖 {r['slot']}<br>"
        f"💰 Rp{r['price']:,}<br>📍 {r['dist']:.1f} km"
        for r in deduped if r.get("lat")
    ]

    fig = go.Figure()

    # Gray dots: venues in radius, no slot
    if na_in_radius:
        fig.add_trace(go.Scattermap(
            lat=[v["lat"] for v in na_in_radius],
            lon=[v["lon"] for v in na_in_radius],
            mode="markers",
            marker=dict(size=7, color="#aaa", opacity=0.45),
            text=[v["name"] for v in na_in_radius],
            hovertemplate="<b>%{text}</b><br>Tidak tersedia<extra></extra>",
            name="Tidak tersedia",
        ))

    # Green markers: available
    fig.add_trace(go.Scattermap(
        lat=av_lats, lon=av_lons,
        mode="markers",
        marker=dict(size=15, color="#00b86b", opacity=0.9),
        hovertext=av_hover,
        hovertemplate="%{hovertext}<extra></extra>",
        name="Tersedia ✅",
    ))

    # Blue star: reference point
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
        height=460,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
    )

    event = st.plotly_chart(fig, on_select="rerun", selection_mode="points", key="main_map")

    # Click marker → update ref point
    if event and event.selection and event.selection.points:
        pt = event.selection.points[0]
        ti = getattr(pt, "trace_index", None)
        if ti == 1:  # green trace = available venues
            idx = getattr(pt, "point_index", 0)
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
        st.info("Tidak ada lapangan tersedia. Coba perluas radius atau ganti jam.")
    else:
        df = pd.DataFrame([{
            "Jarak":    f"{r['dist']:.1f} km" if r.get("dist") else "–",
            "Venue":    r["venue"],
            "Area":     r["area"].replace("Kota ", ""),
            "Lapangan": r["field"],
            "Slot":     r["slot"],
            "Harga":    f"Rp{r['price']:,}" if r["price"] else "?",
            "Booking":  r["url"],
        } for r in deduped])

        st.dataframe(
            df, hide_index=True,
            column_config={
                "Booking": st.column_config.LinkColumn("Booking", display_text="Buka →"),
                "Jarak":   st.column_config.TextColumn(width="small"),
                "Slot":    st.column_config.TextColumn(width="small"),
                "Harga":   st.column_config.TextColumn(width="small"),
                "Area":    st.column_config.TextColumn(width="medium"),
            },
        )

elif not st.session_state.search_done:
    st.info("Atur hari, jam, dan radius di atas, lalu klik **Cari Lapangan**.")
