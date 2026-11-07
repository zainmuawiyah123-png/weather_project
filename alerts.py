"""
محرك التنبيهات - طقس بلاد الشام

الفكرة:
1. نقيّم كل خطر (أمطار/سيول، ثلج، عواصف رعدية، رياح، صقيع، حر) على حدة لكل نموذج جوي.
2. لكل خطر مستوى من 0 إلى 3:  1 أصفر (انتبه)  2 برتقالي (استعد)  3 أحمر (خطر).
3. المستوى النهائي = المستوى الذي يتفق عليه نموذجان على الأقل (ثاني أعلى مستوى بين النماذج).
   إن انفرد نموذج واحد بمستوى أعلى نبقى على المستوى المتفق عليه ونضع علامة upgrade_risk.
   وإن توفر نموذج واحد فقط لا نتجاوز الأصفر.

العتبات أدناه نقطة بداية معقولة وليست معايرة رسمية؛ عدّلها بعد مقارنتها بالتحذيرات الرسمية
وبالواقع من سجل التوقعات.
"""

HORIZON = 48  # ساعات يغطيها التقييم

LEVEL_NAMES = {1: "أصفر", 2: "برتقالي", 3: "أحمر"}
LEVEL_HINTS = {1: "انتبه", 2: "استعد", 3: "خطر"}

TITLES = {
    "rain": "أمطار غزيرة وخطر سيول",
    "snow": "تساقط ثلوج",
    "storm": "عواصف رعدية",
    "wind": "رياح قوية",
    "frost": "صقيع",
    "heat": "موجة حر",
}

TEXTS = {
    "rain": "حتى {peak} ملم خلال 3 ساعات. انتبه للسيول في الأودية ومجاري المياه",
    "snow": "تراكم ثلوج متوقع حتى {peak} سم خلال 24 ساعة",
    "storm": "عواصف رعدية قوية مع احتمال برد ورياح مفاجئة",
    "wind": "هبّات رياح تصل إلى {peak} كم/س",
    "frost": "تنخفض الحرارة إلى {peak}°، احمِ المزروعات والأنابيب",
    "heat": "ترتفع الحرارة إلى {peak}°، قلّل الجهد في الخارج واشرب الماء",
}


# ---------- أدوات صغيرة ----------
def _has(vals):
    return any(v is not None for v in vals)


def _grade(value, thresholds):
    sev = 0
    for level, limit in enumerate(thresholds, start=1):
        if value >= limit:
            sev = level
    return sev


def _window_sum(vals, k):
    """أكبر مجموع لنافذة من k عناصر متتالية، مع فهرس بدايتها."""
    v = [x or 0 for x in vals]
    if not v:
        return 0.0, 0
    k = min(k, len(v))
    cur = sum(v[:k])
    best, best_i = cur, 0
    for i in range(1, len(v) - k + 1):
        cur += v[i + k - 1] - v[i - 1]
        if cur > best:
            best, best_i = cur, i
    return best, best_i


def lead_text(h: int) -> str:
    if h <= 0:
        return "الآن"
    if h == 1:
        return "خلال ساعة"
    if h == 2:
        return "خلال ساعتين"
    if h <= 10:
        return f"خلال {h} ساعات"
    return f"خلال {h} ساعة"


# ---------- تقييم كل خطر لنموذج واحد ----------
# كل دالة تعيد (المستوى، القيمة القصوى، فهرس الساعة) أو None إن لم تتوفر بيانات كافية.
def eval_rain(a, dry):
    if not _has(a["p"]):
        return None
    f = 0.6 if dry else 1.0  # المناطق الجافة (معان، العقبة، البحر الميت) أحسّ بالسيول عند كميات أقل
    p = [x or 0 for x in a["p"]]
    h1 = max(p)
    h3, i3 = _window_sum(p, 3)
    sev = max(
        _grade(h1, (8 * f, 15 * f, 25 * f)),
        _grade(h3, (15 * f, 30 * f, 50 * f)),
    )
    return sev, round(h3, 1), i3


def eval_snow(a, dry):
    if _has(a["sn"]):
        sn = [x or 0 for x in a["sn"]]
    elif _has(a["p"]) and _has(a["t"]):
        # بديل تقريبي: كل 1 ملم مطر بحرارة ≤ 1° يساوي نحو 1 سم ثلج
        sn = [
            (p or 0) * 1.0 if (t is not None and t <= 1) else 0
            for p, t in zip(a["p"], a["t"])
        ]
    else:
        return None
    total, i = _window_sum(sn, 24)
    return _grade(total, (1, 5, 15)), round(total, 1), i


def eval_storm(a, dry):
    if not _has(a["cape"]):
        return None
    sev, peak, idx = 0, 0, 0
    for i, (c, p) in enumerate(zip(a["cape"], a["p"])):
        c, p = c or 0, p or 0
        s = 0
        if c >= 800 and p >= 1:
            s = 1
        if c >= 1500 and p >= 3:
            s = 2
        if c >= 2500 and p >= 6:
            s = 3
        if s > sev:
            sev, peak, idx = s, c, i
    return sev, round(peak), idx


def eval_wind(a, dry):
    if _has(a["g"]):
        g = a["g"]
    else:
        g = [None if x is None else x * 1.4 for x in a["w"]]  # تقدير الهبّات من الرياح المستمرة
    if not _has(g):
        return None
    mx = max(x for x in g if x is not None)
    return _grade(mx, (60, 80, 100)), round(mx), g.index(mx)


def eval_frost(a, dry):
    if not _has(a["t"]):
        return None
    mn = min(x for x in a["t"] if x is not None)
    sev = 0
    if mn <= 2:
        sev = 1
    if mn <= 0:
        sev = 2
    if mn <= -4:
        sev = 3
    return sev, round(mn, 1), a["t"].index(mn)


def eval_heat(a, dry):
    if not _has(a["t"]):
        return None
    mx = max(x for x in a["t"] if x is not None)
    return _grade(mx, (40, 43, 46)), round(mx, 1), a["t"].index(mx)


# اسم الخطر: (دالة التقييم، أي قيمة هي الأسوأ)
HAZARDS = {
    "rain": (eval_rain, "max"),
    "snow": (eval_snow, "max"),
    "storm": (eval_storm, "max"),
    "wind": (eval_wind, "max"),
    "frost": (eval_frost, "min"),
    "heat": (eval_heat, "max"),
}


# ---------- التصويت بين النماذج ----------
def _consensus(hazard, results, worse, times, start):
    n = len(results)
    if n == 0:
        return None
    sevs = sorted((r[0] for r in results), reverse=True)
    level = sevs[1] if n >= 2 else min(sevs[0], 1)
    if level <= 0:
        return None

    agreeing = [r for r in results if r[0] >= level]
    peaks = [r[1] for r in agreeing]
    peak = max(peaks) if worse == "max" else min(peaks)
    lead = min(r[2] for r in agreeing)
    idx = start + lead

    return {
        "type": hazard,
        "level": level,
        "level_name": LEVEL_NAMES[level],
        "level_hint": LEVEL_HINTS[level],
        "title": TITLES[hazard],
        "text": TEXTS[hazard].format(peak=peak),
        "peak": peak,
        "lead_hours": lead,
        "when": lead_text(lead),
        "start": times[idx] if idx < len(times) else None,
        "agree_n": len(agreeing),
        "total": n,
        "upgrade_risk": sevs[0] > level,
    }


def _column(hourly, base, model, start, n):
    series = hourly.get(f"{base}_{model}")
    return list(series[start:start + n]) if series else [None] * n


def evaluate(hourly, times, start, models, dry=False, horizon=HORIZON):
    """يعيد قائمة التنبيهات مرتبة من الأشد إلى الأخف، ثم الأقرب زمنياً."""
    n = max(0, min(horizon, len(times) - start))
    if n == 0:
        return []

    per_model = []
    for m in models:
        a = {
            "t": _column(hourly, "temperature_2m", m, start, n),
            "p": _column(hourly, "precipitation", m, start, n),
            "w": _column(hourly, "wind_speed_10m", m, start, n),
            "g": _column(hourly, "wind_gusts_10m", m, start, n),
            "sn": _column(hourly, "snowfall", m, start, n),
            "cape": _column(hourly, "cape", m, start, n),
        }
        if _has(a["t"]) or _has(a["p"]):  # نتجاهل النموذج الذي لم يُرجع شيئاً
            per_model.append(a)

    out = []
    for hazard, (fn, worse) in HAZARDS.items():
        results = [r for r in (fn(a, dry) for a in per_model) if r is not None]
        alert = _consensus(hazard, results, worse, times, start)
        if alert:
            out.append(alert)

    out.sort(key=lambda x: (-x["level"], x["lead_hours"]))
    return out
