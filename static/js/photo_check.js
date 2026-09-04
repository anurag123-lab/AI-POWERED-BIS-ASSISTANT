/* photo_check.js - ISI mark / hallmark regulatory layout check */
(function () {
  "use strict";
  var file = document.getElementById("pcFile");
  var fileName = document.getElementById("pcFileName");
  var text = document.getElementById("pcText");
  var runBtn = document.getElementById("pcRun");
  var result = document.getElementById("pcResult");

  document.querySelector(".pc-drop").addEventListener("click", function () { file.click(); });
  file.addEventListener("change", function () {
    fileName.textContent = file.files[0] ? file.files[0].name : "No file selected";
  });

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  runBtn.addEventListener("click", function () {
    var payload = (file.files[0] && file.files[0].name) || text.value || "";
    result.innerHTML = '<p class="area-block__body muted">Running check&hellip;</p>';
    fetch("/api/isi/photo-check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: payload })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var a = d.analysis || {};
        var rows = (a.compliance_checks || []).map(function (c) {
          return '<li>' + (c.present ? "&#9989;" : "&#10060;") + " <strong>" + esc(c.element) +
            '</strong><div class="pc-cite">' + esc(c.regulation_citation) + "</div></li>";
        }).join("");
        result.innerHTML =
          '<h3 class="pc-status">' + esc(a.status || "Checked") + "</h3>" +
          '<ul class="pc-list">' + rows + "</ul>" +
          '<div class="pc-disclaimer">' + esc(a.disclaimer || "") +
          (a.bis_care_app_url
            ? ' <a href="' + esc(a.bis_care_app_url) + '" target="_blank" rel="noopener">BIS Care &nearr;</a>'
            : "") + "</div>";
      })
      .catch(function () {
        result.innerHTML = '<p class="area-block__body" style="color:#f87171">Could not run the check.</p>';
      });
  });
})();
