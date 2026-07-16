"use strict";

(function () {
  var LS_KEY = "sa_key";
  var LS_FAIL = "sa_fail";
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

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeClass(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  }

  function signed(value, suffix) {
    var number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return (number >= 0 ? "+" : "") + number + (suffix || "");
  }

  function pct(value, digits) {
    var number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return number.toFixed(digits === undefined ? 0 : digits) + "%";
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

  function inferFamily(dateText, why) {
    var text = String(why || "").replace(/\s/g, "");
    if (/通常|平常|ベース/.test(text)) return "通常";
    if (text.indexOf("周年") >= 0) return "周年";
    if (/月[=＝]日|月と日/.test(text)) return "月=日";
    if (text.indexOf("ゾロ目") >= 0) return "ゾロ目";
    var tailMatch = text.match(/([0-9０-９])のつく日/);
    if (tailMatch) {
      var full = "０１２３４５６７８９";
      var digitText = tailMatch[1];
      var digit = full.indexOf(digitText);
      if (digit < 0) digit = Number(digitText);
      return digit + "のつく日";
    }
    var explicit = text.match(/(?:^|[^0-9])([1-3]?\d)日(?:[^0-9]|$)/);
    var day = explicit ? Number(explicit[1]) : Number(String(dateText || "").slice(-2));
    if (day === 11 || day === 22) return "ゾロ目";
    if (Number.isFinite(day)) return (day % 10) + "のつく日";
    return "通常";
  }

  function rankBadge(rank) {
    return '<span class="badge rank-' + safeClass(rank) + '">' + escapeHtml(rank) + '</span>';
  }

  function patternStatusClass(status) {
    if (status === "検出") return "detected";
    if (status === "兆候" || status === "参考") return "signal";
    if (status === "未検出") return "quiet";
    if (status === "現地観測") return "field";
    return "waiting";
  }

  function renderApp(data) {
    var meta = data.meta || {};
    var rows = Array.isArray(data.rows) ? data.rows : [];
    var freeSource = data.free_source || null;
    if (!rows.length) {
      root.innerHTML = '<div class="error">表示できる予測行がありません。</div>';
      return;
    }

    root.innerHTML =
      '<div class="logout-row"><button type="button" class="logout-btn" id="logout">ログアウト</button></div>' +
      '<h1>🎯 Slot Atlas — 狙い目カレンダー</h1>' +
      '<p class="sub" id="subtitle"></p>' +
      '<div class="banner">公開情報の統計的な傾向整理であり、結果を保証するものではありません。店×日の判定を先に見て、機種・末尾・配置型は二段目の絞り込みとして使ってください。</div>' +
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
      '<div class="card candidates" id="candidates"></div>' +
      '<div class="card detail" id="detail"></div>' +
      '<div class="footer">このページは検索エンジンに登録されていません。URLを知っている人だけが閲覧できます。<br>Slot Atlas ' + escapeHtml(meta.model_version || "") + '</div>';

    document.getElementById("logout").addEventListener("click", function () {
      try { localStorage.removeItem(LS_KEY); } catch (e) {}
      location.reload();
    });

    var dateRange = Array.isArray(meta.date_range) ? meta.date_range : [rows[0].d, rows[rows.length - 1].d];
    var layerCount = meta.free_source_full_halls ? " / FULL " + meta.free_source_full_halls + "店" : "";
    document.getElementById("subtitle").textContent =
      (meta.hall_count || new Set(rows.map(function (r) { return r.id; })).size) + "店舗を追跡・" +
      dateRange[0] + "〜" + dateRange[1] + " / 最終更新 " + (meta.as_of || dateRange[0]) + layerCount;

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
    var candidateBox = document.getElementById("candidates");

    markets.forEach(function (market) {
      var option = document.createElement("option");
      option.value = market;
      option.textContent = market;
      marketSelect.appendChild(option);
    });
    months.forEach(function (month) {
      var option = document.createElement("option");
      option.value = month;
      option.textContent = month.replace("-", "年") + "月";
      monthSelect.appendChild(option);
    });
    monthSelect.value = months[0];
    var selectedDate = rows[0].d;
    var selectedHallId = null;

    function candidates(dateText) {
      var market = marketSelect.value;
      var list = byDate.get(dateText) || [];
      return market === "all" ? list : list.filter(function (row) { return row.m === market; });
    }

    function sortedCandidates(dateText) {
      return candidates(dateText).slice().sort(function (a, b) {
        return ((rankValue[b.r] || 0) - (rankValue[a.r] || 0)) ||
          (Number(b.u || -999) - Number(a.u || -999)) ||
          (Number(b.c || 0) - Number(a.c || 0));
      });
    }

    function bestFor(dateText) {
      return sortedCandidates(dateText)[0];
    }

    function selectedRow() {
      var list = sortedCandidates(selectedDate);
      var found = list.find(function (row) { return row.id === selectedHallId; });
      return found || list[0] || null;
    }

    function labelFor(row) {
      return (row.r === "NO BET" || row.r === "C") ? ("見送り（相対首位: " + row.h + "）") : row.h;
    }

    function daysInMonth(month) {
      var bits = month.split("-").map(Number);
      return new Date(bits[0], bits[1], 0).getDate();
    }

    function iso(month, day) {
      return month + "-" + String(day).padStart(2, "0");
    }

    function renderCandidateList() {
      var list = sortedCandidates(selectedDate);
      if (!list.length) {
        candidateBox.innerHTML = '<div class="section-title">当日の候補</div><div class="empty-note">対象店舗なし</div>';
        return;
      }
      if (!selectedHallId || !list.some(function (row) { return row.id === selectedHallId; })) {
        selectedHallId = list[0].id;
      }
      var html = '<div class="section-title-row"><div class="section-title">' + escapeHtml(selectedDate) + ' の店舗候補</div><div class="section-note">タップで詳細切替</div></div>';
      html += '<div class="candidate-list">';
      list.forEach(function (row) {
        var active = row.id === selectedHallId;
        var layer = freeSource && freeSource.halls && freeSource.halls[row.id] ? freeSource.halls[row.id].layer : null;
        html += '<button type="button" class="candidate-option" data-active="' + (active ? "true" : "false") + '" data-hall-id="' + escapeHtml(row.id) + '">' +
          '<span class="candidate-main">' + rankBadge(row.r) + '<strong>' + escapeHtml(row.h) + '</strong></span>' +
          '<span class="candidate-meta">効用 ' + signed(row.u) + ' / 信頼度 ' + pct(Number(row.c || 0) * 100) +
          (layer ? ' / <b class="mini-layer layer-' + safeClass(layer) + '">' + escapeHtml(layer) + '</b>' : "") + '</span>' +
          '</button>';
      });
      html += '</div>';
      candidateBox.innerHTML = html;
      Array.prototype.forEach.call(candidateBox.querySelectorAll(".candidate-option"), function (button) {
        button.addEventListener("click", function () {
          selectedHallId = button.getAttribute("data-hall-id");
          renderCandidateList();
          renderDetail();
        });
      });
    }

    function metric(label, value, note) {
      return '<div class="signal-metric"><div class="signal-label">' + escapeHtml(label) + '</div>' +
        '<div class="signal-value">' + escapeHtml(value) + '</div>' +
        (note ? '<div class="signal-note">' + escapeHtml(note) + '</div>' : "") + '</div>';
    }

    function renderMachines(machines, summaryMode) {
      if (!machines || !machines.length) return '<div class="empty-note">機種候補を算出できるデータがありません。</div>';
      var html = '<div class="machine-list">';
      machines.forEach(function (machine, index) {
        var score = Number(machine.score || 0);
        var note = summaryMode
          ? [machine.units ? machine.units + "台" : null, machine.n ? "n=" + machine.n : null, Number.isFinite(Number(machine.avg_diff)) ? "平均" + signed(machine.avg_diff, "枚") : null].filter(Boolean).join(" / ")
          : machine.reason || "";
        var bucket = Math.max(5, Math.min(100, Math.round(score / 5) * 5));
        html += '<div class="machine-row">' +
          '<div class="machine-rank">' + (index + 1) + '</div>' +
          '<div class="machine-body"><div class="machine-head"><strong>' + escapeHtml(machine.name) + '</strong><span>' + score.toFixed(1) + '</span></div>' +
          '<div class="bar"><i class="w-' + bucket + '"></i></div>' +
          '<div class="machine-note">' + escapeHtml(note) + '</div></div></div>';
      });
      return html + '</div>';
    }

    function renderTails(tails) {
      if (!tails || !tails.length) return '<div class="empty-note">末尾データなし</div>';
      var html = '<div class="tail-grid">';
      tails.forEach(function (tail) {
        html += '<div class="tail-tile" data-grade="' + escapeHtml(tail.grade || "—") + '">' +
          '<div class="tail-grade">' + escapeHtml(tail.grade || "—") + '</div>' +
          '<div class="tail-number">末尾' + escapeHtml(tail.tail) + '</div>' +
          '<div class="tail-z">' + (Number.isFinite(Number(tail.z)) ? "z=" + signed(Number(tail.z).toFixed(2)) : "zなし") + '</div>' +
          (Number.isFinite(Number(tail.avg_diff)) ? '<div class="tail-diff">平均' + signed(tail.avg_diff, "枚") + '</div>' : "") +
          '</div>';
      });
      return html + '</div>';
    }

    function renderPatterns(patterns) {
      if (!patterns || !patterns.length) return "";
      var active = patterns.filter(function (item) {
        return ["検出", "兆候", "参考", "現地観測"].indexOf(item.status) >= 0;
      });
      var summary = active.length
        ? active.slice(0, 5).map(function (item) { return "#" + item.id + " " + item.name; }).join("・")
        : "検出済みの配置型なし";
      var html = '<details class="pattern-ledger"><summary><span>設定配置パターン台帳 15型</span><small>' + escapeHtml(summary) + '</small></summary><div class="pattern-grid">';
      patterns.forEach(function (item) {
        html += '<div class="pattern-item status-' + patternStatusClass(item.status) + '">' +
          '<div class="pattern-head"><b>#' + escapeHtml(item.id) + ' ' + escapeHtml(item.name) + '</b><span>' + escapeHtml(item.status) + '</span></div>' +
          '<div class="pattern-detail">' + escapeHtml(item.detail || item.needs || "") + '</div>' +
          '</div>';
      });
      return html + '</div></details>';
    }

    function strategyText(machine, tails, patterns) {
      var parts = [];
      if (machine) {
        if (machine.rotation_label === "ローテ型" && machine.latest_selected && machine.latest_selected.length) {
          parts.push("前回選抜「" + machine.latest_selected.slice(0, 3).join("・") + "」を消去");
        }
        if (machine.machines && machine.machines.length) {
          parts.push("機種は" + machine.machines.slice(0, 3).map(function (m) { return m.name; }).join("・") + "から確認");
        }
      }
      if (tails && tails[0] && Number(tails[0].z) >= 1) {
        parts.push("末尾" + tails[0].tail + "は補助根拠");
      }
      var rowPattern = (patterns || []).find(function (item) { return item.id === 2 && item.status === "検出"; });
      if (rowPattern) parts.push("並び兆候を優先");
      return parts.length ? parts.join(" → ") : "店×日の判定を優先し、配置型は現地1時間の答え合わせに使う";
    }

    function renderCapFlags(caps) {
      if (!caps) return "";
      var flags = [
        { key: "hall_daily", label: "日次" },
        { key: "machine_daily", label: "機種" },
        { key: "tail_daily", label: "末尾" },
        { key: "unit_daily", label: "台番" },
      ];
      return '<div class="cap-flags">' + flags.map(function (f) {
        return '<span class="cap-flag ' + (caps[f.key] ? "cap-on" : "cap-off") + '">' + f.label + '</span>';
      }).join("") + '</div>';
    }

    function renderChainInfo(hall) {
      if (!hall.chain_id) return "";
      var html = '<div class="chain-info"><span class="chain-label">系列</span><span class="chain-tag">' + escapeHtml(hall.chain_id) + '</span>';
      if (hall.chain_patterns && hall.chain_patterns.length) {
        var types = hall.chain_patterns.map(function (p) { return p.type; });
        var uniqueTypes = types.filter(function (t, i) { return types.indexOf(t) === i; });
        html += uniqueTypes.map(function (t) {
          var label = { joint_machine: "共通機種", machine_split: "機種分担", date_role_split: "日程分担", intensity_split: "強弱交互" }[t] || t;
          return '<span class="chain-pattern-chip">' + escapeHtml(label) + '</span>';
        }).join("");
      }
      return html + '</div>';
    }

    function renderV12Machines(v12day) {
      if (!v12day || !v12day.machines || !v12day.machines.length) return "";
      var machines = v12day.machines;
      var noData = machines.length === 1 && machines[0].id === "_no_data";
      if (noData) return "";

      var entityType = machines[0].type || "machine_event";
      var typeLabel = entityType === "machine_organic" ? "organic" : "event";
      var html = '<h3>v1.2 機種予測 <span class="entity-type-tag type-' + typeLabel + '">' + escapeHtml(typeLabel) + '</span></h3>';
      html += '<div class="machine-list">';
      machines.forEach(function (m, index) {
        var score = Number(m.score || 0);
        var bucket = Math.max(5, Math.min(100, Math.round(score / 5) * 5));
        var conf = m.confidence !== null && m.confidence !== undefined ? pct(m.confidence * 100) : "—";
        var expl = Array.isArray(m.explanation) ? m.explanation.join(" → ") : "";
        html += '<div class="machine-row">' +
          '<div class="machine-rank">' + (m.rank || index + 1) + '</div>' +
          '<div class="machine-body"><div class="machine-head"><strong>' + escapeHtml(m.name) + '</strong><span>' + score.toFixed(1) + '</span></div>' +
          '<div class="bar"><i class="w-' + bucket + '"></i></div>' +
          '<div class="machine-meta"><span class="machine-note">' + escapeHtml(expl) + '</span><span class="v12-confidence">信頼度 ' + conf + '</span></div>';
        if (m.warnings && m.warnings.length) {
          html += '<div class="v12-warnings">' + m.warnings.map(function (w) { return escapeHtml(w); }).join(" / ") + '</div>';
        }
        html += '</div></div>';
      });
      return html + '</div>';
    }

    function renderFreeSource(row) {
      if (!freeSource || !freeSource.halls) {
        return '<div class="analysis-block"><div class="analysis-head"><strong>無料ソース配置予測</strong><span class="layer layer-none">未搭載</span></div>' +
          '<div class="empty-note">このvaultは旧形式です。更新ビルドを実行すると機種・末尾・配置型が追加されます。</div></div>';
      }
      var hall = freeSource.halls[row.id];
      if (!hall) {
        return '<div class="analysis-block"><div class="analysis-head"><strong>無料ソース配置予測</strong><span class="layer layer-none">NONE</span></div>' +
          '<div class="empty-note">この店舗の機種・末尾データは未接続です。</div></div>';
      }
      var family = inferFamily(selectedDate, row.why);
      var familyData = hall.families && (hall.families[family] || hall.families["通常"] || hall.families["全日参考"]);
      var actualFamily = hall.families && hall.families[family] ? family : (hall.families && hall.families["通常"] ? "通常" : "全日参考");
      var counts = hall.counts || {};
      var html = '<div class="analysis-block">' +
        '<div class="analysis-head"><div><strong>無料ソース配置予測</strong><span class="family-chip">' + escapeHtml(actualFamily) + '</span></div>' +
        '<span class="layer layer-' + safeClass(hall.layer) + '">' + escapeHtml(hall.layer) + '</span></div>';

      html += renderCapFlags(hall.capabilities);
      html += renderChainInfo(hall);

      var v12day = hall.v1_2 && hall.v1_2[selectedDate];

      if (hall.layer === "NONE") {
        html += '<div class="empty-note">機種日次・末尾日次・参考スコアのいずれも未接続です。</div>';
      } else if (!familyData && hall.layer === "SUMMARY") {
        html += '<div class="source-warning">SUMMARY：結果後ハイライト由来を含み得る参考格付けです。着席前確率ではありません。</div>';
        html += renderV12Machines(v12day);
        html += '<h3>機種参考格付け</h3>' + renderMachines(hall.summary_machines || [], true);
      } else if (familyData) {
        var machine = familyData.machine;
        var tails = familyData.tails || [];
        var patterns = familyData.patterns || [];
        if (machine) {
          var bestTail = tails[0];
          html += '<div class="signal-metrics">' +
            metric("全台系度", pct(machine.all_machine_rate, 1), machine.selected_date_n + "/" + machine.date_n + "日") +
            metric("機種選抜", machine.rotation_label || "—", machine.repeat_rate === null || machine.repeat_rate === undefined ? "系列不足" : "再登場率" + pct(machine.repeat_rate, 1)) +
            metric("末尾本命", bestTail ? "末尾" + bestTail.tail : "—", bestTail && Number.isFinite(Number(bestTail.z)) ? "z=" + signed(Number(bestTail.z).toFixed(2)) : "データなし") +
            '</div>' +
            '<div class="strategy"><b>実戦ルート</b><span>' + escapeHtml(strategyText(machine, tails, patterns)) + '</span></div>';
          html += renderV12Machines(v12day);
          html += '<h3>機種候補 <small>候補度（未校正）</small></h3>' + renderMachines(machine.machines || [], false) +
            '<h3>末尾候補</h3>' + renderTails(tails) + renderPatterns(patterns);
        } else {
          html += '<div class="source-warning">SUMMARY：機種日次の同族系列が不足しています。</div>';
          html += renderV12Machines(v12day);
          html += '<h3>機種参考格付け</h3>' + renderMachines(hall.summary_machines || [], true) +
            (tails.length ? '<h3>末尾候補</h3>' + renderTails(tails) : "") + renderPatterns(patterns);
        }
      } else {
        html += renderV12Machines(v12day);
      }

      if (hall.warnings && hall.warnings.length) {
        html += '<div class="warning-list">' + hall.warnings.map(function (warning) {
          return '<div>・' + escapeHtml(warning) + '</div>';
        }).join("") + '</div>';
      }
      var runMeta = freeSource.run_meta;
      html += '<div class="data-counts">machine_days ' + escapeHtml(counts.machine_days || 0) +
        ' / tail_days ' + escapeHtml(counts.tail_days || 0) +
        ' / unit_days ' + escapeHtml(counts.unit_days || 0) +
        ' / position_signals ' + escapeHtml(counts.position_signals || 0) +
        (runMeta ? ' / run ' + escapeHtml(runMeta.prediction_run_id || "") +
          ' / cutoff ' + escapeHtml((runMeta.feature_cutoff_at || "").slice(0, 10)) : "") +
        '</div>';
      return html + '</div>';
    }

    function renderDetail() {
      var row = selectedRow();
      if (!row) {
        detail.textContent = "対象期間外";
        return;
      }
      selectedHallId = row.id;
      var n = row.n === null || row.n === undefined ? "n未記載" : "n=" + row.n;
      var risks = row.risk && row.risk.length ? (" / リスク: " + row.risk.join("・")) : "";
      var warnings = [row.stale, row.hz].filter(Boolean);
      var travel = row.tm === null || row.tm === undefined ? "移動未設定" : ("尾山台から約" + row.tm + "分・調整-" + row.tp);
      detail.innerHTML =
        '<div class="line1">' + rankBadge(row.r) + '<strong>' + escapeHtml(selectedDate + " " + labelFor(row)) + '</strong></div>' +
        '<div class="why">' + escapeHtml(row.why) + " / 予測平均 " + signed(row.p, "枚") + " / 判定余白 " +
        signed(row.e) + " / " + escapeHtml(travel) + " / 効用 " + signed(row.u) +
        " / " + escapeHtml(n) + " / 信頼度 " + pct(Number(row.c || 0) * 100) + escapeHtml(risks) + '</div>' +
        (warnings.length ? '<div class="warn">' + warnings.map(escapeHtml).join(" / ") + '</div>' : "") +
        renderFreeSource(row);
    }

    function renderGrid() {
      var month = monthSelect.value;
      var parts = month.split("-").map(Number);
      var first = new Date(parts[0], parts[1] - 1, 1);
      var leading = (first.getDay() + 6) % 7;
      grid.innerHTML = "";
      for (var i = 0; i < leading; i++) grid.appendChild(document.createElement("div"));
      var counts = new Map();
      var playable = 0;
      var skip = 0;
      var total = daysInMonth(month);
      for (var day = 1; day <= total; day++) {
        var dateText = iso(month, day);
        var row = bestFor(dateText);
        var button = document.createElement("button");
        button.type = "button";
        button.className = "day";
        button.textContent = String(day);
        if (row) {
          button.dataset.rank = row.r;
          button.setAttribute("aria-label", dateText + " " + row.r + " " + labelFor(row));
          if ((rankValue[row.r] || 0) >= 2) {
            playable++;
            counts.set(row.h, (counts.get(row.h) || 0) + 1);
          } else {
            skip++;
          }
        } else {
          button.disabled = true;
          button.setAttribute("aria-label", dateText + " 対象期間外");
        }
        button.setAttribute("aria-pressed", dateText === selectedDate ? "true" : "false");
        button.addEventListener("click", (function (chosenDate) {
          return function () {
            selectedDate = chosenDate;
            selectedHallId = null;
            renderGrid();
          };
        })(dateText));
        grid.appendChild(button);
      }
      document.getElementById("stat-playable").textContent = playable + "日";
      document.getElementById("stat-skip").textContent = skip + "日";
      var top = Array.from(counts.entries()).sort(function (a, b) { return b[1] - a[1]; })[0];
      document.getElementById("stat-top").textContent = top ? top[0] : "—";
      renderCandidateList();
      renderDetail();
    }

    monthSelect.addEventListener("change", function () {
      selectedDate = monthSelect.value + "-01";
      selectedHallId = null;
      renderGrid();
    });
    marketSelect.addEventListener("change", function () {
      selectedHallId = null;
      renderGrid();
    });
    renderGrid();
  }

  async function boot() {
    var vault;
    try {
      vault = await fetch("data/vault.json", { cache: "no-store" }).then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      });
    } catch (e) {
      root.innerHTML = '<div class="error">データの読み込みに失敗しました（' + escapeHtml(e) + '）。オフラインの場合は一度オンラインで開いてください。</div>';
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
