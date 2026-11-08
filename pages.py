"""
صفحات تُرسم على الخادم: صفحات المدن (للـSEO)، صفحة الخطأ، أعلن معنا،
سياسة الخصوصية، ولوحة الإعلانات. + sitemap وrobots.
"""
import html
import json
from datetime import datetime, timezone

import ads
import config
import content

e = html.escape


def gradient(w: dict) -> str:
    cur = w.get("current") or {}
    t = cur.get("temp")
    rainy = any((h.get("precip") or 0) >= 0.5 for h in (w.get("hours") or [])[:8])
    if rainy:
        return "linear-gradient(160deg,#22344f,#4c6a92)"
    if t is None or 8 <= t < 18:
        return "linear-gradient(160deg,#0b5c7a,#2a9aa8)"
    if t < 8:
        return "linear-gradient(160deg,#0b3c5d,#1f6f96)"
    if t < 28:
        return "linear-gradient(160deg,#1b6f8a,#b8730f)"
    return "linear-gradient(160deg,#a8331a,#d9650f)"


def _description(w: dict) -> str:
    sentences = [s.strip() for s in (w.get("summary") or "").split(".") if s.strip()]
    out = ""
    for s in sentences:
        nxt = (out + " " + s + ".").strip()
        if len(nxt) > 155:
            break
        out = nxt
    return out or f"توقعات طقس {w['city']['name']} اليوم والأيام السبعة القادمة."


CSS = """
:root{--bg:#e8f1f2;--surface:#fff;--ink:#0b2f40;--muted:#4b6874;--line:#d3e1e3;--cold:#0b6e99;--y:#e8b100;--o:#ef7b00;--r:#d62828;
--a1:#fff6d6;--a2:#ffe7cc;--a3:#ffd9d9;--ai:#4a3200}
@media(prefers-color-scheme:dark){:root{--bg:#0a1a22;--surface:#112a36;--ink:#e8f2f4;--muted:#8fb0bb;--line:#1f3d4a;--cold:#5cb8e0;
--a1:#3a3210;--a2:#3d2610;--a3:#3d1717;--ai:#f6e3b8}}
*{box-sizing:border-box}html{background:var(--bg)}
body{margin:0 auto;max-width:760px;padding:20px 16px 48px;font-family:"Cairo",system-ui,sans-serif;color:var(--ink);background:var(--bg);line-height:1.7}
header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:12px;color:inherit;text-decoration:none}
.brand img{width:44px;height:44px;border-radius:12px;display:block}
.brand b{font-size:1.15rem;font-weight:800}
nav{display:flex;gap:8px;flex-wrap:wrap}
.btn,.navlink{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:8px 14px;text-decoration:none;cursor:pointer}
button.btn{background:var(--cold);color:#fff;border-color:transparent;font-weight:600}
a:focus-visible,button:focus-visible{outline:3px solid var(--cold);outline-offset:2px}
h1{font-size:1.5rem;font-weight:800;margin:22px 0 0}
h2{font-size:1rem;margin:26px 0 10px;color:var(--muted);font-weight:600}
.hero{position:relative;overflow:hidden;margin:12px 0 8px;padding:22px 24px 64px;border-radius:24px;color:#fff}
.hero::after{content:"";position:absolute;inset-inline:0;bottom:0;height:60px;pointer-events:none;background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 64' preserveAspectRatio='none'%3E%3Cpath d='M0 40 L50 18 L90 34 L150 6 L210 38 L270 14 L330 36 L400 20 V64 H0Z' fill='%23fff' fill-opacity='.14'/%3E%3Cpath d='M0 52 L60 34 L120 48 L190 26 L260 50 L330 30 L400 46 V64 H0Z' fill='%23fff' fill-opacity='.2'/%3E%3C/svg%3E") bottom/100% 100% no-repeat}
.hero .temp{font-size:5.5rem;font-weight:800;line-height:1.05;letter-spacing:-2px}
.hero .meta{display:flex;gap:16px;flex-wrap:wrap;font-weight:600}
.alerts{display:grid;gap:10px;margin:16px 0}
.alert{color:var(--ai);border-radius:12px;padding:12px 14px;border-inline-start:6px solid var(--y);background:var(--a1)}
.alert.l2{border-color:var(--o);background:var(--a2)}.alert.l3{border-color:var(--r);background:var(--a3)}
.alert b{display:block}.alert small{opacity:.85}
.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:14px 16px}
.card p{margin:0}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.tips{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.tips .card small{color:var(--muted);font-weight:600;display:block;margin-bottom:2px}
.wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:16px}
table{border-collapse:collapse;width:100%;font-size:.95rem}
th,td{padding:10px 12px;text-align:center;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:.8rem}
td:first-child,th:first-child{text-align:start;font-weight:600}
tr:last-child td{border-bottom:0}
.muted{color:var(--muted);font-size:.85rem}
.cities{display:flex;flex-wrap:wrap;gap:8px}
.cities a{color:var(--cold);text-decoration:none;border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:4px 12px;font-size:.9rem}
footer{margin-top:30px;color:var(--muted);font-size:.8rem}footer p{margin:6px 0}footer a{color:var(--cold)}
.err{text-align:center;padding:60px 20px}
.err .code{font-size:4rem;font-weight:800;color:var(--cold);line-height:1}
.prose{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:20px 22px}
.prose h2{margin-top:22px;color:var(--ink);font-size:1.05rem}
.prose h2:first-child{margin-top:0}
.prose ul{padding-inline-start:20px}
.prose li{margin:6px 0}
.pricing{width:100%;border-collapse:collapse;font-size:.92rem}
.pricing th,.pricing td{padding:10px;border-bottom:1px solid var(--line);text-align:start}
.pricing th{color:var(--muted);font-size:.8rem}
form.lead{display:grid;gap:10px;margin-top:12px}
form.lead label{font-weight:600;font-size:.9rem}
form.lead input,form.lead textarea,form.lead select{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink);width:100%}
form.lead .msg{font-size:.9rem;font-weight:600}
form.lead .msg.ok{color:#0a7d3c}form.lead .msg.bad{color:var(--r)}
.lang{display:flex;gap:4px}
.lang a{padding:6px 10px;border:1px solid var(--line);border-radius:8px;text-decoration:none;color:var(--ink);font-size:.85rem;background:var(--surface)}
.lang a[aria-current="true"]{background:var(--cold);color:#fff;border-color:transparent}
"""


def _head(title: str, desc: str, url: str, base: str, extra_css: str = "") -> str:
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(url)}">
<meta property="og:type" content="website">
<meta property="og:locale" content="ar_AR">
<meta property="og:site_name" content="طقس بلاد الشام">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(url)}">
<meta property="og:image" content="{e(base)}/static/logo-512.png">
<meta name="twitter:card" content="summary">
<link rel="icon" href="/static/logo.svg" type="image/svg+xml">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0B4F6C">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/ads.css">
<style>{CSS}{extra_css}</style>"""


def _header() -> str:
    return """<header>
  <a class="brand" href="/"><img src="/static/logo.svg" alt="" width="44" height="44"><b>طقس بلاد الشام</b></a>
  <nav>
    <a class="navlink" href="/map">الخريطة</a>
    <a class="navlink" href="/accuracy">دقة التوقعات</a>
    <a class="navlink" href="/advertise">أعلن معنا</a>
  </nav>
</header>"""


def _footer(w=None) -> str:
    src = f"المصدر: {e(w.get('source', ''))}. " if w else ""
    return f"""<footer>
  <p>هذه التوقعات والتنبيهات آلية وتقديرية، وليست بديلاً عن التحذيرات الرسمية من دوائر الأرصاد الجوية والدفاع المدني.</p>
  <p>{src}<a href="/accuracy">لوحة دقة التوقعات وطريقة حسابها</a> · <a href="/privacy">سياسة الخصوصية</a> · <a href="/advertise">أعلن معنا</a></p>
</footer>"""


def render_city_page(w: dict, base: str, cities: dict) -> str:
    c = w["city"]
    name = c["name"]
    slug = c["slug"]
    url = f"{base}/weather/{slug}"
    cur = w.get("current") or {}
    today = (w.get("days") or [{}])[0]
    temp = "–" if cur.get("temp") is None else str(round(cur["temp"]))
    title = f"طقس {name} اليوم وتوقعات 7 أيام | طقس بلاد الشام"
    desc = _description(w)

    alerts_html = "".join(
        f'<div class="alert l{a["level"]}"><b>{e(a["title"])} · {e(a["level_name"])}</b>{e(a["text"])}'
        f'<small>{e(a["when"])} · اتفاق {a["agree_n"]} من {a["total"]} نماذج</small></div>'
        for a in w.get("alerts", [])
    )
    alerts_block = (
        f'<section class="alerts" aria-label="التنبيهات">{alerts_html}</section>'
        if alerts_html else '<p class="muted">لا توجد تنبيهات خلال الـ48 ساعة القادمة.</p>'
    )

    # رعاية موسمية لتنبيهات الطقس (إن وُجدت حملة)
    sponsor = ads.pick("alert_sponsor")  # اختيار فقط؛ لا يُسجَّل ظهور عند رسم الصفحة
    sponsor_html = ""
    if sponsor and sponsor.get("sponsored"):
        sponsor_html = (
            f'<div class="ad-slot" data-placement="alert_sponsor" data-campaign="{sponsor["campaign_id"]}" '
            f'data-creative="{sponsor["creative_id"]}"></div>'
        )

    tips_html = "".join(
        f'<div class="card"><small>{e(t["label"])}</small>{e(t["text"])}</div>' for t in w.get("tips", [])
    )

    rows = "".join(
        f'<tr><td>{e(content.day_label(d["date"], i))}</td><td>{round(d["max"])}°</td><td>{round(d["min"])}°</td>'
        f'<td>{"–" if d["precip"] <= 0 else str(d["precip"]) + " ملم"}</td><td>{e(d["confidence"])}</td></tr>'
        for i, d in enumerate(w.get("days", []))
    )

    other = {}
    for s, ci in cities.items():
        other.setdefault(ci["country"], []).append((s, ci["name"]))
    links = "".join(
        f'<h2>{e(country)}</h2><div class="cities">'
        + "".join(f'<a href="/weather/{e(s)}">طقس {e(n)}</a>' for s, n in items)
        + "</div>"
        for country, items in other.items()
    )

    data = json.dumps(
        {k: w.get(k) for k in ("city", "current", "hours", "days", "alerts", "condition")},
        ensure_ascii=False,
    ).replace("</", "<\\/")

    updated = e(w.get("updated_at", ""))
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
{_head(title, desc, url, base)}
</head>
<body>
{_header()}

<main>
<h1>طقس {e(name)} اليوم</h1>
<section class="hero" style="background:{gradient(w)}">
  <div>{e(name)}، {e(c["country"])}</div>
  <div class="temp">{temp}°</div>
  <div class="meta">
    <span>{e(w.get("condition") or "")}</span>
    <span>العظمى {round(today.get("max", 0))}° · الصغرى {round(today.get("min", 0))}°</span>
    <span>الرياح {cur.get("wind") if cur.get("wind") is not None else "–"} كم/س</span>
  </div>
</section>

{alerts_block}
{sponsor_html}

<div class="ad-slot" data-placement="city_top"></div>

<h2>ملخص اليوم</h2>
<div class="card">
  <p>{e(w.get("summary", ""))}</p>
  <div class="row">
    <button class="btn" id="shareBtn" type="button">شارك بطاقة الطقس</button>
    <a class="navlink" href="/?city={e(slug)}">فعّل إشعارات {e(name)}</a>
  </div>
  <p class="muted" id="shareMsg" role="status"></p>
</div>

<div class="ad-slot" data-placement="city_mid"></div>

<h2>ماذا أفعل اليوم؟</h2>
<div class="tips">{tips_html}</div>

<h2>توقعات {e(name)} للأيام السبعة القادمة</h2>
<div class="wrap">
<table>
  <thead><tr><th>اليوم</th><th>العظمى</th><th>الصغرى</th><th>الأمطار</th><th>الثقة</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
</div>
<p class="muted">الثقة تعبّر عن مدى اتفاق ثلاثة نماذج جوية (ECMWF وGFS وICON). آخر تحديث: <time datetime="{updated}">{updated}</time></p>

<h2>مدن أخرى</h2>
{links}
</main>

{_footer(w)}

<script src="/static/share.js"></script>
<script src="/static/ads.js"></script>
<script>
window.__W = {data};
document.getElementById("shareBtn").addEventListener("click", async function () {{
  var btn = this, msg = document.getElementById("shareMsg");
  btn.disabled = true; msg.textContent = "";
  try {{
    var r = await shareCard(window.__W);
    msg.textContent = r === "downloaded" ? "تم تنزيل البطاقة على جهازك." : "";
  }} catch (err) {{
    msg.textContent = "تعذّر إنشاء البطاقة.";
  }} finally {{ btn.disabled = false; }}
}});
</script>
</body>
</html>"""


def render_error(code: int, message: str) -> str:
    """صفحة خطأ عربية منسّقة بدل JSON خام."""
    titles = {404: "الصفحة غير موجودة", 500: "خطأ في الخادم", 502: "تعذّر جلب البيانات",
              429: "طلبات كثيرة جداً", 413: "الطلب كبير جداً", 401: "يلزم تسجيل الدخول"}
    title = titles.get(code, "حدث خطأ")
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
{_head(f"{title} | طقس بلاد الشام", title, "/", "")}
</head>
<body>
{_header()}
<main>
  <div class="err">
    <div class="code">{code}</div>
    <h1>{e(title)}</h1>
    <p class="muted">{e(message)}</p>
    <div class="row" style="justify-content:center">
      <a class="btn" href="/">العودة إلى الرئيسية</a>
      <a class="navlink" href="/map">الخريطة</a>
    </div>
  </div>
</main>
{_footer()}
</body>
</html>"""


def render_advertise(base: str) -> str:
    """صفحة أعلن معنا (عربي/إنجليزي) بالمقاسات والأسعار بالدينار ونموذج تواصل."""
    rows = "".join(
        f'<tr><td>{e(ads.PLACEMENTS[p["placement"]]["label_ar"])}</td>'
        f'<td>{e(p["size"])}</td><td>{p["cpm"]:.2f} د.أ</td><td>{p["monthly"]} د.أ</td></tr>'
        for p in ads.PRICE_LIST
    )
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
{_head("أعلن معنا | طقس بلاد الشام", "اعرض علامتك أمام زوار طقس بلاد الشام في الأردن وفلسطين ولبنان وسوريا.", f"{base}/advertise", base)}
</head>
<body>
{_header()}
<main>
<h1>أعلن معنا</h1>
<div class="prose">
  <p>طقس بلاد الشام منصة طقس عربية تغطي 25 مدينة في الأردن وفلسطين ولبنان وسوريا،
  بواجهة عربية أولاً وتطبيق ويب يعمل دون تثبيت. نعرض إعلانات في أماكن محددة وواضحة،
  بوسم «إعلان» دائماً، وبمقاسات قياسية لا تُفسد التصميم على الجوال.</p>

  <h2>الأماكن والمقاسات والأسعار</h2>
  <div class="wrap">
  <table class="pricing">
    <thead><tr><th>المكان</th><th>المقاس</th><th>سعر الألف ظهور (CPM)</th><th>باقة شهرية تقديرية</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
  <p class="muted">الأسعار بالدينار الأردني (د.أ) وتشمل الضريبة. الباقة الشهرية تقديرية
  بناءً على حجم الزيارات المتوقع، وتُتفق نهائياً حسب الحملة. تتوفر خصومات للحملات
  الموسمية (رعاية تنبيهات الطقس) وللحزم متعددة الأماكن.</p>

  <h2>صيغ العرض</h2>
  <ul>
    <li><b>بانرات صور:</b> مقاسات IAB قياسية (728×90، 300×250، 336×280، 970×90، 300×600).</li>
    <li><b>إعلانات نصية:</b> عنوان وسطر وصف وزر إجراء — أخفّ وأسرع على الجوال.</li>
    <li><b>رعاية موسمية:</b> رعاية تنبيهات الطقس (مطر، ثلج، رياح، حرارة) باسم علامتك.</li>
  </ul>

  <h2>كيف نعمل</h2>
  <ul>
    <li>نستضيف الإعلان بأنفسنا (Direct) أو عبر شبكة إعلانات معتمدة.</li>
    <li>نُصدر تقريراً بالظهور والنقرات ونسبة النقر (CTR) والإيراد.</li>
    <li>لا نُحمّل أي إعلان قبل موافقة الزائر على ملفات تعريف الارتباط.</li>
  </ul>

  <h2>اطلب عرضاً</h2>
  <form class="lead" id="leadForm" novalidate>
    <label for="lname">الاسم *</label>
    <input id="lname" name="name" required minlength="2" maxlength="120" autocomplete="name">
    <label for="lemail">البريد الإلكتروني *</label>
    <input id="lemail" name="email" type="email" required maxlength="160" autocomplete="email">
    <label for="lcompany">الشركة</label>
    <input id="lcompany" name="company" maxlength="160" autocomplete="organization">
    <label for="lphone">الهاتف</label>
    <input id="lphone" name="phone" maxlength="40" autocomplete="tel" inputmode="tel">
    <label for="lbudget">الميزانية التقديرية</label>
    <select id="lbudget" name="budget">
      <option value="">اختر…</option>
      <option>أقل من 200 د.أ</option>
      <option>200 – 500 د.أ</option>
      <option>500 – 1500 د.أ</option>
      <option>أكثر من 1500 د.أ</option>
    </select>
    <label for="lmsg">رسالتك</label>
    <textarea id="lmsg" name="message" rows="4" maxlength="2000"></textarea>
    <button class="btn" type="submit">أرسل الطلب</button>
    <p class="msg" id="leadMsg" role="status"></p>
  </form>

  <h2>Advertise with us (English)</h2>
  <p>Levant Weather is an Arabic-first weather platform covering 25 cities across Jordan,
  Palestine, Lebanon and Syria. We offer clearly-labelled ads in fixed placements with
  standard IAB sizes, priced in Jordanian Dinar (JOD). Formats: image banners, text ads,
  and seasonal sponsorship of weather alerts. We never load an ad before the visitor
  consents to cookies, and we report impressions, clicks, CTR and revenue.
  Use the form above to request a media kit — we reply within two business days.</p>
</div>
</main>
{_footer()}
<script>
document.getElementById("leadForm").addEventListener("submit", async function (ev) {{
  ev.preventDefault();
  var f = ev.target, msg = document.getElementById("leadMsg");
  msg.className = "msg"; msg.textContent = "";
  var data = Object.fromEntries(new FormData(f).entries());
  if (!data.name || data.name.trim().length < 2) {{ msg.className = "msg bad"; msg.textContent = "الاسم مطلوب."; return; }}
  if (!data.email || !/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(data.email)) {{ msg.className = "msg bad"; msg.textContent = "بريد إلكتروني غير صالح."; return; }}
  try {{
    var r = await fetch("/api/ads/lead", {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(data) }});
    if (!r.ok) {{ var j = await r.json().catch(function(){{return {{}}}}); throw new Error(j.detail || "فشل الإرسال"); }}
    msg.className = "msg ok"; msg.textContent = "تم استلام طلبك، سنتواصل معك قريباً.";
    f.reset();
  }} catch (err) {{
    msg.className = "msg bad"; msg.textContent = err.message || "تعذّر إرسال الطلب، حاول لاحقاً.";
  }}
}});
</script>
</body>
</html>"""


def render_privacy(base: str) -> str:
    if config.ADS_BEFORE_CONSENT:
        consent_line = ("<li><b>الإعلانات من الموقع نفسه تظهر قبل اختيارك،</b> ولا تُستخدم ملفات تعريف ارتباط "
                        "للإعلانات الخارجية قبل موافقتك. يظهر شريط موافقة عند أول زيارة، ويمكنك الرفض "
                        "فتتوقف الإعلانات.</li>")
    else:
        consent_line = ("<li><b>لا نُحمّل أي إعلان قبل موافقتك.</b> يظهر شريط موافقة عند أول زيارة، "
                        "ويمكنك الرفض.</li>")
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
{_head("سياسة الخصوصية | طقس بلاد الشام", "كيف نجمع البيانات ونستخدمها، وملفات تعريف الارتباط والإعلانات.", f"{base}/privacy", base)}
</head>
<body>
{_header()}
<main>
<h1>سياسة الخصوصية</h1>
<div class="prose">
  <p>آخر تحديث: {datetime.now(timezone.utc).strftime("%Y-%m-%d")}. توضّح هذه السياسة كيف
  يتعامل «طقس بلاد الشام» مع بياناتك.</p>

  <h2>ما نجمعه</h2>
  <ul>
    <li><b>لا حساب ولا تسجيل:</b> لا نطلب اسمك أو بريدك لاستخدام الموقع.</li>
    <li><b>الموقع التقريبي:</b> إن منحت المتصفح إذن الموقع، نستخدمه لاختيار أقرب مدينة.
      لا نخزّنه على خادمنا.</li>
    <li><b>اشتراك الإشعارات:</b> إن فعّلت الإشعارات، نخزّن معرّف اشتراك المتصفح والمدينة
      ومستوى التنبيه المطلوب، لإرسال التنبيهات فقط.</li>
    <li><b>بيانات الاستخدام:</b> نعدّ ظهور الإعلانات ونقراتها لأغراض القياس والفوترة. ولمنع الأرقام
      المزوّرة نحسب بصمة مجهولة تتغير يومياً من عنوان الاتصال ونوع المتصفح؛ لا نخزّن عنوانك نفسه
      ولا نستخدم البصمة لتتبعك عبر الأيام أو المواقع.</li>
  </ul>

  <h2>ملفات تعريف الارتباط والإعلانات</h2>
  <ul>
    <li>نستخدم تخزيناً محلياً لحفظ لغتك وموافقتك على ملفات تعريف الارتباط.</li>
    {consent_line}
    <li>الإعلانات موسومة بوضوح بكلمة «إعلان»، ولا تؤثر على المحتوى التحريري.</li>
    <li>قد تستخدم شبكات الإعلانات (مثل Google AdSense) ملفات تعريف ارتباط خاصة بها
      لقياس الأداء أو تخصيص الإعلان؛ يمكنك ضبط تفضيلاتك من إعدادات إعلانات Google.</li>
  </ul>

  <h2>مشاركة البيانات</h2>
  <p>لا نبيع بياناتك. نشارك فقط ما يلزم مع مزوّدي الخدمة: مزوّد بيانات الطقس (Open-Meteo)،
  وخدمة إشعارات المتصفح، وشبكة الإعلانات إن فُعّلت.</p>

  <h2>حقوقك</h2>
  <ul>
    <li>يمكنك إلغاء اشتراك الإشعارات من المتصفح في أي وقت.</li>
    <li>يمكنك مسح تخزين الموقع من إعدادات المتصفح لإزالة اللغة والموافقة.</li>
    <li>لأي طلب يتعلق بالبيانات، راسلنا على {e(config.CONTACT_EMAIL)}.</li>
  </ul>

  <h2>تنبيه مهم</h2>
  <p>التوقعات والتنبيهات في هذا الموقع آلية وتقديرية، وليست بديلاً عن التحذيرات الرسمية
  من دوائر الأرصاد الجوية والدفاع المدني.</p>
</div>
</main>
{_footer()}
</body>
</html>"""


def render_admin_login() -> str:
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
{_head("لوحة الإعلانات | طقس بلاد الشام", "لوحة إدارة الإعلانات", "/admin", "")}
</head>
<body>
{_header()}
<main>
<h1>لوحة الإعلانات</h1>
<div class="prose" style="max-width:420px">
  <p class="muted">أدخل رمز اللوحة (ADMIN_TOKEN). يُضبط في متغيّرات البيئة، أو يُطبع في
  سجل الخادم عند أول تشغيل إن لم يُضبط.</p>
  <form class="lead" id="loginForm">
    <label for="pw">رمز اللوحة</label>
    <input id="pw" type="password" required autocomplete="current-password">
    <button class="btn" type="submit">دخول</button>
    <p class="msg" id="loginMsg" role="status"></p>
  </form>
</div>
</main>
{_footer()}
<script>
document.getElementById("loginForm").addEventListener("submit", async function (ev) {{
  ev.preventDefault();
  var msg = document.getElementById("loginMsg");
  msg.className = "msg"; msg.textContent = "";
  try {{
    var r = await fetch("/admin/login", {{ method: "POST", headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ password: document.getElementById("pw").value }}) }});
    if (!r.ok) {{ var j = await r.json().catch(function(){{return {{}}}}); throw new Error(j.detail || "فشل الدخول"); }}
    location.href = "/admin/dashboard";
  }} catch (err) {{ msg.className = "msg bad"; msg.textContent = err.message; }}
}});
</script>
</body>
</html>"""


def render_admin_dashboard(report: dict, campaigns: list, advertisers: list, leads: list) -> str:
    t = report["totals"]
    camp_rows = "".join(
        f'<tr><td>{e(c["name"])}</td><td>{e(c.get("advertiser_name") or "—")}</td>'
        f'<td>{e(ads.PLACEMENTS.get(c["placement"], {}).get("label_ar", c["placement"]))}</td>'
        f'<td>{e(c["status"])}</td><td>{c["impressions"]}</td><td>{c["clicks"]}</td>'
        f'<td>{c["ctr"]}%</td><td>{c["revenue"]:.2f}</td><td>{c.get("revenue_window", 0):.2f}</td>'
        f'<td><button class="navlink" data-status="{c["id"]}" data-to="{"paused" if c["status"]=="active" else "active"}">'
        f'{"إيقاف" if c["status"]=="active" else "تشغيل"}</button></td></tr>'
        for c in campaigns
    ) or '<tr><td colspan="10" class="muted">لا حملات بعد.</td></tr>'

    adv_rows = "".join(
        f'<tr><td>{e(a["name"])}</td><td>{e(a.get("company") or "—")}</td>'
        f'<td>{e(a.get("email") or "—")}</td><td>{e(a.get("phone") or "—")}</td></tr>'
        for a in advertisers
    ) or '<tr><td colspan="4" class="muted">لا معلنين بعد.</td></tr>'

    lead_rows = "".join(
        f'<tr><td>{e(l.get("name") or "")}</td><td>{e(l.get("email") or "")}</td>'
        f'<td>{e(l.get("company") or "—")}</td><td>{e(l.get("budget") or "—")}</td>'
        f'<td>{e((l.get("message") or "")[:80])}</td></tr>'
        for l in leads
    ) or '<tr><td colspan="5" class="muted">لا طلبات بعد.</td></tr>'

    daily_rows = "".join(
        f'<tr><td>{d["day"]}</td><td>{d["impressions"]}</td><td>{d["clicks"]}</td><td>{d["ctr"]}%</td></tr>'
        for d in report["daily"][-14:]
    )

    placement_opts = "".join(
        f'<option value="{k}">{e(v["label_ar"])} ({e(v["size"])})</option>' for k, v in ads.PLACEMENTS.items()
    )
    advertiser_opts = "".join(f'<option value="{a["id"]}">{e(a["name"])}</option>' for a in advertisers)

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
{_head("لوحة الإعلانات | طقس بلاد الشام", "إدارة الحملات والمعلنين والإيرادات", "/admin/dashboard", "")}
</head>
<body>
{_header()}
<main>
<h1>لوحة الإعلانات</h1>
<div class="row">
  <a class="navlink" href="/admin/report.csv?days=30">تصدير تقرير 30 يوماً (CSV)</a>
  <button class="navlink" id="logoutBtn" type="button">خروج</button>
</div>

<h2>إجماليات آخر {report["days"]} يوماً</h2>
<div class="tips">
  <div class="card"><small>الظهور</small>{t["impressions"]}</div>
  <div class="card"><small>النقرات</small>{t["clicks"]}</div>
  <div class="card"><small>نسبة النقر (CTR)</small>{t["ctr"]}%</div>
  <div class="card"><small>الإيراد (د.أ)</small>{t["revenue_jod"]:.2f}</div>
</div>

<h2>الحملات</h2>
<div class="wrap"><table>
  <thead><tr><th>الحملة</th><th>المعلن</th><th>المكان</th><th>الحالة</th><th>ظهور (إجمالي)</th><th>نقرات (إجمالي)</th><th>CTR</th><th>إيراد إجمالي (د.أ)</th><th>إيراد آخر {report["days"]} يوماً (د.أ)</th><th></th></tr></thead>
  <tbody>{camp_rows}</tbody>
</table></div>

<h2>إضافة حملة</h2>
<div class="prose">
<form class="lead" id="campForm">
  <label>اسم الحملة *</label><input name="name" required minlength="2" maxlength="160">
  <label>المعلن</label><select name="advertiser_id"><option value="">—</option>{advertiser_opts}</select>
  <label>المكان *</label><select name="placement" required>{placement_opts}</select>
  <label>الصيغة</label><select name="format"><option value="image">صورة</option><option value="text">نصية</option><option value="sponsor">رعاية</option></select>
  <label>نموذج التسعير</label><select name="pricing"><option value="cpm">CPM (لكل ألف ظهور)</option><option value="cpc">CPC (لكل نقرة)</option><option value="flat">مبلغ ثابت</option></select>
  <label>السعر (د.أ)</label><input name="rate" type="number" step="0.01" min="0" value="4.5">
  <label>الميزانية (د.أ)</label><input name="budget" type="number" step="0.01" min="0" value="0">
  <label>الحد اليومي للظهور (0 = بلا حد)</label><input name="daily_cap" type="number" min="0" value="0">
  <label>تاريخ البداية</label><input name="start_date" type="date">
  <label>تاريخ النهاية</label><input name="end_date" type="date">
  <label>الأولوية (1-10)</label><input name="priority" type="number" min="1" max="10" value="5">
  <label>رابط الوجهة</label><input name="target_url" maxlength="500" placeholder="https://…">
  <button class="btn" type="submit">أضف الحملة</button>
  <p class="msg" id="campMsg" role="status"></p>
</form>
</div>

<h2>إضافة إبداع لحملة</h2>
<div class="prose">
<form class="lead" id="crForm">
  <label>رقم الحملة *</label><input name="campaign_id" type="number" required min="1">
  <label>النوع</label><select name="kind"><option value="image">صورة</option><option value="text">نصية</option><option value="sponsor">رعاية</option></select>
  <label>رابط الصورة (للبانرات)</label><input name="image_url" maxlength="500" placeholder="https://…">
  <label>العنوان</label><input name="title" maxlength="160">
  <label>الوصف</label><input name="body" maxlength="500">
  <label>زر الإجراء</label><input name="cta" maxlength="60">
  <label>رابط الوجهة</label><input name="target_url" maxlength="500">
  <button class="btn" type="submit">أضف الإبداع</button>
  <p class="msg" id="crMsg" role="status"></p>
</form>
</div>

<h2>المعلنون</h2>
<div class="wrap"><table>
  <thead><tr><th>الاسم</th><th>الشركة</th><th>البريد</th><th>الهاتف</th></tr></thead>
  <tbody>{adv_rows}</tbody>
</table></div>
<div class="prose">
<form class="lead" id="advForm">
  <label>اسم المعلن *</label><input name="name" required minlength="2" maxlength="160">
  <label>الشركة</label><input name="company" maxlength="160">
  <label>البريد</label><input name="email" type="email" maxlength="160">
  <label>الهاتف</label><input name="phone" maxlength="40">
  <button class="btn" type="submit">أضف المعلن</button>
  <p class="msg" id="advMsg" role="status"></p>
</form>
</div>

<h2>طلبات الإعلان (العملاء المحتملون)</h2>
<div class="wrap"><table>
  <thead><tr><th>الاسم</th><th>البريد</th><th>الشركة</th><th>الميزانية</th><th>الرسالة</th></tr></thead>
  <tbody>{lead_rows}</tbody>
</table></div>

<h2>إعدادات الشبكة الإعلانية</h2>
<div class="prose">
<form class="lead" id="setForm">
  <label>معرّف AdSense (مثال: ca-pub-0000000000000000)</label>
  <input name="adsense_client" maxlength="80" value="{e(ads.network_client())}">
  <label>أسطر ads.txt إضافية (سطر لكل جهة، أو مفصولة بـ ;)</label>
  <textarea name="ads_txt_extra" rows="3" maxlength="2000">{e(ads.get_setting("ads_txt_extra"))}</textarea>
  <button class="btn" type="submit">حفظ الإعدادات</button>
  <p class="msg" id="setMsg" role="status"></p>
</form>
<p class="muted">ملف ads.txt الحالي: <a href="/ads.txt" target="_blank">/ads.txt</a></p>
</div>

<h2>آخر 14 يوماً</h2>
<div class="wrap"><table>
  <thead><tr><th>التاريخ</th><th>ظهور</th><th>نقرات</th><th>CTR</th></tr></thead>
  <tbody>{daily_rows}</tbody>
</table></div>
</main>
{_footer()}
<script>
async function post(url, body) {{
  var r = await fetch(url, {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(body) }});
  if (!r.ok) {{ var j = await r.json().catch(function(){{return {{}}}}); throw new Error(j.detail || "فشل الطلب"); }}
  return r.json();
}}
function wire(formId, msgId, url, build) {{
  var f = document.getElementById(formId);
  if (!f) return;
  f.addEventListener("submit", async function (ev) {{
    ev.preventDefault();
    var msg = document.getElementById(msgId);
    msg.className = "msg"; msg.textContent = "";
    try {{
      var data = Object.fromEntries(new FormData(f).entries());
      await post(url, build ? build(data) : data);
      msg.className = "msg ok"; msg.textContent = "تم الحفظ.";
      setTimeout(function(){{ location.reload(); }}, 600);
    }} catch (err) {{ msg.className = "msg bad"; msg.textContent = err.message; }}
  }});
}}
wire("campForm", "campMsg", "/admin/campaigns", function (d) {{
  return {{ name: d.name, placement: d.placement, format: d.format, pricing: d.pricing,
    rate: parseFloat(d.rate || 0), budget: parseFloat(d.budget || 0),
    daily_cap: parseInt(d.daily_cap || 0, 10), priority: parseInt(d.priority || 5, 10),
    start_date: d.start_date || null, end_date: d.end_date || null,
    target_url: d.target_url || "", advertiser_id: d.advertiser_id ? parseInt(d.advertiser_id, 10) : null }};
}});
wire("advForm", "advMsg", "/admin/advertisers");
wire("setForm", "setMsg", "/admin/settings");
document.getElementById("crForm").addEventListener("submit", async function (ev) {{
  ev.preventDefault();
  var msg = document.getElementById("crMsg");
  msg.className = "msg"; msg.textContent = "";
  try {{
    var d = Object.fromEntries(new FormData(ev.target).entries());
    await post("/admin/campaigns/" + parseInt(d.campaign_id, 10) + "/creatives", {{
      kind: d.kind, image_url: d.image_url || "", title: d.title || "",
      body: d.body || "", cta: d.cta || "", target_url: d.target_url || "" }});
    msg.className = "msg ok"; msg.textContent = "تمت الإضافة.";
    setTimeout(function(){{ location.reload(); }}, 600);
  }} catch (err) {{ msg.className = "msg bad"; msg.textContent = err.message; }}
}});
document.querySelectorAll("button[data-status]").forEach(function (b) {{
  b.addEventListener("click", async function () {{
    try {{ await post("/admin/campaigns/" + b.dataset.status + "/status?status=" + b.dataset.to, {{}}); location.reload(); }}
    catch (err) {{ alert(err.message); }}
  }});
}});
document.getElementById("logoutBtn").addEventListener("click", async function () {{
  await fetch("/admin/logout", {{ method: "POST" }}); location.href = "/admin";
}});
</script>
</body>
</html>"""


def render_sitemap(base: str, slugs) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = [(f"{base}/", "hourly", "1.0"), (f"{base}/map", "hourly", "0.7"),
            (f"{base}/accuracy", "daily", "0.6"), (f"{base}/advertise", "weekly", "0.5"),
            (f"{base}/privacy", "monthly", "0.3")]
    urls += [(f"{base}/weather/{s}", "hourly", "0.9") for s in slugs]
    body = "".join(
        f"<url><loc>{e(u)}</loc><lastmod>{today}</lastmod><changefreq>{f}</changefreq><priority>{p}</priority></url>"
        for u, f, p in urls
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'


def render_robots(base: str) -> str:
    return f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /admin\nSitemap: {base}/sitemap.xml\n"
