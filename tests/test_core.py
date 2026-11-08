"""
اختبارات آلية - طقس بلاد الشام
Automated tests for the core engine: alerts, accuracy, routes, ads, security.

التشغيل:  python -m pytest tests/ -v
أو:       python tests/test_core.py   (بلا pytest)
"""
import os
import sys
import tempfile
from pathlib import Path

# نعزل البيانات في مجلد مؤقت قبل استيراد أي وحدة
_TMP = tempfile.mkdtemp(prefix="lw-test-")
os.environ["DATA_DIR"] = _TMP
os.environ["ADMIN_TOKEN"] = "test-token-1234567890abc"
os.environ["SITE_URL"] = "https://test.example"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import accuracy  # noqa: E402
import ads  # noqa: E402
import alerts  # noqa: E402
import config  # noqa: E402
import content  # noqa: E402
import push  # noqa: E402
import security  # noqa: E402


# ============================================================
# محرّك التنبيهات
# ============================================================
def _series(vals, n=48):
    return list(vals) + [vals[-1]] * (n - len(vals))


def _hourly(**cols):
    """يبني قاموس hourly بأعمدة لكل نموذج.

    المفاتيح المختصرة تُترجم إلى أسماء أعمدة Open-Meteo الحقيقية التي يقرأها
    محرّك التنبيهات: t=temperature_2m, p=precipitation, w=wind_speed_10m,
    g=wind_gusts_10m, sn=snowfall, cape=cape.
    """
    names = {"t": "temperature_2m", "p": "precipitation", "w": "wind_speed_10m",
             "g": "wind_gusts_10m", "sn": "snowfall", "cape": "cape"}
    out = {}
    for base, per_model in cols.items():
        full = names.get(base, base)
        for model, vals in per_model.items():
            out[f"{full}_{model}"] = _series(vals)
    return out


TIMES = [f"2026-11-07T{h:02d}:00" for h in range(48)]


def test_alerts_two_of_three_heavy_rain_is_orange():
    """مطر غزير في نموذجين من ثلاثة => برتقالي (2)."""
    h = _hourly(p={
        "ecmwf_ifs025": [0, 0, 20, 20, 0],
        "gfs_seamless": [0, 0, 18, 18, 0],
        "icon_seamless": [0, 0, 0, 0, 0],
    })
    out = alerts.evaluate(h, TIMES, 0, ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"])
    rain = [a for a in out if a["type"] == "rain"]
    assert rain, "يجب أن يظهر تنبيه مطر"
    assert rain[0]["level"] == 2, f"المستوى المتوقع 2، وجاء {rain[0]['level']}"
    assert rain[0]["agree_n"] == 2


def test_alerts_three_of_three_is_red():
    """مطر غزير في النماذج الثلاثة => أحمر (3)."""
    h = _hourly(p={
        "ecmwf_ifs025": [0, 30, 30, 0],
        "gfs_seamless": [0, 28, 28, 0],
        "icon_seamless": [0, 26, 26, 0],
    })
    out = alerts.evaluate(h, TIMES, 0, ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"])
    rain = [a for a in out if a["type"] == "rain"][0]
    assert rain["level"] == 3
    assert rain["agree_n"] == 3


def test_alerts_single_model_capped_at_yellow():
    """نموذجان يتفقان على الأصفر ونموذج ينفرد بالأحمر => أصفر مع upgrade_risk."""
    h = _hourly(p={
        "ecmwf_ifs025": [0, 10, 10, 0],   # مستوى 1 (≥8 ملم)
        "gfs_seamless": [0, 9, 9, 0],     # مستوى 1
        "icon_seamless": [0, 30, 30, 0],  # مستوى 3 (≥25 ملم)
    })
    out = alerts.evaluate(h, TIMES, 0, ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"])
    rain = [a for a in out if a["type"] == "rain"][0]
    assert rain["level"] == 1, "المستوى المتفق عليه (2 من 3) هو الأصفر"
    assert rain["upgrade_risk"] is True, "يجب وسم خطر التصعيد"


def test_alerts_single_model_alone_is_no_alert():
    """نموذج واحد يتوقع خطراً والاثنان الآخران صفراً => لا تنبيه (إجماع على عدم الخطر)."""
    h = _hourly(p={
        "ecmwf_ifs025": [0, 0, 0, 0],
        "gfs_seamless": [0, 0, 0, 0],
        "icon_seamless": [0, 40, 40, 0],
    })
    out = alerts.evaluate(h, TIMES, 0, ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"])
    assert not [a for a in out if a["type"] == "rain"], "2 من 3 يقولان لا مطر => لا تنبيه"


def test_alerts_no_data_returns_empty():
    """بلا بيانات => لا تنبيهات، وبلا انهيار."""
    h = _hourly(p={"ecmwf_ifs025": [None], "gfs_seamless": [None], "icon_seamless": [None]})
    assert alerts.evaluate(h, TIMES, 0, ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]) == []


def test_alerts_frost_and_heat():
    """الصقيع والحرارة يُقيّمان من الحرارة وحدها."""
    cold = _hourly(t={"ecmwf_ifs025": [-5], "gfs_seamless": [-4], "icon_seamless": [-6]})
    out = alerts.evaluate(cold, TIMES, 0, ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"])
    assert any(a["type"] == "frost" and a["level"] == 3 for a in out)

    hot = _hourly(t={"ecmwf_ifs025": [47], "gfs_seamless": [46], "icon_seamless": [48]})
    out = alerts.evaluate(hot, TIMES, 0, ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"])
    assert any(a["type"] == "heat" and a["level"] == 3 for a in out)


def test_alerts_sorted_by_severity():
    """التنبيهات مرتّبة من الأشد إلى الأخف."""
    h = _hourly(
        t={"ecmwf_ifs025": [45], "gfs_seamless": [45], "icon_seamless": [45]},
        p={"ecmwf_ifs025": [0, 10, 0], "gfs_seamless": [0, 10, 0], "icon_seamless": [0, 10, 0]},
    )
    out = alerts.evaluate(h, TIMES, 0, ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"])
    levels = [a["level"] for a in out]
    assert levels == sorted(levels, reverse=True)


# ============================================================
# الدقة
# ============================================================
def test_accuracy_parse_metar():
    rec = {"icaoId": "OJAM", "obsTime": 1762500000, "temp": 16.0, "rawOb": "METAR OJAM 010000Z 21003KT 5000 HZ NSC 16/10 Q1021"}
    icao, ts, temp, rain = accuracy.parse_metar(rec)
    assert icao == "OJAM" and temp == 16.0 and rain == 0


def test_accuracy_parse_metar_rain_and_negative_temp():
    rec = {"icaoId": "OSDI", "obsTime": 1762500000, "rawOb": "METAR OSDI 010000Z 00000KT 2000 RA BKN010 M02/M03 Q1015"}
    icao, ts, temp, rain = accuracy.parse_metar(rec)
    assert temp == -2 and rain == 1, "يجب كشف المطر والحرارة السالبة"


def test_accuracy_parse_ogimet_line():
    line = "OJAM,2026,11,01,00,00,METAR OJAM 010000Z 21003KT 5000 HZ NSC 16/10 Q1021 NOSIG="
    icao, ts, temp, rain = accuracy.parse_ogimet_line(line)
    assert icao == "OJAM" and temp == 16 and rain == 0


def test_accuracy_parse_ogimet_nil_returns_none():
    line = "OJAM,2026,11,01,00,00,METAR OJAM 010000Z NIL="
    assert accuracy.parse_ogimet_line(line) is None


def test_accuracy_stats_shape_without_data():
    """بلا قراءات: لا أرقام ولا ادعاءات، وكل مدينة إما متاحة بلا بيانات أو غير متاحة."""
    import main
    db = Path(_TMP) / "acc-test.db"
    accuracy.init_db(db)
    cities = {s: {**c} for s, c in main.CITIES.items()}
    st = accuracy.stats(db, cities, 30)
    assert st["overall"]["cities_total"] == 25
    for slug, c in st["cities"].items():
        assert c["basis"] in ("station", "unavailable")
        assert c["basis"] != "regional"
        assert c["leads"]["1"]["n"] == 0


def test_accuracy_daily_summaries():
    from datetime import datetime, timezone
    tz = timezone.utc
    base = int(datetime(2026, 11, 1, 0, tzinfo=tz).timestamp())
    rows = [(base + h * 3600, 10 + h, 1 if h == 5 else 0) for h in range(24)]
    days = accuracy.daily_summaries(rows, tz)
    assert "2026-11-01" in days
    d = days["2026-11-01"]
    assert d["tmax"] == 33 and d["tmin"] == 10 and d["rain"] == 1


# ============================================================
# الأمان: SSRF وحدود الطلب
# ============================================================
def test_ssrf_rejects_internal_addresses():
    for bad in ("http://127.0.0.1:9999/x", "http://169.254.169.254/latest/meta-data",
                "https://localhost/push", "https://10.0.0.1/push", "https://192.168.1.1/push"):
        try:
            security.validate_push_endpoint(bad)
            raise AssertionError(f"كان يجب رفض {bad}")
        except ValueError:
            pass


def test_ssrf_rejects_non_https_and_unknown_hosts():
    for bad in ("ftp://fcm.googleapis.com/x", "https://evil.example.com/push",
                "https://fcm.googleapis.com:8080/x", "https://user:pw@fcm.googleapis.com/x"):
        try:
            security.validate_push_endpoint(bad)
            raise AssertionError(f"كان يجب رفض {bad}")
        except ValueError:
            pass


def test_ssrf_accepts_known_push_hosts():
    for good in ("https://fcm.googleapis.com/fcm/send/abc123",
                 "https://updates.push.services.mozilla.com/wpush/v2/xyz",
                 "https://wns2-par02p.notify.windows.com/w/?token=abc"):
        assert security.validate_push_endpoint(good) == good


def test_validate_subscription_shape():
    ok = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc",
          "keys": {"p256dh": "B" * 40, "auth": "a" * 20}}
    assert security.validate_subscription(ok)["endpoint"].startswith("https://fcm")
    for bad in ({}, {"endpoint": "https://fcm.googleapis.com/x"},
                {"endpoint": "https://fcm.googleapis.com/x", "keys": {"p256dh": "x", "auth": "y"}}):
        try:
            security.validate_subscription(bad)
            raise AssertionError("كان يجب رفض الاشتراك الناقص")
        except ValueError:
            pass


def test_rate_limit_blocks_after_limit():
    ip = "203.0.113.9"
    limit, _ = config.RATE_LIMITS["lead"]
    for _ in range(limit):
        assert security.rate_limit("lead", ip) is True
    assert security.rate_limit("lead", ip) is False, "يجب الحجب بعد تجاوز الحد"


def test_admin_session_roundtrip():
    tok = security.make_session()
    assert security.check_session(tok) is True
    assert security.check_session("9999999999.deadbeef") is False
    assert security.check_session(None) is False
    assert security.check_password("test-token-1234567890abc") is True
    assert security.check_password("wrong") is False


# ============================================================
# الإشعارات: المفاتيح التالفة لا تُسقط الخادم
# ============================================================
def test_push_malformed_keys_returns_false_not_raise():
    """مفاتيح تالفة => False، وليس استثناءً (كان يسبّب 500)."""
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc",
           "keys": {"p256dh": "!!!not-base64!!!", "auth": "!!!"}}
    assert push.send_push(sub, {"title": "t"}) is False


def test_push_owner_binding():
    """لا يمكن حذف اشتراك بلا رمز المالك الصحيح."""
    push.init_db()
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/owner-test",
           "keys": {"p256dh": "B" * 40, "auth": "a" * 20}}
    owner = push.save_sub(sub, "amman", 1, [])
    assert push.get_sub(sub["endpoint"], "wrong-owner") is None
    assert push.get_sub(sub["endpoint"], owner) is not None
    assert push.remove_sub(sub["endpoint"], "wrong-owner") is False
    assert push.remove_sub(sub["endpoint"], owner) is True


# ============================================================
# الإعلانات
# ============================================================
def test_ads_placements_have_standard_sizes():
    for key, p in ads.PLACEMENTS.items():
        assert "label_ar" in p and "size" in p
    assert ads.PLACEMENTS["home_top"]["size"] == "728x90"
    assert ads.PLACEMENTS["city_mid"]["size"] == "300x250"


def test_ads_serve_returns_house_ad_when_empty():
    """بلا حملات => إعلان البيت، لا فراغ."""
    ads.init_db()
    ad = ads.serve("home_bottom", "/")
    assert ad is not None and ad["kind"] == "house" and ad["sponsored"] is False


def test_ads_campaign_lifecycle_and_tracking():
    ads.init_db()
    adv = ads.add_advertiser("معلن اختبار", "Test Co", "t@example.com")
    cid = ads.create_campaign({
        "advertiser_id": adv, "name": "حملة اختبار", "placement": "home_mid",
        "pricing": "cpm", "rate": 5.0, "start_date": "2020-01-01", "end_date": "2099-01-01",
    })
    ads.add_creative(cid, {"kind": "text", "title": "عنوان", "body": "وصف", "cta": "اضغط"})
    ad = ads.serve("home_mid", "/")
    assert ad["campaign_id"] == cid and ad["sponsored"] is True

    for _ in range(1000):
        ads.record_impression(cid, ad["creative_id"], "home_mid", "/")
    for _ in range(10):
        ads.record_click(cid, ad["creative_id"], "home_mid", "/")

    c = ads.get_campaign(cid)
    rev = ads.campaign_revenue(c)
    assert rev == 5.0, f"1000 ظهور × 5.0/1000 = 5.0، وجاء {rev}"

    rep = ads.report(30)
    assert rep["totals"]["impressions"] >= 1000
    assert rep["totals"]["clicks"] >= 10
    assert rep["totals"]["ctr"] > 0


def test_ads_scheduling_respected():
    """حملة منتهية لا تُعرض."""
    ads.init_db()
    cid = ads.create_campaign({
        "name": "منتهية", "placement": "city_top", "pricing": "cpm", "rate": 1,
        "start_date": "2020-01-01", "end_date": "2020-02-01",
    })
    ads.add_creative(cid, {"kind": "text", "title": "قديم"})
    ad = ads.serve("city_top", "/")
    assert ad["campaign_id"] != cid, "الحملة المنتهية يجب ألا تُعرض"


def test_ads_cpc_revenue():
    ads.init_db()
    cid = ads.create_campaign({
        "name": "CPC", "placement": "map_side", "pricing": "cpc", "rate": 0.25,
        "start_date": "2020-01-01", "end_date": "2099-01-01",
    })
    crid = ads.add_creative(cid, {"kind": "text", "title": "ن"})
    for _ in range(8):
        assert ads.record_click(cid, crid, "map_side", "/") is True
    assert ads.campaign_revenue(ads.get_campaign(cid)) == 2.0


def test_ads_txt_contains_network_when_set():
    ads.init_db()
    ads.set_setting("adsense_client", "ca-pub-1234567890123456")
    txt = ads.ads_txt()
    assert "google.com, pub-1234567890123456, DIRECT" in txt
    ads.set_setting("adsense_client", "")


def test_ads_report_csv_has_headers():
    csv_text = ads.report_csv(7)
    assert "التاريخ" in csv_text and "الإيراد" in csv_text


def test_ads_lead_validation():
    ads.init_db()
    lid = ads.add_lead({"name": "عميل", "email": "a@b.com", "message": "مرحبا"})
    assert lid > 0
    assert any(l["email"] == "a@b.com" for l in ads.list_leads())


# ============================================================
# المحتوى
# ============================================================
def test_content_condition_and_summary():
    hours = [{"time": "2026-11-07T10:00", "temp": 20, "precip": 0, "cloud": 10,
              "feels": 20, "wind": 5, "gust": 10, "snow": 0, "uv": 3}]
    assert content.condition(hours) == "صافية"
    w = {"city": {"name": "عمّان"}, "hours": hours,
         "days": [{"date": "2026-11-07", "max": 22, "min": 12, "precip": 0}], "alerts": []}
    s = content.build_summary(w)
    assert "عمّان" in s and "22" in s


def test_content_arabic_dual_forms():
    assert content.deg_text(1) == "درجة"
    assert content.deg_text(2) == "درجتين"
    assert content.deg_text(5) == "5 درجات"
    assert content.deg_text(15) == "15 درجة"


# ============================================================
# المسارات (عبر TestClient)
# ============================================================
def _client():
    from fastapi.testclient import TestClient
    import main
    # https: كوكي اللوحة يُرسَل بعلامة Secure فلا يعود إلا عبر اتصال https (كما في الإنتاج)
    return TestClient(main.app, raise_server_exceptions=False, base_url="https://testserver")


def test_routes_public_pages():
    c = _client()
    for path in ("/", "/map", "/accuracy", "/advertise", "/privacy",
                 "/sitemap.xml", "/robots.txt", "/ads.txt", "/manifest.webmanifest"):
        r = c.get(path)
        assert r.status_code == 200, f"{path} أعاد {r.status_code}"


def test_route_unknown_city_returns_arabic_html():
    """صفحة خطأ عربية منسّقة، لا JSON خام."""
    c = _client()
    r = c.get("/weather/nowhere", headers={"accept": "text/html"})
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "الصفحة غير موجودة" in r.text


def test_route_api_error_is_json():
    c = _client()
    r = c.get("/api/weather/nowhere")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_route_ads_serve_and_track():
    c = _client()
    r = c.get("/api/ads/serve?placement=home_top")
    assert r.status_code == 200
    ad = r.json()
    assert "placement" in ad
    assert ad["click_url"].startswith(("/api/ads/click?t=", "/advertise"))
    r2 = c.post("/api/ads/impression")  # قديم: لا يفعل شيئاً
    assert r2.status_code == 200


def test_route_ads_serve_bad_placement():
    c = _client()
    assert c.get("/api/ads/serve?placement=nope").status_code == 404


def test_route_lead_validation():
    c = _client()
    assert c.post("/api/ads/lead", json={"name": "أ", "email": "bad"}).status_code == 422
    ok = c.post("/api/ads/lead", json={"name": "عميل", "email": "a@b.com"})
    assert ok.status_code == 200 and ok.json()["ok"] is True


def test_route_push_subscribe_rejects_ssrf():
    c = _client()
    r = c.post("/api/push/subscribe", json={
        "subscription": {"endpoint": "http://127.0.0.1:9999/evil",
                         "keys": {"p256dh": "B" * 40, "auth": "a" * 20}},
        "city": "amman"})
    assert r.status_code == 400, "يجب رفض عنوان داخلي"


def test_route_push_test_malformed_keys_no_500():
    """اختبار الإشعارات بمفاتيح تالفة لا يعيد 500."""
    c = _client()
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/route-test",
           "keys": {"p256dh": "B" * 40, "auth": "a" * 20}}
    r = c.post("/api/push/subscribe", json={"subscription": sub, "city": "amman"})
    assert r.status_code == 200
    owner = r.json()["owner"]
    r2 = c.post("/api/push/test", json={"endpoint": sub["endpoint"], "owner": owner})
    assert r2.status_code == 200, f"أعاد {r2.status_code} بدل 200"
    assert r2.json()["ok"] is False


def test_route_admin_requires_auth():
    c = _client()
    assert c.get("/admin/dashboard").status_code == 401
    # جسم صالح تماماً: يجب أن يُرفض بالصلاحيات (401) لا بالتحقق (422)
    r = c.post("/admin/campaigns", json={"name": "حملة", "placement": "home_top"})
    assert r.status_code == 401, f"أعاد {r.status_code} بدل 401"
    assert c.get("/admin/report.csv").status_code == 401


def test_route_admin_login_and_dashboard():
    c = _client()
    assert c.post("/admin/login", json={"password": "wrong"}).status_code == 401
    r = c.post("/admin/login", json={"password": "test-token-1234567890abc"})
    assert r.status_code == 200
    d = c.get("/admin/dashboard")
    assert d.status_code == 200 and "لوحة الإعلانات" in d.text


def test_route_admin_campaign_validation():
    c = _client()
    c.post("/admin/login", json={"password": "test-token-1234567890abc"})
    bad = c.post("/admin/campaigns", json={"name": "حملة", "placement": "nope"})
    assert bad.status_code == 422
    good = c.post("/admin/campaigns", json={
        "name": "حملة صحيحة", "placement": "home_top", "pricing": "cpm", "rate": 4.5})
    assert good.status_code == 200


def test_route_body_size_limit():
    """الطلب الأكبر من الحد يُرفض بـ413."""
    c = _client()
    big = "x" * (config.MAX_BODY_BYTES + 1000)
    r = c.post("/api/ads/lead", json={"name": big, "email": "a@b.com"})
    assert r.status_code in (413, 422)


def test_route_ads_txt_served():
    c = _client()
    r = c.get("/ads.txt")
    assert r.status_code == 200 and "ads.txt" in r.text


# ============================================================
# مشغّل بلا pytest
# ============================================================
def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  ✓ {name}")
        except Exception as exc:
            failed.append((name, exc))
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} اختبار ناجح")
    if failed:
        print("فشل:")
        for n, e in failed:
            print(f"  - {n}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_run_all())
