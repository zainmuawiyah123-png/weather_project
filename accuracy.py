"""
قياس الدقة - طقس بلاد الشام

كيف يعمل:
1. كل 6 ساعات يجلب الخادم تقارير METAR (قراءات مطارات حقيقية) من aviationweather.gov
   ويخزّنها في جدول obs داخل forecast_log.db.
2. من القراءات نحسب لكل يوم محلي: العظمى والصغرى، وهل سُجّل مطر أو ثلج أو رعد.
3. نقارن ذلك بالتوقعات المسجلة في forecast_log (قبل يوم ويومين وثلاثة أيام من الموعد).

حدود يجب أن تعرفها:
- المحطات مطارات، وقد تختلف حرارتها عن قلب المدينة (الارتفاع والعمران)؛ لذلك الدقة المعروضة "مقابل محطة المطار".
- METAR لا يعطي كمية المطر؛ نعرف فقط إن حدث هطول أم لا، وقد يفوته هطول قصير بين قراءتين.
- لا يظهر أي رقم قبل توفر أيام كافية للمقارنة.

تشغيل أدوات مساعدة من سطر الأوامر:
  python accuracy.py --check      أي محطات من القائمة ترد فعلاً
  python accuracy.py --discover   محطات METAR القريبة من كل مدينة (لتحديث STATIONS)
  python accuracy.py --collect    جلب القراءات الآن
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

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

log = logging.getLogger("levant-weather.accuracy")

METAR_URL = "https://aviationweather.gov/api/data/metar"
HEADERS = {"User-Agent": "levant-weather/1.0"}

# مدينة: رموز ICAO المرشّحة (نختار الأكثر قراءات). ليست كل المدن لها محطة METAR.
# تحقق بـ: python accuracy.py --check  ثم  --discover  وعدّل القائمة حسب النتيجة.
STATIONS = {
    "amman": ["OJAM", "OJAI"],
    "aqaba": ["OJAQ"],
    "maan": ["OJMN"],
    "beirut": ["OLBA"],
    "damascus": ["OSDI"],
    "aleppo": ["OSAP"],
    "latakia": ["OSLK"],
}

RAIN_MM = 1.0        # توقع "يوم ماطر" عندما يبلغ مجموع التوقع هذا الحد
MIN_HOURS = 16       # أقل عدد ساعات مختلفة من اليوم نحتاجه لنعتبر اليوم مكتمل القراءات
MIN_OBS = 10
LEADS = (1, 2, 3)    # قبل كم يوم صدر التوقع
KEEP_DAYS = 120


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
        # على ويندوز بدون الحزمة tzdata؛ تقريب لا يراعي التوقيت الصيفي
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


# ---------- قاعدة البيانات والجلب ----------
def init_db(db_path) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS obs (
                icao TEXT, ts INTEGER, temp REAL, rain INTEGER,
                PRIMARY KEY (icao, ts))"""
        )


def _fetch(ids: list[str], hours: int) -> list:
    with httpx.Client(timeout=30, headers=HEADERS) as client:
        r = client.get(METAR_URL, params={"ids": ",".join(ids), "format": "json", "hours": hours})
        if r.status_code == 204 or not r.content.strip():
            return []
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else []


def collect(db_path, hours: int = 48, fetch=_fetch) -> dict:
    """يجلب قراءات كل المحطات المرشّحة ويخزّنها. يعيد ملخصاً."""
    init_db(db_path)
    ids = sorted({i for v in STATIONS.values() for i in v})
    recs = fetch(ids, hours)
    wanted = set(ids)
    rows = []
    for rec in recs:
        p = parse_metar(rec)
        if p and p[0] in wanted:
            rows.append(p)
    with sqlite3.connect(db_path) as db:
        db.executemany("INSERT OR IGNORE INTO obs VALUES (?,?,?,?)", rows)
        db.execute("DELETE FROM obs WHERE ts < ?", (int(_time.time()) - KEEP_DAYS * 86400,))
    per: dict[str, int] = {}
    for r in rows:
        per[r[0]] = per.get(r[0], 0) + 1
    return {"records": len(recs), "parsed": len(rows), "stations": per}


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


# ---------- الإحصاءات ----------
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
            return {"n": 0}

        def mae(vals):
            return round(sum(abs(x) for x in vals) / len(vals), 1)

        return {
            "n": n,
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
    """cities: {slug: {"name":..., "country":...}}"""
    init_db(db_path)
    out = {}
    pooled = {lead: _Acc() for lead in LEADS}

    with sqlite3.connect(db_path) as db:
        for slug, icaos in STATIONS.items():
            if slug not in cities:
                continue
            info = cities[slug]
            tz = _tz(slug, info["country"])
            today = datetime.now(tz).date()
            since = today - timedelta(days=days)
            lo = int(datetime.combine(since - timedelta(days=1), dtime(0, 0), tzinfo=tz).timestamp())

            best_icao, best_rows = None, []
            for icao in icaos:
                rows = db.execute(
                    "SELECT ts, temp, rain FROM obs WHERE icao=? AND ts>=?", (icao, lo)
                ).fetchall()
                if len(rows) > len(best_rows):
                    best_icao, best_rows = icao, rows

            obs = {
                d: e
                for d, e in daily_summaries(best_rows, tz).items()
                if _complete(e) and since <= date.fromisoformat(d) < today
            }

            accs = {lead: _Acc() for lead in LEADS}
            forecasts = db.execute(
                "SELECT issued_date, target_date, max_t, min_t, precip FROM forecast_log WHERE city=?",
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

            out[slug] = {
                "name": info["name"],
                "station": best_icao,
                "obs_days": len(obs),
                "leads": {str(lead): accs[lead].summary() for lead in LEADS},
            }

    return {
        "days": days,
        "cities": out,
        "overall": {
            "stations": sum(1 for c in out.values() if c["obs_days"] > 0),
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
        ids = sorted({i for v in STATIONS.values() for i in v})
        recs = _fetch(ids, 6)
        seen = {}
        for rec in recs:
            p = parse_metar(rec)
            if p:
                seen[p[0]] = seen.get(p[0], 0) + 1
        for slug, icaos in STATIONS.items():
            for i in icaos:
                print(f"{slug:10s} {i}: {seen.get(i, 0)} تقرير خلال 6 ساعات")
        if not recs:
            print("لم يصل أي رد من aviationweather.gov؛ تحقق من الاتصال.")
    elif cmd == "--discover":
        for slug, c in main.CITIES.items():
            la, lo = c["lat"], c["lon"]
            bbox = f"{la - 0.5},{lo - 0.6},{la + 0.5},{lo + 0.6}"
            with httpx.Client(timeout=30, headers=HEADERS) as client:
                r = client.get(METAR_URL, params={"bbox": bbox, "format": "json", "hours": 6})
            recs = r.json() if r.status_code == 200 and r.content.strip() else []
            found = {}
            for rec in recs:
                icao = rec.get("icaoId")
                if icao and rec.get("lat") is not None and rec.get("lon") is not None:
                    found[icao] = (_km(la, lo, rec["lat"], rec["lon"]), rec.get("name", ""))
            line = ", ".join(f"{k} ({d:.0f} كم) {n}" for k, (d, n) in sorted(found.items(), key=lambda x: x[1][0]))
            print(f"{c['name']}: {line or 'لا محطات METAR ضمن ~50 كم'}")
    elif cmd == "--collect":
        print(collect(main.DB_PATH))
    elif cmd == "--stats":
        info = {s: {"name": c["name"], "country": c["country"]} for s, c in main.CITIES.items()}
        print(json.dumps(stats(main.DB_PATH, info), ensure_ascii=False, indent=1))
    else:
        print(__doc__)


if __name__ == "__main__":
    _cli(sys.argv)
