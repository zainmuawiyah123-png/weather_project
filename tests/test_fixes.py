"""
اختبارات الإصلاحات - طقس بلاد الشام

كل اختبار هنا يثبت إصلاحاً محدداً، ومصمَّم ليفشل لو عاد الخلل القديم.
التشغيل:  python -m pytest tests/ -v      أو:  python tests/test_fixes.py
"""
import contextlib
import os
import socket
import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lw-fix-")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("ADMIN_TOKEN", "test-token-1234567890abc")
os.environ.setdefault("SITE_URL", "https://test.example")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import accuracy  # noqa: E402
import ads  # noqa: E402
import config  # noqa: E402
import security  # noqa: E402

ADMIN = config.ADMIN_TOKEN


# ---------------- أدوات ----------------
@contextlib.contextmanager
def fresh_ads_db():
    """قاعدة إعلانات معزولة لكل اختبار حتى لا تتداخل الحملات."""
    old = (ads.DB_PATH, ads._initialized)
    ads.DB_PATH = Path(tempfile.mkdtemp(prefix="lw-ads-")) / "ads.db"
    ads._initialized = False
    try:
        yield
    finally:
        ads.DB_PATH, ads._initialized = old


@contextlib.contextmanager
def patched(obj, **attrs):
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


def client(ip="203.0.113.1"):
    """عميل اختبار يتصرف كأن وسيطاً أضاف عنوان العميل الحقيقي في نهاية X-Forwarded-For."""
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app, raise_server_exceptions=False, base_url="https://testserver",
                      headers={"X-Forwarded-For": ip})


def make_campaign(placement, target="https://shop.example/offer", pricing="cpm", rate=5.0,
                  start="2020-01-01", end="2099-01-01"):
    cid = ads.create_campaign({"name": "اختبار", "placement": placement, "pricing": pricing,
                               "rate": rate, "start_date": start, "end_date": end, "target_url": target})
    crid = ads.add_creative(cid, {"kind": "text", "title": "عنوان", "body": "وصف", "cta": "اضغط",
                                  "target_url": target})
    return cid, crid


def count(table, cid):
    with ads._db() as db:
        return db.execute(f"SELECT COUNT(*) FROM {table} WHERE campaign_id=?", (cid,)).fetchone()[0]


class FakeRequest:
    def __init__(self, xff=None, peer="10.1.1.1", ua="UA"):
        self.headers = {k: v for k, v in (("x-forwarded-for", xff), ("user-agent", ua)) if v is not None}
        self.client = type("C", (), {"host": peer})()


# ============================================================
# 1) حملات تجريبية وهمية لا تظهر للزوار
# ============================================================
def test_demo_seed_is_off_by_default_and_purgeable():
    assert config.SEED_DEMO is False
    with fresh_ads_db():
        ads.seed_demo()
        assert len(ads.list_campaigns()) == 3
        assert ads.pick("alert_sponsor")["sponsored"] is True      # كان يظهر للزوار
        assert ads.purge_demo() == 3
        assert ads.list_campaigns() == [] and ads.list_advertisers() == []
        assert ads.pick("alert_sponsor")["sponsored"] is False     # عاد إعلان البيت
        assert ads.purge_demo() == 0


def test_purge_demo_never_touches_real_advertisers():
    with fresh_ads_db():
        ads.add_advertiser("معلن حقيقي", "Real Co", "real@shop.example")
        make_campaign("home_top")
        assert ads.purge_demo() == 0
        assert len(ads.list_campaigns()) == 1


def test_startup_purges_leftover_demo_and_does_not_reseed():
    from fastapi.testclient import TestClient
    import main
    with fresh_ads_db():
        ads.seed_demo()                       # كما لو أنشأته نسخة سابقة
        assert ads.list_campaigns()
        with TestClient(main.app):            # يشغّل lifespan
            assert ads.list_campaigns() == [], "التشغيل يجب أن ينظّف الحملات الوهمية"
        with TestClient(main.app):
            assert ads.list_campaigns() == [], "ولا يعيد إنشاءها"


# ============================================================
# 2) إعادة التوجيه المفتوحة
# ============================================================
def test_click_cannot_redirect_to_arbitrary_urls():
    c = client("198.51.100.10")
    for q in ("/api/ads/click?u=https://evil.example/login",
              "/api/ads/click?c=1&cr=1&p=home_top&u=https://evil.example/",
              "/api/ads/click?t=garbage", "/api/ads/click?t=", "/api/ads/click"):
        r = c.get(q, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/", q


def test_click_follows_database_target_through_signed_token():
    with fresh_ads_db():
        make_campaign("home_top", target="https://shop.example/offer")
        c = client("198.51.100.11")
        ad = c.get("/api/ads/serve?placement=home_top").json()
        assert "target_url" not in ad, "وجهة الإعلان لا تُكشف للمتصفح"
        assert ad["click_url"].startswith("/api/ads/click?t=")
        r = c.get(ad["click_url"], follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "https://shop.example/offer"
        # رمز معدَّل: يرجع للرئيسية ولا يُحتسب
        bad = c.get(ad["click_url"][:-4] + "0000", follow_redirects=False)
        assert bad.headers["location"] == "/"


def test_expired_or_forged_token_is_rejected():
    with fresh_ads_db():
        cid, crid = make_campaign("home_top")
        with patched(config, AD_TOKEN_HOURS=-1):
            expired = security.sign_ad_token(cid, crid, "home_top")
        assert security.verify_ad_token(expired) is None
        good = security.sign_ad_token(cid, crid, "home_top")
        assert security.verify_ad_token(good) == (cid, crid, "home_top")
        # تبديل رقم الحملة داخل الرمز يُبطل التوقيع
        import base64
        b64, _, sig = good.partition(".")
        payload = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode().split("|")
        payload[0] = str(cid + 1)
        forged = base64.urlsafe_b64encode("|".join(payload).encode()).decode().rstrip("=") + "." + sig
        assert security.verify_ad_token(forged) is None


def test_target_urls_are_validated():
    bad = ["javascript:alert(1)", "http://insecure.example", "//evil.example", "https://u:p@x.example",
           "data:text/html,x", 'https://x.example/"><script>']
    for u in bad:
        try:
            ads.create_campaign({"name": "س", "placement": "home_top", "target_url": u})
        except ValueError:
            continue
        raise AssertionError(f"قُبل رابط غير آمن: {u}")
    assert security.validate_http_url("/advertise") == "/advertise"
    assert security.validate_http_url("https://shop.example/a?b=1")
    c = client("198.51.100.12")
    r = c.post("/admin/login", json={"password": ADMIN})
    assert r.status_code == 200
    r = c.post("/admin/campaigns", json={"name": "حملة", "placement": "home_top",
                                          "target_url": "javascript:alert(1)"})
    assert r.status_code == 422
    r = c.post("/admin/settings", json={"adsense_client": "x'><script>"})
    assert r.status_code == 422


# ============================================================
# 3) أرقام الإعلانات لا تُزوَّر بسهولة
# ============================================================
def test_impressions_counted_only_on_server_when_ad_is_served():
    with fresh_ads_db():
        cid, crid = make_campaign("home_top")
        c = client("198.51.100.20")
        for _ in range(5):
            c.post("/api/ads/impression", json={"campaign_id": cid, "creative_id": crid,
                                                 "placement": "home_top"})
        assert count("impressions", cid) == 0, "طلب العميل المباشر لا يُحتسب"
        c.get("/api/ads/serve?placement=home_top")
        assert count("impressions", cid) == 1


def test_tracking_rejects_nonexistent_or_mismatched_ids():
    with fresh_ads_db():
        cid, crid = make_campaign("home_top")
        other, other_cr = make_campaign("home_mid")
        assert ads.record_impression(99999, 1, "home_top") is False
        assert ads.record_impression(cid, 77777, "home_top") is False
        assert ads.record_impression(cid, other_cr, "home_top") is False, "إبداع يتبع حملة أخرى"
        assert ads.record_click(99999, 1, "home_top") is False
        assert ads.record_impression(cid, crid, "not_a_placement") is False
        assert ads.record_impression(cid, crid, "home_top") is True


def test_per_visitor_daily_caps():
    with fresh_ads_db():
        cid, crid = make_campaign("home_top")
        ok = sum(ads.record_impression(cid, crid, "home_top", "/", vhash="aaaa") for _ in range(50))
        assert ok == config.AD_IMPRESSION_CAP
        ok = sum(ads.record_click(cid, crid, "home_top", "/", vhash="aaaa") for _ in range(10))
        assert ok == config.AD_CLICK_CAP
        # زائر آخر له حدّه الخاص
        assert ads.record_impression(cid, crid, "home_top", "/", vhash="bbbb") is True


def test_serving_many_times_from_one_visitor_is_capped_through_the_route():
    with fresh_ads_db():
        cid, _ = make_campaign("home_top")
        c = client("198.51.100.21")
        for _ in range(45):
            assert c.get("/api/ads/serve?placement=home_top").status_code == 200
        assert count("impressions", cid) == config.AD_IMPRESSION_CAP


def test_house_ad_is_not_counted_and_links_to_advertise_page():
    with fresh_ads_db():
        c = client("198.51.100.22")
        ad = c.get("/api/ads/serve?placement=home_bottom").json()
        assert ad["kind"] == "house" and ad["click_url"] == "/advertise"
        with ads._db() as db:
            assert db.execute("SELECT COUNT(*) FROM impressions").fetchone()[0] == 0


# ============================================================
# 4) عنوان العميل وتحديد المعدل
# ============================================================
def test_client_ip_ignores_spoofable_left_side_of_xff():
    req = FakeRequest(xff="1.2.3.4, 5.6.7.8, 9.9.9.9")
    with patched(config, TRUSTED_PROXY_HOPS=1):
        assert security.client_ip(req) == "9.9.9.9"
    with patched(config, TRUSTED_PROXY_HOPS=2):
        assert security.client_ip(req) == "5.6.7.8"
    with patched(config, TRUSTED_PROXY_HOPS=0):
        assert security.client_ip(req) == "10.1.1.1"
    with patched(config, TRUSTED_PROXY_HOPS=1):
        assert security.client_ip(FakeRequest(xff=None, peer="10.1.1.1")) == "10.1.1.1"
        assert security.client_ip(FakeRequest(xff="", peer="10.1.1.1")) == "10.1.1.1"


def test_rate_limit_is_not_bypassed_by_spoofing_xff():
    c = client("192.0.2.77")
    codes = []
    for i in range(config.RATE_LIMITS["lead"][0] + 4):
        # المهاجم يبدّل الجزء الأيسر فقط؛ الوسيط يضيف عنوانه الحقيقي في النهاية
        r = c.post("/api/ads/lead", json={"name": "اختبار", "email": "a@b.co"},
                   headers={"X-Forwarded-For": f"10.0.{i}.1, 192.0.2.77"})
        codes.append(r.status_code)
    assert codes.count(200) == config.RATE_LIMITS["lead"][0], codes
    assert 429 in codes


def test_rate_limit_memory_is_garbage_collected():
    from collections import deque
    old = time.time() - 10 * 3600
    for i in range(300):
        security._hits[f"lead:stale-{i}"] = deque([old])
    before = len(security._hits)
    assert before >= 300
    security._calls = security._GC_EVERY - 1   # الاستدعاء التالي يشغّل التنظيف
    security.rate_limit("lead", "gc-probe-ip")
    assert len(security._hits) < before - 250, "العناوين القديمة يجب أن تُحذف"


def test_admin_login_attempts_are_limited():
    c = client("192.0.2.88")
    codes = [c.post("/admin/login", json={"password": f"wrong-{i}"}).status_code
             for i in range(config.RATE_LIMITS["admin_login"][0] + 3)]
    assert codes[0] == 401 and 429 in codes
    # حتى الرمز الصحيح يُحجب مؤقتاً بعد التجاوز
    assert c.post("/admin/login", json={"password": ADMIN}).status_code == 429


# ============================================================
# 5) ترويسات الأمان وكوكي الإدارة
# ============================================================
def test_security_headers_on_pages_errors_and_api():
    c = client("192.0.2.91")
    for path in ("/", "/map", "/api/weather/nowhere", "/nope-page"):
        h = c.get(path).headers
        assert "default-src 'self'" in h["content-security-policy"], path
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert "unsafe-eval" not in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "strict-transport-security" in h
    assert c.get("/admin").headers["cache-control"] == "no-store"


def test_admin_cookie_flags_and_no_store():
    c = client("192.0.2.92")
    r = c.post("/admin/login", json={"password": ADMIN})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert c.get("/admin/dashboard").headers["cache-control"] == "no-store"


def test_admin_dashboard_renders_with_report_columns():
    with fresh_ads_db():
        make_campaign("home_top")
        c = client("192.0.2.93")
        c.post("/admin/login", json={"password": ADMIN})
        r = c.get("/admin/dashboard")
        assert r.status_code == 200
        assert "إيراد آخر 30 يوماً" in r.text and "ظهور (إجمالي)" in r.text


# ============================================================
# 6) مصادقة اللوحة بأحرف غير ASCII + رمز ضعيف
# ============================================================
def test_non_ascii_credentials_do_not_crash():
    c = client("192.0.2.94")
    r = c.post("/admin/login", json={"password": "كلمة مرور عربية ✓"})
    assert r.status_code == 401
    assert security.check_password("é") is False
    assert security.check_session("é.é") is False
    assert security.check_session("123.كلمة") is False
    assert security.check_session("abc") is False


def test_weak_admin_token_disables_the_panel():
    with patched(config, ADMIN_TOKEN="short-token"):
        assert security.admin_token() == ""
        assert security.check_password("short-token") is False
        assert security.check_session("9999999999.deadbeef") is False
        r = client("192.0.2.95").post("/admin/login", json={"password": "short-token"})
        assert r.status_code == 401


# ============================================================
# 7) فحص نقطة الإشعارات
# ============================================================
def test_push_allowlist_is_not_a_wildcard_for_all_googleapis():
    for bad in ("https://storage.googleapis.com/some-bucket/x", "https://anything.googleapis.com/x",
                "https://evil.example/fcm.googleapis.com", "https://fcm.googleapis.com.evil.example/x"):
        try:
            security.validate_push_endpoint(bad)
        except ValueError:
            continue
        raise AssertionError(f"قُبل نطاق غير مسموح: {bad}")
    assert security.validate_push_endpoint("https://fcm.googleapis.com/fcm/send/abc")


def test_push_validation_is_independent_of_dns_availability():
    real = socket.getaddrinfo

    def down(*a, **k):
        raise socket.gaierror("no dns")

    def private(*a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    try:
        socket.getaddrinfo = down
        assert security.validate_push_endpoint("https://fcm.googleapis.com/fcm/send/abc")
        socket.getaddrinfo = private
        try:
            security.validate_push_endpoint("https://fcm.googleapis.com/fcm/send/abc")
            raise AssertionError("يجب رفض مضيف يشير إلى عنوان داخلي")
        except ValueError:
            pass
    finally:
        socket.getaddrinfo = real


# ============================================================
# 8) الإيراد والتقارير
# ============================================================
def _utc_today():
    return datetime.now(timezone.utc).date()


def test_flat_revenue_is_prorated_not_booked_upfront():
    with fresh_ads_db():
        t = _utc_today()
        future, _ = make_campaign("home_top", pricing="flat", rate=300,
                                  start=(t + timedelta(days=1)).isoformat(),
                                  end=(t + timedelta(days=30)).isoformat())
        running, _ = make_campaign("home_mid", pricing="flat", rate=300,
                                   start=(t - timedelta(days=9)).isoformat(),
                                   end=(t + timedelta(days=20)).isoformat())   # 30 يوماً، مضى 10
        ended, _ = make_campaign("city_top", pricing="flat", rate=300,
                                 start=(t - timedelta(days=39)).isoformat(),
                                 end=(t - timedelta(days=10)).isoformat())    # 30 يوماً كلها مضت
        assert ads.campaign_revenue(ads.get_campaign(future)) == 0.0
        assert ads.campaign_revenue(ads.get_campaign(running)) == 100.0
        assert ads.campaign_revenue(ads.get_campaign(ended)) == 300.0
        # نافذة 30 يوماً: الحملة المنتهية بدأت قبل 39 يوماً فيُحتسب منها 21 يوماً فقط
        assert ads.campaign_revenue(ads.get_campaign(ended), days=30) == 210.0


def test_report_totals_cover_only_the_requested_window():
    with fresh_ads_db():
        cid, crid = make_campaign("home_top", pricing="cpm", rate=10)
        for _ in range(4):
            ads.record_impression(cid, crid, "home_top", "/")
        old_day = (_utc_today() - timedelta(days=60)).isoformat()
        with ads._db() as db:
            for _ in range(100):
                db.execute("INSERT INTO impressions (campaign_id,creative_id,placement,ts,day,page) "
                           "VALUES (?,?,?,?,?,?)", (cid, crid, "home_top", "x", old_day, "/"))
        rep = ads.report(30)
        assert rep["totals"]["impressions"] == 4
        camp = rep["campaigns"][0]
        assert camp["impressions"] == 104, "جدول الحملات إجمالي منذ البداية"
        assert camp["revenue_window"] == 0.04 and camp["revenue"] == 1.04


def test_fresh_database_reports_zero_revenue_after_startup():
    """قاعدة جديدة + تشغيل التطبيق = لا إيراد ولا حملات (كانت الحملات الوهمية تُنشأ فتُحتسب 320 د.أ)."""
    from fastapi.testclient import TestClient
    import main
    with fresh_ads_db():
        with TestClient(main.app):
            rep = ads.report(30)
            assert rep["campaigns"] == []
            assert rep["totals"]["revenue_jod"] == 0


# ============================================================
# 9) موافقة الإعلانات
# ============================================================
def test_ads_config_endpoint_reflects_setting_and_js_uses_it():
    c = client("192.0.2.96")
    assert c.get("/api/ads/config").json() == {"before_consent": False}
    with patched(config, ADS_BEFORE_CONSENT=True):
        assert c.get("/api/ads/config").json() == {"before_consent": True}
    js = (Path(__file__).resolve().parent.parent / "static" / "ads.js").read_text(encoding="utf-8")
    assert "/api/ads/config" in js
    assert "ADS_BEFORE_CONSENT = false" not in js, "المتغير كان ثابتاً ولا يقرأ الإعداد"
    assert "/api/ads/impression" not in js, "العميل لم يعد يبلّغ بالظهور"


def test_privacy_text_matches_consent_setting():
    import pages
    assert "لا نُحمّل أي إعلان قبل موافقتك" in pages.render_privacy("https://x.example")
    with patched(config, ADS_BEFORE_CONSENT=True):
        txt = pages.render_privacy("https://x.example")
        assert "لا نُحمّل أي إعلان قبل موافقتك" not in txt
        assert "تظهر قبل اختيارك" in txt


def test_sponsor_in_city_page_does_not_record_an_impression():
    """رسم صفحة المدينة على الخادم يختار الراعي فقط؛ لا يُسجَّل ظهور قبل أن يطلب متصفح موافق الإعلان."""
    import pages
    with fresh_ads_db():
        cid, _ = make_campaign("alert_sponsor", pricing="flat", rate=100)
        ad = ads.pick("alert_sponsor")
        assert ad["campaign_id"] == cid
        assert count("impressions", cid) == 0
        assert pages.ads.pick is ads.pick


# ============================================================
# 10) الدقة: محطات مطابقة، توقعات فعلية فقط، بلا متوسط إقليمي
# ============================================================
def _cities():
    import main
    return {s: {**c} for s, c in main.CITIES.items()}


def _tz(slug):
    import main
    return accuracy._tz(slug, main.CITIES[slug]["country"])


def insert_obs_day(db, icao, tz, day, tmax, rain=0, tmin_off=6):
    for h in range(24):
        ts = int(datetime(day.year, day.month, day.day, h, tzinfo=tz).timestamp())
        temp = tmax if h == 14 else tmax - tmin_off
        db.execute("INSERT OR IGNORE INTO obs VALUES (?,?,?,?)",
                   (icao, ts, temp, 1 if (rain and h == 10) else 0))


def insert_live(db, city, target, lead, fmax, fmin=0.0, precip=0.0, source="live"):
    issued = (target - timedelta(days=lead)).isoformat()
    db.execute("INSERT OR IGNORE INTO forecast_log "
               "(city,issued_date,target_date,max_t,min_t,precip,source) VALUES (?,?,?,?,?,?,?)",
               (city, issued, target.isoformat(), fmax, fmin, precip, source))


def new_acc_db():
    path = Path(tempfile.mkdtemp(prefix="lw-acc-")) / "acc.db"
    accuracy.init_db(path)
    return path


def test_station_matching_rejects_far_or_different_elevation_stations():
    cities = _cities()
    matched = {s: [c["icao"] for c in accuracy.candidates_for(ci)] for s, ci in cities.items()}
    # هذه المدن كانت تُقارن بمحطات بعيدة أو على ارتفاع مختلف كثيراً، فصارت غير متاحة
    for slug in ("tafilah", "maan", "deadsea", "jerusalem", "ramallah", "hebron", "gaza", "nablus",
                 "karak", "irbid", "zahle", "homs", "daraa", "ajloun", "zarqa", "jerash"):
        assert matched[slug] == [], f"{slug} طابقت {matched[slug]}"
    expected = {"amman": "OJAM", "salt": "OJAM", "madaba": "OJAI", "aqaba": "OJAQ",
                "beirut": "OLBA", "damascus": "OSDI", "aleppo": "OSAP", "latakia": "OSLK"}
    for slug, icao in expected.items():
        assert matched[slug] and matched[slug][0] == icao, (slug, matched[slug])
    # كل محطة مطابقة ضمن الحدود المعلنة
    for slug, ci in cities.items():
        for cand in accuracy.candidates_for(ci):
            assert cand["km"] <= config.ACC_MAX_KM and abs(cand["elev_diff"]) <= config.ACC_MAX_ELEV


def test_cities_without_matching_station_get_no_numbers_and_no_regional_pooling():
    path = new_acc_db()
    cities = _cities()
    tz = _tz("amman")
    today = datetime.now(tz).date()
    with sqlite3.connect(path) as db:
        for k in range(1, 8):
            d = today - timedelta(days=k)
            insert_obs_day(db, "OJAM", tz, d, 20)
            insert_live(db, "amman", d, 1, 21)
            insert_live(db, "tafilah", d, 1, 40)     # مدينة بلا محطة: يجب ألا تحصل على أي رقم
            insert_live(db, "ramallah", d, 1, 40)
    st = accuracy.stats(path, cities, 30)
    for slug in ("tafilah", "ramallah", "gaza", "maan"):
        c = st["cities"][slug]
        assert c["basis"] == "unavailable" and c["reason"] == "no_station"
        assert c["station"] is None and c["leads"]["1"]["n"] == 0
    assert not any(c["basis"] == "regional" for c in st["cities"].values())
    amman = st["cities"]["amman"]
    assert amman["basis"] == "station" and amman["station"] == "OJAM"
    assert amman["leads"]["1"]["n"] == 7 and amman["leads"]["1"]["mae_max"] == 1.0
    # المجموع العام يُحسب من أمان فقط، لا من توقعات المدن بلا محطة
    assert st["overall"]["leads"]["1"]["n"] == 7 and st["overall"]["leads"]["1"]["mae_max"] == 1.0
    assert st["overall"]["stations"] == 1


def test_nearest_matching_station_wins_over_the_one_with_more_observations():
    path = new_acc_db()
    cities = _cities()
    tz = _tz("madaba")
    today = datetime.now(tz).date()
    with sqlite3.connect(path) as db:
        for k in range(1, 4):          # OJAI الأقرب لمأدبا: 3 أيام
            insert_obs_day(db, "OJAI", tz, today - timedelta(days=k), 20)
        for k in range(1, 9):          # OJAM أبعد: 8 أيام (أكثر قراءات)
            insert_obs_day(db, "OJAM", tz, today - timedelta(days=k), 20)
    st = accuracy.stats(path, cities, 30)
    assert st["cities"]["madaba"]["station"] == "OJAI"
    assert st["cities"]["madaba"]["obs_days"] == 3


def test_only_live_forecasts_count_not_archive_or_legacy_rows():
    path = new_acc_db()
    cities = _cities()
    tz = _tz("amman")
    today = datetime.now(tz).date()
    with sqlite3.connect(path) as db:
        for k in range(1, 6):
            d = today - timedelta(days=k)
            insert_obs_day(db, "OJAM", tz, d, 20)
            insert_live(db, "amman", d, 1, 22, source="live")          # خطأ +2 (حقيقي)
            insert_live(db, "amman", d, 2, 30, source=None)            # قديم بلا مصدر: يُهمل
            db.execute("INSERT OR IGNORE INTO forecast_archive VALUES (?,?,?,?,?,?)",
                       ("amman", (d - timedelta(days=3)).isoformat(), d.isoformat(), 20.0, 10.0, 0.0))
    amman = accuracy.stats(path, cities, 30)["cities"]["amman"]
    assert amman["leads"]["1"]["n"] == 5 and amman["leads"]["1"]["mae_max"] == 2.0
    assert amman["leads"]["2"]["n"] == 0, "صفوف بلا مصدر لا تدخل"
    assert amman["leads"]["3"]["n"] == 0, "الأرشيف لا يدخل"


def test_backfill_writes_to_archive_only():
    path = new_acc_db()
    cities = {"amman": _cities()["amman"]}

    def fake_fetch(lat, lon, start, end):
        days = [(date.fromisoformat(start) + timedelta(days=i)).isoformat() for i in range(3)]
        d = {"time": days}
        for m in ("ecmwf_ifs025", "gfs_seamless", "icon_seamless"):
            d[f"temperature_2m_max_{m}"] = [20.0] * 3
            d[f"temperature_2m_min_{m}"] = [10.0] * 3
            d[f"precipitation_sum_{m}"] = [0.0] * 3
        return {"daily": d}

    res = accuracy.backfill_forecasts(path, cities, days=3, fetch=fake_fetch)
    assert res["written"] == 3
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM forecast_log").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM forecast_archive").fetchone()[0] == 3


def test_main_logs_forecasts_as_live_and_old_tables_are_migrated():
    import main
    with patched(main, DB_PATH=Path(tempfile.mkdtemp()) / "f.db"):
        main.init_db()
        main.log_forecast("amman", "2026-11-07", [{"date": "2026-11-08", "max": 20, "min": 10, "precip": 0}])
        with sqlite3.connect(main.DB_PATH) as db:
            assert db.execute("SELECT source FROM forecast_log").fetchone()[0] == "live"
    # قاعدة قديمة بلا عمود المصدر
    old = Path(tempfile.mkdtemp()) / "old.db"
    with sqlite3.connect(old) as db:
        db.execute("CREATE TABLE forecast_log (city TEXT, issued_date TEXT, target_date TEXT, "
                   "max_t REAL, min_t REAL, precip REAL, PRIMARY KEY (city, issued_date, target_date))")
        db.execute("INSERT INTO forecast_log VALUES ('amman','2026-11-01','2026-11-02',20,10,0)")
    accuracy.init_db(old)
    with sqlite3.connect(old) as db:
        assert db.execute("SELECT source FROM forecast_log").fetchone()[0] is None


def test_small_sample_flag_and_readiness():
    acc = accuracy._Acc()
    assert acc.summary() == {"n": 0, "ready": False}
    for _ in range(2):
        acc.add(1, 1, False, False)
    s = acc.summary()
    assert s["ready"] is False and s["small_sample"] is True
    for _ in range(3):
        acc.add(1, 1, False, False)
    assert acc.summary()["ready"] is True and acc.summary()["small_sample"] is True
    for _ in range(config.ACC_SMALL_SAMPLE):
        acc.add(1, 1, False, False)
    assert acc.summary()["small_sample"] is False


def test_accuracy_endpoint_exposes_basis_and_thresholds():
    r = client("192.0.2.97").get("/api/accuracy?days=30")
    assert r.status_code == 200
    j = r.json()
    assert j["thresholds"]["max_km"] == config.ACC_MAX_KM
    assert j["cities"]["tafilah"]["basis"] == "unavailable"
    assert all(c["basis"] in ("station", "unavailable") for c in j["cities"].values())


# ============================================================
# مشغّل بلا pytest
# ============================================================
def _run_all():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ✓ {name}")
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} اختبار ناجح")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
