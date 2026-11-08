"""
طقس بلاد الشام - الخادم
Levant Weather - server.

المسارات:
  /                      الصفحة الرئيسية
  /map                   خريطة الطقس والتنبيهات
  /weather/{city}        صفحة المدينة (تُرسم على الخادم للـSEO)
  /accuracy              لوحة الدقة
  /advertise             أعلن معنا (عربي/إنجليزي)
  /privacy               سياسة الخصوصية
  /sitemap.xml, /robots.txt, /ads.txt
  /api/cities            قائمة المدن
  /api/weather/{city}    توقع المدينة + التنبيهات
  /api/overview          حالة كل المدن (للخريطة)
  /api/grid              شبكة توقعات فوق بلاد الشام (طبقات الخريطة)
  /api/accuracy          دقة التوقعات مقابل قراءات محطات الرصد
  /api/push/*            الإشعارات
  /api/ads/*             الإعلانات (تقديم، ظهور، نقرة، طلب)
  /admin/*               لوحة الإعلانات (محمية برمز)

مهم: واجهة Open-Meteo المجانية للاستخدام غير التجاري فقط.
قبل الإطلاق التجاري ضع مفتاح الخطة التجارية في OPEN_METEO_API_KEY.
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import accuracy
import ads
import alerts as alert_engine
import config
import content
import pages
import push
import security

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("levant-weather")

BASE_DIR = config.BASE_DIR
DB_PATH = config.FORECAST_DB
CACHE_TTL = config.CACHE_TTL
GRID_TTL = config.GRID_TTL
PUSH_EVERY = config.PUSH_EVERY
ACCURACY_EVERY = config.ACCURACY_EVERY

API_KEY = config.OPEN_METEO_API_KEY
API_URL = config.API_URL

MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]
FULL_HOURLY = (
    "temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m,snowfall,cape,"
    "cloud_cover,relative_humidity_2m,apparent_temperature,uv_index"
)
MID_HOURLY = "temperature_2m,precipitation,wind_speed_10m,cloud_cover,relative_humidity_2m,apparent_temperature"
BASIC_HOURLY = "temperature_2m,precipitation,wind_speed_10m"
SITE_URL = config.SITE_URL


def _c(name, country, lat, lon, elev, dry=False):
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

GRID_LATS = [round(29.25 + 0.5 * i, 2) for i in range(17)]
GRID_LONS = [round(34.25 + 0.5 * j, 2) for j in range(14)]
GRID_VARS = {"t": "temperature_2m", "p": "precipitation", "g": "wind_gusts_10m", "s": "snowfall"}

_cache: dict[str, tuple[float, dict]] = {}
_grid_cache: dict = {"ts": 0.0, "data": None}
_grid_lock = asyncio.Lock()
_sem = asyncio.Semaphore(2)


# ---------- سجل التوقعات ----------
def init_db() -> None:
    import sqlite3

    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS forecast_log (
                city TEXT, issued_date TEXT, target_date TEXT,
                max_t REAL, min_t REAL, precip REAL,
                PRIMARY KEY (city, issued_date, target_date)
            )"""
        )


def log_forecast(city: str, issued_date: str, days: list[dict]) -> None:
    import sqlite3

    with sqlite3.connect(DB_PATH) as db:
        for d in days:
            db.execute(
                "INSERT OR IGNORE INTO forecast_log VALUES (?,?,?,?,?,?)",
                (city, issued_date, d["date"], d["max"], d["min"], d["precip"]),
            )


# ---------- معالجة البيانات ----------
def combine(block: dict, base: str, i: int, models=None):
    vals = []
    for m in (models or MODELS):
        series = block.get(f"{base}_{m}")
        if series and i < len(series) and series[i] is not None:
            vals.append(series[i])
    if not vals:
        return None, None
    return sum(vals) / len(vals), max(vals) - min(vals)


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
    models = raw.get("models", MODELS)
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
        hours.append({
            "time": times[i], "temp": _r(t), "precip": _r(p),
            "wind": None if w is None else round(w), "gust": None if g is None else round(g),
            "snow": _r(s), "cloud": None if cl is None else round(cl),
            "humidity": None if hu is None else round(hu), "feels": _r(fe), "uv": _r(uv),
        })

    days = []
    for i, date in enumerate(daily["time"]):
        mx, mx_spread = combine(daily, "temperature_2m_max", i, models)
        mn, mn_spread = combine(daily, "temperature_2m_min", i, models)
        pr, _ = combine(daily, "precipitation_sum", i, models)
        if mx is None or mn is None:
            continue
        spreads = [s for s in (mx_spread, mn_spread) if s is not None]
        days.append({
            "date": date, "max": round(mx, 1), "min": round(mn, 1),
            "precip": round(pr or 0, 1),
            "confidence": confidence(max(spreads) if spreads else None),
        })

    alerts = alert_engine.evaluate(hourly, times, start, models, dry=c["dry"])
    log_forecast(slug, local_now.strftime("%Y-%m-%d"), days)

    payload = {
        "city": {"slug": slug, "name": c["name"], "country": c["country"],
                 "lat": c["lat"], "lon": c["lon"], "elev": c["elev"]},
        "current": hours[0] if hours else None,
        "hours": hours, "days": days, "alerts": alerts,
        "level": max((a["level"] for a in alerts), default=0),
        "local_hour": local_now.hour, "models": models,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": raw.get("_source", "Open-Meteo (ECMWF + GFS + ICON)"),
    }
    payload["condition"] = content.condition(hours)
    payload["summary"] = content.build_summary(payload)
    payload["tips"] = content.build_tips(payload)
    return payload


# ---------- مزود احتياطي: WeatherAPI ----------
async def fetch_weatherapi(lat: float, lon: float) -> dict:
    if not config.WEATHERAPI_KEY:
        raise httpx.HTTPError("WeatherAPI key is not configured")
    url = "https://api.weatherapi.com/v1/forecast.json"
    params = {"key": config.WEATHERAPI_KEY, "q": f"{lat},{lon}", "days": 7,
              "aqi": "no", "alerts": "no"}
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    model = "weatherapi"
    hourly = {"time": []}
    fields = {
        "temperature_2m": "temp_c", "precipitation": "precip_mm",
        "wind_speed_10m": "wind_kph", "wind_gusts_10m": "gust_kph",
        "cloud_cover": "cloud", "relative_humidity_2m": "humidity",
        "apparent_temperature": "feelslike_c", "uv_index": "uv",
    }
    for target in fields:
        hourly[f"{target}_{model}"] = []
    hourly[f"snowfall_{model}"] = []
    daily = {"time": []}
    for target in ("temperature_2m_max", "temperature_2m_min", "precipitation_sum"):
        daily[f"{target}_{model}"] = []

    for day in data.get("forecast", {}).get("forecastday", []):
        daily["time"].append(day.get("date"))
        d = day.get("day", {})
        daily[f"temperature_2m_max_{model}"].append(d.get("maxtemp_c"))
        daily[f"temperature_2m_min_{model}"].append(d.get("mintemp_c"))
        daily[f"precipitation_sum_{model}"].append(d.get("totalprecip_mm"))
        for hour in day.get("hour", []):
            hourly["time"].append(hour.get("time", "").replace(" ", "T"))
            for target, source in fields.items():
                hourly[f"{target}_{model}"].append(hour.get(source))
            hourly[f"snowfall_{model}"].append(hour.get("snow_cm", 0))

    if not hourly["time"] or not daily["time"]:
        raise ValueError("WeatherAPI returned no forecast data")
    local_epoch = data.get("location", {}).get("localtime_epoch")
    offset = int(local_epoch - time.time()) if local_epoch else 0
    return {"hourly": hourly, "daily": daily, "utc_offset_seconds": offset,
            "models": [model], "_source": "WeatherAPI (مصدر احتياطي)"}


# ---------- الجلب من Open-Meteo ----------
async def fetch_forecast(lat: float, lon: float) -> dict:
    base = {
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "models": ",".join(MODELS), "timezone": "auto",
        "forecast_days": 7, "wind_speed_unit": "kmh",
    }
    if API_KEY:
        base["apikey"] = API_KEY
    async with httpx.AsyncClient(timeout=25) as client:
        for hourly in (FULL_HOURLY, MID_HOURLY, BASIC_HOURLY):
            for attempt in range(3):
                r = await client.get(API_URL, params={**base, "hourly": hourly})
                if r.status_code == 400 and hourly != BASIC_HOURLY:
                    break
                if r.status_code == 429 or r.status_code >= 500:
                    if attempt < 2:
                        retry_after = r.headers.get("Retry-After", "")
                        try:
                            delay = min(max(float(retry_after), 2.0), 10.0)
                        except (TypeError, ValueError):
                            delay = 2.0 * (attempt + 1)
                        await asyncio.sleep(delay)
                        continue
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
            try:
                raw = await fetch_forecast(c["lat"], c["lon"])
            except (httpx.HTTPError, KeyError, ValueError) as primary_exc:
                if not config.WEATHERAPI_KEY:
                    raise
                log.warning("Open-Meteo unavailable for %s; trying WeatherAPI: %s", slug, primary_exc)
                raw = await fetch_weatherapi(c["lat"], c["lon"])
        payload = build_payload(slug, raw)
    except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
        log.warning("forecast fetch failed for %s: %s", slug, exc)
        if hit:
            return {**hit[1], "stale": True}
        raise HTTPException(502, "تعذّر جلب بيانات الطقس الآن، حاول بعد قليل")

    _cache[slug] = (time.time(), payload)
    return payload


async def fetch_grid_chunk(lats: list, lons: list) -> list:
    params = {
        "latitude": ",".join(map(str, lats)), "longitude": ",".join(map(str, lons)),
        "hourly": ",".join(GRID_VARS.values()), "timezone": "UTC",
        "forecast_days": 2, "wind_speed_unit": "kmh",
    }
    if API_KEY:
        params["apikey"] = API_KEY
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(API_URL, params=params)
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else [data]


async def get_grid() -> dict:
    def fresh():
        return _grid_cache["data"] and time.time() - _grid_cache["ts"] < GRID_TTL

    if fresh():
        return _grid_cache["data"]
    async with _grid_lock:
        if fresh():
            return _grid_cache["data"]
        points = [(la, lo) for la in GRID_LATS for lo in GRID_LONS]
        chunks = [points[i:i + 60] for i in range(0, len(points), 60)]
        try:
            parts = await asyncio.gather(
                *(fetch_grid_chunk([p[0] for p in ch], [p[1] for p in ch]) for ch in chunks)
            )
            flat = [loc for part in parts for loc in part]
            if len(flat) != len(points):
                raise ValueError("grid size mismatch")
            times = flat[0]["hourly"]["time"]
            vars_ = {}
            for key, name in GRID_VARS.items():
                grid, i = [], 0
                for _ in GRID_LATS:
                    row = []
                    for _ in GRID_LONS:
                        series = flat[i]["hourly"][name]
                        i += 1
                        row.append([round(v, 1) if v is not None else 0.0 for v in series])
                    grid.append(row)
                vars_[key] = grid
        except (httpx.HTTPError, KeyError, ValueError, IndexError):
            if _grid_cache["data"]:
                return {**_grid_cache["data"], "stale": True}
            raise HTTPException(502, "تعذّر جلب بيانات الخريطة الآن")

        data = {
            "times": times, "lats": GRID_LATS, "lons": GRID_LONS, "vars": vars_,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "Open-Meteo",
        }
        _grid_cache.update(ts=time.time(), data=data)
        return data


# ---------- حلقات الخلفية ----------
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
        except Exception:
            log.exception("push cycle failed")
        await asyncio.sleep(PUSH_EVERY)


async def accuracy_cycle() -> None:
    # Forecasts are fetched on demand. Do not burst-request every city here:
    # Open-Meteo may rate-limit the shared public endpoint (HTTP 429).
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
    init_db()
    push.init_db()
    accuracy.init_db(DB_PATH)
    ads.init_db()
    ads.seed_demo()

    async def _backfill():
        try:
            info = {s: {**c} for s, c in CITIES.items()}
            res = await asyncio.to_thread(accuracy.backfill_forecasts, DB_PATH, info)
            log.info("forecast backfill: %s", res.get("written"))
        except Exception:
            log.exception("backfill failed")

    tasks = [
        asyncio.create_task(push_loop()),
        asyncio.create_task(accuracy_loop()),
        asyncio.create_task(_backfill()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="طقس بلاد الشام", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)


# ---------- وسطاء: حدود الحجم وتحديد المعدل ----------
@app.middleware("http")
async def guard(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > config.MAX_BODY_BYTES:
        return JSONResponse({"detail": "حجم الطلب أكبر من الحد المسموح"}, status_code=413)

    path = request.url.path
    bucket = None
    if path.startswith("/api/weather"):
        bucket = "weather"
    elif path.startswith("/api/overview"):
        bucket = "overview"
    elif path.startswith("/api/grid"):
        bucket = "grid"
    elif path.startswith("/api/push"):
        bucket = "push"
    elif path.startswith("/api/ads/lead"):
        bucket = "lead"
    elif path.startswith("/api/ads"):
        bucket = "ads"
    elif path.startswith("/admin"):
        bucket = "admin"
    if bucket and not security.rate_limit(bucket, security.client_ip(request)):
        return JSONResponse({"detail": "طلبات كثيرة جداً، حاول بعد قليل"}, status_code=429)

    return await call_next(request)


# ---------- معالجات الأخطاء: صفحة عربية منسّقة بدل JSON خام ----------
def _wants_html(request: Request) -> bool:
    path = request.url.path
    if path.startswith("/api/") or path in ("/ads.txt", "/robots.txt", "/sitemap.xml"):
        return False
    accept = request.headers.get("accept", "")
    return ("text/html" in accept or path in ("/", "/map", "/accuracy", "/advertise", "/privacy")
            or path.startswith("/weather/") or path.startswith("/admin"))


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    if _wants_html(request):
        return HTMLResponse(pages.render_error(exc.status_code, str(exc.detail)), status_code=exc.status_code)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    if _wants_html(request):
        return HTMLResponse(pages.render_error(500, "حدث خطأ غير متوقع، حاول لاحقاً"), status_code=500)
    return JSONResponse({"detail": "حدث خطأ غير متوقع"}, status_code=500)


# ---------- المسارات ----------
@app.get("/api/cities")
def cities():
    return [{"slug": s, "name": c["name"], "country": c["country"],
             "lat": c["lat"], "lon": c["lon"], "elev": c["elev"]} for s, c in CITIES.items()]


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
        out.append({
            "slug": slug, "name": c["name"], "country": c["country"],
            "lat": c["lat"], "lon": c["lon"], "elev": c["elev"],
            "temp": r["current"]["temp"] if r["current"] else None,
            "condition": r.get("condition"),
            "cloud": r["current"].get("cloud") if r["current"] else None,
            "precip": r["current"].get("precip") if r["current"] else None,
            "wind": r["current"].get("wind") if r["current"] else None,
            "local_hour": r.get("local_hour"),
            "level": r["level"], "alerts": r["alerts"][:3],
        })
    if not out:
        raise HTTPException(502, "تعذّر جلب حالة المدن الآن")
    return out


@app.get("/api/grid")
async def grid():
    return await get_grid()


# ---------- الإشعارات ----------
class SubscribeBody(BaseModel):
    subscription: dict
    city: str
    min_level: int = Field(default=1, ge=1, le=3)
    types: list[str] = Field(default_factory=list)

    @field_validator("types")
    @classmethod
    def _cap_types(cls, v):
        if len(v) > config.MAX_TYPES:
            raise ValueError("عدد الأنواع أكبر من الحد")
        for t in v:
            if not isinstance(t, str) or len(t) > config.MAX_TYPES_LEN:
                raise ValueError("نوع تنبيه غير صالح")
        return v


class EndpointBody(BaseModel):
    endpoint: str = Field(max_length=config.MAX_ENDPOINT_LEN)
    owner: str | None = Field(default=None, max_length=128)


@app.get("/api/push/key")
def push_key():
    return {"publicKey": push.public_key_b64()}


@app.post("/api/push/subscribe")
def push_subscribe(body: SubscribeBody):
    if body.city not in CITIES:
        raise HTTPException(404, "المدينة غير موجودة")
    try:
        sub = security.validate_subscription(body.subscription)
    except ValueError as e:
        raise HTTPException(400, str(e))
    owner = push.save_sub(sub, body.city, body.min_level, body.types)
    return {"ok": True, "owner": owner}


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: EndpointBody):
    try:
        endpoint = security.validate_push_endpoint(body.endpoint)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not push.remove_sub(endpoint, body.owner):
        raise HTTPException(403, "لا تملك صلاحية إلغاء هذا الاشتراك")
    return {"ok": True}


@app.post("/api/push/test")
async def push_test(body: EndpointBody):
    try:
        endpoint = security.validate_push_endpoint(body.endpoint)
    except ValueError as e:
        raise HTTPException(400, str(e))
    sub = push.get_sub(endpoint, body.owner)
    if not sub:
        raise HTTPException(404, "الاشتراك غير موجود أو لا تملك صلاحيته")
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


# ---------- الإعلانات ----------
@app.get("/api/ads/serve")
def ads_serve(placement: str, page: str = ""):
    ad = ads.serve(placement, page)
    if not ad:
        raise HTTPException(404, "مكان إعلاني غير معروف")
    return ad


class ImpressionBody(BaseModel):
    campaign_id: int = Field(ge=0)
    creative_id: int = Field(ge=0)
    placement: str = Field(max_length=40)
    page: str = Field(default="", max_length=200)


@app.post("/api/ads/impression")
def ads_impression(body: ImpressionBody):
    if body.placement not in ads.PLACEMENTS:
        raise HTTPException(400, "مكان إعلاني غير معروف")
    ads.record_impression(body.campaign_id, body.creative_id, body.placement, body.page)
    return {"ok": True}


@app.get("/api/ads/click")
def ads_click(c: int = 0, cr: int = 0, p: str = "", u: str = "/"):
    if p in ads.PLACEMENTS:
        ads.record_click(c, cr, p, u)
    if u.startswith("/") and not u.startswith("//"):
        return RedirectResponse(u, status_code=302)
    if u.startswith("https://"):
        return RedirectResponse(u, status_code=302)
    return RedirectResponse("/", status_code=302)


class LeadBody(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=160)
    company: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=40)
    message: str = Field(default="", max_length=2000)
    budget: str = Field(default="", max_length=60)

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("بريد إلكتروني غير صالح")
        return v


@app.post("/api/ads/lead")
def ads_lead(body: LeadBody):
    lid = ads.add_lead(body.model_dump())
    return {"ok": True, "id": lid}


# ---------- الصفحات ----------
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


@app.get("/advertise", response_class=HTMLResponse)
def advertise_page(request: Request):
    return HTMLResponse(pages.render_advertise(_base(request)))


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return HTMLResponse(pages.render_privacy(_base(request)))


@app.get("/sitemap.xml")
def sitemap(request: Request):
    return Response(pages.render_sitemap(_base(request), CITIES), media_type="application/xml")


@app.get("/robots.txt")
def robots(request: Request):
    return Response(pages.render_robots(_base(request)), media_type="text/plain")


@app.get("/ads.txt")
def ads_txt():
    return PlainTextResponse(ads.ads_txt(), media_type="text/plain")


@app.get("/map")
def map_page():
    return FileResponse(BASE_DIR / "static" / "map.html")


@app.get("/accuracy")
def accuracy_page():
    return FileResponse(BASE_DIR / "static" / "accuracy.html")


@app.get("/sw.js")
def service_worker():
    return FileResponse(BASE_DIR / "static" / "sw.js", media_type="application/javascript")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(BASE_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")


# ---------- لوحة الإعلانات ----------
def _admin_ok(request: Request) -> bool:
    return security.check_session(request.cookies.get(config.ADMIN_COOKIE))


def _require_admin(request: Request):
    if not _admin_ok(request):
        raise HTTPException(401, "يلزم تسجيل الدخول إلى اللوحة")


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    if not _admin_ok(request):
        return HTMLResponse(pages.render_admin_login())
    return RedirectResponse("/admin/dashboard", status_code=302)


class LoginBody(BaseModel):
    password: str = Field(max_length=200)


@app.post("/admin/login")
def admin_login(body: LoginBody):
    if not security.check_password(body.password):
        raise HTTPException(401, "كلمة المرور غير صحيحة")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        config.ADMIN_COOKIE, security.make_session(),
        httponly=True, samesite="lax", max_age=config.ADMIN_SESSION_HOURS * 3600,
    )
    return resp


@app.post("/admin/logout")
def admin_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(config.ADMIN_COOKIE)
    return resp


@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    _require_admin(request)
    return HTMLResponse(pages.render_admin_dashboard(ads.report(30), ads.list_campaigns(),
                                                     ads.list_advertisers(), ads.list_leads()))


@app.get("/admin/report.csv")
def admin_report_csv(request: Request, days: int = 30):
    _require_admin(request)
    days = max(1, min(days, 365))
    return Response(
        ads.report_csv(days), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="ads-report-{days}d.csv"'},
    )


class CampaignBody(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    placement: str
    advertiser_id: int | None = None
    format: str = "image"
    start_date: str | None = None
    end_date: str | None = None
    status: str = "active"
    priority: int = Field(default=5, ge=1, le=10)
    pricing: str = "cpm"
    rate: float = Field(default=0, ge=0)
    budget: float = Field(default=0, ge=0)
    daily_cap: int = Field(default=0, ge=0)
    target_url: str = Field(default="", max_length=500)

    @field_validator("placement")
    @classmethod
    def _placement(cls, v):
        if v not in ads.PLACEMENTS:
            raise ValueError("مكان إعلاني غير معروف")
        return v

    @field_validator("pricing")
    @classmethod
    def _pricing(cls, v):
        if v not in ("cpm", "cpc", "flat"):
            raise ValueError("نموذج تسعير غير معروف")
        return v


@app.post("/admin/campaigns")
def admin_create_campaign(request: Request, body: CampaignBody):
    _require_admin(request)
    cid = ads.create_campaign(body.model_dump())
    return {"ok": True, "id": cid}


@app.post("/admin/campaigns/{cid}/status")
def admin_campaign_status(request: Request, cid: int, status: str):
    _require_admin(request)
    if status not in ("active", "paused", "ended"):
        raise HTTPException(400, "حالة غير معروفة")
    ads.update_campaign(cid, {"status": status})
    return {"ok": True}


@app.post("/admin/campaigns/{cid}/delete")
def admin_campaign_delete(request: Request, cid: int):
    _require_admin(request)
    ads.delete_campaign(cid)
    return {"ok": True}


class CreativeBody(BaseModel):
    kind: str = "image"
    image_url: str = Field(default="", max_length=500)
    width: int = 0
    height: int = 0
    title: str = Field(default="", max_length=160)
    body: str = Field(default="", max_length=500)
    cta: str = Field(default="", max_length=60)
    target_url: str = Field(default="", max_length=500)


@app.post("/admin/campaigns/{cid}/creatives")
def admin_add_creative(request: Request, cid: int, body: CreativeBody):
    _require_admin(request)
    if not ads.get_campaign(cid):
        raise HTTPException(404, "الحملة غير موجودة")
    kid = ads.add_creative(cid, body.model_dump())
    return {"ok": True, "id": kid}


class AdvertiserBody(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    company: str = Field(default="", max_length=160)
    email: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=40)
    notes: str = Field(default="", max_length=1000)


@app.post("/admin/advertisers")
def admin_add_advertiser(request: Request, body: AdvertiserBody):
    _require_admin(request)
    aid = ads.add_advertiser(body.name, body.company, body.email, body.phone, body.notes)
    return {"ok": True, "id": aid}


class SettingsBody(BaseModel):
    adsense_client: str = Field(default="", max_length=80)
    ads_txt_extra: str = Field(default="", max_length=2000)


@app.post("/admin/settings")
def admin_settings(request: Request, body: SettingsBody):
    _require_admin(request)
    ads.set_setting("adsense_client", body.adsense_client.strip())
    ads.set_setting("ads_txt_extra", body.ads_txt_extra.strip())
    return {"ok": True}


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
