"use strict";

(function () {
  var LS_KEY = "sa_key";       // base64 raw AES key, cached after a correct login on this device
  var LS_FAIL = "sa_fail";     // { count, lastAt } failed-attempt tracker for client-side lockout
  var root = document.getElementById("root");

  function b64ToBytes(b64) {
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }
  function bytesToB64(bytes) {
    var bin = "";
    var arr = new Uint8Array(bytes);
    for (var i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
    return btoa(bin);
  }

  function readFail() {
    try { return JSON.parse(localStorage.getItem(LS_FAIL)) || { count: 0, lastAt: 0 }; }
    catch (e) { return { count: 0, lastAt: 0 }; }
  }
  function writeFail(state) {
    try { localStorage.setItem(LS_FAIL, JSON.stringify(state)); } catch (e) {}
  }
  function lockoutRemainingMs() {
    var state = readFail();
    if (state.count <= 3) return 0;
    var delaySec = Math.min(30, Math.pow(2, state.count - 3));
    var readyAt = state.lastAt + delaySec * 1000;
    return Math.max(0, readyAt - Date.now());
  }

  async function deriveKey(id, password, saltB64, iterations) {
    var enc = new TextEncoder();
    var keyMaterial = await crypto.subtle.importKey(
      "raw", enc.encode(id + password), "PBKDF2", false, ["deriveKey"]
    );
    return crypto.subtle.deriveKey(
      { name: "PBKDF2", salt: b64ToBytes(saltB64), iterations: iterations, hash: "SHA-256" },
      keyMaterial, { name: "AES-GCM", length: 256 }, true, ["decrypt"]
    );
  }

  async function tryDecrypt(vault, key) {
    var pt = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: b64ToBytes(vault.iv) }, key, b64ToBytes(vault.ct)
    );
    return JSON.parse(new TextDecoder().decode(pt));
  }

  function renderLogin(vault, onUnlocked) {
    root.innerHTML =
      '<div class="login-wrap"><div class="login-card">' +
      '  <h2>ログイン</h2>' +
      '  <p>この先は仲間内限定です。IDとパスワードを入力してください。</p>' +
      '  <form id="login-form" autocomplete="on">' +
      '    <div class="field"><label for="login-id">ID</label>' +
      '      <input id="login-id" name="username" autocomplete="username" required autocapitalize="off" autocorrect="off"></div>' +
      '    <div class="field"><label for="login-pw">パスワード</label>' +
      '      <input id="login-pw" name="password" type="password" autocomplete="current-password" required></div>' +
      '    <button type="submit" class="login-btn" id="login-submit">開く</button>' +
      '    <div class="login-error" id="login-error"></div>' +
      '  </form>' +
      '</div></div>';

    var form = document.getElementById("login-form");
    var idInput = document.getElementById("login-id");
    var pwInput = document.getElementById("login-pw");
    var submitBtn = document.getElementById("login-submit");
    var errorBox = document.getElementById("login-error");
    var timer = null;

    function refreshLockoutUI() {
      var remaining = lockoutRemainingMs();
      if (remaining > 0) {
        submitBtn.disabled = true;
        errorBox.textContent = "試行回数が多いため " + Math.ceil(remaining / 1000) + " 秒待ってください…";
        clearTimeout(timer);
        timer = setTimeout(refreshLockoutUI, 500);
      } else {
        submitBtn.disabled = false;
        clearTimeout(timer);
      }
    }
    refreshLockoutUI();

    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      if (lockoutRemainingMs() > 0) { refreshLockoutUI(); return; }
      submitBtn.disabled = true;
      errorBox.textContent = "確認しています…";
      try {
        var key = await deriveKey(idInput.value, pwInput.value, vault.salt, vault.iterations);
        var data = await tryDecrypt(vault, key);
        writeFail({ count: 0, lastAt: 0 });
        try {
          var raw = await crypto.subtle.exportKey("raw", key);
          localStorage.setItem(LS_KEY, bytesToB64(raw));
        } catch (e) {}
        onUnlocked(data);
      } catch (e) {
        var state = readFail();
        state.count += 1;
        state.lastAt = Date.now();
        writeFail(state);
        pwInput.value = "";
        errorBox.textContent = "IDまたはパスワードが違います";
        submitBtn.disabled = false;
        refreshLockoutUI();
      }
    });
  }

  function renderApp(data) {
    var meta = data.meta;
    var rows = data.rows;

    root.innerHTML =
      '<div class="logout-row"><button type="button" class="logout-btn" id="logout">ログアウト</button></div>' +
      '<h1>🎯 Slot Atlas — 狙い目カレンダー</h1>' +
      '<p class="sub" id="subtitle"></p>' +
      '<div class="banner">公開情報の統計的な傾向整理であり、結果を保証するものではありません。閲覧のみが目的で、実際の来店判断は自己責任でお願いします。</div>' +
      '<div class="controls">' +
      '  <label>月<select id="month"></select></label>' +
      '  <label>市場<select id="market"><option value="all">全登録市場</option></select></label>' +
      '</div>' +
      '<div class="stats">' +
      '  <div class="stat"><div class="label">S/A/B</div><div class="value" id="stat-playable">0日</div></div>' +
      '  <div class="stat"><div class="label">見送り</div><div class="value" id="stat-skip">0日</div></div>' +
      '  <div class="stat"><div class="label">最多候補</div><div class="value" id="stat-top">—</div></div>' +
      '</div>' +
      '<div class="weekdays"><span>月</span><span>火</span><span>水</span><span>木</span><span>金</span><span>土</span><span>日</span></div>' +
      '<div class="grid" id="grid"></div>' +
      '<div class="legend">' +
      '  <span><i class="key s"></i>S</span><span><i class="key a"></i>A</span>' +
      '  <span><i class="key b"></i>B</span><span><i class="key c"></i>C</span>' +
      '  <span><i class="key n"></i>見送り</span>' +
      '</div>' +
      '<div class="card detail" id="detail"></div>' +
      '<div class="footer">このページは検索エンジンに登録されていません。URLを知っている人だけが閲覧できます。<br>Slot Atlas ' + (meta.model_version || "") + '</div>';

    document.getElementById("logout").addEventListener("click", function () {
      try { localStorage.removeItem(LS_KEY); } catch (e) {}
      location.reload();
    });

    document.getElementById("subtitle").textContent =
      meta.hall_count + "店舗を追跡・" + meta.date_range[0] + "〜" + meta.date_range[1] +
      " / 最終更新 " + meta.as_of;

    var rankValue = { "NO BET": 0, "C": 1, "B": 2, "A": 3, "S": 4 };
    var byDate = new Map();
    rows.forEach(function (row) {
      if (!byDate.has(row.d)) byDate.set(row.d, []);
      byDate.get(row.d).push(row);
    });
    var months = Array.from(new Set(rows.map(function (r) { return r.d.slice(0, 7); }))).sort();
    var markets = Array.from(new Set(rows.map(function (r) { return r.m; }))).sort();

    var monthSelect = document.getElementById("month");
    var marketSelect = document.getElementById("market");
    var grid = document.getElementById("grid");
    var detail = document.getElementById("detail");

    markets.forEach(function (m) {
      var o = document.createElement("option");
      o.value = m; o.textContent = m;
      marketSelect.appendChild(o);
    });
    months.forEach(function (m) {
      var o = document.createElement("option");
      o.value = m; o.textContent = m.replace("-", "年") + "月";
      monthSelect.appendChild(o);
    });
    monthSelect.value = months[0];
    var selectedDate = rows[0].d;

    function candidates(date) {
      var market = marketSelect.value;
      var list = byDate.get(date) || [];
      return market === "all" ? list : list.filter(function (r) { return r.m === market; });
    }
    function bestFor(date) {
      var list = candidates(date).slice().sort(function (a, b) {
        return (rankValue[b.r] - rankValue[a.r]) || (b.u - a.u) || (b.c - a.c);
      });
      return list[0];
    }
    function labelFor(row) {
      return (row.r === "NO BET" || row.r === "C") ? ("見送り（相対首位: " + row.h + "）") : row.h;
    }
    function daysInMonth(month) {
      var bits = month.split("-").map(Number);
      return new Date(bits[0], bits[1], 0).getDate();
    }
    function iso(month, day) { return month + "-" + String(day).padStart(2, "0"); }

    function renderDetail() {
      var row = bestFor(selectedDate);
      if (!row) { detail.textContent = "対象期間外"; return; }
      var n = row.n === null ? "n未記載" : "n=" + row.n;
      var risks = row.risk.length ? (" / リスク: " + row.risk.join("・")) : "";
      var warnings = [row.stale, row.hz].filter(Boolean);
      var travel = row.tm === null ? "移動未設定" : ("尾山台から約" + row.tm + "分・調整-" + row.tp);
      detail.innerHTML =
        '<div class="line1"><span class="badge">' + row.r + '</span><strong>' + selectedDate + " " + labelFor(row) + '</strong></div>' +
        '<div class="why">' + row.why + " / 予測平均 " + (row.p >= 0 ? "+" : "") + row.p + "枚 / 判定余白 " +
        (row.e >= 0 ? "+" : "") + row.e + " / " + travel + " / 効用 " + (row.u >= 0 ? "+" : "") + row.u +
        " / " + n + " / 信頼度 " + Math.round(row.c * 100) + "%" + risks + '</div>' +
        (warnings.length ? '<div class="warn">' + warnings.join(" / ") + '</div>' : "");
    }

    function renderGrid() {
      var month = monthSelect.value;
      var parts = month.split("-").map(Number);
      var first = new Date(parts[0], parts[1] - 1, 1);
      var leading = (first.getDay() + 6) % 7;
      grid.innerHTML = "";
      for (var i = 0; i < leading; i++) grid.appendChild(document.createElement("div"));
      var counts = new Map(); var playable = 0; var skip = 0;
      var total = daysInMonth(month);
      for (var day = 1; day <= total; day++) {
        var date = iso(month, day);
        var row = bestFor(date);
        var btn = document.createElement("button");
        btn.type = "button"; btn.className = "day"; btn.textContent = String(day);
        if (row) {
          btn.dataset.rank = row.r;
          btn.setAttribute("aria-label", date + " " + row.r + " " + labelFor(row));
          if (rankValue[row.r] >= 2) { playable++; counts.set(row.h, (counts.get(row.h) || 0) + 1); }
          else { skip++; }
        } else {
          btn.disabled = true;
          btn.setAttribute("aria-label", date + " 対象期間外");
        }
        btn.setAttribute("aria-pressed", date === selectedDate ? "true" : "false");
        btn.addEventListener("click", (function (d) { return function () { selectedDate = d; renderGrid(); }; })(date));
        grid.appendChild(btn);
      }
      document.getElementById("stat-playable").textContent = playable + "日";
      document.getElementById("stat-skip").textContent = skip + "日";
      var top = Array.from(counts.entries()).sort(function (a, b) { return b[1] - a[1]; })[0];
      document.getElementById("stat-top").textContent = top ? top[0] : "—";
      renderDetail();
    }

    monthSelect.addEventListener("change", function () { selectedDate = monthSelect.value + "-01"; renderGrid(); });
    marketSelect.addEventListener("change", renderGrid);
    renderGrid();
  }

  async function boot() {
    var vault;
    try {
      vault = await fetch("data/vault.json").then(function (r) { return r.json(); });
    } catch (e) {
      root.innerHTML = '<div class="error">データの読み込みに失敗しました（' + e + '）。オフラインの場合は一度オンラインで開いてください。</div>';
      return;
    }

    var cachedKeyB64 = null;
    try { cachedKeyB64 = localStorage.getItem(LS_KEY); } catch (e) {}

    if (cachedKeyB64) {
      try {
        var key = await crypto.subtle.importKey("raw", b64ToBytes(cachedKeyB64), "AES-GCM", false, ["decrypt"]);
        var data = await tryDecrypt(vault, key);
        renderApp(data);
        return;
      } catch (e) {
        try { localStorage.removeItem(LS_KEY); } catch (e2) {}
      }
    }

    renderLogin(vault, renderApp);
  }

  boot();

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("sw.js").catch(function () {});
    });
  }
})();
