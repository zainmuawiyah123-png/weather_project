"""Manual advertising workflow. SQLite is intentionally exportable because Render Free has ephemeral storage."""
import hmac
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field, HttpUrl

DB_PATH = Path(os.getenv("ADS_DB_PATH", "/tmp/levant-weather-ads.db"))
ADMIN_PASSWORD = os.getenv("ADS_ADMIN_PASSWORD", "").strip()
router = APIRouter()
_rate = {}

def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with _db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL, contact TEXT NOT NULL,
          phone TEXT NOT NULL, email TEXT DEFAULT '', city TEXT DEFAULT '', slot TEXT NOT NULL,
          duration TEXT DEFAULT '', website TEXT DEFAULT '', message TEXT DEFAULT '',
          created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS ads (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, image_url TEXT DEFAULT '',
          target_url TEXT NOT NULL, slot TEXT NOT NULL, city TEXT DEFAULT '',
          starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
          impressions INTEGER NOT NULL DEFAULT 0, clicks INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        """)
init_db()

class AdRequest(BaseModel):
    company: str = Field(min_length=2, max_length=120)
    contact: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=7, max_length=40)
    email: str = Field(default="", max_length=160)
    city: str = Field(default="", max_length=80)
    slot: str = Field(min_length=2, max_length=30)
    duration: str = Field(default="", max_length=80)
    website: str = Field(default="", max_length=300)
    message: str = Field(default="", max_length=1000)
    website_confirm: str = ""  # honeypot

class AdCreate(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    image_url: str = Field(default="", max_length=1000)
    target_url: str = Field(min_length=8, max_length=1000)
    slot: str
    city: str = ""
    starts_at: str
    ends_at: str
    status: str = "active"

class StatusBody(BaseModel):
    status: str

class RestoreBody(BaseModel):
    data: dict

def _admin(password):
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "يجب ضبط ADS_ADMIN_PASSWORD في إعدادات Render أولاً")
    if not password or not hmac.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(401, "كلمة المرور غير صحيحة")

def _valid_url(value, required=False):
    if not value and not required:
        return True
    u = urlparse(value)
    return u.scheme == "https" and bool(u.netloc)

@router.post("/api/ads/requests")
def submit_request(body: AdRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    recent = [t for t in _rate.get(ip, []) if now - t < 3600]
    if len(recent) >= 5:
        raise HTTPException(429, "تم تجاوز عدد المحاولات. حاول لاحقاً.")
    recent.append(now)
    _rate[ip] = recent
    if body.website_confirm:
        return {"ok": True}
    if body.slot not in {"home_top", "map_top", "city_top", "sponsorship"}:
        raise HTTPException(422, "المساحة الإعلانية غير صحيحة")
    if body.website and not _valid_url(body.website):
        raise HTTPException(422, "رابط الموقع يجب أن يبدأ بـ https://")
    with _db() as db:
        cur = db.execute("""INSERT INTO requests
          (company,contact,phone,email,city,slot,duration,website,message,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?)""",
          (body.company.strip(),body.contact.strip(),body.phone.strip(),body.email.strip(),
           body.city.strip(),body.slot,body.duration.strip(),body.website.strip(),
           body.message.strip(),datetime.now(timezone.utc).isoformat()))
        rid = cur.lastrowid
    return {"ok": True, "id": rid, "message": "تم استلام طلبك. سنتواصل معك بعد مراجعته."}

@router.get("/api/ads/active")
def active_ads(slot: str, city: str = ""):
    if slot not in {"home_top", "map_top", "city_top"}:
        raise HTTPException(400, "مساحة غير صحيحة")
    now = datetime.now(timezone.utc).isoformat()
    with _db() as db:
        rows = db.execute("""SELECT id,title,image_url,target_url,slot,city FROM ads
          WHERE status='active' AND starts_at<=? AND ends_at>=? AND (city='' OR city IS NULL OR city=?) AND slot=?
          ORDER BY CASE WHEN city=? THEN 0 ELSE 1 END, id DESC LIMIT 3""", (now,now,city,slot,city)).fetchall()
    return [dict(r) for r in rows]

@router.post("/api/ads/{ad_id}/impression")
def ad_impression(ad_id: int):
    return _count_event(ad_id, "impression")

@router.get("/api/ads/{ad_id}/click")
def ad_click(ad_id: int):
    with _db() as db:
        row=db.execute("SELECT target_url FROM ads WHERE id=? AND status='active' AND starts_at<=? AND ends_at>=?",(ad_id,datetime.now(timezone.utc).isoformat(),datetime.now(timezone.utc).isoformat())).fetchone()
        if not row: raise HTTPException(404, "الإعلان غير متاح")
        db.execute("UPDATE ads SET clicks=clicks+1 WHERE id=?",(ad_id,))
    return RedirectResponse(row["target_url"], status_code=302)

def _count_event(ad_id: int, event: str):
    if event not in {"impression", "click"}:
        raise HTTPException(404, "حدث غير معروف")
    col = "impressions" if event == "impression" else "clicks"
    with _db() as db:
        cur = db.execute(f"UPDATE ads SET {col}={col}+1 WHERE id=? AND status='active'", (ad_id,))
        if not cur.rowcount:
            raise HTTPException(404, "الإعلان غير موجود")
    return {"ok": True}

@router.get("/admin/ads")
def admin_page():
    return FileResponse(Path(__file__).parent / "static" / "ads-admin.html")

@router.get("/api/admin/ads/requests")
def admin_requests(x_admin_password: str = Header(default="")):
    _admin(x_admin_password)
    with _db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()]

@router.post("/api/admin/ads/requests/{request_id}/status")
def admin_request_status(request_id: int, body: StatusBody, x_admin_password: str = Header(default="")):
    _admin(x_admin_password)
    if body.status not in {"pending", "contacted", "rejected", "approved"}:
        raise HTTPException(422, "حالة غير صحيحة")
    with _db() as db:
        cur = db.execute("UPDATE requests SET status=? WHERE id=?", (body.status,request_id))
        if not cur.rowcount: raise HTTPException(404, "الطلب غير موجود")
    return {"ok": True}

@router.get("/api/admin/ads")
def admin_ads(x_admin_password: str = Header(default="")):
    _admin(x_admin_password)
    with _db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM ads ORDER BY id DESC").fetchall()]

@router.post("/api/admin/ads")
def create_ad(body: AdCreate, x_admin_password: str = Header(default="")):
    _admin(x_admin_password)
    if body.slot not in {"home_top","map_top","city_top"}: raise HTTPException(422,"مساحة غير صحيحة")
    if not _valid_url(body.target_url, True) or not _valid_url(body.image_url):
        raise HTTPException(422,"يجب استخدام روابط HTTPS")
    try:
        start=datetime.fromisoformat(body.starts_at.replace("Z","+00:00")).astimezone(timezone.utc).isoformat()
        end=datetime.fromisoformat(body.ends_at.replace("Z","+00:00")).astimezone(timezone.utc).isoformat()
        if end <= start: raise ValueError()
    except ValueError: raise HTTPException(422,"تحقق من تاريخ البداية والنهاية")
    with _db() as db:
        cur=db.execute("""INSERT INTO ads(title,image_url,target_url,slot,city,starts_at,ends_at,status,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",(body.title.strip(),body.image_url.strip(),body.target_url.strip(),
        body.slot,body.city.strip(),start,end,body.status,datetime.now(timezone.utc).isoformat()))
        aid=cur.lastrowid
    return {"ok":True,"id":aid}

@router.post("/api/admin/ads/{ad_id}/status")
def admin_ad_status(ad_id: int, body: StatusBody, x_admin_password: str = Header(default="")):
    _admin(x_admin_password)
    if body.status not in {"active","paused","ended"}: raise HTTPException(422,"حالة غير صحيحة")
    with _db() as db:
        cur=db.execute("UPDATE ads SET status=? WHERE id=?",(body.status,ad_id))
        if not cur.rowcount: raise HTTPException(404,"الإعلان غير موجود")
    return {"ok":True}

@router.get("/api/admin/ads/backup")
def backup_ads(x_admin_password: str = Header(default="")):
    _admin(x_admin_password)
    with _db() as db:
        requests=[dict(r) for r in db.execute("SELECT * FROM requests").fetchall()]
        ads=[dict(r) for r in db.execute("SELECT * FROM ads").fetchall()]
    return {"version":1,"exported_at":datetime.now(timezone.utc).isoformat(),"requests":requests,"ads":ads}

@router.post("/api/admin/ads/restore")
def restore_ads(body: RestoreBody, x_admin_password: str = Header(default="")):
    _admin(x_admin_password)
    data=body.data
    if data.get("version") != 1 or not isinstance(data.get("requests"),list) or not isinstance(data.get("ads"),list):
        raise HTTPException(422,"ملف النسخة الاحتياطية غير صالح")
    with _db() as db:
        db.execute("DELETE FROM requests"); db.execute("DELETE FROM ads")
        for r in data["requests"]:
            db.execute("""INSERT INTO requests(id,company,contact,phone,email,city,slot,duration,website,message,created_at,status)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",tuple(r.get(k) for k in ("id","company","contact","phone","email","city","slot","duration","website","message","created_at","status")))
        for a in data["ads"]:
            db.execute("""INSERT INTO ads(id,title,image_url,target_url,slot,city,starts_at,ends_at,status,impressions,clicks,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",tuple(a.get(k) for k in ("id","title","image_url","target_url","slot","city","starts_at","ends_at","status","impressions","clicks","created_at")))
    return {"ok":True,"requests":len(data["requests"]),"ads":len(data["ads"])}
