"""
ayo.co.id scraper module — semua logic fetch/check, no UI.
"""
# v2

import html as html_lib
import math
import re
import time
from datetime import datetime, timedelta

import threading

import requests

BASE_URL = "https://ayo.co.id"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9",
}

JAKARTA_AREAS = [
    "Kota Jakarta Selatan",
    "Kota Jakarta Barat",
    "Kota Jakarta Timur",
    "Kota Jakarta Utara",
    "Kota Jakarta Pusat",
    "Kota Depok",
    "Kota Tangerang Selatan",
    "Kota Bekasi",
]

SPORT_NAMES = {
    1: "Futsal", 2: "Futsal", 4: "Badminton", 5: "Badminton",
    7: "Tennis", 8: "Basket", 12: "Mini Soccer",
}

DAY_NAMES = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


# ─── Math ──────────────────────────────────────────────────────────────────────

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def avg_dist(lat, lon, points: list[dict]) -> float | None:
    if not points or lat is None or lon is None:
        return None
    return sum(haversine(lat, lon, p["lat"], p["lon"]) for p in points) / len(points)


def next_weekday(weekday: int) -> datetime:
    today = datetime.today()
    days = weekday - today.weekday()
    if days <= 0:
        days += 7
    return today + timedelta(days=days)


def day_index(name: str) -> int:
    return DAY_NAMES.index(name)


# ─── Session ──────────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(BASE_URL, timeout=15)
    except Exception:
        pass
    return s


def make_session_bare() -> requests.Session:
    """Session tanpa homepage hit — aman untuk concurrent use."""
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# ─── Geocoding ────────────────────────────────────────────────────────────────

_geo_lock = threading.Lock()
_geo_last_call = 0.0


def geocode(query: str, limit: int = 5) -> list[dict]:
    """
    Cari koordinat dari nama tempat via Nominatim (OpenStreetMap).
    Returns list of {name, lat, lon, display_name}.
    Rate-limited 1 req/s sesuai ToS Nominatim.
    """
    global _geo_last_call
    with _geo_lock:
        elapsed = time.time() - _geo_last_call
        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)
        _geo_last_call = time.time()

    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{query}, Indonesia", "format": "json",
                    "limit": limit, "countrycodes": "id"},
            headers={"User-Agent": "ayo-venue-checker/1.0 (github.com/fassabilf/ayo-venue-checker)"},
            timeout=8,
        )
        items = r.json()
        return [
            {
                "name":         i.get("display_name", "").split(",")[0].strip(),
                "display_name": i.get("display_name", ""),
                "lat":          float(i["lat"]),
                "lon":          float(i["lon"]),
            }
            for i in items
        ]
    except Exception:
        return []


# ─── Venue list ────────────────────────────────────────────────────────────────

def get_venues_for_area(session: requests.Session, cabor: int, lokasi: str) -> list[dict]:
    venues = []
    seen: set[str] = set()
    for page in range(1, 20):
        try:
            r = session.get(f"{BASE_URL}/venues", params={
                "sortby": 5, "tipe": "venue", "nameuser": "",
                "lokasi": lokasi, "cabor": cabor,
                "biaya_min": "", "biaya_max": "", "page": page,
            }, timeout=20)
            r.raise_for_status()
        except Exception:
            break

        text = r.text
        ids   = re.findall(r"id=['\"]venue-(\d+)['\"]", text)
        slugs = re.findall(r"href=['\"]https://ayo\.co\.id/v/([^'\"]+)['\"]", text)
        names = re.findall(r"class=['\"]text-left s20-500 turncate['\"]>([^<]+)<", text)

        if not ids:
            break

        for i, vid in enumerate(ids):
            if vid in seen:
                continue
            seen.add(vid)
            venues.append({
                "id": int(vid),
                "slug": slugs[i] if i < len(slugs) else None,
                "name": html_lib.unescape(names[i].strip()) if i < len(names) else f"Venue {vid}",
                "area": lokasi,
                "lat": None,
                "lon": None,
            })

        if '<link rel="next"' not in text and "rel='next'" not in text:
            break
        time.sleep(0.35)

    return venues


def get_all_venues(session: requests.Session, cabor: int,
                   on_area=None) -> list[dict]:
    """
    Fetch semua venue dari seluruh JAKARTA_AREAS.
    on_area(area_name, count, total): optional callback for progress.
    """
    all_venues: list[dict] = []
    seen_ids: set[int] = set()

    for lokasi in JAKARTA_AREAS:
        area_venues = get_venues_for_area(session, cabor, lokasi)
        new = [v for v in area_venues if v["id"] not in seen_ids]
        seen_ids.update(v["id"] for v in new)
        all_venues.extend(new)
        if on_area:
            on_area(lokasi, len(new), len(all_venues))
        time.sleep(0.2)

    return all_venues


# ─── Coordinates ──────────────────────────────────────────────────────────────

def fetch_coords_solo(venue: dict) -> dict:
    """Thread-safe: buat session sendiri, fetch coords satu venue."""
    return fetch_coords(make_session_bare(), venue)


def fetch_coords(session: requests.Session, venue: dict) -> dict:
    if not venue.get("slug"):
        return venue
    try:
        r = session.get(f"{BASE_URL}/v/{venue['slug']}", timeout=15)
        m = re.search(r"open_map\((-?\d+\.\d+),\s*(\d+\.\d+)\)", r.text)
        if m:
            venue["lat"] = float(m.group(1))
            venue["lon"] = float(m.group(2))
    except Exception:
        pass
    return venue


# ─── Availability — flexible window ───────────────────────────────────────────

def check_fields_flexible(
    session: requests.Session,
    venue_id: int,
    date_str: str,
    jam_main: int,
    durasi_jam: float,
    sport_id: int | None = None,
) -> list[dict]:
    """
    Cek field yang punya `durasi_jam` jam berturut-turut tersedia,
    dengan slot mulai di window [jam_main - 1, jam_main + 1].

    Returns list of:
      {field_id, field_name, slot_start, slot_end, price_total}
    Diurutkan: slot yang paling dekat ke jam_main lebih dulu.
    """
    try:
        r = session.get(
            f"{BASE_URL}/venues-ajax/op-times-and-fields",
            params={"venue_id": venue_id, "date": date_str},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []

    n_hours = math.ceil(durasi_jam)
    # Window of candidate start hours
    candidates = list(range(jam_main - 1, jam_main + 2))  # ±1 jam

    results: list[dict] = []
    seen_fids: set = set()

    for field in data.get("fields", []):
        fid = field.get("field_id") or field.get("id")
        if fid in seen_fids:
            continue
        seen_fids.add(fid)

        if sport_id and field.get("sport_id") != sport_id:
            continue

        # Build hour → available & price map
        hour_avail: dict[int, bool] = {}
        hour_price: dict[int, int]  = {}
        for sl in field.get("slots", []):
            try:
                sh = int(sl["start_time"].split(":")[0])
                eh = int(sl["end_time"].split(":")[0])
                avail = bool(sl.get("is_available", 0))
                price = sl.get("price", 0)
                for h in range(sh, eh):
                    hour_avail[h] = hour_avail.get(h, avail) and avail
                    if avail and h not in hour_price:
                        hour_price[h] = price
            except (KeyError, ValueError):
                continue

        # Try each candidate start hour
        found: list[dict] = []
        for start in candidates:
            hours_needed = range(start, start + n_hours)
            if not all(hour_avail.get(h, False) for h in hours_needed):
                continue
            price_total = sum(hour_price.get(h, 0) for h in hours_needed)
            found.append({
                "field_id":   fid,
                "field_name": field.get("field_name", "?"),
                "slot_start": start,
                "slot_end":   start + n_hours,
                "price_total": price_total,
            })

        if found:
            best = min(found, key=lambda x: abs(x["slot_start"] - jam_main))
            alt  = [f for f in found if f is not best]
            results.append({
                **best,
                "field_id":   fid,
                "field_name": field.get("field_name", "?"),
                "alt_slots":  alt,   # other valid windows for this field
            })

    return results
