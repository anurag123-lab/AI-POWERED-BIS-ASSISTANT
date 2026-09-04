/* ai_upgrade.js - progressive enhancement: the page renders the instant
   100%-BIS answer, then this swaps in the 70/30 Gemini answer for the page's
   primary area via POST /api/ai/area. Fails silently -> the BIS answer stands. */
(function () {
  "use strict";
  var area = window.__AI_UPGRADE__;
  if (!area) return;

  var block = document.getElementById("area-" + area) ||
              document.querySelector('[data-area="' + area + '"]');
  if (!block) return;
  var bodyEl = block.querySelector(".area-block__body");
  var badgeEl = block.querySelector(".badge-tag");
  if (!bodyEl) return;

  var pill = document.createElement("span");
  pill.className = "ai-refining";
  pill.textContent = "refining with AI…";
  (block.querySelector(".area-block__meta") || block).appendChild(pill);

  fetch("/api/ai/area", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ area: area })
  })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      pill.remove();
      if (d.status !== "success" || !d.llm_used || !d.body_html) return;
      bodyEl.innerHTML = d.body_html;
      if (badgeEl && d.blend && d.blend.ai) {
        badgeEl.className = "badge-tag badge-emerald";
        badgeEl.textContent = d.blend.bis + "% BIS · " + d.blend.ai + "% AI";
      }
      block.classList.add("ai-upgraded");
    })
    .catch(function () { pill.remove(); });
})();
