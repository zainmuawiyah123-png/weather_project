"""
الأمان - طقس بلاد الشام
Security helpers: SSRF protection for push endpoints, per-IP rate limiting,
request body size limits, and admin session auth.

لماذا هذا الملف:
- ثغرة SSRF الأصلية: كان الخادم يقبل أي رابط اشتراك ثم يرسل إليه طلباً،
  فيمكن استخدامه لمسح الشبكة الداخلية وخدمات السحابة الوصفية (169.254.169.254).
- لا حدود لحجم الطلب ولا لطول الحقول، ولا تحديد لمعدل الطلبات.
"""
import hashlib
import hmac
import ipaddress
import logging
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
    ".googleapis.com",
)


def _is_private_ip(host: str) -> bool:
    """هل يشير المضيف إلى عنوان داخلي/محلي/وصفي؟"""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return True  # لا يمكن تحليله: نعتبره غير آمن
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


def rate_limit(bucket: str, client_ip: str) -> bool:
    """
    True إن كان الطلب مسموحاً، False إن تجاوز الحد.
    نافذة منزلقة بسيطة في الذاكرة (كافية لعملية واحدة؛ للإنتاج متعدد العمليات
    استخدم Redis أو ما يعادله).
    """
    limit, window = config.RATE_LIMITS.get(bucket, (60, 60))
    key = f"{bucket}:{client_ip}"
    now = time.time()
    q = _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def client_ip(request) -> str:
    """عنوان العميل، مع احترام ترويسة الوسيط الأولى فقط."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------- مصادقة اللوحة ----------
def _admin_token() -> str:
    """رمز اللوحة: من البيئة، أو مولَّد ومحفوظ في مجلد البيانات."""
    if config.ADMIN_TOKEN:
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
    """توقيع جلسة اللوحة: HMAC على الرمز + وقت الانتهاء."""
    exp = int(time.time()) + config.ADMIN_SESSION_HOURS * 3600
    payload = f"{exp}"
    sig = hmac.new(_admin_token().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def check_session(cookie: str | None) -> bool:
    """هل كوكي الجلسة صالح وغير منتهٍ؟"""
    if not cookie or "." not in cookie:
        return False
    payload, _, sig = cookie.partition(".")
    try:
        exp = int(payload)
    except ValueError:
        return False
    if exp < time.time():
        return False
    expected = hmac.new(_admin_token().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def check_password(password: str) -> bool:
    """مقارنة كلمة مرور اللوحة بزمن ثابت."""
    if not isinstance(password, str):
        return False
    return hmac.compare_digest(password, _admin_token())
