/*!
 * ads.js - طقس بلاد الشام
 * طبقة الإعلانات في الواجهة: موافقة الخصوصية، تحميل الإعلانات، ووسمها.
 * الظهور يحتسبه الخادم عند التقديم، والنقرة تمرّ برابط يحمل رمزاً موقَّعاً؛ لا يرسل هذا الملف أي أرقام.
 * لا يُحمَّل أي إعلان قبل موافقة المستخدم، إلا إن فعّل مدير الموقع ADS_BEFORE_CONSENT على الخادم.
 */
(function () {
  "use strict";

  var CONSENT_KEY = "lw_consent";

  function getConsent() {
    try { return localStorage.getItem(CONSENT_KEY); } catch (e) { return null; }
  }
  function setConsent(v) {
    try { localStorage.setItem(CONSENT_KEY, v); } catch (e) {}
  }

  /* ---------- شريط الموافقة ---------- */
  function showConsentBar(beforeConsent) {
    if (getConsent()) return;
    var bar = document.createElement("div");
    bar.className = "lw-consent";
    bar.setAttribute("role", "dialog");
    bar.setAttribute("aria-label", "إشعار ملفات تعريف الارتباط");
    var text = beforeConsent
      ? "نعرض إعلانات ونقيس ظهورها ونقراتها دون تخزين عنوانك، ويمكنك رفض الإعلانات."
      : "نستخدم ملفات تعريف الارتباط لعرض إعلانات مناسبة وقياس أدائها. لا نُحمّل أي إعلان قبل موافقتك.";
    bar.innerHTML =
      '<p>' + text + ' <a href="/privacy">سياسة الخصوصية</a></p>' +
      '<div class="lw-consent-actions">' +
      '<button type="button" data-consent="yes">أوافق</button>' +
      '<button type="button" data-consent="no" class="ghost">رفض</button>' +
      "</div>";
    document.body.appendChild(bar);
    bar.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-consent]");
      if (!b) return;
      setConsent(b.dataset.consent);
      bar.remove();
      if (b.dataset.consent === "yes") loadAll();
    });
  }

  /* ---------- تحميل إعلان في مكان ---------- */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // رابط النقرة يأتي من الخادم (رمز موقَّع). لا نقبل إلا مساراً داخلياً من موقعنا.
  function clickUrl(ad) {
    var u = ad.click_url;
    return (typeof u === "string" && u.charAt(0) === "/" && u.charAt(1) !== "/") ? u : "/advertise";
  }

  function renderAd(slot, ad) {
    var label = '<span class="lw-ad-label">' + esc(ad.label || "إعلان") + "</span>";
    var inner;
    if (ad.kind === "image" && ad.image_url) {
      inner =
        '<a class="lw-ad-link" href="' + esc(clickUrl(ad)) + '" rel="nofollow sponsored noopener" target="_blank">' +
        '<img src="' + esc(ad.image_url) + '" alt="' + esc(ad.title || "إعلان") + '" ' +
        'width="' + (ad.width || "") + '" height="' + (ad.height || "") + '" loading="lazy">' +
        "</a>";
    } else {
      inner =
        '<a class="lw-ad-link lw-ad-text" href="' + esc(clickUrl(ad)) + '" rel="nofollow sponsored noopener" target="_blank">' +
        (ad.title ? '<b>' + esc(ad.title) + "</b>" : "") +
        (ad.body ? "<span>" + esc(ad.body) + "</span>" : "") +
        (ad.cta ? '<em class="lw-ad-cta">' + esc(ad.cta) + "</em>" : "") +
        "</a>";
    }
    slot.innerHTML = label + inner;
    slot.classList.add("lw-ad-filled");
    slot.dataset.placement = ad.placement;
    slot.dataset.kind = ad.kind;
  }

  function loadSlot(slot) {
    var placement = slot.dataset.placement;
    if (!placement) return;
    fetch("/api/ads/serve?placement=" + encodeURIComponent(placement) +
      "&page=" + encodeURIComponent(location.pathname))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (ad) { if (ad && ad.placement) renderAd(slot, ad); })
      .catch(function () {});
  }

  function loadAll() {
    var slots = document.querySelectorAll(".ad-slot:not(.lw-ad-filled)");
    for (var i = 0; i < slots.length; i++) loadSlot(slots[i]);
  }

  /* ---------- التهيئة ---------- */
  function init() {
    // إعادة المحاولة عند تغيّر المحتوى (مثل تبديل المدينة)
    document.addEventListener("lw:ads-refresh", loadAll);
    var consent = getConsent();
    if (consent === "yes") { loadAll(); return; }
    if (consent === "no") return;
    // لا قرار بعد: الخادم يحدد هل تُعرض الإعلانات قبل الموافقة (ADS_BEFORE_CONSENT)
    fetch("/api/ads/config")
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (cfg) {
        var before = !!(cfg && cfg.before_consent);
        if (before) loadAll();
        showConsentBar(before);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.lwAds = { loadAll: loadAll, getConsent: getConsent, setConsent: setConsent };
})();
