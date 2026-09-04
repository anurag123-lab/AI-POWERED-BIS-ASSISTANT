/* ask_anything.js - AI orchestrator UI: intent -> route -> navigate | answer */
(function () {
  "use strict";
  var root = document.querySelector(".ask-anything");
  if (!root || root.dataset.wired) return;
  root.dataset.wired = "1";

  var form = root.querySelector(".ask-form");
  var input = root.querySelector(".ask-input");
  var box = root.querySelector(".ask-result");
  var service = root.dataset.service || "";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function mdlite(s) {
    return esc(s).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>");
  }
  function show(html, cls) {
    box.hidden = false;
    box.className = "ask-result" + (cls ? " " + cls : "");
    box.innerHTML = html;
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (!q) return;
    show("&hellip; thinking");
    fetch("/api/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: q, service: service })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.status !== "success") { show("Something went wrong.", "err"); return; }
        var tag = '<span class="ask-intent">intent: ' + esc(d.intent) +
          "  &bull;  action: " + esc(d.action) + "</span>";
        if (d.action === "navigate" && d.target_url) {
          show(tag + "<p>" + mdlite(d.response) + "</p>", "nav");
          setTimeout(function () { window.location.href = d.target_url; }, 700);
        } else if (d.action === "unsupported") {
          show(tag + "<p>" + mdlite(d.response) + "</p>", "err");
        } else {
          show(tag + "<p>" + mdlite(d.response) + "</p>");
        }
      })
      .catch(function () { show("Network error.", "err"); });
  });
})();
