/*!
 * ads.js - طقس بلاد الشام
 * طبقة الإعلانات في الواجهة: موافقة الخصوصية، تحميل الإعلانات، وسمها،
 * وعدّ الظهور والنقرات. لا يُحمَّل أي إعلان قبل موافقة المستخدم.
 */
(function () {
  "use strict";

  var CONSENT_KEY = "lw_consent";
  var ADS_BEFORE_CONSENT = false; // يطابق config.ADS_BEFORE_CONSENT افتراضياً

  function getConsent() {
    try { return localStorage.getItem(CONSENT_KEY); } catch (e) { return null; }
  }
  function setConsent(v) {
    try { localStorage.setItem(CONSENT_KEY, v); } catch (e) {}
  }

  /* ---------- شريط الموافقة ---------- */
  function showConsentBar() {
    if (getConsent()) return;
    var bar = document.createElement("div");
    bar.className = "lw-consent";
    bar.setAttribute("role", "dialog");
    bar.setAttribute("aria-label", "إشعار ملفات تعريف الارتباط");
    bar.innerHTML =
      '<p>نستخدم ملفات تعريف الارتباط لعرض إعلانات مناسبة وقياس أدائها. ' +
      'لا نُحمّل أي إعلان قبل موافقتك. <a href="/privacy">سياسة الخصوصية</a></p>' +
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

  function trackImpression(ad) {
    if (!ad.campaign_id) return;
    try {
      fetch("/api/ads/impression", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          campaign_id: ad.campaign_id,
          creative_id: ad.creative_id,
          placement: ad.placement,
          page: location.pathname
        }),
        keepalive: true
      }).catch(function () {});
    } catch (e) {}
  }

  function clickUrl(ad) {
    return "/api/ads/click?c=" + encodeURIComponent(ad.campaign_id) +
      "&cr=" + encodeURIComponent(ad.creative_id) +
      "&p=" + encodeURIComponent(ad.placement) +
      "&u=" + encodeURIComponent(ad.target_url || "/");
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
    trackImpression(ad);
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
    var consent = getConsent();
    if (consent === "yes" || (ADS_BEFORE_CONSENT && consent !== "no")) {
      loadAll();
    } else if (!consent) {
      showConsentBar();
    }
    // إعادة المحاولة عند تغيّر المحتوى (مثل تبديل المدينة)
    document.addEventListener("lw:ads-refresh", loadAll);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.lwAds = { loadAll: loadAll, getConsent: getConsent, setConsent: setConsent };
})();
