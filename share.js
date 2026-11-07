/* بطاقة مشاركة الطقس: تُرسم في المتصفح (فيُشكَّل العربي صحيحاً) ثم تُشارَك أو تُنزَّل كصورة */
(function () {
  const W = 1080, H = 1350;
  const GRADS = {
    rain: ["#22344f", "#4c6a92"],
    cold: ["#0b3c5d", "#1f6f96"],
    mild: ["#0b5c7a", "#2a9aa8"],
    warm: ["#1b6f8a", "#b8730f"],
    hot: ["#a8331a", "#d9650f"],
  };
  const LEVEL_COLORS = { 1: "#e8b100", 2: "#ef7b00", 3: "#d62828" };

  function pickGrad(w) {
    const rainy = (w.hours || []).slice(0, 8).some(h => (h.precip || 0) >= 0.5);
    const t = w.current && w.current.temp;
    if (rainy) return GRADS.rain;
    if (t == null) return GRADS.mild;
    if (t < 8) return GRADS.cold;
    if (t < 18) return GRADS.mild;
    if (t < 28) return GRADS.warm;
    return GRADS.hot;
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function loadImg(src) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null); // نكمل الرسم بدون الشعار إن تعذّر تحميله
      img.src = src;
    });
  }

  function ridge(ctx, pts, alpha) {
    ctx.fillStyle = `rgba(255,255,255,${alpha})`;
    ctx.beginPath();
    ctx.moveTo(0, H);
    pts.forEach(([x, y]) => ctx.lineTo(x, y));
    ctx.lineTo(W, H);
    ctx.closePath();
    ctx.fill();
  }

  async function renderBlob(w) {
    if (document.fonts && document.fonts.load) {
      try { await Promise.all([document.fonts.load("800 80px Cairo"), document.fonts.load("600 40px Cairo")]); } catch (e) {}
    }
    const logo = await loadImg("/static/logo-512.png");
    const cv = document.createElement("canvas");
    cv.width = W; cv.height = H;
    const ctx = cv.getContext("2d");
    const font = (weight, size) => `${weight} ${size}px Cairo, system-ui, sans-serif`;
    const R = W - 80; // الحافة اليمنى (الصفحة RTL)

    // الخلفية
    const [c1, c2] = pickGrad(w);
    const g = ctx.createLinearGradient(0, 0, W * 0.6, H);
    g.addColorStop(0, c1); g.addColorStop(1, c2);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    // الجبال
    ridge(ctx, [[0, 1120], [160, 1040], [300, 1100], [480, 1000], [660, 1110], [840, 1030], [1000, 1090], [W, 1050]], 0.12);
    ridge(ctx, [[0, 1190], [200, 1110], [380, 1170], [560, 1090], [760, 1180], [920, 1120], [W, 1160]], 0.18);

    // الرأس: الشعار والاسم
    if (logo) {
      ctx.save();
      roundRect(ctx, R - 110, 70, 110, 110, 30);
      ctx.clip();
      ctx.drawImage(logo, R - 110, 70, 110, 110);
      ctx.restore();
    }
    ctx.direction = "rtl"; ctx.textAlign = "right"; ctx.textBaseline = "alphabetic";
    ctx.fillStyle = "#ffffff";
    ctx.font = font(800, 46);
    ctx.fillText("طقس بلاد الشام", R - 134, 140);

    // التاريخ
    const now = new Date();
    const dateStr = now.toLocaleDateString("ar-JO-u-nu-latn", { weekday: "long", day: "numeric", month: "long" });
    ctx.font = font(600, 40);
    ctx.fillStyle = "rgba(255,255,255,.88)";
    ctx.fillText(dateStr, R, 270);

    // المدينة
    ctx.fillStyle = "#ffffff";
    ctx.font = font(800, 120);
    ctx.fillText(w.city.name, R, 410);
    ctx.font = font(600, 40);
    ctx.fillStyle = "rgba(255,255,255,.85)";
    ctx.fillText(w.city.country, R, 470);

    // الحرارة (أرقام: اتجاه LTR حتى تبقى علامة الدرجة بجوار الرقم)
    const cur = w.current || {};
    ctx.direction = "ltr"; ctx.textAlign = "right";
    ctx.fillStyle = "#ffffff";
    ctx.font = font(800, 340);
    ctx.fillText(cur.temp == null ? "–" : Math.round(cur.temp) + "°", R, 800);

    // الحالة والعظمى والصغرى
    ctx.direction = "rtl"; ctx.textAlign = "right";
    if (w.condition) {
      ctx.font = font(600, 56);
      ctx.fillText(w.condition, R, 890);
    }
    const d0 = (w.days || [])[0];
    if (d0) {
      ctx.font = font(600, 48);
      ctx.fillStyle = "rgba(255,255,255,.92)";
      ctx.fillText(`العظمى ${Math.round(d0.max)}° · الصغرى ${Math.round(d0.min)}°`, R, 960);
    }

    // شارة التنبيه الأهم
    const a = (w.alerts || [])[0];
    if (a) {
      const label = `تنبيه ${a.level_name}: ${a.title}`;
      ctx.font = font(800, 42);
      const tw = Math.min(ctx.measureText(label).width + 70, W - 160);
      roundRect(ctx, R - tw, 1005, tw, 82, 41);
      ctx.fillStyle = LEVEL_COLORS[a.level] || "#e8b100";
      ctx.fill();
      ctx.fillStyle = a.level === 3 ? "#ffffff" : "#2b1b00";
      ctx.textAlign = "center";
      ctx.fillText(label, R - tw / 2, 1062, tw - 40);
      ctx.textAlign = "right";
    }

    // الأيام القادمة
    const next = (w.days || []).slice(1, 5);
    const colW = (W - 160) / Math.max(next.length, 1);
    next.forEach((d, i) => {
      const cx = R - colW * i - colW / 2;
      const name = new Date(d.date + "T12:00").toLocaleDateString("ar-JO-u-nu-latn", { weekday: "short" });
      ctx.textAlign = "center";
      ctx.direction = "rtl";
      ctx.fillStyle = "rgba(255,255,255,.9)";
      ctx.font = font(600, 38);
      ctx.fillText(name, cx, 1225);
      ctx.direction = "ltr";
      ctx.fillStyle = "#ffffff";
      ctx.font = font(800, 42);
      ctx.fillText(`${Math.round(d.max)}° / ${Math.round(d.min)}°`, cx, 1280);
    });

    // التذييل
    ctx.direction = "rtl"; ctx.textAlign = "center";
    ctx.fillStyle = "rgba(255,255,255,.75)";
    ctx.font = font(600, 30);
    const host = location.hostname;
    const local = !host || host === "localhost" || /^[0-9.]+$/.test(host);
    ctx.fillText(local ? "طقس بلاد الشام" : `طقس بلاد الشام · ${host}`, W / 2, 1325);

    return new Promise((resolve, reject) => {
      cv.toBlob(b => (b ? resolve(b) : reject(new Error("تعذّر إنشاء الصورة"))), "image/png");
    });
  }

  async function shareCard(w) {
    const blob = await renderBlob(w);
    const file = new File([blob], `taqs-${w.city.slug}.png`, { type: "image/png" });
    const url = `${location.origin}/weather/${w.city.slug}`;
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({ files: [file], title: `طقس ${w.city.name}`, text: `طقس ${w.city.name} اليوم`, url });
        return "shared";
      } catch (e) {
        if (e && e.name === "AbortError") return "cancelled";
      }
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = file.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    return "downloaded";
  }

  window.shareCard = shareCard;
  window.renderShareBlob = renderBlob;
})();
