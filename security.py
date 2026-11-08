"""
الأمان - طقس بلاد الشام
Security helpers: SSRF protection for push endpoints, per-IP rate limiting,
request body size limits, and admin session auth.

لماذا هذا الملف:
- ثغرة SSRF الأصلية: كان الخادم يقبل أي رابط اشتراك ثم يرسل إليه طلباً،
  فيمكن استخدامه لمسح الشبكة الداخلية وخدمات السحابة الوصفية (169.254.169.254).
- لا حدود لحجم الطلب ولا لطول الحقول، ولا تحديد لمعدل الطلبات.
"""
import base64
import hashlib
import hmac
import ipaddress
import logging
import re
import secrets
import socket
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

import config

log = logging.getLogger("levant-weather.security")

# ---------- نطاقات خدمات الإشعارات المسموح بها ----------
# لا نقبل أي رابط خارج هذه القائمة. هذه هي نطاقات خدمات Web Push المعروفة.
ALLOWED_PUSH_HOSTS = {
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "updates-autopush.stage.mozaws.net",
    "push.services.mozilla.com",
    "wns2-par02p.notify.windows.com",
    "wns2-sg2p.notify.windows.com",
    "wns2-bl2p.notify.windows.com",
    "notify.windows.com",
    "web.push.apple.com",
}
# لاحقات مسموح بها (نطاق فرعي لأي منها مقبول)
ALLOWED_PUSH_SUFFIXES = (
    ".notify.windows.com",
    ".push.apple.com",
    ".push.services.mozilla.com",
)
# ملاحظة: لا نسمح بلاحقة ".googleapis.com" (تشمل storage.googleapis.com وأي نطاق فرعي).
# خدمة Chrome/Android هي fcm.googleapis.com فقط وهي في القائمة أعلاه.


def _is_private_ip(host: str) -> bool:
    """هل يشير المضيف إلى عنوان داخلي/محلي/وصفي؟

    المضيف هنا سبق وأن مرّ بقائمة النطاقات المسموحة، فهذا فحص إضافي فقط. إن تعذّر تحليل الاسم
    (انقطاع DNS مؤقت) لا نرفض المشترك: الإرسال نفسه سيفشل وقتها ولا ضرر، ورفضه يضيّع مشتركاً صالحاً.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def validate_push_endpoint(endpoint: str) -> str:
    """
    يتحقق من رابط اشتراك الإشعارات ويعيده نظيفاً، أو يرفع ValueError.
    يمنع: المخططات غير https، العناوين الداخلية، النطاقات غير المعروفة،
    ومنافذ غير قياسية، وأي محاولة للالتفاف عبر اسم مستخدم/كلمة مرور في الرابط.
    """
    if not isinstance(endpoint, str) or not endpoint:
        raise ValueError("رابط الاشتراك مطلوب")
    if len(endpoint) > config.MAX_ENDPOINT_LEN:
        raise ValueError("رابط الاشتراك أطول من الحد المسموح")

    try:
        u = urlparse(endpoint)
    except ValueError:
        raise ValueError("رابط الاشتراك غير صالح")

    if u.scheme != "https":
        raise ValueError("يجب أن يكون رابط الاشتراك عبر HTTPS")
    if u.username or u.password:
        raise ValueError("رابط الاشتراك لا يجوز أن يحمل بيانات دخول")
    host = (u.hostname or "").lower()
    if not host:
        raise ValueError("رابط الاشتراك بلا مضيف")

    # المنفذ: نسمح بالافتراضي 443 فقط
    try:
        port = u.port
    except ValueError:
        raise ValueError("منفذ غير صالح في رابط الاشتراك")
    if port not in (None, 443):
        raise ValueError("منفذ غير مسموح في رابط الاشتراك")

    allowed = host in ALLOWED_PUSH_HOSTS or host.endswith(ALLOWED_PUSH_SUFFIXES)
    if not allowed:
        raise ValueError("مضيف الإشعارات غير معروف")

    # فحص العناوين الداخلية (حماية إضافية ضد DNS rebinding البسيط)
    if _is_private_ip(host):
        raise ValueError("عنوان الإشعارات يشير إلى شبكة داخلية")

    return endpoint


def validate_subscription(sub: dict) -> dict:
    """يتحقق من كائن الاشتراك كاملاً (endpoint + keys) ويعيد نسخة منظفة."""
    if not isinstance(sub, dict):
        raise ValueError("الاشتراك غير صالح")
    endpoint = validate_push_endpoint(sub.get("endpoint", ""))
    keys = sub.get("keys")
    if not isinstance(keys, dict):
        raise ValueError("مفاتيح الاشتراك مفقودة")
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not isinstance(p256dh, str) or not isinstance(auth, str):
        raise ValueError("مفاتيح الاشتراك غير صالحة")
    if not (20 <= len(p256dh) <= 200) or not (8 <= len(auth) <= 100):
        raise ValueError("طول مفاتيح الاشتراك غير صالح")
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


# ---------- تحديد المعدل ----------
_hits: dict[str, deque] = defaultdict(deque)
_calls = 0
_GC_EVERY = 500          # ننظّف الذاكرة كل هذا العدد من الطلبات
_MAX_WINDOW = 3600       # أطول نافذة تحديد معدل مستخدمة (ثانية)


def _gc(now: float) -> None:
    """يحذف عناوين لم تعد لها طلبات ضمن أطول نافذة، حتى لا تتضخم الذاكرة."""
    horizon = max([w for _, w in config.RATE_LIMITS.values()] + [_MAX_WINDOW])
    for key in [k for k, q in _hits.items() if not q or now - q[-1] > horizon]:
        del _hits[key]


def rate_limit(bucket: str, client_ip: str) -> bool:
    """
    True إن كان الطلب مسموحاً، False إن تجاوز الحد.
    نافذة منزلقة بسيطة في الذاكرة (كافية لعملية واحدة؛ للإنتاج متعدد العمليات
    استخدم Redis أو ما يعادله).
    """
    limit, window = config.RATE_LIMITS.get(bucket, (60, 60))
    key = f"{bucket}:{client_ip}"
    now = time.time()
    global _calls
    _calls += 1
    if _calls % _GC_EVERY == 0:
        _gc(now)
    q = _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def client_ip(request) -> str:
    """
    عنوان العميل الحقيقي.
    الوسيط (Render وغيره) يُلحق عنوان من اتصل به في نهاية X-Forwarded-For. الجزء الأيسر من الترويسة
    يكتبه العميل فلا يجوز الوثوق به (كان تزويره يتجاوز تحديد المعدل). لذلك نعدّ من اليمين
    بعدد الوسطاء الموثوقين TRUSTED_PROXY_HOPS.
    """
    peer = request.client.host if request.client else "unknown"
    hops = config.TRUSTED_PROXY_HOPS
    if hops <= 0:
        return peer
    parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if len(parts) >= hops:
        return parts[-hops]
    return peer


# ---------- المفتاح السري (توقيع رموز الإعلانات وبصمة الزائر) ----------
_secret_cache: bytes | None = None


def _secret() -> bytes:
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    if config.SECRET_KEY:
        _secret_cache = config.SECRET_KEY.encode()
        return _secret_cache
    path = config.DATA_DIR / "secret.key"
    if path.exists():
        _secret_cache = path.read_bytes().strip()
        if _secret_cache:
            return _secret_cache
    key = secrets.token_urlsafe(48).encode()
    try:
        path.write_bytes(key)
        path.chmod(0o600)
    except OSError:
        log.warning("تعذّر حفظ المفتاح السري؛ تنتهي صلاحية الرموز عند كل إعادة تشغيل")
    _secret_cache = key
    return key


def _sign(data: bytes) -> str:
    return hmac.new(_secret(), data, hashlib.sha256).hexdigest()[:32]


def sign_ad_token(campaign_id: int, creative_id: int, placement: str) -> str:
    """رمز نقرة موقَّع: يربط النقرة بإعلان قدّمه الخادم فعلاً ولمدة محدودة."""
    exp = int(time.time()) + config.AD_TOKEN_HOURS * 3600
    payload = f"{int(campaign_id)}|{int(creative_id)}|{placement}|{exp}".encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + _sign(payload)


def verify_ad_token(token: str):
    """يعيد (campaign_id, creative_id, placement) أو None إن كان الرمز مزوَّراً أو منتهياً."""
    if not isinstance(token, str) or "." not in token or len(token) > 300:
        return None
    b64, _, sig = token.partition(".")
    try:
        payload = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        c, cr, p, exp = payload.decode().split("|")
        c, cr, exp = int(c), int(cr), int(exp)
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(sig.encode(), _sign(payload).encode()):
        return None
    if exp < time.time():
        return None
    return c, cr, p


def visitor_hash(request) -> str:
    """بصمة زائر مجهولة تتغير يومياً (عنوان + متصفح) لاحتساب حد الظهور والنقرات دون تخزين العنوان."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    ua = request.headers.get("user-agent", "")[:120]
    return _sign(f"{day}|{client_ip(request)}|{ua}".encode())[:20]


# ---------- التحقق من الروابط ----------
_BAD_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f<>\"'`\\]")


def validate_http_url(value: str, allow_relative: bool = True) -> str:
    """
    يقبل: فارغاً، أو مساراً داخلياً يبدأ بـ "/" (وليس "//")، أو رابط https بمضيف.
    يرفض javascript: وdata: وhttp العادي وأي رابط يحمل بيانات دخول أو رموزاً مشبوهة.
    """
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 500 or _BAD_URL_CHARS.search(value):
        raise ValueError("رابط غير صالح")
    if value.startswith("/") and not value.startswith("//"):
        if allow_relative:
            return value
        raise ValueError("يلزم رابط https كامل")
    from urllib.parse import urlparse
    u = urlparse(value)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise ValueError("يلزم رابط https صالح")
    return value


# ---------- مصادقة اللوحة ----------
def _admin_token() -> str:
    """
    رمز اللوحة: من البيئة، أو مولَّد ومحفوظ في مجلد البيانات.
    إن كان الرمز في البيئة أقصر من الحد الأدنى تُعطَّل اللوحة (يرجع "") بدل أن تبقى بحماية ضعيفة.
    """
    if config.ADMIN_TOKEN:
        if len(config.ADMIN_TOKEN) < config.ADMIN_MIN_TOKEN_LEN:
            log.error("ADMIN_TOKEN أقصر من %d حرفاً؛ لوحة الإدارة معطّلة حتى تضبط رمزاً أطول.",
                      config.ADMIN_MIN_TOKEN_LEN)
            return ""
        return config.ADMIN_TOKEN
    path = config.DATA_DIR / "admin_token.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(24)
    try:
        path.write_text(token, encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        log.warning("تعذّر حفظ رمز اللوحة في مجلد البيانات")
    log.warning("=" * 64)
    log.warning("رمز لوحة الإعلانات (ADMIN_TOKEN) لم يُضبط في البيئة.")
    log.warning("الرمز المولَّد: %s", token)
    log.warning("احفظه الآن؛ لن يُطبع مرة أخرى. أو اضبط ADMIN_TOKEN في البيئة.")
    log.warning("=" * 64)
    return token


def admin_token() -> str:
    return _admin_token()


def make_session() -> str:
    """توقيع جلسة اللوحة: HMAC على وقت الانتهاء بمفتاح مشتق من الرمز."""
    exp = int(time.time()) + config.ADMIN_SESSION_HOURS * 3600
    payload = f"{exp}"
    sig = hmac.new(_admin_token().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def check_session(cookie: str | None) -> bool:
    """هل كوكي الجلسة صالح وغير منتهٍ؟ لا يرفع أي استثناء لمدخلات غريبة."""
    token = _admin_token()
    if not token or not cookie or "." not in cookie:
        return False
    payload, _, sig = cookie.partition(".")
    try:
        exp = int(payload)
    except ValueError:
        return False
    if exp < time.time():
        return False
    expected = hmac.new(token.encode(), payload.encode(), hashlib.sha256).hexdigest()
    # المقارنة على بايتات: compare_digest ترفع TypeError على نصوص غير ASCII
    return hmac.compare_digest(sig.encode("utf-8", "ignore"), expected.encode())


def check_password(password: str) -> bool:
    """مقارنة كلمة مرور اللوحة بزمن ثابت، وبأي أحرف (عربية وغيرها) دون انهيار."""
    token = _admin_token()
    if not token or not isinstance(password, str):
        return False
    return hmac.compare_digest(password.encode("utf-8"), token.encode("utf-8"))
