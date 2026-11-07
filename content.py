"""
محتوى مكتوب مولَّد من البيانات: ملخص اليوم + نصائح اللباس والخروج.

الملخص والنصائح قواعد ثابتة تقرأ التوقع وتصوغه بالعربية، وليست تحليل أرصاد بشرياً.
التنبيهات الخطرة (سيول، ثلوج، رياح) تأتي من محرك التنبيهات؛ هنا نعرضها فقط ولا نضيف عليها.
"""
from datetime import date
from statistics import median

WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


# ---------- أدوات الصياغة ----------
def deg_text(n: int) -> str:
    if n == 1:
        return "درجة"
    if n == 2:
        return "درجتين"
    if n <= 10:
        return f"{n} درجات"
    return f"{n} درجة"


def part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "صباحاً"
    if 12 <= hour < 17:
        return "بعد الظهر"
    if 17 <= hour < 21:
        return "مساءً"
    return "ليلاً"


def hour_label(hour: int) -> str:
    return f"{hour % 12 or 12} {part_of_day(hour)}"


def hour_of(h: dict) -> int:
    return int(h["time"][11:13])


def weekday_name(date_str: str) -> str:
    return WEEKDAYS[date.fromisoformat(date_str).weekday()]


def day_label(date_str: str, index: int) -> str:
    if index == 0:
        return "اليوم"
    if index == 1:
        return "غداً"
    return weekday_name(date_str)


def _num(v, default=None):
    return default if v is None else v


# ---------- حالة السماء ----------
def condition(hours: list):
    """وصف مؤنث يُقال بعد «الأجواء»: صافية، غائمة... أو None إن لم تتوفر بيانات."""
    if not hours:
        return None
    cur = hours[0]
    if _num(cur.get("precip"), 0) >= 0.2:
        temp = cur.get("temp")
        if _num(cur.get("snow"), 0) > 0 or (temp is not None and temp <= 1):
            return "ثلجية"
        return "ماطرة"
    clouds = [h["cloud"] for h in hours[:6] if h.get("cloud") is not None]
    if not clouds:
        return None
    c = sum(clouds) / len(clouds)
    if c < 20:
        return "صافية"
    if c < 45:
        return "قليلة الغيوم"
    if c < 70:
        return "غائمة جزئياً"
    if c < 90:
        return "غائمة"
    return "ملبّدة بالغيوم"


# ---------- نافذة المطر ----------
def rain_window(hours: list):
    idx = [i for i, h in enumerate(hours) if _num(h.get("precip"), 0) >= 0.2]
    if not idx:
        return None
    total = sum(_num(hours[i].get("precip"), 0) for i in idx)
    return hours[idx[0]], hours[idx[-1]], total, len(idx)


def _rain_sentence(hours: list) -> str:
    rw = rain_window(hours)
    if not rw:
        return "لا أمطار متوقعة خلال الـ24 ساعة القادمة."
    first, last, total, count = rw
    word = "خفيفة" if total < 2 else "متوسطة" if total < 10 else "غزيرة"
    amount = "أقل من 1 ملم" if total < 1 else f"نحو {round(total)} ملم"
    first_date = first["time"][:10]
    last_date = last["time"][:10]
    today_date = hours[0]["time"][:10]
    a, b = hour_of(first), hour_of(last)
    first_day = "غداً " if first_date > today_date else ""
    last_day = "غداً " if last_date > today_date else ""
    if count == 1 or (a == b and first_date == last_date):
        when = f"{first_day}نحو الساعة {hour_label(a)}"
    else:
        when = f"بين الساعة {first_day}{hour_label(a)} و{last_day}{hour_label(b)}"
    return f"أمطار {word} متوقعة {when} بمجموع {amount}."


# ---------- الملخص ----------
def build_summary(w: dict) -> str:
    hours, days = w.get("hours") or [], w.get("days") or []
    if not hours or not days:
        return ""
    city = w["city"]["name"]
    cur, today = hours[0], days[0]
    parts = []

    cond = condition(hours)
    t = cur.get("temp")
    if cond:
        s = f"الأجواء الآن في {city} {cond}"
        if t is not None:
            s += f" والحرارة {round(t)}°"
    elif t is not None:
        s = f"الحرارة الآن في {city} {round(t)}°"
    else:
        s = f"توقعات {city} لليوم"
    feels = cur.get("feels")
    if t is not None and feels is not None and abs(feels - t) >= 3:
        s += f" (تُحَسّ كأنها {round(feels)}°)"
    parts.append(s + ".")

    parts.append(f"العظمى المتوقعة اليوم {round(today['max'])}° والصغرى {round(today['min'])}°.")
    parts.append(_rain_sentence(hours))

    if len(days) > 1:
        diff = round(days[1]["max"] - days[0]["max"])
        if diff <= -2:
            s = f"الغد أبرد بحوالي {deg_text(abs(diff))}"
        elif diff >= 2:
            s = f"الغد أدفأ بحوالي {deg_text(diff)}"
        else:
            s = "الغد قريب من اليوم في درجات الحرارة"
        wet = next((i for i in range(1, min(4, len(days))) if days[i]["precip"] >= 3), None)
        if wet is not None:
            when = "غداً" if wet == 1 else f"يوم {weekday_name(days[wet]['date'])}"
            s += f"، وتشير التوقعات إلى أمطار {when}"
        parts.append(s + ".")

    gusts = [h["gust"] for h in hours if h.get("gust") is not None]
    if gusts and max(gusts) >= 40:
        parts.append(f"رياح نشطة بهبّات تصل إلى {max(gusts)} كم/س.")

    alerts = w.get("alerts") or []
    if alerts:
        a = alerts[0]
        parts.append(f"تنبيه {a['level_name']}: {a['title']} {a['when']}.")

    return " ".join(parts)


# ---------- النصائح ----------
def _clothing(typical: float, lowest: float, swing: float) -> str:
    if typical <= 4:
        text = "معطف ثقيل مع قبعة وقفازات"
    elif typical <= 10:
        text = "جاكيت ثقيل وطبقات دافئة"
    elif typical <= 16:
        text = "جاكيت خفيف أو كنزة"
    elif typical <= 23:
        text = "ملابس خفيفة مع كنزة"
    elif typical <= 31:
        text = "ملابس قطنية خفيفة"
    else:
        text = "ملابس فاتحة وخفيفة مع قبعة"
    if typical > 10 and (lowest <= typical - 6 or swing >= 12):
        text += "، وخذ طبقة إضافية للمساء فالفرق كبير بين النهار والليل"
    return text


def build_tips(w: dict) -> list:
    hours, days = w.get("hours") or [], w.get("days") or []
    if not hours or not days:
        return []
    alerts = w.get("alerts") or []
    types = {a["type"]: a["level"] for a in alerts}

    feel = [h["feels"] if h.get("feels") is not None else h.get("temp") for h in hours[:16]]
    feel = [v for v in feel if v is not None]
    if not feel:
        return []
    typical, lowest = median(feel[:12]), min(feel)
    swing = days[0]["max"] - days[0]["min"]
    rain12 = sum(_num(h.get("precip"), 0) for h in hours[:12])
    gust = max((h["gust"] for h in hours[:12] if h.get("gust") is not None), default=0)
    uv = max((h["uv"] for h in hours[:12] if h.get("uv") is not None), default=None)
    clouds = [h["cloud"] for h in hours[:12] if h.get("cloud") is not None]
    clear = (sum(clouds) / len(clouds) < 40) if clouds else True

    tips = [{"label": "اللباس", "text": _clothing(typical, lowest, swing)}]

    extras = []
    if rain12 >= 0.5:
        extras.append("خذ مظلة أو واقي مطر")
    if gust >= 50:
        extras.append("ثبّت الأغراض الخفيفة في الخارج وتجنب الوقوف تحت الأشجار واللافتات")
    if (uv is not None and uv >= 6) or (typical >= 28 and clear):
        extras.append("واقي شمس ونظارة شمسية، وتجنب الشمس المباشرة بين 11 صباحاً و3 عصراً")
    if "frost" in types:
        extras.append("غطِّ المزروعات الحساسة وانتبه للجليد على الطرق صباحاً")
    if extras:
        tips.append({"label": "احتياطات", "text": "؛ ".join(extras)})

    if types.get("rain", 0) >= 2 or "storm" in types:
        out = "ابتعد عن الأودية ومجاري السيول وأجّل الرحلات المكشوفة"
    elif "snow" in types:
        out = "راجع حالة الطرق قبل السفر إلى المرتفعات وجهّز السيارة"
    elif rain12 >= 0.5:
        out = "الأنسب أنشطة داخلية، ولو خرجت فانتبه للطرق الزلقة"
    elif gust >= 35:
        out = "الرياح قد تزعج الأنشطة الخارجية، فاختر مكاناً محمياً"
    elif typical >= 32:
        out = "اجعل الأنشطة الخارجية صباحاً مبكراً أو بعد الغروب"
    elif 12 <= typical <= 28:
        out = "أجواء مناسبة للمشي والنزهات والأنشطة الخارجية"
    else:
        out = "الأنشطة الخارجية ممكنة مع ملابس مناسبة وعدم الإطالة في البرد"
    tips.append({"label": "للخروج والأنشطة", "text": out})
    return tips
