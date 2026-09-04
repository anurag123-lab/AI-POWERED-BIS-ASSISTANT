/* home.js - AI Assistant + search history for the personalised BIS workspace */
(function () {
  "use strict";

  var thread = document.getElementById("chatThread");
  var form = document.getElementById("chatForm");
  var input = document.getElementById("chatInput");
  var answerArea = document.getElementById("answerArea");
  var historyList = document.getElementById("historyList");
  var clearBtn = document.getElementById("clearHistory");

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // minimal markdown: **bold**, *em*, `code`, [t](url), - lists, blank-line paragraphs
  function md(text) {
    if (!text) return "";
    return text.trim().split(/\n{2,}/).map(function (block) {
      var lines = block.split("\n");
      var isList = lines.every(function (l) { return !l.trim() || /^\s*[-*]\s+/.test(l); });
      function inline(s) {
        s = esc(s);
        s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
          '<a href="$2" target="_blank" rel="noopener">$1</a>');
        s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        s = s.replace(/(^|[^*])\*([^*]+)\*([^*]|$)/g, "$1<em>$2</em>$3");
        s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
        return s;
      }
      if (isList) {
        return "<ul>" + lines.filter(function (l) { return l.trim(); })
          .map(function (l) { return "<li>" + inline(l.replace(/^\s*[-*]\s+/, "")) + "</li>"; })
          .join("") + "</ul>";
      }
      return "<p>" + lines.map(inline).join("<br>") + "</p>";
    }).join("");
  }

  function bubble(kind, html, extraClass) {
    var el = document.createElement("div");
    el.className = "bubble " + kind + (extraClass ? " " + extraClass : "");
    el.innerHTML = html;
    thread.appendChild(el);
    el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return el;
  }

  function sourcesHtml(sources) {
    if (!sources || !sources.length) return "";
    return '<div class="src">' + sources.slice(0, 4).map(function (s) {
      var bits = [esc(s.doc || "")];
      if (s.page) bits.push("p." + esc(s.page));
      if (s.clause) bits.push("cl." + esc(s.clause));
      var t = bits.join(" &bull; ");
      return s.url ? t + ' &mdash; <a href="' + esc(s.url) + '" target="_blank" rel="noopener">View Source</a>' : t;
    }).join("<br>") + "</div>";
  }

  function renderCard(a) {
    var el = document.getElementById("card-" + a.area);
    var badge = a.llm_used
      ? '<span class="badge-tag badge-emerald">AI-refined</span>'
      : '<span class="badge-tag badge-muted">From BIS KB</span>';
    var srcBlock = "";
    if (a.sources && a.sources.length) {
      srcBlock = '<details class="answer-card__sources"><summary>' + a.sources.length +
        ' BIS source' + (a.sources.length === 1 ? "" : "s") + '</summary><ul>' +
        a.sources.map(function (s) {
          var bits = [esc(s.doc || "")];
          if (s.page) bits.push("p." + esc(s.page));
          if (s.clause) bits.push("cl." + esc(s.clause));
          var line = bits.join(" &bull; ");
          if (s.url) line += ' &mdash; <a href="' + esc(s.url) + '" target="_blank" rel="noopener">View Source &nearr;</a>';
          return "<li>" + line + "</li>";
        }).join("") + "</ul></details>";
    }
    var html =
      '<div class="answer-card__head"><h3>' + esc(a.title) + "</h3>" + badge + "</div>" +
      '<div class="answer-card__body">' + md(a.body_md) + "</div>" + srcBlock;
    if (!el) {
      el = document.createElement("article");
      el.className = "answer-card";
      el.id = "card-" + a.area;
      answerArea.appendChild(el);
    }
    el.innerHTML = html;
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
  }

  function handleResult(data) {
    if (data.mode === "refused") {
      var a = data.answer || {};
      bubble("bot", "<strong>" + esc(a.title || "Not covered") + "</strong>" + md(a.body_md || ""), "refused");
      return;
    }
    if (data.mode === "seven") {
      bubble("bot", "Here is the full picture for <strong>" + esc(data.product_name || "your product") +
        "</strong> - the 7 cards below are updated.");
      (data.answers || []).forEach(renderCard);
      return;
    }
    // area
    (data.answers || []).forEach(function (a) {
      bubble("bot", "<strong>" + esc(a.title) + "</strong>" + md(a.body_md) + sourcesHtml(a.sources));
      renderCard(a);
    });
  }

  function ask(q) {
    if (!q.trim()) return;
    bubble("user", esc(q));
    input.value = "";
    var loading = bubble("bot", "&hellip; checking the BIS knowledge base");
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: q })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        loading.remove();
        if (data.status === "success") { handleResult(data); loadHistory(); }
        else bubble("bot", "Sorry, something went wrong.", "refused");
      })
      .catch(function () { loading.remove(); bubble("bot", "Network error.", "refused"); });
  }

  if (form) {
    form.addEventListener("submit", function (e) { e.preventDefault(); ask(input.value); });
  }
  document.querySelectorAll(".suggest-chip").forEach(function (c) {
    c.addEventListener("click", function () { ask(c.textContent); });
  });

  // ---- history ----
  function loadHistory() {
    fetch("/api/history").then(function (r) { return r.json(); }).then(function (d) {
      if (d.status !== "success") return;
      if (!d.items.length) {
        historyList.innerHTML = '<li class="history-empty">Ask the assistant anything about your product - your questions appear here.</li>';
        return;
      }
      historyList.innerHTML = d.items.map(function (h) {
        return '<li class="history-item" data-id="' + h.id + '"><span class="hi-q">' + esc(h.query) +
          '</span><span class="hi-meta">' + esc(h.mode || "") + (h.area ? " &bull; " + esc(h.area) : "") + "</span></li>";
      }).join("");
    });
  }

  if (historyList) {
    historyList.addEventListener("click", function (e) {
      var li = e.target.closest(".history-item");
      if (!li) return;
      fetch("/api/history/" + li.dataset.id).then(function (r) { return r.json(); }).then(function (d) {
        if (d.status !== "success") return;
        var it = d.item;
        bubble("user", esc(it.query));
        bubble("bot", md(it.answer_md || "") + sourcesHtml(it.sources));
      });
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      fetch("/api/history", { method: "DELETE" }).then(function () { loadHistory(); });
    });
  }
})();
