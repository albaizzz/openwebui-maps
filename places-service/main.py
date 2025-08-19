import os
import time
from typing import List, Optional
from urllib.parse import quote_plus

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
DEFAULT_RADIUS = int(os.getenv("DEFAULT_RADIUS_METERS", "2500"))
MAX_RESULTS_DEFAULT = int(os.getenv("MAX_RESULTS", "5"))

if not API_KEY:
    raise RuntimeError("GOOGLE_MAPS_API_KEY is required")

app = FastAPI(
    title="OpenWebUI Places Service",
    version="1.0.0",
    description="Service kecil untuk mencari tempat (Google Places) dan membuat link arah/preview map."
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # kalau mau dibatasi bisa ganti misal ["http://localhost:3000"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


PLACES_TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"


class LatLng(BaseModel):
    lat: float
    lng: float

class Place(BaseModel):
    name: str
    address: Optional[str] = None
    location: Optional[LatLng] = None
    place_id: Optional[str] = None
    rating: Optional[float] = None
    user_ratings_total: Optional[int] = None
    maps_url: Optional[str] = None
    directions_url: Optional[str] = None
    static_map_image_url: Optional[str] = None
    embed_iframe: Optional[str] = None

class PlacesResponse(BaseModel):
    query: str
    radius: int
    count: int
    took_ms: int
    places: List[Place]


def build_maps_url(place_id: Optional[str], name: str, lat: Optional[float], lng: Optional[float]) -> str:
    if place_id:
        return f"https://www.google.com/maps/place/?q=place_id:{quote_plus(place_id)}"
    q = quote_plus(name)
    if lat is not None and lng is not None:
        return f"https://www.google.com/maps/search/?api=1&query={q}&center={lat}%2C{lng}"
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def build_directions_url(place_id: Optional[str], lat: Optional[float], lng: Optional[float]) -> str:
    if place_id and lat is not None and lng is not None:
        return (
            "https://www.google.com/maps/dir/?api=1"
            f"&destination_place_id={quote_plus(place_id)}"
            f"&destination={lat}%2C{lng}"
        )
    if lat is not None and lng is not None:
        return f"https://www.google.com/maps/dir/?api=1&destination={lat}%2C{lng}"
    return "https://www.google.com/maps/dir/?api=1"


def build_static_map(lat: Optional[float], lng: Optional[float], label: str = "A") -> Optional[str]:
    if lat is None or lng is None:
        return None
    params = {
        "center": f"{lat},{lng}",
        "zoom": "16",
        "size": "600x360",
        "markers": f"label:{label}|{lat},{lng}",
        "key": API_KEY,
        "scale": "2",
        "maptype": "roadmap",
    }
    query = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
    return f"{STATIC_MAPS_URL}?{query}"


def build_embed_iframe(place_id: Optional[str], name: str) -> Optional[str]:
    if not place_id:
        return None
    src = f"https://www.google.com/maps/embed/v1/place?key={API_KEY}&q=place_id:{quote_plus(place_id)}"
    return (
        f'<iframe width="600" height="360" style="border:0" loading="lazy" allowfullscreen '
        f'referrerpolicy="no-referrer-when-downgrade" src="{src}"></iframe>'
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/find_places", response_model=PlacesResponse)
def find_places(
    query: str = Query(..., description="Teks pencarian, mis: 'ramen dekat Monas'"),
    lat: Optional[float] = Query(None, description="Latitude untuk bias hasil"),
    lng: Optional[float] = Query(None, description="Longitude untuk bias hasil"),
    radius: int = Query(DEFAULT_RADIUS, description="Radius meter"),
    max_results: int = Query(MAX_RESULTS_DEFAULT, ge=1, le=20, description="Jumlah hasil maksimal")
):
    t0 = time.time()
    params = {"query": query, "key": API_KEY}
    if lat is not None and lng is not None:
        params["location"] = f"{lat},{lng}"
        params["radius"] = str(radius)

    r = requests.get(PLACES_TEXTSEARCH_URL, params=params, timeout=20)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Upstream error: {r.text}")

    data = r.json()
    status = data.get("status")
    if status not in ("OK", "ZERO_RESULTS"):
        raise HTTPException(status_code=400, detail={"status": status, "raw": data})

    results = data.get("results", [])[:max_results]
    places: List[Place] = []

    for i, it in enumerate(results, start=1):
        name = it.get("name")
        address = it.get("formatted_address") or it.get("vicinity")
        place_id = it.get("place_id")
        geom = it.get("geometry", {}).get("location", {})
        plat = geom.get("lat")
        plng = geom.get("lng")
        rating = it.get("rating")
        urt = it.get("user_ratings_total")

        maps_url = build_maps_url(place_id, name, plat, plng)
        directions_url = build_directions_url(place_id, plat, plng)
        static_map = build_static_map(plat, plng, label=chr(64 + i))
        iframe = build_embed_iframe(place_id, name)

        places.append(Place(
            name=name,
            address=address,
            location=LatLng(lat=plat, lng=plng) if plat is not None and plng is not None else None,
            place_id=place_id,
            rating=rating,
            user_ratings_total=urt,
            maps_url=maps_url,
            directions_url=directions_url,
            static_map_image_url=static_map,
            embed_iframe=iframe
        ))

    took_ms = int((time.time() - t0) * 1000)
    return PlacesResponse(
        query=query,
        radius=radius,
        count=len(places),
        took_ms=took_ms,
        places=places
    )
