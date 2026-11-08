"""
محرك الإعلانات - طقس بلاد الشام
Advertising engine: advertisers, campaigns, creatives, placements, serving,
impression/click tracking, revenue reporting, ads.txt and network codes.

المفاهيم:
- المعلن (advertiser): الجهة المعلنة.
- الحملة (campaign): لمعلن، لها مكان (placement) وصيغة (format) وجدولة (بداية/نهاية)
  وميزانية وسعر (CPM أو CPC أو مبلغ ثابت) وأولوية.
- الإبداع (creative): بانر صورة أو إعلان نصي أو رعاية موسمية.
- الظهور: يُحتسب على الخادم لحظة تقديم الإعلان لمتصفح (بعد موافقة الزائر)، لا بطلب يرسله العميل.
- النقرة: تمرّ عبر رابط يحمل رمزاً موقَّعاً من الخادم؛ وجهة الإعلان تُقرأ من قاعدة البيانات لا من الرابط.
- الحماية من التضخيم: حدّ أقصى للظهور والنقرات لكل زائر ولكل حملة في اليوم (بصمة مجهولة تتغير يومياً).
  هذا يقلل التزوير ولا يلغيه، فعامل الأرقام كتقدير وراجعها قبل محاسبة المعلنين.
- الإيراد: CPM = ظهور/1000 × السعر، CPC = نقرات × السعر،
  ثابت = السعر موزّعاً بالتساوي على أيام الحملة ويُحتسب ما انقضى منها فقط.

الأماكن القياسية (مقاسات IAB):
  home_top      728×90   لوحة قيادة (Leaderboard)
  home_mid      336×280  مستطيل كبير
  home_bottom   970×90   لوحة عريضة
  city_top      728×90
  city_mid      300×250  مستطيل متوسط
  map_side      300×600  نصف صفحة
  alert_sponsor نصية     رعاية موسمية لتنبيهات الطقس
"""
import csv
import io
import logging
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone

import config
import security

log = logging.getLogger("levant-weather.ads")

DB_PATH = config.ADS_DB

# ---------- الأماكن والمقاسات ----------
PLACEMENTS = {
    "home_top": {"label_ar": "الرئيسية - أعلى", "label_en": "Home - Top",
                 "size": "728x90", "w": 728, "h": 90, "formats": ["image", "text"]},
    "home_mid": {"label_ar": "الرئيسية - وسط", "label_en": "Home - Middle",
                 "size": "336x280", "w": 336, "h": 280, "formats": ["image", "text"]},
    "home_bottom": {"label_ar": "الرئيسية - أسفل", "label_en": "Home - Bottom",
                    "size": "970x90", "w": 970, "h": 90, "formats": ["image", "text"]},
    "city_top": {"label_ar": "صفحة المدينة - أعلى", "label_en": "City - Top",
                 "size": "728x90", "w": 728, "h": 90, "formats": ["image", "text"]},
    "city_mid": {"label_ar": "صفحة المدينة - وسط", "label_en": "City - Middle",
                 "size": "300x250", "w": 300, "h": 250, "formats": ["image", "text"]},
    "map_side": {"label_ar": "الخريطة - جانبي", "label_en": "Map - Side",
                 "size": "300x600", "w": 300, "h": 600, "formats": ["image", "text"]},
    "alert_sponsor": {"label_ar": "رعاية تنبيهات الطقس", "label_en": "Weather Alert Sponsorship",
                      "size": "native", "w": 0, "h": 0, "formats": ["sponsor", "text"]},
}

# أسعار مقترحة بالدينار الأردني (تُعرض في صفحة "أعلن معنا")
PRICE_LIST = [
    {"placement": "home_top", "size": "728x90", "cpm": 4.5, "monthly": 180},
    {"placement": "home_mid", "size": "336x280", "cpm": 5.0, "monthly": 200},
    {"placement": "home_bottom", "size": "970x90", "cpm": 4.0, "monthly": 160},
    {"placement": "city_top", "size": "728x90", "cpm": 3.5, "monthly": 140},
    {"placement": "city_mid", "size": "300x250", "cpm": 4.0, "monthly": 160},
    {"placement": "map_side", "size": "300x600", "cpm": 6.0, "monthly": 240},
    {"placement": "alert_sponsor", "size": "native", "cpm": 8.0, "monthly": 320},
]

_initialized = False


# ---------- قاعدة البيانات ----------
def _db():
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    global _initialized
    if _initialized:
        return
    with _db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS advertisers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, company TEXT, email TEXT, phone TEXT,
                notes TEXT, created TEXT
            );
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                advertiser_id INTEGER, name TEXT NOT NULL, placement TEXT NOT NULL,
                format TEXT NOT NULL DEFAULT 'image',
                start_date TEXT, end_date TEXT, status TEXT DEFAULT 'active',
                priority INTEGER DEFAULT 5, pricing TEXT DEFAULT 'cpm',
                rate REAL DEFAULT 0, budget REAL DEFAULT 0, daily_cap INTEGER DEFAULT 0,
                target_url TEXT, created TEXT
            );
            CREATE TABLE IF NOT EXISTS creatives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL, kind TEXT DEFAULT 'image',
                image_url TEXT, width INTEGER, height INTEGER,
                title TEXT, body TEXT, cta TEXT, target_url TEXT,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS impressions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER, creative_id INTEGER, placement TEXT,
                ts TEXT, day TEXT, page TEXT, vhash TEXT
            );
            CREATE TABLE IF NOT EXISTS clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER, creative_id INTEGER, placement TEXT,
                ts TEXT, day TEXT, page TEXT, vhash TEXT
            );
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, email TEXT, company TEXT, phone TEXT,
                message TEXT, budget TEXT, status TEXT DEFAULT 'new', created TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE INDEX IF NOT EXISTS idx_imp_day ON impressions(day, campaign_id);
            CREATE INDEX IF NOT EXISTS idx_clk_day ON clicks(day, campaign_id);
            """
        )
        # ترقية قواعد البيانات القديمة: عمود بصمة الزائر
        for table in ("impressions", "clicks"):
            cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
            if "vhash" not in cols:
                db.execute(f"ALTER TABLE {table} ADD COLUMN vhash TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS idx_imp_v ON impressions(campaign_id, day, vhash)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_clk_v ON clicks(campaign_id, day, vhash)")
    _initialized = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------- الإعدادات ----------
def get_setting(key: str, default: str = "") -> str:
    init_db()
    with _db() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    init_db()
    with _db() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))


def network_client() -> str:
    """معرّف شبكة الإعلانات (AdSense) — من الإعدادات أو البيئة."""
    return get_setting("adsense_client", config.ADSENSE_CLIENT)


# ---------- المعلنون ----------
def add_advertiser(name, company="", email="", phone="", notes="") -> int:
    init_db()
    with _db() as db:
        cur = db.execute(
            "INSERT INTO advertisers (name,company,email,phone,notes,created) VALUES (?,?,?,?,?,?)",
            (name, company, email, phone, notes, _now()),
        )
        return cur.lastrowid


def list_advertisers() -> list[dict]:
    init_db()
    with _db() as db:
        rows = db.execute("SELECT * FROM advertisers ORDER BY id DESC").fetchall()
    cols = ["id", "name", "company", "email", "phone", "notes", "created"]
    return [dict(zip(cols, r)) for r in rows]


# ---------- الحملات ----------
def create_campaign(data: dict) -> int:
    init_db()
    data = {**data, "target_url": security.validate_http_url(data.get("target_url", ""))}
    with _db() as db:
        cur = db.execute(
            """INSERT INTO campaigns
               (advertiser_id,name,placement,format,start_date,end_date,status,priority,
                pricing,rate,budget,daily_cap,target_url,created)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data.get("advertiser_id"), data["name"], data["placement"],
                data.get("format", "image"), data.get("start_date"), data.get("end_date"),
                data.get("status", "active"), int(data.get("priority", 5)),
                data.get("pricing", "cpm"), float(data.get("rate", 0)),
                float(data.get("budget", 0)), int(data.get("daily_cap", 0)),
                data.get("target_url", ""), _now(),
            ),
        )
        return cur.lastrowid


def update_campaign(cid: int, data: dict) -> bool:
    init_db()
    fields = ["advertiser_id", "name", "placement", "format", "start_date", "end_date",
              "status", "priority", "pricing", "rate", "budget", "daily_cap", "target_url"]
    if "target_url" in data:
        data = {**data, "target_url": security.validate_http_url(data["target_url"])}
    sets, vals = [], []
    for f in fields:
        if f in data:
            sets.append(f"{f}=?")
            vals.append(data[f])
    if not sets:
        return False
    vals.append(cid)
    with _db() as db:
        db.execute(f"UPDATE campaigns SET {','.join(sets)} WHERE id=?", vals)
    return True


def delete_campaign(cid: int) -> None:
    init_db()
    with _db() as db:
        db.execute("DELETE FROM creatives WHERE campaign_id=?", (cid,))
        db.execute("DELETE FROM campaigns WHERE id=?", (cid,))


def list_campaigns() -> list[dict]:
    init_db()
    with _db() as db:
        cols = [d[0] for d in db.execute("SELECT * FROM campaigns LIMIT 0").description]
        rows = db.execute(
            """SELECT c.*, a.name AS advertiser_name,
                      (SELECT COUNT(*) FROM impressions i WHERE i.campaign_id=c.id) AS impressions,
                      (SELECT COUNT(*) FROM clicks k WHERE k.campaign_id=c.id) AS clicks
               FROM campaigns c LEFT JOIN advertisers a ON a.id=c.advertiser_id
               ORDER BY c.id DESC"""
        ).fetchall()
    out = []
    for r in rows:
        d = dict(zip(cols, r[:len(cols)]))
        d["advertiser_name"] = r[len(cols)]
        d["impressions"] = r[len(cols) + 1]
        d["clicks"] = r[len(cols) + 2]
        d["ctr"] = round(100 * d["clicks"] / d["impressions"], 2) if d["impressions"] else 0.0
        d["revenue"] = campaign_revenue(d)
        out.append(d)
    return out


def get_campaign(cid: int) -> dict | None:
    init_db()
    with _db() as db:
        cols = [d[0] for d in db.execute("SELECT * FROM campaigns LIMIT 0").description]
        row = db.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    return dict(zip(cols, row)) if row else None


# ---------- الإبداعات ----------
def add_creative(campaign_id: int, data: dict) -> int:
    init_db()
    data = {
        **data,
        "target_url": security.validate_http_url(data.get("target_url", "")),
        "image_url": security.validate_http_url(data.get("image_url", "")),
    }
    with _db() as db:
        cur = db.execute(
            """INSERT INTO creatives
               (campaign_id,kind,image_url,width,height,title,body,cta,target_url,active)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                campaign_id, data.get("kind", "image"), data.get("image_url", ""),
                int(data.get("width", 0)), int(data.get("height", 0)),
                data.get("title", ""), data.get("body", ""), data.get("cta", ""),
                data.get("target_url", ""), 1 if data.get("active", True) else 0,
            ),
        )
        return cur.lastrowid


def list_creatives(campaign_id: int) -> list[dict]:
    init_db()
    with _db() as db:
        cols = [d[0] for d in db.execute("SELECT * FROM creatives LIMIT 0").description]
        rows = db.execute("SELECT * FROM creatives WHERE campaign_id=?", (campaign_id,)).fetchall()
    return [dict(zip(cols, r)) for r in rows]


# ---------- التقديم (Serving) ----------
def _active_campaigns(placement: str, today: str) -> list[dict]:
    init_db()
    with _db() as db:
        cols = [d[0] for d in db.execute("SELECT * FROM campaigns LIMIT 0").description]
        rows = db.execute(
            """SELECT * FROM campaigns
               WHERE placement=? AND status='active'
                 AND (start_date IS NULL OR start_date='' OR start_date<=?)
                 AND (end_date IS NULL OR end_date='' OR end_date>=?)
               ORDER BY priority DESC, id ASC""",
            (placement, today, today),
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def _impressions_today(campaign_id: int, today: str) -> int:
    with _db() as db:
        return db.execute(
            "SELECT COUNT(*) FROM impressions WHERE campaign_id=? AND day=?", (campaign_id, today)
        ).fetchone()[0]


def pick(placement: str) -> dict | None:
    """
    يختار إعلاناً للمكان المطلوب دون أي تسجيل (آمن للاستدعاء عند رسم الصفحة). يعيد dict أو None.
    - يحترم الجدولة (بداية/نهاية) والحالة والحد اليومي.
    - يوزّع بالتناوب بين الحملات المؤهلة (rotation) حسب الأولوية.
    - إن لم توجد حملة، يعيد إعلان البيت (self-promo) حتى لا يبقى المكان فارغاً.
    """
    if placement not in PLACEMENTS:
        return None
    init_db()
    today = _today()
    cands = _active_campaigns(placement, today)
    eligible = []
    for c in cands:
        if c["daily_cap"] and _impressions_today(c["id"], today) >= c["daily_cap"]:
            continue
        creatives = [x for x in list_creatives(c["id"]) if x["active"]]
        if creatives:
            eligible.append((c, creatives))
    if not eligible:
        return house_ad(placement)

    # توزيع بالتناوب: نختار الحملة الأقل ظهوراً اليوم (عدالة بين المعلنين)
    eligible.sort(key=lambda x: (_impressions_today(x[0]["id"], today), -x[0]["priority"]))
    c, creatives = eligible[0]
    cr = creatives[secrets.randbelow(len(creatives))]
    return {
        "campaign_id": c["id"],
        "creative_id": cr["id"],
        "placement": placement,
        "kind": cr["kind"],
        "image_url": cr["image_url"],
        "width": cr["width"] or PLACEMENTS[placement]["w"],
        "height": cr["height"] or PLACEMENTS[placement]["h"],
        "title": cr["title"],
        "body": cr["body"],
        "cta": cr["cta"] or "اعرف المزيد",
        "target_url": cr["target_url"] or c["target_url"],
        "sponsored": True,
        "label": "إعلان",
    }


def serve(placement: str, page: str = "", vhash: str = "") -> dict | None:
    """
    يقدّم إعلاناً لزائر: يختاره، ويحتسب الظهور على الخادم (ضمن حد الزائر)، ويرفق رابط نقرة موقَّعاً.
    وجهة الإعلان لا تُرسل للمتصفح؛ يتبع رابط النقرة فقط (يُحمّل الوجهة من قاعدة البيانات).
    """
    ad = pick(placement)
    if ad is None:
        return None
    if ad["campaign_id"]:
        record_impression(ad["campaign_id"], ad["creative_id"], placement, page, vhash)
        token = security.sign_ad_token(ad["campaign_id"], ad["creative_id"], placement)
        ad["click_url"] = "/api/ads/click?t=" + token
        ad.pop("target_url", None)
    else:
        ad["click_url"] = "/advertise"
    return ad


def house_ad(placement: str) -> dict:
    """إعلان البيت: دعوة للإعلان معنا. يظهر حين لا توجد حملة نشطة."""
    return {
        "campaign_id": 0,
        "creative_id": 0,
        "placement": placement,
        "kind": "house",
        "image_url": "",
        "width": PLACEMENTS[placement]["w"],
        "height": PLACEMENTS[placement]["h"],
        "title": "أعلن معنا",
        "body": "اعرض علامتك أمام زوار طقس بلاد الشام في الأردن وفلسطين ولبنان وسوريا.",
        "cta": "اطّلع على الأسعار",
        "target_url": "/advertise",
        "sponsored": False,
        "label": "إعلان",
    }


# ---------- التتبع ----------
def _valid_pair(db, campaign_id: int, creative_id: int) -> bool:
    """الحملة موجودة، والإبداع (إن ذُكر) يتبع هذه الحملة."""
    if not db.execute("SELECT 1 FROM campaigns WHERE id=?", (campaign_id,)).fetchone():
        return False
    if creative_id and not db.execute(
        "SELECT 1 FROM creatives WHERE id=? AND campaign_id=?", (creative_id, campaign_id)
    ).fetchone():
        return False
    return True


def _record(table: str, cap: int, campaign_id: int, creative_id: int, placement: str,
            page: str, vhash: str) -> bool:
    if not campaign_id or placement not in PLACEMENTS:
        return False
    init_db()
    today = _today()
    with _db() as db:
        if not _valid_pair(db, campaign_id, creative_id):
            return False
        if vhash and cap > 0:
            n = db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE campaign_id=? AND day=? AND vhash=?",
                (campaign_id, today, vhash),
            ).fetchone()[0]
            if n >= cap:
                return False
        db.execute(
            f"INSERT INTO {table} (campaign_id,creative_id,placement,ts,day,page,vhash) VALUES (?,?,?,?,?,?,?)",
            (campaign_id, creative_id, placement, _now(), today, page[:200], vhash or None),
        )
    return True


def record_impression(campaign_id: int, creative_id: int, placement: str, page: str = "",
                      vhash: str = "") -> bool:
    """يسجّل ظهوراً إن كانت الحملة والإبداع حقيقيين ولم يتجاوز الزائر حدّه اليومي."""
    return _record("impressions", config.AD_IMPRESSION_CAP, campaign_id, creative_id, placement, page, vhash)


def record_click(campaign_id: int, creative_id: int, placement: str, page: str = "",
                 vhash: str = "") -> bool:
    return _record("clicks", config.AD_CLICK_CAP, campaign_id, creative_id, placement, page, vhash)


def click_target(campaign_id: int, creative_id: int) -> str | None:
    """وجهة النقرة من قاعدة البيانات فقط (لا من الرابط). None إن لم تُوجد أو كانت غير صالحة."""
    init_db()
    with _db() as db:
        row = db.execute(
            """SELECT cr.target_url, c.target_url
               FROM creatives cr JOIN campaigns c ON c.id = cr.campaign_id
               WHERE cr.id=? AND c.id=?""",
            (creative_id, campaign_id),
        ).fetchone()
    if not row:
        return None
    try:
        return security.validate_http_url(row[0] or row[1] or "") or None
    except ValueError:
        return None


# ---------- الإيراد ----------
def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _flat_earned(c: dict, since: date | None = None) -> float:
    """
    إيراد الحملة بسعر ثابت: السعر موزّع بالتساوي على أيامها، ويُحتسب منه ما انقضى فقط.
    since: إن حُدّد، نحتسب الأيام من هذا التاريخ فصاعداً (نافذة التقرير).
    حملة بلا تاريخَي بداية ونهاية صالحَين تُعامَل كمبلغ مقطوع يُحتسب كاملاً في الإجمالي،
    وفي النافذة إن أُنشئت ضمنها.
    """
    rate = float(c.get("rate") or 0)
    try:
        start = date.fromisoformat(c.get("start_date") or "")
        end = date.fromisoformat(c.get("end_date") or "")
    except ValueError:
        if since is None:
            return rate
        created = (c.get("created") or "")[:10]
        return rate if created and created >= since.isoformat() else 0.0
    total_days = max((end - start).days + 1, 1)
    lo = max(start, since) if since else start
    hi = min(end, _utc_today())
    elapsed = max((hi - lo).days + 1, 0)
    return rate * min(elapsed, total_days) / total_days


def campaign_revenue(c: dict, days: int | None = None) -> float:
    """
    إيراد الحملة بالدينار الأردني (الإجمالي، أو آخر days يوماً).
    cpm: ظهور/1000 × السعر. cpc: نقرات × السعر. flat: ما انقضى من السعر الموزّع على أيام الحملة.
    """
    init_db()
    pricing = (c.get("pricing") or "cpm").lower()
    rate = float(c.get("rate") or 0)
    since = (_utc_today() - timedelta(days=days)) if days else None
    if pricing == "flat":
        return round(_flat_earned(c, since), 2)
    with _db() as db:
        if since:
            imp = db.execute(
                "SELECT COUNT(*) FROM impressions WHERE campaign_id=? AND day>=?",
                (c["id"], since.isoformat())).fetchone()[0]
            clk = db.execute(
                "SELECT COUNT(*) FROM clicks WHERE campaign_id=? AND day>=?",
                (c["id"], since.isoformat())).fetchone()[0]
        else:
            imp = db.execute("SELECT COUNT(*) FROM impressions WHERE campaign_id=?", (c["id"],)).fetchone()[0]
            clk = db.execute("SELECT COUNT(*) FROM clicks WHERE campaign_id=?", (c["id"],)).fetchone()[0]
    if pricing == "cpc":
        return round(clk * rate, 2)
    return round(imp / 1000.0 * rate, 2)


def report(days: int = 30) -> dict:
    """
    تقرير إيرادات. المجاميع (totals) تخص آخر `days` يوماً فقط؛ أما جدول الحملات فيعرض
    الإجمالي منذ البداية في الظهور والنقرات والإيراد، وبجانبه revenue_window لنافذة التقرير.
    """
    init_db()
    today = _utc_today()
    since = (today - timedelta(days=days)).isoformat()
    with _db() as db:
        daily_imp = dict(db.execute(
            "SELECT day, COUNT(*) FROM impressions WHERE day>=? GROUP BY day", (since,)).fetchall())
        daily_clk = dict(db.execute(
            "SELECT day, COUNT(*) FROM clicks WHERE day>=? GROUP BY day", (since,)).fetchall())
        monthly_imp = dict(db.execute(
            "SELECT substr(day,1,7), COUNT(*) FROM impressions GROUP BY substr(day,1,7)").fetchall())
        monthly_clk = dict(db.execute(
            "SELECT substr(day,1,7), COUNT(*) FROM clicks GROUP BY substr(day,1,7)").fetchall())

    campaigns = list_campaigns()
    for c in campaigns:
        c["revenue_window"] = campaign_revenue(c, days)
    total_rev = round(sum(c["revenue_window"] for c in campaigns), 2)
    total_imp = sum(daily_imp.values())
    total_clk = sum(daily_clk.values())

    days_list = []
    for i in range(days, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        imp = daily_imp.get(d, 0)
        clk = daily_clk.get(d, 0)
        days_list.append({"day": d, "impressions": imp, "clicks": clk,
                          "ctr": round(100 * clk / imp, 2) if imp else 0.0})

    months = sorted(set(monthly_imp) | set(monthly_clk))
    months_list = [{"month": m, "impressions": monthly_imp.get(m, 0),
                    "clicks": monthly_clk.get(m, 0),
                    "ctr": round(100 * monthly_clk.get(m, 0) / monthly_imp.get(m, 1), 2)
                    if monthly_imp.get(m) else 0.0} for m in months]

    return {
        "days": days,
        "totals": {
            "impressions": total_imp, "clicks": total_clk,
            "ctr": round(100 * total_clk / total_imp, 2) if total_imp else 0.0,
            "revenue_jod": total_rev,
        },
        "daily": days_list,
        "monthly": months_list,
        "campaigns": campaigns,
        "generated_at": _now(),
    }


def report_csv(days: int = 30) -> str:
    """تقرير الإيراد اليومي بصيغة CSV (قابل للتصدير إلى Excel)."""
    r = report(days)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["التاريخ", "الظهور", "النقرات", "نسبة النقر %"])
    for d in r["daily"]:
        w.writerow([d["day"], d["impressions"], d["clicks"], d["ctr"]])
    w.writerow([])
    w.writerow(["الحملة", "المعلن", "المكان", "الظهور (إجمالي)", "النقرات (إجمالي)", "نسبة النقر %",
                "الإيراد الإجمالي (د.أ)", f"إيراد آخر {r['days']} يوماً (د.أ)"])
    for c in r["campaigns"]:
        w.writerow([c["name"], c.get("advertiser_name") or "", c["placement"],
                    c["impressions"], c["clicks"], c["ctr"], c["revenue"], c["revenue_window"]])
    w.writerow([])
    w.writerow([f"إجمالي آخر {r['days']} يوماً", "", "", r["totals"]["impressions"], r["totals"]["clicks"],
                r["totals"]["ctr"], "", r["totals"]["revenue_jod"]])
    return buf.getvalue()


# ---------- العملاء المحتملون (طلبات الإعلان) ----------
def add_lead(data: dict) -> int:
    init_db()
    with _db() as db:
        cur = db.execute(
            "INSERT INTO leads (name,email,company,phone,message,budget,status,created) VALUES (?,?,?,?,?,?,?,?)",
            (data.get("name", ""), data.get("email", ""), data.get("company", ""),
             data.get("phone", ""), data.get("message", ""), data.get("budget", ""),
             "new", _now()),
        )
        return cur.lastrowid


def list_leads() -> list[dict]:
    init_db()
    with _db() as db:
        cols = [d[0] for d in db.execute("SELECT * FROM leads LIMIT 0").description]
        rows = db.execute("SELECT * FROM leads ORDER BY id DESC").fetchall()
    return [dict(zip(cols, r)) for r in rows]


# ---------- ads.txt ----------
def ads_txt() -> str:
    """
    ملف ads.txt: يعلن من يحق له بيع مساحاتنا الإعلانية.
    يُبنى من معرّف الشبكة (AdSense) ومن أسطر مخصّصة تُضبط من اللوحة.
    """
    lines = ["# ads.txt - طقس بلاد الشام / Levant Weather",
             "# يُدار من لوحة الإعلانات. لا تعدّله يدوياً إن كنت تستخدم اللوحة.", ""]
    client = network_client()
    if client:
        pub = client if client.startswith("pub-") else client.replace("ca-", "")
        lines.append(f"google.com, {pub}, DIRECT, f08c47fec0942fa0")
    custom = get_setting("ads_txt_extra", config.ADS_TXT_ENTRIES)
    if custom:
        for entry in custom.replace(";", "\n").splitlines():
            if entry.strip():
                lines.append(entry.strip())
    if len(lines) <= 3:
        lines.append("# لم تُضبط شبكة إعلانات بعد. أضف معرّف AdSense من لوحة الإعلانات.")
    return "\n".join(lines) + "\n"


# ---------- بيانات أولية للعرض ----------
DEMO_EMAIL = "ads@ufuq.jo"   # توقيع المعلن التجريبي؛ لا يستخدمه أي معلن حقيقي


def purge_demo() -> int:
    """
    يحذف حملات المعلن التجريبي ("شركة الأفق للاتصالات") وإبداعاتها وإحصاءاتها.
    كانت هذه الحملات تُنشأ تلقائياً عند أول تشغيل على قاعدة جديدة فتظهر للزوار وتدخل في الإيراد.
    يعيد عدد الحملات المحذوفة.
    """
    init_db()
    with _db() as db:
        adv = [r[0] for r in db.execute("SELECT id FROM advertisers WHERE email=?", (DEMO_EMAIL,))]
        if not adv:
            return 0
        qa = ",".join("?" * len(adv))
        cids = [r[0] for r in db.execute(f"SELECT id FROM campaigns WHERE advertiser_id IN ({qa})", adv)]
        if cids:
            qc = ",".join("?" * len(cids))
            for table in ("creatives", "impressions", "clicks"):
                col = "campaign_id"
                db.execute(f"DELETE FROM {table} WHERE {col} IN ({qc})", cids)
            db.execute(f"DELETE FROM campaigns WHERE id IN ({qc})", cids)
        db.execute(f"DELETE FROM advertisers WHERE id IN ({qa})", adv)
    return len(cids)


def seed_demo() -> None:
    """
    ينشئ حملات تجريبية للمعاينة المحلية فقط (SEED_DEMO=1). لا تستدعها على موقع حقيقي:
    فيها راعٍ وهمي وروابط example.com وإيراد ثابت وهمي.
    """
    init_db()
    with _db() as db:
        if db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]:
            return
    adv = add_advertiser("شركة الأفق للاتصالات", "Al-Ufuq Telecom", "ads@ufuq.jo", "+962 6 000 0000")
    today = date.today()
    c1 = create_campaign({
        "advertiser_id": adv, "name": "حملة الأفق - الشتاء", "placement": "home_top",
        "format": "text", "start_date": (today - timedelta(days=5)).isoformat(),
        "end_date": (today + timedelta(days=60)).isoformat(), "priority": 8,
        "pricing": "cpm", "rate": 4.5, "budget": 500, "target_url": "https://example.com/ufuq",
    })
    add_creative(c1, {"kind": "text", "title": "باقات إنترنت شتوية",
                      "body": "سرعات أعلى وأسعار أوفر لعملاء الأردن. عرض محدود.",
                      "cta": "اطلب الآن", "target_url": "https://example.com/ufuq"})
    c2 = create_campaign({
        "advertiser_id": adv, "name": "رعاية تنبيهات الطقس", "placement": "alert_sponsor",
        "format": "sponsor", "start_date": (today - timedelta(days=2)).isoformat(),
        "end_date": (today + timedelta(days=90)).isoformat(), "priority": 9,
        "pricing": "flat", "rate": 320, "budget": 320, "target_url": "https://example.com/ufuq",
    })
    add_creative(c2, {"kind": "sponsor", "title": "رعاية تنبيهات الطقس",
                      "body": "تُعرض تنبيهات الطقس برعاية شركة الأفق للاتصالات.",
                      "cta": "تعرف على الراعي", "target_url": "https://example.com/ufuq"})
    c3 = create_campaign({
        "advertiser_id": adv, "name": "حملة الأفق - صفحة المدينة", "placement": "city_mid",
        "format": "text", "start_date": (today - timedelta(days=1)).isoformat(),
        "end_date": (today + timedelta(days=45)).isoformat(), "priority": 6,
        "pricing": "cpc", "rate": 0.25, "budget": 200, "target_url": "https://example.com/ufuq",
    })
    add_creative(c3, {"kind": "text", "title": "تأمين سيارات شامل",
                      "body": "أسعار تنافسية وتغطية في كل محافظات المملكة.",
                      "cta": "احسب قسطك", "target_url": "https://example.com/ufuq"})
