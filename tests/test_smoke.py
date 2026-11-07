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


def test_advertising_request_and_admin_workflow(tmp_path, monkeypatch):
    import ads
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(ads, "DB_PATH", tmp_path / "ads-test.db")
    monkeypatch.setattr(ads, "ADMIN_PASSWORD", "test-admin-secret")
    ads.init_db()
    with TestClient(main.app) as client:
        public = client.post("/api/ads/requests", json={
            "company": "شركة اختبار", "contact": "مسؤول الإعلان",
            "phone": "+962790000000", "email": "ads@example.com",
            "city": "عمّان", "slot": "home_top", "duration": "شهر",
            "website": "https://example.com", "message": "طلب تجريبي"
        })
        assert public.status_code == 200
        assert public.json()["ok"] is True

        assert client.get("/api/admin/ads/requests").status_code == 401
        headers = {"X-Admin-Password": "test-admin-secret"}
        reqs = client.get("/api/admin/ads/requests", headers=headers)
        assert reqs.status_code == 200 and len(reqs.json()) == 1

        now = datetime.now(timezone.utc)
        created = client.post("/api/admin/ads", headers=headers, json={
            "title": "إعلان تجريبي", "target_url": "https://example.com",
            "slot": "home_top", "city": "", "starts_at": (now-timedelta(minutes=1)).isoformat(),
            "ends_at": (now+timedelta(days=2)).isoformat(), "status": "active"
        })
        assert created.status_code == 200
        ad_id = created.json()["id"]
        active = client.get("/api/ads/active?slot=home_top")
        assert active.status_code == 200 and active.json()[0]["id"] == ad_id
        assert client.post(f"/api/ads/{ad_id}/impression").status_code == 200
        click = client.get(f"/api/ads/{ad_id}/click", follow_redirects=False)
        assert click.status_code == 302 and click.headers["location"] == "https://example.com"
        metrics = client.get("/api/admin/ads", headers=headers).json()[0]
        assert metrics["impressions"] == 1 and metrics["clicks"] == 1

        backup = client.get("/api/admin/ads/backup", headers=headers)
        assert backup.status_code == 200 and len(backup.json()["ads"]) == 1
        paused = client.post(f"/api/admin/ads/{ad_id}/status", headers=headers, json={"status":"paused"})
        assert paused.status_code == 200
        assert client.get("/api/ads/active?slot=home_top").json() == []


def test_advertiser_form_and_admin_page_are_served():
    with TestClient(main.app) as client:
        page = client.get("/advertise")
        assert page.status_code == 200
        assert "adForm" in page.text
        admin = client.get("/admin/ads")
        assert admin.status_code == 200
        assert "إدارة إعلانات" in admin.text
        js = client.get("/static/ads.js")
        assert js.status_code == 200 and "loadWeatherAd" in js.text
