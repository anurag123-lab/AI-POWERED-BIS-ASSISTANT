/* testing_labs.js - state filter, Google Maps directions, enquiry -> completion card */
(function () {
  "use strict";

  // ---- state filter ----
  var filter = document.getElementById("labStateFilter");
  var grid = document.getElementById("labGrid");
  function applyFilter() {
    var want = (filter && filter.value || "").toLowerCase();
    grid.querySelectorAll(".lab-card").forEach(function (c) {
      var st = (c.dataset.state || "").toLowerCase();
      c.style.display = (!want || st === want) ? "" : "none";
    });
  }
  if (filter) { filter.addEventListener("change", applyFilter); applyFilter(); }

  // ---- directions from my location ----
  grid.addEventListener("click", function (e) {
    var b = e.target.closest(".lab-directions");
    if (!b) return;
    var dest = b.dataset.dest;
    var plain = "https://www.google.com/maps/search/?api=1&query=" + dest;
    if (!navigator.geolocation) { window.open(plain, "_blank"); return; }
    b.disabled = true; b.textContent = "Locating…";
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        b.disabled = false; b.textContent = "Directions from my location";
        var o = pos.coords.latitude + "," + pos.coords.longitude;
        window.open("https://www.google.com/maps/dir/?api=1&origin=" + o + "&destination=" + dest, "_blank");
      },
      function () {
        b.disabled = false; b.textContent = "Directions from my location";
        window.open(plain, "_blank");
      },
      { timeout: 8000 }
    );
  });

  // ---- enquiry modal ----
  var modal = document.getElementById("enquiryModal");
  var done = document.getElementById("enquiryDone");
  var toEl = document.getElementById("enqTo");
  var subEl = document.getElementById("enqSubject");
  var bodyEl = document.getElementById("enqBody");
  var current = { name: "", email: "" };
  var PRODUCT = document.querySelector(".product-chip strong");
  var pname = PRODUCT ? PRODUCT.textContent.trim() : "our product";
  var isnum = (function () {
    var m = document.querySelector(".product-chip");
    var t = m ? m.textContent : "";
    var g = t.match(/IS\s[\d.\-()A-Za-z ]+/);
    return g ? g[0].trim() : "";
  })();

  function openModal(name, email) {
    current = { name: name, email: email };
    toEl.textContent = name + " <" + email + ">";
    subEl.value = "BIS type-testing enquiry - " + pname + (isnum ? " (" + isnum + ")" : "");
    bodyEl.value =
      "Dear " + name + " team,\n\n" +
      "We are seeking BIS type testing for our product: " + pname +
      (isnum ? " under Indian Standard " + isnum : "") + ".\n\n" +
      "Please share:\n" +
      "1. Sample quantity and form required\n" +
      "2. Test fee schedule and turnaround time\n" +
      "3. Document / sample submission format\n\n" +
      "Regards,\nCompliance team";
    modal.classList.add("active");
  }
  function closeModal() { modal.classList.remove("active"); }

  grid.addEventListener("click", function (e) {
    var b = e.target.closest(".lab-enquiry");
    if (b) openModal(b.dataset.name, b.dataset.email);
  });
  document.getElementById("enquiryClose").addEventListener("click", closeModal);
  document.getElementById("enquiryCancel").addEventListener("click", closeModal);

  document.getElementById("enquirySend").addEventListener("click", function () {
    var btn = this;
    btn.disabled = true; btn.textContent = "Sending…";
    fetch("/api/labs/enquiry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lab_name: current.name, lab_email: current.email,
        subject: subEl.value, body: bodyEl.value
      })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        btn.disabled = false; btn.textContent = "Approve & send enquiry";
        if (d.status !== "success") { alert(d.message || "Could not send."); return; }
        closeModal();
        document.getElementById("doneLab").textContent = " to " + d.lab_name;
        document.getElementById("doneCopy").textContent =
          d.cc_user ? "A copy has been emailed to you." : "The enquiry was sent to the lab.";
        done.classList.add("active");
        var n = 4;
        var t = setInterval(function () {
          n -= 1;
          document.getElementById("rdct").textContent = n;
          if (n <= 0) { clearInterval(t); window.location.href = "/testing-labs"; }
        }, 1000);
      })
      .catch(function () {
        btn.disabled = false; btn.textContent = "Approve & send enquiry";
        alert("Network error while sending the enquiry.");
      });
  });
})();
