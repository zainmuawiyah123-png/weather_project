"""صفحات المدن (تُرسم على الخادم ليقرأها محرك البحث) + sitemap وrobots."""
import html
import json
from datetime import datetime, timezone

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
    """وصف بحثي بحدود 155 حرفاً من أول جملتين من الملخص."""
    sentences = [s.strip() for s in (w.get("summary") or "").split(".") if s.strip()]
    out = ""
    for s in sentences:
        nxt = (out + " " + s + ".").strip()
        if len(nxt) > 155:
            break
        out = nxt
    return out or f"توقعات طقس {w['city']['name']} اليوم والأيام الثلاثة القادمة."


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
.ad-slot{display:block;margin:14px 0;padding:16px;text-align:center;border:1px dashed var(--cold);border-radius:14px;background:var(--surface);color:var(--muted);text-decoration:none}.ad-slot strong{display:block;color:var(--ink);font-size:.95rem}.ad-slot span{display:block;font-size:.78rem;margin-top:3px}
"""


def render_city_page(w: dict, base: str, cities: dict) -> str:
    c = w["city"]
    name = c["name"]
    slug = c["slug"]
    url = f"{base}/weather/{slug}"
    cur = w.get("current") or {}
    today = (w.get("days") or [{}])[0]
    temp = "–" if cur.get("temp") is None else str(round(cur["temp"]))
    title = f"طقس {name} اليوم وتوقعات 3 أيام | طقس بلاد الشام"
    desc = _description(w)

    alerts_html = "".join(
        f'<div class="alert l{a["level"]}"><b>{e(a["title"])} · {e(a["level_name"])}</b>{e(a["text"])}'
        f'<small>{e(a["when"])} · ' + (f'اتفاق {a["agree_n"]} من {a["total"]} نماذج' if a["total"] >= 2 else 'تقدير آلي من مصدر واحد') + '</small></div>'
        for a in w.get("alerts", [])
    )
    alerts_block = (
        f'<section class="alerts" aria-label="التنبيهات">{alerts_html}</section>'
        if alerts_html else '<p class="muted">لا توجد تنبيهات خلال الـ48 ساعة القادمة.</p>'
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

    # بيانات البطاقة تُضمَّن في الصفحة؛ نهرّب "</" حتى لا تغلق وسم السكربت
    data = json.dumps(
        {k: w.get(k) for k in ("city", "current", "hours", "days", "alerts", "condition")},
        ensure_ascii=False,
    ).replace("</", "<\\/")

    updated = e(w.get("updated_at", ""))
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
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
<style>{CSS}</style>
</head>
<body>
<header>
  <a class="brand" href="/"><img src="/static/logo.svg" alt="" width="44" height="44"><b>طقس بلاد الشام</b></a>
  <nav><a class="navlink" href="/map">الخريطة</a><a class="navlink" href="/accuracy">دقة التوقعات</a></nav>
</header>

<main>
<h1>طقس {e(name)} اليوم</h1>
<a class="ad-slot" href="/advertise"><strong>مساحة إعلانية متاحة في {e(name)}</strong><span>رعاية محلية تصل إلى المهتمين بطقس المدينة</span></a>
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

<h2>ملخص اليوم</h2>
<div class="card">
  <p>{e(w.get("summary", ""))}</p>
  <div class="row">
    <button class="btn" id="shareBtn" type="button">شارك بطاقة الطقس</button>
    <a class="navlink" href="/?city={e(slug)}">فعّل إشعارات {e(name)}</a>
  </div>
  <p class="muted" id="shareMsg" role="status"></p>
</div>

<h2>ماذا أفعل اليوم؟</h2>
<div class="tips">{tips_html}</div>

<h2>توقعات {e(name)} للأيام الثلاثة القادمة</h2>
<div class="wrap">
<table>
  <thead><tr><th>اليوم</th><th>العظمى</th><th>الصغرى</th><th>الأمطار</th><th>الثقة</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
</div>
<p class="muted">تعتمد درجة الثقة على مقارنة مصادر جوية متعددة؛ وعند توفر مصدر واحد فقط لا يمكن قياس التوافق، فتظهر «غير متوفرة». آخر تحديث: <time datetime="{updated}">{updated}</time></p>

<h2>مدن أخرى</h2>
{links}
</main>

<footer>
  <p><a href="/advertise">أعلن معنا · احجز مساحة إعلانية</a></p>
  <p>هذه التوقعات والتنبيهات آلية وتقديرية، وليست بديلاً عن التحذيرات الرسمية من دوائر الأرصاد الجوية والدفاع المدني.</p>
  <p>المصدر: {e(w.get("source", ""))}. <a href="/accuracy">لوحة دقة التوقعات وطريقة حسابها</a></p>
</footer>

<script src="/static/share.js"></script>
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


def render_sitemap(base: str, slugs) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = [(f"{base}/", "hourly", "1.0"), (f"{base}/map", "hourly", "0.7"), (f"{base}/accuracy", "daily", "0.6")]
    urls += [(f"{base}/weather/{s}", "hourly", "0.9") for s in slugs]
    body = "".join(
        f"<url><loc>{e(u)}</loc><lastmod>{today}</lastmod><changefreq>{f}</changefreq><priority>{p}</priority></url>"
        for u, f, p in urls
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'


def render_robots(base: str) -> str:
    return f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {base}/sitemap.xml\n"
