"""
قياس الدقة - طقس بلاد الشام

المبدأ: لا نعرض رقم دقة إلا إن كان يقيس التوقع فعلاً. لذلك:

1. القراءات الحقيقية: تقارير METAR لمطارات (Ogimet أساساً، وaviationweather.gov احتياطياً).
2. التوقعات المقارَنة: ما أصدره التطبيق فعلاً فقط (جدول forecast_log، source='live').
   الأرشيف التاريخي (backfill) يُحفظ في جدول منفصل forecast_archive ولا يدخل في الأرقام المنشورة،
   لأنه ليس توقعاً صدر للمستخدمين، وحسب علمنا يعكس توقعاً قصير الأمد جداً فيبدو أدق مما وصل لهم.
3. مطابقة المحطة: لا تُقارن مدينة بمحطة إلا إن كانت ضمن ACC_MAX_KM كم وفرق الارتفاع ضمن ACC_MAX_ELEV م.
   الأقرب أولاً. وإلا تظهر المدينة "غير متاحة" بدل رقم مضلِّل يعكس فرق المكان لا خطأ التوقع
   (مثلاً مدينة على 1000 م مقابل مطار على مستوى البحر: فرق حرارة نحو 6 درجات).
4. لا يوجد "متوسط إقليمي" يُنسب إلى مدينة لا محطة لها.

حدود يجب أن تعرفها:
- إحداثيات المحطات وارتفاعاتها في STATION_INFO تقريبية؛ تحقق منها بـ --discover قبل الاعتماد.
- المحطات مطارات، وقد تختلف حرارتها عن قلب المدينة (العمران والمنخفضات المحلية).
- METAR لا يعطي كمية المطر؛ نعرف فقط إن حدث هطول أم لا، وقد يفوته هطول قصير بين قراءتين.
- لا يظهر أي رقم قبل ACC_MIN_DAYS أيام مقارنة، ويُوسم "عينة صغيرة" حتى ACC_SMALL_SAMPLE يوماً.

أدوات سطر الأوامر:
  python accuracy.py --check      أي محطات ترد فعلاً (Ogimet)
  python accuracy.py --match      أي مدينة تطابق أي محطة (المسافة وفرق الارتفاع)
  python accuracy.py --discover   محطات METAR القريبة من كل مدينة (لتحديث STATION_INFO)
  python accuracy.py --collect    جلب القراءات الآن
  python accuracy.py --backfill   تعبئة جدول الأرشيف الداخلي (لا يظهر في اللوحة)
  python accuracy.py --stats      طباعة الإحصاءات الحالية
"""
import json
import logging
import math
import re
import sqlite3
import sys
import time as _time
from datetime import date, datetime, time as dtime, timedelta, timezone

import httpx

import config

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

log = logging.getLogger("levant-weather.accuracy")

METAR_URL = "https://aviationweather.gov/api/data/metar"
OGIMET_URL = "https://www.ogimet.com/cgi-bin/getmetar"
HEADERS = {"User-Agent": "levant-weather/1.0 (+https://github.com/levant-weather)"}

# ---------- المحطات ----------
# رمز ICAO: (خط العرض، خط الطول، الارتفاع بالمتر، الاسم). قيم تقريبية؛ تحقق منها بـ --discover.
# أضف محطة هنا فقط بعد أن تتأكد أن Ogimet يُرجع قراءاتها (--check). المدن المطابقة تُحدَّد تلقائياً بالمسافة والارتفاع.
STATION_INFO = {
    "OJAM": (31.9727, 35.9916, 779, "عمّان - مطار عمّان المدني"),
    "OJAI": (31.7226, 35.9932, 722, "عمّان - مطار الملكة علياء"),
    "OJAQ": (29.6116, 35.0181, 51, "العقبة - مطار الملك حسين"),
    "OLBA": (33.8209, 35.4884, 27, "بيروت - مطار رفيق الحريري"),
    "OSDI": (33.4115, 36.5156, 616, "دمشق الدولي"),
    "OSAP": (36.1807, 37.2244, 389, "حلب الدولي"),
    "OSLK": (35.4011, 35.9487, 48, "اللاذقية - مطار باسل الأسد"),
}


def candidates_for(city: dict) -> list[dict]:
    """
    محطات تصلح لمقارنة هذه المدينة، الأقرب أولاً: ضمن ACC_MAX_KM كم، وفرق الارتفاع ضمن ACC_MAX_ELEV م.
    city: {"lat":..., "lon":..., "elev":...}
    """
    out = []
    for icao, (la, lo, el, name) in STATION_INFO.items():
        km = _km(city["lat"], city["lon"], la, lo)
        diff = (city.get("elev") or 0) - el
        if km <= config.ACC_MAX_KM and abs(diff) <= config.ACC_MAX_ELEV:
            out.append({"icao": icao, "name": name, "km": round(km), "elev_diff": round(diff)})
    return sorted(out, key=lambda x: x["km"])


RAIN_MM = 1.0        # توقع "يوم ماطر" عندما يبلغ مجموع التوقع هذا الحد
MIN_HOURS = 16       # أقل عدد ساعات مختلفة من اليوم نحتاجه لنعتبر اليوم مكتمل القراءات
MIN_OBS = 10
LEADS = (1, 2, 3)    # قبل كم يوم صدر التوقع
KEEP_DAYS = 120
BACKFILL_DAYS = 30   # كم يوماً نعبّئها في جدول الأرشيف الداخلي


# ---------- المنطقة الزمنية ----------
def _tz(slug: str, country: str):
    if slug == "gaza":
        name = "Asia/Gaza"
    elif country == "الأردن":
        name = "Asia/Amman"
    elif country == "سوريا":
        name = "Asia/Damascus"
    elif country == "لبنان":
        name = "Asia/Beirut"
    else:
        name = "Asia/Hebron"
    try:
        return ZoneInfo(name)
    except Exception:
        log.warning("tzdata غير متوفرة: pip install tzdata")
        hours = 3 if country in ("الأردن", "سوريا") else 2
        return timezone(timedelta(hours=hours))


# ---------- تحليل METAR ----------
_TEMP_RE = re.compile(r"\s(M?\d{2})/(?:M?\d{2}|//)(?=\s|$)")
_ICAO_RE = re.compile(r"\b([A-Z]{4})\s+\d{6}Z")
_WX_RE = re.compile(r"^(?:[-+])?(?:MI|BC|PR|DR|BL|SH|TS|FZ)?(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP)+$")


def _num_temp(token: str):
    return -int(token[1:]) if token.startswith("M") else int(token)


def has_precip(text: str) -> bool:
    """هل في نص METAR هطول (مطر، رذاذ، ثلج، برد) أو رعد في المحطة نفسها؟"""
    body = text.split(" RMK")[0]
    for tok in body.split():
        if tok.startswith("VC"):  # في الجوار وليس في المحطة
            continue
        if tok.lstrip("+-") == "TS" or _WX_RE.match(tok):
            return True
    return False


def parse_metar(rec: dict):
    """يعيد (icao, ts_epoch, temp, rain) أو None إن تعذّر التحليل."""
    raw = rec.get("rawOb") or rec.get("raw_text") or ""
    icao = rec.get("icaoId") or rec.get("icao")
    if not icao:
        m = _ICAO_RE.search(raw)
        icao = m.group(1) if m else None
    if not icao:
        return None

    ts = rec.get("obsTime")
    if isinstance(ts, str) and ts.isdigit():
        ts = int(ts)
    if not isinstance(ts, (int, float)):
        rt = rec.get("reportTime") or rec.get("observation_time")
        try:
            ts = int(datetime.fromisoformat(str(rt).replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            return None

    temp = rec.get("temp")
    if not isinstance(temp, (int, float)):
        m = _TEMP_RE.search(raw)
        temp = _num_temp(m.group(1)) if m else None

    wx = f"{rec.get('wxString') or ''} {raw}"
    return icao, int(ts), temp, 1 if has_precip(wx) else 0


def parse_ogimet_line(line: str):
    """
    يحلّل سطر Ogimet بالشكل:
      OJAM,2026,11,01,00,00,METAR OJAM 010000Z 21003KT 5000 HZ NSC 16/10 Q1021 NOSIG=
    ويعيد (icao, ts_epoch, temp, rain) أو None.
    """
    parts = line.split(",", 6)
    if len(parts) < 7:
        return None
    icao = parts[0].strip().upper()
    if not re.fullmatch(r"[A-Z]{4}", icao):
        return None
    try:
        y, mo, d, h, mi = (int(parts[i]) for i in range(1, 6))
        dt = datetime(y, mo, d, h, mi, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None
    body = parts[6]
    if "NIL" in body:
        return None
    m = _TEMP_RE.search(body)
    temp = _num_temp(m.group(1)) if m else None
    return icao, int(dt.timestamp()), temp, 1 if has_precip(body) else 0


# ---------- قاعدة البيانات ----------
def init_db(db_path) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS obs (
                icao TEXT, ts INTEGER, temp REAL, rain INTEGER,
                PRIMARY KEY (icao, ts))"""
        )
        # جدول سجل التوقعات (يُنشئه main أيضاً؛ نضمنه هنا حتى تعمل الإحصاءات وحدها)
        db.execute(
            """CREATE TABLE IF NOT EXISTS forecast_log (
                city TEXT, issued_date TEXT, target_date TEXT,
                max_t REAL, min_t REAL, precip REAL, source TEXT,
                PRIMARY KEY (city, issued_date, target_date))"""
        )
        cols = [r[1] for r in db.execute("PRAGMA table_info(forecast_log)")]
        if "source" not in cols:  # قاعدة قديمة: صفوفها بلا مصدر ولا تدخل في الدقة المنشورة
            db.execute("ALTER TABLE forecast_log ADD COLUMN source TEXT")
        # أرشيف تاريخي داخلي: ليس توقعاً صدر للمستخدمين، ولا يدخل في الأرقام المنشورة
        db.execute(
            """CREATE TABLE IF NOT EXISTS forecast_archive (
                city TEXT, issued_date TEXT, target_date TEXT,
                max_t REAL, min_t REAL, precip REAL,
                PRIMARY KEY (city, issued_date, target_date))"""
        )


# ---------- الجلب ----------
def _fetch(ids: list[str], hours: int) -> list:
    """قراءات من aviationweather (احتياطي؛ لا يغطي كل المحطات)."""
    with httpx.Client(timeout=30, headers=HEADERS) as client:
        r = client.get(METAR_URL, params={"ids": ",".join(ids), "format": "json", "hours": hours})
        if r.status_code == 204 or not r.content.strip():
            return []
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else []


def fetch_ogimet(ids: list[str], hours: int = 48) -> list:
    """
    قراءات METAR حقيقية من Ogimet. هذا هو المصدر الأساسي لأنه يغطي
    المحطات الأردنية والسورية واللبنانية التي لا يوفرها aviationweather.
    """
    end = datetime.now(timezone.utc)
    begin = end - timedelta(hours=hours)
    fmt = "%Y%m%d%H%M"
    rows = []
    with httpx.Client(timeout=45, headers=HEADERS, follow_redirects=True) as client:
        for icao in ids:
            try:
                r = client.get(
                    OGIMET_URL,
                    params={"icao": icao, "begin": begin.strftime(fmt), "end": end.strftime(fmt)},
                )
                if r.status_code != 200 or not r.content.strip():
                    continue
                for line in r.text.splitlines():
                    if "METAR" not in line and "SPECI" not in line:
                        continue
                    p = parse_ogimet_line(line)
                    if p:
                        rows.append(p)
            except (httpx.HTTPError, ValueError) as e:
                log.warning("Ogimet %s: %s", icao, e)
    return rows


def collect(db_path, hours: int = 48, fetch=None) -> dict:
    """يجلب قراءات كل المحطات المرشّحة ويخزّنها. يعيد ملخصاً."""
    init_db(db_path)
    ids = sorted(STATION_INFO)
    rows = []
    if fetch is not None:
        recs = fetch(ids, hours)
        wanted = set(ids)
        for rec in recs:
            p = parse_metar(rec)
            if p and p[0] in wanted:
                rows.append(p)
    else:
        # المصدر الأساسي: Ogimet (يغطي كل المحطات المتاحة فعلاً)
        rows = fetch_ogimet(ids, hours)
        # احتياطي: aviationweather لأي محطة لم تصل من Ogimet
        got = {r[0] for r in rows}
        missing = [i for i in ids if i not in got]
        if missing:
            try:
                for rec in _fetch(missing, hours):
                    p = parse_metar(rec)
                    if p and p[0] in set(ids):
                        rows.append(p)
            except (httpx.HTTPError, ValueError) as e:
                log.warning("aviationweather احتياطي: %s", e)

    with sqlite3.connect(db_path) as db:
        db.executemany("INSERT OR IGNORE INTO obs VALUES (?,?,?,?)", rows)
        db.execute("DELETE FROM obs WHERE ts < ?", (int(_time.time()) - KEEP_DAYS * 86400,))
    per: dict[str, int] = {}
    for r in rows:
        per[r[0]] = per.get(r[0], 0) + 1
    return {"parsed": len(rows), "stations": per}


# ---------- تعبئة سجل التوقعات من الأرشيف ----------
def backfill_forecasts(db_path, cities: dict, days: int = BACKFILL_DAYS, fetch=None) -> dict:
    """
    يملأ جدول forecast_archive (داخلي) بتوقعات النماذج المؤرشفة لكل مدينة.
    تنبيه: هذه ليست توقعات أصدرها التطبيق، ولا تدخل في أرقام الدقة المنشورة في اللوحة.
    تصلح للمعايرة الداخلية فقط.

    cities: {slug: {"name":..., "country":..., "lat":..., "lon":...}}
    """
    init_db(db_path)
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days)
    end = today - timedelta(days=1)
    written = 0
    per_city = {}

    def _get(lat, lon, tzname):
        if fetch is not None:
            return fetch(lat, lon, start.isoformat(), end.isoformat())
        with httpx.Client(timeout=60) as client:
            r = client.get(
                config.HISTORICAL_FORECAST_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                    "models": "ecmwf_ifs025,gfs_seamless,icon_seamless",
                    "timezone": tzname,
                },
            )
            r.raise_for_status()
            return r.json()

    with sqlite3.connect(db_path) as db:
        for slug, c in cities.items():
            tz = _tz(slug, c["country"])
            try:
                data = _get(c["lat"], c["lon"], str(tz))
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.warning("backfill %s: %s", slug, e)
                continue
            daily = data.get("daily") or {}
            times = daily.get("time") or []
            n = 0
            for i, target in enumerate(times):
                # متوسط النماذج الثلاثة المتاحة
                maxes = [
                    daily.get(f"temperature_2m_max_{m}", [None] * len(times))[i]
                    for m in ("ecmwf_ifs025", "gfs_seamless", "icon_seamless")
                ]
                mins = [
                    daily.get(f"temperature_2m_min_{m}", [None] * len(times))[i]
                    for m in ("ecmwf_ifs025", "gfs_seamless", "icon_seamless")
                ]
                precs = [
                    daily.get(f"precipitation_sum_{m}", [None] * len(times))[i]
                    for m in ("ecmwf_ifs025", "gfs_seamless", "icon_seamless")
                ]
                maxes = [v for v in maxes if v is not None]
                mins = [v for v in mins if v is not None]
                precs = [v for v in precs if v is not None]
                if not maxes or not mins:
                    continue
                # تقدير من الأرشيف، لا توقع صدر للمستخدمين: يُحفظ في جدول منفصل ولا يدخل في الدقة المنشورة
                issued = (date.fromisoformat(target) - timedelta(days=1)).isoformat()
                db.execute(
                    "INSERT OR IGNORE INTO forecast_archive VALUES (?,?,?,?,?,?)",
                    (slug, issued, target, round(sum(maxes) / len(maxes), 1),
                     round(sum(mins) / len(mins), 1), round(sum(precs) / len(precs), 1) if precs else 0.0),
                )
                n += 1
            per_city[slug] = n
            written += n
    return {"written": written, "cities": per_city}


# ---------- الملخصات اليومية ----------
def daily_summaries(rows, tz) -> dict:
    days: dict[str, dict] = {}
    for ts, temp, rain in rows:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)
        e = days.setdefault(
            dt.date().isoformat(),
            {"n": 0, "tmax": None, "tmin": None, "rain": 0, "hours": set()},
        )
        e["n"] += 1
        e["rain"] = e["rain"] or rain
        e["hours"].add(dt.hour)
        if temp is not None:
            e["tmax"] = temp if e["tmax"] is None else max(e["tmax"], temp)
            e["tmin"] = temp if e["tmin"] is None else min(e["tmin"], temp)
    return days


def _complete(e: dict) -> bool:
    return e["n"] >= MIN_OBS and len(e["hours"]) >= MIN_HOURS and e["tmax"] is not None


# ---------- الإحصائيات ----------
class _Acc:
    def __init__(self):
        self.emax, self.emin, self.rain = [], [], []

    def add(self, emax, emin, rain_f, rain_o):
        self.emax.append(emax)
        self.emin.append(emin)
        self.rain.append((rain_f, rain_o))

    def summary(self) -> dict:
        n = len(self.emax)
        if n == 0:
            return {"n": 0, "ready": False}

        def mae(vals):
            return round(sum(abs(x) for x in vals) / len(vals), 1)

        return {
            "n": n,
            "ready": n >= config.ACC_MIN_DAYS,
            "small_sample": n < config.ACC_SMALL_SAMPLE,
            "mae_max": mae(self.emax),
            "mae_min": mae(self.emin),
            "bias_max": round(sum(self.emax) / n, 1),
            "within2_max": round(100 * sum(1 for x in self.emax if abs(x) <= 2) / n),
            "rain_acc": round(100 * sum(1 for f, o in self.rain if f == o) / n),
            "rain_days": sum(1 for _, o in self.rain if o),
            "hits": sum(1 for f, o in self.rain if f and o),
            "false_alarms": sum(1 for f, o in self.rain if f and not o),
            "misses": sum(1 for f, o in self.rain if (not f) and o),
        }


def stats(db_path, cities: dict, days: int = 30) -> dict:
    """
    cities: {slug: {"name","country","lat","lon","elev"}}
    لكل مدينة basis واحد من:
      "station"     مقارنة بمحطة مطابقة (قريبة ومتقاربة الارتفاع) لها قراءات
      "unavailable" لا رقم: reason = no_station (لا محطة مطابقة) أو no_data (محطة مطابقة بلا قراءات كافية بعد)
    لا توجد أرقام "إقليمية" تُنسب لمدينة لا محطة لها. والمجموع العام يُحسب من المدن ذات المحطات المطابقة فقط.
    """
    init_db(db_path)
    out = {}
    pooled = {lead: _Acc() for lead in LEADS}
    eligible = 0

    with sqlite3.connect(db_path) as db:
        for slug, info in cities.items():
            cands = candidates_for(info)
            eligible += 1 if cands else 0
            entry = {
                "name": info["name"],
                "country": info["country"],
                "basis": "unavailable",
                "reason": "no_station" if not cands else "no_data",
                "station": None, "station_name": None, "station_km": None, "elev_diff": None,
                "obs_days": 0,
                "leads": {str(lead): {"n": 0, "ready": False} for lead in LEADS},
            }
            if cands:
                tz = _tz(slug, info["country"])
                today = datetime.now(tz).date()
                since = today - timedelta(days=days)
                lo = int(datetime.combine(since - timedelta(days=1), dtime(0, 0), tzinfo=tz).timestamp())

                chosen = None
                for cand in cands:  # الأقرب أولاً؛ نأخذ أول محطة لها أيام مكتملة
                    rows = db.execute(
                        "SELECT ts, temp, rain FROM obs WHERE icao=? AND ts>=?", (cand["icao"], lo)
                    ).fetchall()
                    obs = {
                        d: e
                        for d, e in daily_summaries(rows, tz).items()
                        if _complete(e) and since <= date.fromisoformat(d) < today
                    }
                    if obs:
                        chosen = (cand, obs)
                        break

                if chosen:
                    cand, obs = chosen
                    accs = {lead: _Acc() for lead in LEADS}
                    forecasts = db.execute(
                        "SELECT issued_date, target_date, max_t, min_t, precip FROM forecast_log "
                        "WHERE city=? AND source='live'",
                        (slug,),
                    ).fetchall()
                    for issued, target, fmax, fmin, fprecip in forecasts:
                        e = obs.get(target)
                        if not e:
                            continue
                        lead = (date.fromisoformat(target) - date.fromisoformat(issued)).days
                        if lead not in accs:
                            continue
                        args = (fmax - e["tmax"], fmin - e["tmin"] if e["tmin"] is not None else 0,
                                (fprecip or 0) >= RAIN_MM, bool(e["rain"]))
                        accs[lead].add(*args)
                        pooled[lead].add(*args)
                    entry.update(
                        basis="station", reason=None,
                        station=cand["icao"], station_name=cand["name"],
                        station_km=cand["km"], elev_diff=cand["elev_diff"],
                        obs_days=len(obs),
                        leads={str(lead): accs[lead].summary() for lead in LEADS},
                    )
            out[slug] = entry

    return {
        "days": days,
        "thresholds": {"max_km": config.ACC_MAX_KM, "max_elev": config.ACC_MAX_ELEV,
                       "min_days": config.ACC_MIN_DAYS},
        "cities": out,
        "overall": {
            # عدد المدن التي تساهم فعلاً في الأرقام (لها مقارنة واحدة على الأقل)، لا مجرد مدن لها قراءات
            "stations": sum(1 for c in out.values()
                            if c["basis"] == "station" and c["leads"]["1"]["n"] > 0),
            "eligible": eligible,
            "cities_total": len(out),
            "leads": {str(lead): pooled[lead].summary() for lead in LEADS},
        },
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------- أدوات سطر الأوامر ----------
def _km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def _cli(argv):
    import main  # يستورد المدن وقاعدة البيانات

    cmd = argv[1] if len(argv) > 1 else "--check"
    if cmd == "--check":
        rows = fetch_ogimet(sorted(STATION_INFO), 6)
        seen = {}
        for r in rows:
            seen[r[0]] = seen.get(r[0], 0) + 1
        for icao, (_, _, _, name) in STATION_INFO.items():
            print(f"{icao}  {name}: {seen.get(icao, 0)} تقرير خلال 6 ساعات")
        if not rows:
            print("لم يصل أي رد من Ogimet؛ تحقق من الاتصال.")
    elif cmd == "--match":
        for slug, c in main.CITIES.items():
            cands = candidates_for(c)
            if cands:
                best = cands[0]
                print(f"{c['name']}: {best['icao']} ({best['km']} كم، فرق ارتفاع {best['elev_diff']} م)")
            else:
                near = min(
                    ((_km(c["lat"], c["lon"], la, lo), i, c.get("elev", 0) - el)
                     for i, (la, lo, el, _) in STATION_INFO.items()),
                    default=None,
                )
                why = f"أقرب {near[1]} على {near[0]:.0f} كم وفرق ارتفاع {near[2]:.0f} م" if near else "لا محطات"
                print(f"{c['name']}: غير متاحة ({why})")
    elif cmd == "--discover":
        for slug, c in main.CITIES.items():
            la, lo = c["lat"], c["lon"]
            bbox = f"{la - 1.2},{lo - 1.4},{la + 1.2},{lo + 1.4}"
            with httpx.Client(timeout=30, headers=HEADERS) as client:
                r = client.get(METAR_URL, params={"bbox": bbox, "format": "json", "hours": 6})
            recs = r.json() if r.status_code == 200 and r.content.strip() else []
            found = {}
            for rec in recs:
                icao = rec.get("icaoId")
                if icao and rec.get("lat") is not None and rec.get("lon") is not None:
                    found[icao] = (_km(la, lo, rec["lat"], rec["lon"]), rec.get("name", ""),
                                   rec.get("lat"), rec.get("lon"), rec.get("elev"))
            line = ", ".join(
                f"{k} ({d[0]:.0f} كم، lat={d[2]}, lon={d[3]}, elev={d[4]}) {d[1]}"
                for k, d in sorted(found.items(), key=lambda x: x[1][0])
            )
            print(f"{c['name']}: {line or 'لا محطات METAR ضمن ~120 كم'}")
    elif cmd == "--collect":
        print(collect(main.DB_PATH))
    elif cmd == "--backfill":
        info = {s: {**c} for s, c in main.CITIES.items()}
        print("تنبيه: هذه تعبئة لجدول الأرشيف الداخلي ولن تظهر في لوحة الدقة.")
        print(backfill_forecasts(main.DB_PATH, info))
    elif cmd == "--stats":
        info = {s: {**c} for s, c in main.CITIES.items()}
        print(json.dumps(stats(main.DB_PATH, info), ensure_ascii=False, indent=1))
    else:
        print(__doc__)


if __name__ == "__main__":
    _cli(sys.argv)
