/* feature.js - shared "Mark reviewed" behaviour for KB feature pages */
(function () {
  "use strict";
  document.querySelectorAll(".save-area-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var area = btn.dataset.area;
      var done = btn.classList.contains("is-done") || /Reviewed/.test(btn.textContent);
      var next = done ? "Not Started" : "Reviewed";
      btn.disabled = true;
      fetch("/api/case/save-area", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ area: area, status: next })
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          btn.disabled = false;
          if (d.status !== "success") return;
          if (d.new_status === "Reviewed" || d.new_status === "Completed") {
            btn.classList.add("is-done");
            btn.textContent = "Reviewed ✓";
          } else {
            btn.classList.remove("is-done");
            btn.textContent = "Mark reviewed";
          }
          var badge = document.getElementById("checklistCount");
          if (badge) badge.textContent = d.reviewed + " / " + d.total;
        })
        .catch(function () { btn.disabled = false; });
    });
  });
})();
