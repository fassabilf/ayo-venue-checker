"""
Ayo Venue Checker — Streamlit Web App
Run: streamlit run app.py
"""

import json
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
    get_venues_for_area, fetch_coords_solo,
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
  .badge { display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:500 }
  .badge-green  { background:#e8f5e9; color:#2e7d32 }
  .badge-yellow { background:#fff3e0; color:#e65100 }
</style>
""", unsafe_allow_html=True)

# ─── Session-state cache helpers ──────────────────────────────────────────────

def _age_seconds(iso: str) -> float:
    return (datetime.now() - datetime.fromisoformat(iso)).total_seconds()

def catalog_from_cache(cabor: int):
    key = f"cat_{cabor}"
    if key in st.session_state and _age_seconds(st.session_state[key]["at"]) < 6 * 3600:
        return st.session_state[key]["data"]
    return None

def save_catalog(cabor: int, data: dict):
    st.session_state[f"cat_{cabor}"] = {"data": data, "at": datetime.now().isoformat()}

def avail_from_cache(h: str):
    key = f"av_{h}"
    if key in st.session_state and _age_seconds(st.session_state[key]["at"]) < 15 * 60:
        return st.session_state[key]["data"]
    return None

def save_avail(h: str, data: list):
    st.session_state[f"av_{h}"] = {"data": data, "at": datetime.now().isoformat()}

# ─── Session state init ───────────────────────────────────────────────────────

for k, v in {
    "ref_lat": -6.2896, "ref_lon": 106.8400, "ref_name": "Pasar Minggu",
    "available": None, "all_venues": [], "search_done": False,
    "last_search": {}, "geo_results": [],
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─── Header ───────────────────────────────────────────────────────────────────

st.title("🎾 Cari Lapangan Kosong — Jakarta")
st.caption("Data dari ayo.co.id · Atur parameter lalu klik **Cari Lapangan**")

sport_opts = {7: "🎾 Tennis", 1: "⚽ Futsal", 4: "🏸 Badminton",
              8: "🏀 Basket", 12: "⚽ Mini Soccer"}

# ─── Form ─────────────────────────────────────────────────────────────────────

qp = st.query_params
def qp_get(key, default, cast=str):
    try: return cast(qp[key]) if key in qp else default
    except Exception: return default

with st.container(border=True):
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    with c1:
        hari = st.selectbox("📅 Hari", DAY_NAMES,
                            index=DAY_NAMES.index(qp_get("hari", "Rabu"))
                                  if qp_get("hari","Rabu") in DAY_NAMES else 2)
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
    ref_lat  = rc2.number_input("Lat", value=st.session_state.ref_lat, format="%.5f", step=0.001)
    ref_lon  = rc3.number_input("Lon", value=st.session_state.ref_lon, format="%.5f", step=0.001)
    max_km   = rc4.selectbox("Radius", [5, 10, 15, 20, 999], index=1,
                             format_func=lambda x: "Semua Jkt" if x == 999 else f"{x} km")
    if rc5.button("✔ Terapkan", width="stretch"):
        st.session_state.ref_lat  = ref_lat
        st.session_state.ref_lon  = ref_lon
        st.session_state.ref_name = ref_name
        st.rerun()

with st.expander("⚙️ Opsi tampilan", expanded=False):
    sort_by = st.radio("Urutkan hasil", ["📍 Jarak", "💰 Harga"], horizontal=True)

st.divider()

# ─── Proses (hanya saat klik Cari) ───────────────────────────────────────────

if cari_btn:
    ref      = {"lat": st.session_state.ref_lat, "lon": st.session_state.ref_lon}
    date_str = target_date.strftime("%Y-%m-%d")
    radius_lbl = "semua Jakarta" if max_km == 999 else f"dalam {max_km} km"

    st.query_params.update({"hari": hari, "jam": str(jam_main), "dur": str(durasi),
                             "cabor": str(cabor), "lat": str(round(ref["lat"],5)),
                             "lon": str(round(ref["lon"],5)), "km": str(max_km)})

    # ── Fase 1: Katalog venue ─────────────────────────────────────────────────

    cached_cat = catalog_from_cache(cabor)

    if cached_cat:
        all_venues = cached_cat["venues"]
        cat_age = int(_age_seconds(st.session_state[f"cat_{cabor}"]["at"]) / 60)
        age_str = f"{cat_age} mnt lalu" if cat_age < 60 else f"{cat_age//60} jam lalu"
        st.success(f"📦 Katalog dari cache ({age_str}) — {cached_cat['total']} venue", icon=None)
    else:
        with st.status("📦 Mengambil daftar venue Jakarta…", expanded=True) as cat_status:

            # Step A: venue list per area
            st.write("**Step 1 dari 2 — Ambil daftar venue dari 8 area Jakarta**")
            all_venues, seen = [], set()
            prog_area = st.progress(0.0)

            for i, lokasi in enumerate(JAKARTA_AREAS):
                st.write(f"&nbsp;&nbsp;&nbsp;⟳ {lokasi}…")
                session = make_session()
                area_v  = get_venues_for_area(session, cabor, lokasi)
                new     = [v for v in area_v if v["id"] not in seen]
                seen.update(v["id"] for v in new)
                all_venues.extend(new)
                prog_area.progress((i + 1) / len(JAKARTA_AREAS))
                st.write(f"&nbsp;&nbsp;&nbsp;✅ {lokasi} — **{len(new)} venue baru** (total {len(all_venues)})")
                time.sleep(0.15)

            prog_area.progress(1.0)

            # Step B: koordinat (concurrent)
            st.divider()
            st.write(f"**Step 2 dari 2 — Ambil koordinat {len(all_venues)} venue** (5 paralel)")
            prog_coord  = st.progress(0.0)
            coord_text  = st.empty()
            done_coord  = 0
            coord_map   = {v["id"]: i for i, v in enumerate(all_venues)}

            with ThreadPoolExecutor(max_workers=5) as ex:
                futures = {ex.submit(fetch_coords_solo, dict(v)): v["id"] for v in all_venues}
                for fut in as_completed(futures):
                    vid = futures[fut]
                    v_result = fut.result()
                    all_venues[coord_map[vid]] = v_result
                    done_coord += 1
                    prog_coord.progress(
                        done_coord / len(all_venues),
                        f"[{done_coord}/{len(all_venues)}] {v_result['name'][:45]}"
                    )

            cat_data = {"venues": all_venues, "total": len(all_venues),
                        "fetched_at": datetime.now().isoformat()}
            save_catalog(cabor, cat_data)
            cat_status.update(label=f"✅ Katalog siap — {len(all_venues)} venue dimuat",
                              state="complete", expanded=False)

    # ── Fase 2: Filter radius ─────────────────────────────────────────────────

    venues_in_radius = [
        v for v in all_venues
        if v.get("lat") and v.get("lon") and (
            max_km == 999 or
            haversine(v["lat"], v["lon"], ref["lat"], ref["lon"]) <= max_km
        )
    ]
    n_filt   = len(venues_in_radius)
    est_secs = max(2, (n_filt + 4) // 5)

    st.info(
        f"📍 **{n_filt} venue** {radius_lbl} dari **{st.session_state.ref_name}** "
        f"akan dicek ketersediaannya (~{est_secs} detik)",
        icon=None
    )

    # ── Fase 3: Availability check (concurrent) ───────────────────────────────

    avail_hash = str(hash((
        tuple(sorted(v["id"] for v in venues_in_radius)),
        date_str, jam_main, int(durasi * 2), cabor
    )))
    cached_avail = avail_from_cache(avail_hash)

    if cached_avail is not None:
        av_age = int(_age_seconds(st.session_state[f"av_{avail_hash}"]["at"]) / 60)
        st.success(f"⚡ Hasil availability dari cache ({av_age} mnt lalu) — {len(cached_avail)} lapangan", icon=None)
        raw = cached_avail
    else:
        def check_one(v: dict):
            s = make_session_bare()
            fields = check_fields_flexible(s, v["id"], date_str, jam_main, durasi, cabor)
            return v, fields

        with st.status(f"🔍 Cek ketersediaan {n_filt} venue…", expanded=True) as av_status:
            st.write(f"Hari: **{hari}** · Slot: **{jam_main-1}:00–{jam_main+1}:00** · "
                     f"Durasi: **{durasi:g} jam** · {sport_opts[cabor]}")
            st.write(f"Radius: **{radius_lbl}** dari **{st.session_state.ref_name}** · "
                     f"5 venue paralel · estimasi ~{est_secs}s")
            st.divider()

            prog_av    = st.progress(0.0)
            av_current = st.empty()
            av_found   = st.empty()

            raw         = []
            done_av     = 0
            found_names = []

            with ThreadPoolExecutor(max_workers=5) as ex:
                futures = {ex.submit(check_one, v): v for v in venues_in_radius}
                for fut in as_completed(futures):
                    v, fields = fut.result()
                    done_av += 1

                    for f in fields:
                        url = (f"https://ayo.co.id/v/{v['slug']}?date={date_str}&field_id={f['field_id']}"
                               if v.get("slug") else f"https://ayo.co.id/venue/{v['id']}")
                        alt_str = ", ".join(
                            f"{a['slot_start']:02d}:00–{a['slot_end']:02d}:00"
                            for a in f.get("alt_slots", [])
                        )
                        raw.append({
                            "venue_id": v["id"], "venue": v["name"], "area": v["area"],
                            "lat": v["lat"], "lon": v["lon"],
                            "field_id": f["field_id"], "field": f["field_name"],
                            "slot": f"{f['slot_start']:02d}:00–{f['slot_end']:02d}:00",
                            "slot_start": f["slot_start"], "price": f["price_total"],
                            "alt_slots": alt_str, "url": url,
                        })
                        if v["name"] not in found_names:
                            found_names.append(v["name"])

                    prog_av.progress(
                        done_av / n_filt,
                        f"[{done_av}/{n_filt}] {v['name'][:45]}"
                    )
                    if found_names:
                        av_found.success(
                            "✅ Tersedia: " + " · ".join(found_names[-4:])
                            + (f" (+{len(found_names)-4} lain)" if len(found_names) > 4 else "")
                        )
                    else:
                        av_found.caption("Belum ada lapangan tersedia yang ditemukan…")

            n_lap = len(raw)
            n_ven = len(found_names)
            av_status.update(
                label=f"✅ Selesai — {n_lap} lapangan tersedia dari {n_ven} venue",
                state="complete", expanded=False
            )

        save_avail(avail_hash, raw)

    st.session_state.available   = raw
    st.session_state.all_venues  = all_venues
    st.session_state.search_done = True
    st.session_state.last_search = {
        "hari": hari, "jam": jam_main, "durasi": durasi,
        "cabor": cabor, "sport": sport_opts[cabor],
        "ref_name": st.session_state.ref_name,
        "ref_lat": round(ref["lat"], 4), "ref_lon": round(ref["lon"], 4),
        "radius": max_km, "date_str": date_str,
        "n_checked": n_filt,
    }
    st.rerun()

# ─── Hasil ────────────────────────────────────────────────────────────────────

if st.session_state.search_done and st.session_state.available is not None:
    ls  = st.session_state.last_search
    ref = {"lat": st.session_state.ref_lat, "lon": st.session_state.ref_lon}

    avail = list(st.session_state.available)
    for r in avail:
        if r.get("lat") and r.get("lon"):
            r["dist"] = haversine(r["lat"], r["lon"], ref["lat"], ref["lon"])

    seen_k: set = set()
    deduped: list[dict] = []
    for r in sorted(avail, key=lambda r: r.get("dist") or 9999):
        key = (r["venue_id"], r["field"].lower())
        if key not in seen_k:
            seen_k.add(key)
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

    # Cache info + share link
    col_info, col_ref = st.columns([5, 1])
    with col_info:
        radius_info = "semua Jakarta" if ls["radius"] == 999 else f"{ls['radius']} km"
        st.caption(
            f"**{ls['hari']}** · jam ~{ls['jam']}:00 · {ls['durasi']:g}j · "
            f"{ls['sport']} · {radius_info} dari **{ls['ref_name']}**"
        )
        share = (f"?hari={ls['hari']}&jam={ls['jam']}&dur={ls['durasi']}&cabor={ls['cabor']}"
                 f"&lat={ls['ref_lat']}&lon={ls['ref_lon']}&km={ls['radius']}")
        st.caption(f"🔗 Share: `{share}`")
    with col_ref:
        if st.button("🔄 Hapus Cache"):
            for k in list(st.session_state.keys()):
                if k.startswith("cat_") or k.startswith("av_"):
                    del st.session_state[k]
            st.rerun()

    # Metrics
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Lapangan tersedia", len(deduped))
    mc2.metric("Venue tersedia",    n_venues)
    mc3.metric("Venue dicek",       ls["n_checked"])
    mc4.metric("Terdekat",
               deduped[0]["venue"][:22] if deduped else "–",
               f"{deduped[0]['dist']:.1f} km" if deduped and deduped[0].get("dist") else "")

    st.divider()

    # ── Peta ──────────────────────────────────────────────────────────────────
    st.subheader("🗺️ Peta")
    st.caption("Klik marker **hijau** → set titik referensi baru, tabel re-sort otomatis.")

    av_lats  = [r["lat"] for r in deduped if r.get("lat")]
    av_lons  = [r["lon"] for r in deduped if r.get("lon")]
    av_hover = [
        f"<b>{r['venue']}</b><br>{r['area']}<br>🏅 {r['field']}<br>🕖 {r['slot']}"
        + (f"<br><span style='color:#888'>juga: {r['alt_slots']}</span>" if r.get("alt_slots") else "")
        + f"<br>💰 Rp{r['price']:,}<br>📍 {r.get('dist',0):.1f} km"
        for r in deduped if r.get("lat")
    ]

    fig = go.Figure()
    if na_in_radius:
        fig.add_trace(go.Scattermap(
            lat=[v["lat"] for v in na_in_radius], lon=[v["lon"] for v in na_in_radius],
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
        st.info("Tidak ada lapangan tersedia. Coba perluas radius atau ganti jam.")
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

        st.dataframe(df, hide_index=True, column_config={
            "Booking":   st.column_config.LinkColumn("Booking", display_text="Buka →"),
            "Jarak":     st.column_config.TextColumn(width="small"),
            "Slot":      st.column_config.TextColumn(width="small"),
            "Slot lain": st.column_config.TextColumn("Slot lain", width="medium",
                                                     help="Slot alternatif yang juga kosong"),
            "Harga":     st.column_config.TextColumn(width="small"),
            "Area":      st.column_config.TextColumn(width="medium"),
        })

else:
    st.markdown("""
### Cara pakai
1. Pilih **hari** dan **sekitar jam berapa** mau main
2. Set **titik referensi** (default Pasar Minggu) — bisa cari nama atau isi koordinat manual
3. Pilih **radius** (default 10 km dari titik referensi)
4. Klik **🔍 Cari Lapangan**

> **Pertama kali:** ~2–3 menit (ambil semua venue Jakarta + koordinatnya)
> **Selanjutnya:** instan dari cache — cek availability ~3–6 detik
""")
