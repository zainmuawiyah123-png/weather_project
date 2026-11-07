"""
الإشعارات (Web Push) - طقس بلاد الشام

- مفاتيح VAPID تُولَّد تلقائياً أول مرة وتُحفظ في vapid_private.pem (لا ترفعها إلى GitHub)
- الاشتراكات في push.db: جهاز واحد = مدينة واحدة + أدنى مستوى تنبيه
- منع التكرار: نفس (المدينة، النوع، المستوى، اليوم) يُرسل مرة واحدة لكل جهاز، والتصعيد إلى مستوى أعلى يُرسل
- ساعات الهدوء 23:00-06:00 بتوقيت المدينة، وحد أقصى 3 إشعارات يومياً لكل جهاز؛ الأحمر يتجاوز الاثنين
"""
import base64
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from alerts import LEVEL_NAMES

try:
    from pywebpush import WebPushException, webpush
except ImportError:  # يسمح بتشغيل بقية التطبيق حتى لو لم تُثبَّت الحزمة
    webpush = None

    class WebPushException(Exception):
        response = None

log = logging.getLogger("levant-weather.push")

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "push.db"
VAPID_PEM = BASE_DIR / "vapid_private.pem"
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:admin@example.com")

MAX_PER_DAY = 3
QUIET_FROM, QUIET_TO = 23, 6
MAX_LEAD_HOURS = 24  # لا نرسل إلا إن كان الخطر خلال 24 ساعة (الأحمر يُرسل دائماً)


# ---------- المفاتيح ----------
def ensure_keys() -> None:
    if VAPID_PEM.exists():
        return
    key = ec.generate_private_key(ec.SECP256R1())
    VAPID_PEM.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def public_key_b64() -> str:
    ensure_keys()
    key = serialization.load_pem_private_key(VAPID_PEM.read_bytes(), password=None)
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# ---------- قاعدة البيانات ----------
def _db():
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with _db() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS subs (
                endpoint TEXT PRIMARY KEY, sub_json TEXT, city TEXT,
                min_level INTEGER, types TEXT, created TEXT)"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS sent (
                endpoint TEXT, key TEXT, sent_at TEXT,
                PRIMARY KEY (endpoint, key))"""
        )


def save_sub(sub: dict, city: str, min_level: int, types: list[str]) -> None:
    init_db()
    with _db() as db:
        db.execute(
            "INSERT OR REPLACE INTO subs VALUES (?,?,?,?,?,?)",
            (
                sub["endpoint"],
                json.dumps(sub),
                city,
                max(1, min(3, min_level)),
                ",".join(types),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )


def remove_sub(endpoint: str) -> None:
    init_db()
    with _db() as db:
        db.execute("DELETE FROM subs WHERE endpoint=?", (endpoint,))
        db.execute("DELETE FROM sent WHERE endpoint=?", (endpoint,))


def get_sub(endpoint: str):
    init_db()
    with _db() as db:
        row = db.execute("SELECT sub_json FROM subs WHERE endpoint=?", (endpoint,)).fetchone()
    return json.loads(row[0]) if row else None


def subscribed_cities() -> list[str]:
    init_db()
    with _db() as db:
        return [r[0] for r in db.execute("SELECT DISTINCT city FROM subs")]


# ---------- الإرسال ----------
def send_push(sub: dict, payload: dict):
    """True عند النجاح، 'gone' إن انتهى الاشتراك، False لأي فشل آخر."""
    if webpush is None:
        log.warning("pywebpush غير مثبتة: pip install pywebpush")
        return False
    ensure_keys()
    try:
        webpush(
            subscription_info=sub,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=str(VAPID_PEM),
            vapid_claims={"sub": VAPID_SUBJECT},
        )
        return True
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            return "gone"
        log.warning("فشل إرسال إشعار: %s", e)
        return False


def alert_message(w: dict, a: dict) -> dict:
    city = w["city"]["name"]
    extra = " (نموذج واحد يتوقع أشد)" if a.get("upgrade_risk") else ""
    return {
        "title": f"تنبيه {LEVEL_NAMES[a['level']]}: {a['title']} في {city}",
        "body": f"{a['text']}. {a['when']}{extra}",
        "url": f"/?city={w['city']['slug']}",
        "tag": f"{w['city']['slug']}:{a['type']}",
    }


def notify_city(slug: str, w: dict, send=send_push) -> int:
    """يرسل تنبيهات مدينة لمشتركيها. يعيد عدد الإشعارات المرسلة."""
    init_db()
    hour = w.get("local_hour", 12)
    quiet = hour >= QUIET_FROM or hour < QUIET_TO
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sent_total = 0

    with _db() as db:
        subs = db.execute(
            "SELECT endpoint, sub_json, min_level, types FROM subs WHERE city=?", (slug,)
        ).fetchall()

        for endpoint, sub_json, min_level, types in subs:
            allowed = {t for t in types.split(",") if t} if types else set()
            count_today = db.execute(
                "SELECT COUNT(*) FROM sent WHERE endpoint=? AND sent_at>=?", (endpoint, today)
            ).fetchone()[0]
            sub = json.loads(sub_json)

            for a in w.get("alerts", []):
                red = a["level"] >= 3
                if a["level"] < min_level:
                    continue
                if allowed and a["type"] not in allowed:
                    continue
                if a["lead_hours"] > MAX_LEAD_HOURS and not red:
                    continue
                if not red and (quiet or count_today >= MAX_PER_DAY):
                    continue

                day = (a.get("start") or "")[:10] or today
                key = f"{slug}:{a['type']}:{a['level']}:{day}"
                if db.execute(
                    "SELECT 1 FROM sent WHERE endpoint=? AND key=?", (endpoint, key)
                ).fetchone():
                    continue

                result = send(sub, alert_message(w, a))
                if result == "gone":
                    db.execute("DELETE FROM subs WHERE endpoint=?", (endpoint,))
                    break
                if result:
                    db.execute(
                        "INSERT OR IGNORE INTO sent VALUES (?,?,?)",
                        (endpoint, key, datetime.now(timezone.utc).isoformat(timespec="seconds")),
                    )
                    count_today += 1
                    sent_total += 1
    return sent_total
