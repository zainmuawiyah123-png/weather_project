"""
الإعدادات المركزية - طقس بلاد الشام
Central configuration.

كل المسارات القابلة للكتابة تُشتق من DATA_DIR حتى لا يُكتب أي ملف داخل مجلد الكود،
وهذا يجعل التطبيق يعمل على نظام ملفات للقراءة فقط (حاويات read-only، serverless).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


# ---------- مجلد البيانات ----------
# كل ما يُكتب (قواعد البيانات، مفاتيح VAPID) يذهب هنا. الافتراضي ./data
DATA_DIR = Path(_env("DATA_DIR") or (BASE_DIR / "data")).expanduser()
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # نظام ملفات للقراءة فقط: نرجع إلى مجلد مؤقت حتى لا يسقط الاستيراد
    import tempfile

    DATA_DIR = Path(tempfile.gettempdir()) / "levant-weather-data"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

FORECAST_DB = DATA_DIR / "forecast_log.db"
PUSH_DB = DATA_DIR / "push.db"
ADS_DB = DATA_DIR / "ads.db"
VAPID_PEM = DATA_DIR / "vapid_private.pem"

# ---------- الموقع ----------
SITE_URL = _env("SITE_URL").rstrip("/")
SITE_NAME_AR = "طقس بلاد الشام"
SITE_NAME_EN = "Levant Weather"
CONTACT_EMAIL = _env("CONTACT_EMAIL", "ads@levant-weather.example")

# ---------- Open-Meteo ----------
OPEN_METEO_API_KEY = _env("OPEN_METEO_API_KEY")
API_URL = (
    "https://customer-api.open-meteo.com/v1/forecast"
    if OPEN_METEO_API_KEY
    else "https://api.open-meteo.com/v1/forecast"
)
HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# ---------- الإشعارات ----------
VAPID_SUBJECT = _env("VAPID_SUBJECT", "mailto:admin@example.com")

# ---------- حدود الطلب ----------
MAX_BODY_BYTES = _env_int("MAX_BODY_BYTES", 64 * 1024)          # 64 كيلوبايت لأي طلب JSON
MAX_ENDPOINT_LEN = _env_int("MAX_ENDPOINT_LEN", 1024)
MAX_TYPES = 8
MAX_TYPES_LEN = 32

# ---------- تحديد المعدل (لكل عنوان IP) ----------
RATE_LIMITS = {
    # المسار: (عدد الطلبات، النافذة بالثواني)
    "weather": (_env_int("RL_WEATHER", 60), 60),
    "overview": (_env_int("RL_OVERVIEW", 20), 60),
    "grid": (_env_int("RL_GRID", 30), 60),
    "push": (_env_int("RL_PUSH", 20), 60),
    "ads": (_env_int("RL_ADS", 120), 60),
    "lead": (_env_int("RL_LEAD", 5), 3600),
    "admin": (_env_int("RL_ADMIN", 30), 60),
    # محاولات تسجيل الدخول: 10 كل 15 دقيقة لكل عنوان (ضد تخمين الرمز)
    "admin_login": (_env_int("RL_ADMIN_LOGIN", 10), 15 * 60),
}

# ---------- الشبكة والوسطاء ----------
# كم وسيطاً موثوقاً أمام التطبيق (Render وغيره = 1). نأخذ عنوان العميل من ترويسة
# X-Forwarded-For بعدّ هذا العدد من اليمين، فلا يستطيع العميل تزويرها.
# 0 = تجاهل الترويسة واستخدام عنوان الاتصال المباشر.
TRUSTED_PROXY_HOPS = _env_int("TRUSTED_PROXY_HOPS", 1)

# مفتاح التوقيع لرموز الإعلانات وبصمة الزائر. إن لم يُضبط يُولَّد ويُحفظ في DATA_DIR.
SECRET_KEY = _env("SECRET_KEY")

# ---------- لوحة الإعلانات ----------
# رمز دخول اللوحة. إن لم يُضبط نُولّد واحداً ونطبعه في السجل عند أول تشغيل.
ADMIN_TOKEN = _env("ADMIN_TOKEN")
ADMIN_COOKIE = "lw_admin"
ADMIN_SESSION_HOURS = _env_int("ADMIN_SESSION_HOURS", 12)
ADMIN_MIN_TOKEN_LEN = 16   # أقصر من هذا تُعطَّل اللوحة (رمز ضعيف = لوحة مكشوفة)

# ---------- الإعلانات ----------
# هل تُحمَّل الإعلانات قبل موافقة المستخدم على ملفات تعريف الارتباط؟
# الافتراضي False: لا يُحمَّل أي إعلان قبل الموافقة (احترام الخصوصية).
ADS_BEFORE_CONSENT = _env("ADS_BEFORE_CONSENT", "0") == "1"

# حملات تجريبية للمعاينة المحلية فقط. لا تُفعَّل أبداً على موقع حقيقي.
SEED_DEMO = _env("SEED_DEMO", "0") == "1"

# حدود احتساب الإعلانات (ضد تضخيم الأرقام): الحد الأقصى لكل زائر ولكل حملة
AD_IMPRESSION_CAP = _env_int("AD_IMPRESSION_CAP", 30)   # ظهور/يوم
AD_CLICK_CAP = _env_int("AD_CLICK_CAP", 3)               # نقرات/يوم
AD_TOKEN_HOURS = _env_int("AD_TOKEN_HOURS", 6)           # صلاحية رمز النقر

# شبكة إعلانات خارجية (اختياري). تُضبط من لوحة التحكم أو من البيئة.
ADSENSE_CLIENT = _env("ADSENSE_CLIENT")          # مثال: ca-pub-0000000000000000
ADS_TXT_ENTRIES = _env("ADS_TXT_ENTRIES")        # أسطر ads.txt إضافية، مفصولة بـ ;

# ---------- الكاش ----------
CACHE_TTL = _env_int("CACHE_TTL", 30 * 60)
GRID_TTL = _env_int("GRID_TTL", 60 * 60)
PUSH_EVERY = _env_int("PUSH_EVERY", 30 * 60)
ACCURACY_EVERY = _env_int("ACCURACY_EVERY", 6 * 3600)

# ---------- مطابقة محطات الرصد ----------
# لا تُقارن مدينة بمحطة إلا إن كانت قريبة ومتقاربة الارتفاع، وإلا صارت "الأخطاء" فروق مكان لا أخطاء توقع.
ACC_MAX_KM = _env_int("ACC_MAX_KM", 30)
ACC_MAX_ELEV = _env_int("ACC_MAX_ELEV", 150)   # متر؛ 150 م ≈ 1° فرق حرارة تقريباً
ACC_MIN_DAYS = _env_int("ACC_MIN_DAYS", 3)      # أقل أيام مقارنة لعرض أي رقم
ACC_SMALL_SAMPLE = _env_int("ACC_SMALL_SAMPLE", 14)  # أقل من هذا يُوسم "عينة صغيرة"
