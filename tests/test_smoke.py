import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import main


def test_app_starts_and_serves_core_pages():
    with TestClient(main.app) as client:
        for path in (
            "/",
            "/advertise",
            "/map",
            "/accuracy",
            "/sw.js",
            "/manifest.webmanifest",
            "/robots.txt",
            "/sitemap.xml",
        ):
            response = client.get(path)
            assert response.status_code == 200, (path, response.status_code)
            assert response.content


def test_city_api_has_jordan_and_other_levant_cities():
    with TestClient(main.app) as client:
        response = client.get("/api/cities")
        assert response.status_code == 200
        cities = response.json()
        assert len(cities) == 25
        jordan = [city for city in cities if city["country"] == "الأردن"]
        assert len(jordan) == 12
        assert {city["slug"] for city in jordan} >= {
            "amman", "irbid", "zarqa", "ajloun", "jerash", "salt",
            "madaba", "karak", "tafilah", "maan", "aqaba", "deadsea",
        }


def test_map_uses_weatherapi_tiles_not_open_meteo_grid():
    with TestClient(main.app) as client:
        response = client.get("/map")
        assert response.status_code == 200
        assert "weathermaps.weatherapi.com" in response.text
        assert "Open-Meteo" not in response.text
        assert "الضغط" in response.text


def test_advertising_page_has_contact_and_ad_types():
    with TestClient(main.app) as client:
        response = client.get("/advertise")
        assert response.status_code == 200
        assert "أعلن معنا" in response.text
        assert "wa.me/962795177640" in response.text
        assert "رعاية" in response.text
