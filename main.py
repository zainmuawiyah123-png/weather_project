"""
طقس بلاد الشام - الخادم (النسخة الثانية: تنبيهات + خريطة + إشعارات)

المسارات:
  /                      الصفحة الرئيسية
  /map                   خريطة الطقس والتنبيهات
  /weather/{city}        صفحة المدينة (تُرسم على الخادم للـSEO)
  /sitemap.xml, /robots.txt
  /api/cities            قائمة المدن
  /api/weather/{city}    توقع المدينة + التنبيهات
  /api/overview          حالة كل المدن (للخريطة)
  طبقات الخريطة تُحمّل مباشرة من WeatherAPI Weather Maps
  /api/push/*            الإشعارات
  /api/accuracy          دقة التوقعات مقابل قراءات محطات الرصد
  /accuracy              لوحة الدقة

مهم: واجهة Open-Meteo المجانية للاستخدام غير التجاري فقط.
قبل الإطلاق التجاري ضع مفتاح الخطة التجارية في OPEN_METEO_API_KEY.
"""
import asyncio
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import accuracy
import alerts as alert_engine
import content
import pages
import push
import weatherapi as weather_api
import ads

log = logging.getLogger("levant-weather")

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "forecast_log.db"
CACHE_TTL = 30 * 60      # كاش المدينة: 30 دقيقة
PUSH_EVERY = 30 * 60     # فحص التنبيهات للمشتركين كل 30 دقيقة
ACCURACY_EVERY = 6 * 3600  # جلب قراءات المحطات وتسجيل توقعات كل المدن كل 6 ساعات

API_KEY = os.getenv("OPEN_METEO_API_KEY", "").strip()
WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY", "").strip()
API_URL = (
    "https://customer-api.open-meteo.com/v1/forecast"
    if API_KEY
    else "https://api.open-meteo.com/v1/forecast"
)

MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]
FULL_HOURLY = (
    "temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m,snowfall,cape,"
    "cloud_cover,relative_humidity_2m,apparent_temperature,uv_index"
)
MID_HOURLY = "temperature_2m,precipitation,wind_speed_10m,cloud_cover,relative_humidity_2m,apparent_temperature"
BASIC_HOURLY = "temperature_2m,precipitation,wind_speed_10m"
SITE_URL = os.getenv("SITE_URL", "").strip().rstrip("/")  # مثال: https://levant-weather.example.com


def _c(name, country, lat, lon, elev, dry=False):
    # elev: الارتفاع التقريبي بالمتر (للعرض)، dry: منطقة جافة تُخفَّض فيها عتبات السيول
    return {"name": name, "country": country, "lat": lat, "lon": lon, "elev": elev, "dry": dry}


CITIES = {
    "amman": _c("عمّان", "الأردن", 31.9539, 35.9106, 900),
    "irbid": _c("إربد", "الأردن", 32.5556, 35.8500, 620),
    "zarqa": _c("الزرقاء", "الأردن", 32.0728, 36.0880, 600),
    "ajloun": _c("عجلون", "الأردن", 32.3326, 35.7519, 780),
    "jerash": _c("جرش", "الأردن", 32.2747, 35.8961, 600),
    "salt": _c("السلط", "الأردن", 32.0392, 35.7272, 800),
    "madaba": _c("مأدبا", "الأردن", 31.7160, 35.7939, 780),
    "karak": _c("الكرك", "الأردن", 31.1853, 35.7048, 930),
    "tafilah": _c("الطفيلة", "الأردن", 30.8375, 35.6042, 1000),
    "maan": _c("معان", "الأردن", 30.1962, 35.7341, 1070, dry=True),
    "aqaba": _c("العقبة", "الأردن", 29.5321, 35.0063, 5, dry=True),
    "deadsea": _c("البحر الميت", "الأردن", 31.5590, 35.4732, -400, dry=True),
    "jerusalem": _c("القدس", "فلسطين", 31.7683, 35.2137, 780),
    "ramallah": _c("رام الله", "فلسطين", 31.9038, 35.2034, 880),
    "nablus": _c("نابلس", "فلسطين", 32.2211, 35.2544, 550),
    "hebron": _c("الخليل", "فلسطين", 31.5326, 35.0998, 930),
    "gaza": _c("غزة", "فلسطين", 31.5017, 34.4668, 30),
    "beirut": _c("بيروت", "لبنان", 33.8938, 35.5018, 20),
    "tripoli": _c("طرابلس", "لبنان", 34.4367, 35.8497, 10),
    "zahle": _c("زحلة", "لبنان", 33.8463, 35.9020, 950),
    "damascus": _c("دمشق", "سوريا", 33.5138, 36.2765, 700),
    "aleppo": _c("حلب", "سوريا", 36.2021, 37.1343, 380),
    "homs": _c("حمص", "سوريا", 34.7324, 36.7137, 500),
    "latakia": _c("اللاذقية", "سوريا", 35.5317, 35.7900, 10),
    "daraa": _c("درعا", "سوريا", 32.6189, 36.1021, 530),
}

# شبكة الخريطة: كل 0.5 درجة فوق بلاد الشام والمناطق المحيطة
GRID_LATS = [round(29.25 + 0.5 * i, 2) for i in range(17)]   # 29.25 .. 37.25
GRID_LONS = [round(34.25 + 0.5 * j, 2) for j in range(14)]   # 34.25 .. 40.75

_cache: dict[str, tuple[float, dict]] = {}
_sem = asyncio.Semaphore(6)  # حد للطلبات المتزامنة نحو Open-Meteo


# ---------- سجل التوقعات (أساس نسبة الدقة لاحقاً) ----------
def init_db() -> None:
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS forecast_log (
                city TEXT, issued_date TEXT, target_date TEXT,
                max_t REAL, min_t REAL, precip REAL,
                PRIMARY KEY (city, issued_date, target_date)
            )"""
        )


def log_forecast(city: str, issued_date: str, days: list[dict]) -> None:
    with sqlite3.connect(DB_PATH) as db:
        for d in days:
            db.execute(
                "INSERT OR IGNORE INTO forecast_log VALUES (?,?,?,?,?,?)",
                (city, issued_date, d["date"], d["max"], d["min"], d["precip"]),
            )


init_db()
push.init_db()
accuracy.init_db(DB_PATH)


# ---------- معالجة البيانات ----------
def combine(block: dict, base: str, i: int, models=None):
    """يجمع قيم المتغير من النماذج المتاحة ويعيد (المتوسط، الفارق)."""
    vals = []
    for m in (models or MODELS):
        series = block.get(f"{base}_{m}")
        if series and i < len(series) and series[i] is not None:
            vals.append(series[i])
    if not vals:
        return None, None
    # لا يمكن قياس التوافق أو الثقة من مصدر واحد فقط.
    spread = max(vals) - min(vals) if len(vals) >= 2 else None
    return sum(vals) / len(vals), spread


def confidence(spread):
    if spread is None:
        return "غير متوفر"
    if spread <= 2:
        return "عالية"
    if spread <= 4:
        return "متوسطة"
    return "منخفضة"


def _r(v, nd=1):
    return None if v is None else round(v, nd)


def build_payload(slug: str, raw: dict) -> dict:
    c = CITIES[slug]
    hourly, daily = raw["hourly"], raw["daily"]
    models = raw.get("_models", MODELS)
    offset = timedelta(seconds=raw.get("utc_offset_seconds", 0))
    local_now = datetime.now(timezone.utc).replace(tzinfo=None) + offset
    now_key = local_now.strftime("%Y-%m-%dT%H:00")
    times = hourly["time"]
    start = times.index(now_key) if now_key in times else 0

    hours = []
    for i in range(start, min(start + 24, len(times))):
        t, _ = combine(hourly, "temperature_2m", i, models)
        p, _ = combine(hourly, "precipitation", i, models)
        w, _ = combine(hourly, "wind_speed_10m", i, models)
        g, _ = combine(hourly, "wind_gusts_10m", i, models)
        s, _ = combine(hourly, "snowfall", i, models)
        cl, _ = combine(hourly, "cloud_cover", i, models)
        hu, _ = combine(hourly, "relative_humidity_2m", i, models)
        fe, _ = combine(hourly, "apparent_temperature", i, models)
        uv, _ = combine(hourly, "uv_index", i, models)
        hours.append(
            {
                "time": times[i],
                "temp": _r(t),
                "precip": _r(p),
                "wind": None if w is None else round(w),
                "gust": None if g is None else round(g),
                "snow": _r(s),
                "cloud": None if cl is None else round(cl),
                "humidity": None if hu is None else round(hu),
                "feels": _r(fe),
                "uv": _r(uv),
            }
        )

    days = []
    for i, date in enumerate(daily["time"]):
        mx, mx_spread = combine(daily, "temperature_2m_max", i, models)
        mn, mn_spread = combine(daily, "temperature_2m_min", i, models)
        pr, _ = combine(daily, "precipitation_sum", i, models)
        if mx is None or mn is None:
            continue
        spreads = [s for s in (mx_spread, mn_spread) if s is not None]
        days.append(
            {
                "date": date,
                "max": round(mx, 1),
                "min": round(mn, 1),
                "precip": round(pr or 0, 1),
                "confidence": confidence(max(spreads) if spreads else None),
            }
        )

    alerts = alert_engine.evaluate(hourly, times, start, models, dry=c["dry"])
    log_forecast(slug, local_now.strftime("%Y-%m-%d"), days)

    payload = {
        "city": {"slug": slug, "name": c["name"], "country": c["country"],
                 "lat": c["lat"], "lon": c["lon"], "elev": c["elev"]},
        "current": hours[0] if hours else None,
        "hours": hours,
        "days": days,
        "alerts": alerts,
        "level": max((a["level"] for a in alerts), default=0),
        "local_hour": local_now.hour,
        "models": models,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": raw.get("_source", "Open-Meteo (ECMWF + GFS + ICON)"),
    }
    payload["condition"] = content.condition(hours)
    payload["summary"] = content.build_summary(payload)
    payload["tips"] = content.build_tips(payload)
    return payload


# ---------- الجلب من Open-Meteo ----------
async def fetch_forecast(lat: float, lon: float) -> dict:
    if WEATHERAPI_KEY:
        return await weather_api.fetch_forecast(lat, lon, WEATHERAPI_KEY)
    base = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "models": ",".join(MODELS),
        "timezone": "auto",
        "forecast_days": 7,
        "wind_speed_unit": "kmh",
    }
    if API_KEY:
        base["apikey"] = API_KEY
    async with httpx.AsyncClient(timeout=20) as client:
        tiers = (FULL_HOURLY, MID_HOURLY, BASIC_HOURLY)
        for hourly in tiers:
            r = await client.get(API_URL, params={**base, "hourly": hourly})
            if r.status_code == 400 and hourly != BASIC_HOURLY:
                continue  # متغير غير مدعوم؛ نجرّب مجموعة أصغر
            r.raise_for_status()
            return r.json()
    raise httpx.HTTPError("no response")


async def get_weather(slug: str) -> dict:
    if slug not in CITIES:
        raise HTTPException(404, "المدينة غير موجودة")
    hit = _cache.get(slug)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    c = CITIES[slug]
    try:
        async with _sem:
            raw = await fetch_forecast(c["lat"], c["lon"])
        payload = build_payload(slug, raw)
    except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
        log.exception("Open-Meteo forecast failed for city %s: %s", slug, exc)
        if hit:  # نعيد آخر نسخة محفوظة بدل أن نترك المستخدم بلا بيانات
            return {**hit[1], "stale": True}
        raise HTTPException(502, "تعذّر جلب بيانات الطقس الآن، حاول بعد قليل")

    _cache[slug] = (time.time(), payload)
    return payload




# ---------- حلقة الإشعارات ----------
async def push_cycle() -> None:
    for slug in push.subscribed_cities():
        if slug not in CITIES:
            continue
        try:
            w = await get_weather(slug)
        except HTTPException:
            continue
        await asyncio.to_thread(push.notify_city, slug, w)


async def push_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await push_cycle()
        except Exception:  # لا نسمح لخطأ واحد بإيقاف الحلقة
            log.exception("push cycle failed")
        await asyncio.sleep(PUSH_EVERY)


async def accuracy_cycle() -> None:
    # 1) نضمن تسجيل توقعات كل المدن حتى لو لم يزرها أحد (التسجيل يتم عند الجلب)
    await asyncio.gather(*(get_weather(s) for s in CITIES), return_exceptions=True)
    # 2) نجمع قراءات المحطات الحقيقية
    try:
        info = await asyncio.to_thread(accuracy.collect, DB_PATH)
        log.info("observations collected: %s", info)
    except Exception:
        log.exception("observation collection failed")


async def accuracy_loop() -> None:
    await asyncio.sleep(90)
    while True:
        try:
            await accuracy_cycle()
        except Exception:
            log.exception("accuracy cycle failed")
        await asyncio.sleep(ACCURACY_EVERY)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(push_loop()), asyncio.create_task(accuracy_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="طقس بلاد الشام", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.include_router(ads.router)


# ---------- المسارات ----------
@app.get("/api/cities")
def cities():
    return [
        {"slug": s, "name": c["name"], "country": c["country"],
         "lat": c["lat"], "lon": c["lon"], "elev": c["elev"]}
        for s, c in CITIES.items()
    ]


@app.get("/api/weather/{slug}")
async def weather(slug: str):
    return await get_weather(slug)


@app.get("/api/overview")
async def overview():
    slugs = list(CITIES)
    results = await asyncio.gather(*(get_weather(s) for s in slugs), return_exceptions=True)
    out = []
    for slug, r in zip(slugs, results):
        if isinstance(r, Exception):
            continue
        c = CITIES[slug]
        out.append(
            {
                "slug": slug, "name": c["name"], "country": c["country"],
                "lat": c["lat"], "lon": c["lon"], "elev": c["elev"],
                "temp": r["current"]["temp"] if r["current"] else None,
                "level": r["level"],
                "alerts": r["alerts"][:3],
            }
        )
    return out


class SubscribeBody(BaseModel):
    subscription: dict
    city: str
    min_level: int = Field(default=1, ge=1, le=3)
    types: list[str] = []


class EndpointBody(BaseModel):
    endpoint: str


@app.get("/api/push/key")
def push_key():
    return {"publicKey": push.public_key_b64()}


@app.post("/api/push/subscribe")
def push_subscribe(body: SubscribeBody):
    if body.city not in CITIES:
        raise HTTPException(404, "المدينة غير موجودة")
    if "endpoint" not in body.subscription:
        raise HTTPException(400, "اشتراك غير صالح")
    push.save_sub(body.subscription, body.city, body.min_level, body.types)
    return {"ok": True}


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: EndpointBody):
    push.remove_sub(body.endpoint)
    return {"ok": True}


@app.post("/api/push/test")
async def push_test(body: EndpointBody):
    sub = push.get_sub(body.endpoint)
    if not sub:
        raise HTTPException(404, "الاشتراك غير موجود")
    ok = await asyncio.to_thread(
        push.send_push, sub,
        {"title": "طقس بلاد الشام", "body": "الإشعارات تعمل على هذا الجهاز", "url": "/", "tag": "test"},
    )
    return {"ok": ok is True}


@app.get("/api/accuracy")
async def accuracy_stats(days: int = 30):
    days = max(7, min(days, 90))
    info = {s: {"name": c["name"], "country": c["country"]} for s, c in CITIES.items()}
    return await asyncio.to_thread(accuracy.stats, DB_PATH, info, days)


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


def _base(request: Request) -> str:
    return SITE_URL or str(request.base_url).rstrip("/")


@app.get("/weather/{slug}", response_class=HTMLResponse)
async def city_page(slug: str, request: Request):
    if slug not in CITIES:
        raise HTTPException(404, "المدينة غير موجودة")
    w = await get_weather(slug)
    html_ = pages.render_city_page(w, _base(request), CITIES)
    return HTMLResponse(html_, headers={"Cache-Control": "public, max-age=600"})


@app.get("/sitemap.xml")
def sitemap(request: Request):
    return Response(pages.render_sitemap(_base(request), CITIES), media_type="application/xml")


@app.get("/robots.txt")
def robots(request: Request):
    return Response(pages.render_robots(_base(request)), media_type="text/plain")


@app.get("/map")
def map_page():
    return FileResponse(BASE_DIR / "static" / "map.html")


@app.get("/advertise")
def advertise_page():
    return FileResponse(BASE_DIR / "static" / "advertise.html")


@app.get("/accuracy")
def accuracy_page():
    return FileResponse(BASE_DIR / "static" / "accuracy.html")


@app.get("/sw.js")
def service_worker():
    # يجب أن يُخدَّم من الجذر ليغطي الموقع كله
    return FileResponse(BASE_DIR / "static" / "sw.js", media_type="application/javascript")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(BASE_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
