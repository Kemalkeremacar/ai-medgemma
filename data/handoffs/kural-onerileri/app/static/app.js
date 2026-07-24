(() => {
  const DECISIONS_KEY = "dgx_rule_demo_decisions_v1";
  const API_BASE = (
    document.querySelector('meta[name="api-base"]')?.getAttribute("content") || ""
  ).replace(/\/$/, "");
  const state = {
    summary: null,
    labels: {},
    route: { name: "dashboard" },
  };
  let routeGen = 0;
  let routeAbort = null;

  const $ = (sel) => document.querySelector(sel);
  const views = {
    dashboard: $("#view-dashboard"),
    proposals: $("#view-proposals"),
    proposal: $("#view-proposal"),
    decisions: $("#view-decisions"),
    "oneri-ai": $("#view-oneri-ai"),
    error: $("#view-error"),
  };

  const oneriAiState = {
    history: [],
    busy: false,
    proposalId: "",
  };

  const COL_HELP = {
    candidate: "İşlem ve kural tipine göre öneri.",
    ruleType: "Süre/frekans, birlikte ödenmez veya yaş.",
    priority: "A önce, B normal, C sonra bakın.",
    evidence: "Resmî kaynak alıntısı sayısı. 0 ise dayanak zayıf.",
    status: "Alanların doluluk durumu ve dikkat uyarıları.",
  };

  const FALLBACK_FIELD_LABELS = {
    adet: "Adet / limit",
    periyotDeger: "Periyot değeri",
    surePeriyot: "Periyot birimi",
    islemlerGrupMu: "İşlemler grup mu?",
    sourceSutCode: "Kaynak SUT kodu",
    targetSutCodes: "Hedef SUT kodları",
    evrakBazliMi: "Evrak bazlı mı?",
    yasBaslangic: "Yaş başlangıç",
    yasBitis: "Yaş bitiş",
    yasBirimi: "Yaş birimi",
  };

  const FALLBACK_PERIOD_LABELS = { G: "Gün", H: "Hafta", M: "Ay", Y: "Yıl" };

  const FALLBACK_FLAG_LABELS = {
    explicit_frequency_fields_not_parsed: "Frekans alanları ayrıştırılamadı",
    canonical_together_target_not_found: "Birlikte ödenmez hedefi bulunamadı",
    source_crosswalk_not_trusted: "Kaynak eşleştirmesi güvenilir değil",
    official_source_locator_or_quote_missing: "Resmî kaynak alıntısı eksik",
    frequency_period_or_limit_incomplete: "Frekans / süre bilgisi eksik",
    explicit_age_bounds_not_parsed: "Yaş sınırları ayrıştırılamadı",
    official_source_verification_failed: "Resmî kaynak doğrulanamadı",
    unresolved_target_sut_codes: "Hedef SUT kodları çözülemedi",
    ambiguous_target_sut_codes: "Hedef SUT kodları belirsiz",
  };

  const AI_STATUS_LABELS = {
    accepted: "Teknik doğrulamayı geçti",
    blocked: "Güvenlik kontrolünde engellendi",
    call_or_parse_error: "Çağrı / ayrıştırma hatası",
  };

  const AI_OUTCOME_LABELS = {
    proposal: "AI kural hipotezi",
    no_change: "Değişiklik önermiyor",
    insufficient_evidence: "Kanıt yetersiz",
  };

  const DECISION_LABELS = {
    approve: "Uygun",
    edit: "Düzenle",
    reject: "Uygun değil",
    needs_more_evidence: "Ek kanıt",
  };

  const RELATION_LABELS = {
    new: "Yeni aday",
    same: "Mevcut kural ile aynı",
    conflict: "Mevcut kural ile çelişebilir",
    update: "Mevcut kuralı güncelleme adayı",
    overlap: "Mevcut kural ile örtüşebilir",
  };

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmt(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "boolean") return value ? "Evet" : "Hayır";
    return String(value);
  }

  function uiLabels() {
    return state.summary?.uiLabels || {};
  }

  function fieldLabel(key) {
    return uiLabels().fields?.[key] || FALLBACK_FIELD_LABELS[key] || key;
  }

  function periodLabel(value) {
    const v = String(value ?? "");
    return uiLabels().periods?.[v] || FALLBACK_PERIOD_LABELS[v] || v;
  }

  function completenessLabel(value, explicit) {
    if (explicit) return explicit;
    return uiLabels().completeness?.[value] ||
      ({ complete: "Tam", partial: "Kısmi" }[value] || value || "—");
  }

  function flagLabel(flag, explicit) {
    if (explicit) return explicit;
    const raw = String(flag || "");
    if (!raw) return "—";
    if (raw.startsWith("unresolved_target_sut_codes")) {
      const codes = raw.includes(":") ? raw.split(":").slice(1).join(":") : "";
      const base = FALLBACK_FLAG_LABELS.unresolved_target_sut_codes;
      return codes ? `${base}: ${codes}` : base;
    }
    if (raw.startsWith("ambiguous_target_sut_codes")) {
      const codes = raw.includes(":") ? raw.split(":").slice(1).join(":") : "";
      const base = FALLBACK_FLAG_LABELS.ambiguous_target_sut_codes;
      return codes ? `${base}: ${codes}` : base;
    }
    if (raw.startsWith("official_source_verification_failed")) {
      return FALLBACK_FLAG_LABELS.official_source_verification_failed;
    }
    return FALLBACK_FLAG_LABELS[raw] || raw.replace(/_/g, " ");
  }

  function proposalTitle(p) {
    if (p.displayTitle) return p.displayTitle;
    if (p.shortTitle) return p.shortTitle;
    const rule = p.targetRuleTypeLabel || p.targetRuleType || "Kural";
    const liste = p.listeTipi || (p.primaryProcedure || {}).listeTipi || "?";
    const kod = p.procedureKod || (p.primaryProcedure || {}).kod || "—";
    const ad = p.procedureAd || (p.primaryProcedure || {}).ad || "";
    return ad ? `${liste} ${kod} · ${rule} — ${ad}` : `${liste} ${kod} · ${rule}`;
  }

  function optionList(items, selected, valueKey = "value", labelKey = "label") {
    return (items || [])
      .map((item) => {
        if (item && typeof item === "object") {
          const value = item[valueKey];
          const label = item[labelKey] || value;
          return `<option value="${esc(value)}" ${String(value) === String(selected) ? "selected" : ""}>${esc(label)}</option>`;
        }
        return `<option value="${esc(item)}" ${String(item) === String(selected) ? "selected" : ""}>${esc(item)}</option>`;
      })
      .join("");
  }

  function formatFieldValue(key, value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "boolean") return value ? "Evet" : "Hayır";
    if (key === "surePeriyot" || key === "yasBirimi") return periodLabel(value);
    if (Array.isArray(value)) return value.map((v) => fmt(v)).join(", ");
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  async function api(path, options = {}) {
    const url = `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    let body = options.body;
    if (body && typeof body === "object" && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const res = await fetch(url, {
      method: options.method || "GET",
      headers,
      body,
      signal: options.signal || (routeAbort ? routeAbort.signal : undefined),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail;
      const detailText = Array.isArray(detail)
        ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
        : detail;
      const err = new Error(
        detailText || data.message || data.error || `HTTP ${res.status}`
      );
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function isRouteStale(gen) {
    return gen !== routeGen;
  }

  function th(label, helpKey) {
    const tip = COL_HELP[helpKey] || "";
    return tip
      ? `<th title="${esc(tip)}">${esc(label)} <span class="th-hint" aria-hidden="true">?</span></th>`
      : `<th>${esc(label)}</th>`;
  }

  function mdToHtml(md) {
    const lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let i = 0;
    let inUl = false;
    let inOl = false;
    let inTable = false;

    const closeLists = () => {
      if (inUl) {
        out.push("</ul>");
        inUl = false;
      }
      if (inOl) {
        out.push("</ol>");
        inOl = false;
      }
    };
    const closeTable = () => {
      if (inTable) {
        out.push("</tbody></table>");
        inTable = false;
      }
    };
    const inline = (text) =>
      esc(text)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      if (!trimmed) {
        closeLists();
        closeTable();
        i += 1;
        continue;
      }
      if (/^---+$/.test(trimmed)) {
        closeLists();
        closeTable();
        out.push("<hr />");
        i += 1;
        continue;
      }
      if (trimmed.startsWith("|") && i + 1 < lines.length && /^\|?\s*-+/.test(lines[i + 1].trim())) {
        closeLists();
        closeTable();
        const parseRow = (row) =>
          row
            .replace(/^\|/, "")
            .replace(/\|$/, "")
            .split("|")
            .map((c) => c.trim());
        const headers = parseRow(trimmed);
        out.push('<table class="data help-table"><thead><tr>');
        headers.forEach((h) => out.push(`<th>${inline(h)}</th>`));
        out.push("</tr></thead><tbody>");
        i += 2;
        inTable = true;
        while (i < lines.length && lines[i].trim().startsWith("|")) {
          const cells = parseRow(lines[i].trim());
          out.push("<tr>");
          cells.forEach((c) => out.push(`<td>${inline(c)}</td>`));
          out.push("</tr>");
          i += 1;
        }
        closeTable();
        continue;
      }
      const h = trimmed.match(/^(#{1,3})\s+(.*)$/);
      if (h) {
        closeLists();
        closeTable();
        const level = h[1].length;
        out.push(`<h${level}>${inline(h[2])}</h${level}>`);
        i += 1;
        continue;
      }
      if (/^[-*]\s+/.test(trimmed)) {
        closeTable();
        if (inOl) {
          out.push("</ol>");
          inOl = false;
        }
        if (!inUl) {
          out.push("<ul class='list-plain'>");
          inUl = true;
        }
        out.push(`<li>${inline(trimmed.replace(/^[-*]\s+/, ""))}</li>`);
        i += 1;
        continue;
      }
      if (/^\d+\.\s+/.test(trimmed)) {
        closeTable();
        if (inUl) {
          out.push("</ul>");
          inUl = false;
        }
        if (!inOl) {
          out.push("<ol>");
          inOl = true;
        }
        out.push(`<li>${inline(trimmed.replace(/^\d+\.\s+/, ""))}</li>`);
        i += 1;
        continue;
      }
      closeLists();
      closeTable();
      out.push(`<p>${inline(trimmed)}</p>`);
      i += 1;
    }
    closeLists();
    closeTable();
    return out.join("\n");
  }

  function loadDecisions() {
    try {
      return JSON.parse(localStorage.getItem(DECISIONS_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function saveDecisions(all) {
    localStorage.setItem(DECISIONS_KEY, JSON.stringify(all));
  }

  function getDecision(proposalId) {
    return loadDecisions()[proposalId] || null;
  }

  function setDecision(proposalId, payload) {
    const all = loadDecisions();
    all[proposalId] = {
      ...payload,
      proposalId,
      updatedAt: new Date().toISOString(),
      demoOnly: true,
    };
    saveDecisions(all);
  }

  function statusPill(status, label) {
    const cls =
      status === "accepted" ? "ok" : status === "blocked" ? "danger" : "muted-pill";
    return `<span class="pill ${cls}">${esc(label || status || "—")}</span>`;
  }

  function priorityPill(p) {
    const cls = p === "A" ? "priority-a" : p === "B" ? "priority-b" : "priority-c";
    return `<span class="pill ${cls}">${esc(p || "—")}</span>`;
  }

  function statusCell(p) {
    const flags = p.qualityFlagLabels || (p.qualityFlags || []).map((f) => flagLabel(f));
    if (!flags.length) {
      return completenessPill(p.completeness, p.completenessLabel);
    }
    return `${completenessPill(p.completeness, p.completenessLabel)}
      <div class="muted" style="margin-top:4px;font-size:0.8rem">${esc(flags[0])}${flags.length > 1 ? ` (+${flags.length - 1})` : ""}</div>`;
  }

  function completenessPill(value, label) {
    const text = completenessLabel(value, label);
    const cls = value === "complete" ? "ok" : value === "partial" ? "warn" : "muted-pill";
    return `<span class="pill ${cls}">${esc(text)}</span>`;
  }

  function parseRoute() {
    const rawHash = location.hash.replace(/^#\/?/, "");
    const pathOnly = rawHash.split("?")[0];
    const parts = pathOnly.split("/").filter(Boolean);
    const [name, id, third] = parts;
    if (!name) return { name: "dashboard" };
    if (name === "proposals" && id) return { name: "proposal", id };
    return { name, id };
  }

  function setActiveNav(name) {
    document.querySelectorAll(".tabs a").forEach((a) => {
      const route = a.getAttribute("data-route");
      const active =
        route === name ||
        (route === "proposals" && name === "proposal");
      a.classList.toggle("active", active);
    });
  }

  function showView(name) {
    Object.entries(views).forEach(([key, el]) => {
      if (el) el.hidden = key !== name;
    });
    $(".main")?.classList.toggle("chat-mode", name === "oneri-ai");
  }

  function setTitle(title, sub) {
    $("#page-title").textContent = title;
    const subEl = $("#page-sub");
    if (subEl) {
      subEl.textContent = sub || "";
      subEl.hidden = !sub;
    }
  }

  function kvRows(pairs) {
    return `<div class="kv">${pairs
      .map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${v}</div>`)
      .join("")}</div>`;
  }

  function fieldsTable(obj) {
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
      return `<div class="empty">Alan yok</div>`;
    }
    const rows = Object.entries(obj)
      .map(
        ([k, v]) =>
          `<tr><td>${esc(fieldLabel(k))}</td><td>${esc(formatFieldValue(k, v))}</td></tr>`
      )
      .join("");
    return `<div class="table-wrap"><table class="data"><thead><tr><th>Alan</th><th>Değer</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function pagerHtml(page, totalPages, total, onPrev, onNext) {
    return `<div class="pager">
      <div>${total} kayıt · sayfa ${page} / ${Math.max(totalPages, 1)}</div>
      <div class="actions">
        <button type="button" class="btn secondary" data-act="prev" ${page <= 1 ? "disabled" : ""}>Önceki</button>
        <button type="button" class="btn secondary" data-act="next" ${page >= totalPages ? "disabled" : ""}>Sonraki</button>
      </div>
    </div>`;
  }

  async function renderDashboard() {
    const gen = routeGen;
    setTitle("Özet", "İncelemeye buradan başlayın");
    setActiveNav("dashboard");
    showView("dashboard");
    const s = state.summary;
    const c = s.counts || {};
    const stage = c.stage || {};
    const status = c.status || {};
    views.dashboard.innerHTML = `<div class="panel"><div class="muted">Yükleniyor…</div></div>`;
    const help = await api("/api/help").catch(() => ({ markdown: "" }));
    if (isRouteStale(gen)) return;
    views.dashboard.innerHTML = `
      <div class="banner info">
        Bu ekran canlı kural yazmaz. HUV ve SUT önerilerini <strong>ayrı ayrı</strong> inceleyin.
        <code>accepted</code> insan onayı değildir.
      </div>
      <div class="grid-stats">
        <div class="stat"><div class="label">Kural önerisi</div><div class="value">${esc(c.deterministicProposals)}</div></div>
        <div class="stat"><div class="label">İşlem</div><div class="value">${esc(c.procedureCoverage)}</div></div>
        <div class="stat"><div class="label">Resmî kanıt</div><div class="value">${esc(c.officialEvidence)}</div></div>
        <div class="stat"><div class="label">AI paket</div><div class="value">${esc(c.completedPackets)}</div></div>
      </div>
      <div class="panel">
        <h3 style="margin:0 0 10px">AI aşama / durum</h3>
        <div class="chips">
          <span class="pill ai">rule_synthesis ${esc(stage.rule_synthesis || 0)}</span>
          <span class="pill muted-pill">crosswalk ${esc(stage.crosswalk_adjudication || 0)}</span>
          <span class="pill ok">geçti ${esc(status.accepted || 0)}</span>
          <span class="pill warn">engellendi ${esc(status.blocked || 0)}</span>
          ${status.call_or_parse_error ? `<span class="pill danger">hata ${esc(status.call_or_parse_error)}</span>` : ""}
        </div>
        <p class="muted" style="margin:12px 0 0">Öneri detayında yalnız <strong>rule_synthesis</strong> hipotezi gösterilir. Crosswalk sonuçları HUV↔SUT birleştirme UI’si değildir.</p>
        <div class="chips" style="margin-top:14px">
          <a class="btn primary" href="#/proposals">Önerilere git</a>
          <a class="btn secondary" href="#/proposals?hasAi=1">AI hipotezi olanlar</a>
          <a class="btn secondary" href="#/oneri-ai">Öneri AI</a>
        </div>
      </div>
      <div class="panel help-doc">
        ${mdToHtml(help.markdown || "")}
      </div>
    `;
  }

  async function renderProposals() {
    const gen = routeGen;
    setTitle("Öneriler", "İncelemek istediğiniz kural adayını seçin");
    setActiveNav("proposals");
    showView("proposals");
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    const q = params.get("q") || "";
    const ruleType = params.get("ruleType") || "";
    const priority = params.get("priority") || "";
    const listeTipi = params.get("listeTipi") || "";
    const hasAi = params.get("hasAi") || "";
    const page = Number(params.get("page") || 1);
    const opts = state.summary.filterOptions || {};
    const priorityOpts = (opts.priorities || []).map((p) =>
      typeof p === "object" ? { value: p.value, label: p.value } : { value: p, label: p }
    );

    views.proposals.innerHTML = `<div class="panel"><div class="muted">Yükleniyor…</div></div>`;
    const data = await api(
      `/api/proposals?${new URLSearchParams({
        q, ruleType, priority, listeTipi, hasAi, page: String(page), pageSize: "25",
      })}`
    );
    if (isRouteStale(gen)) return;

    const rows = (data.items || [])
      .map((p) => `<tr>
          <td>
            <a class="proposal-link" href="#/proposals/${esc(p.proposalId)}">
              <span class="title">${esc(p.shortTitle || proposalTitle(p))}</span>
              <span class="sub">${esc(p.procedureAd || "")}</span>
            </a>
          </td>
          <td>${esc(p.targetRuleTypeLabel || p.targetRuleType)}</td>
          <td>${priorityPill(p.priority)}</td>
          <td><strong>${esc(p.evidenceCount)}</strong></td>
          <td>${statusCell(p)}${p.hasAiHypothesis ? ` <span class="pill ai" title="AI kural hipotezi var">AI</span>` : ""}</td>
        </tr>`)
      .join("");

    views.proposals.innerHTML = `
      <div class="panel">
        <form class="toolbar" id="proposal-filters">
          <div class="field grow"><label>Ara</label><input name="q" value="${esc(q)}" placeholder="İşlem kodu veya adı" /></div>
          <div class="field"><label>Liste</label>
            <select name="listeTipi">
              <option value="">Tümü</option>
              <option value="HUV" ${listeTipi==="HUV"?"selected":""}>HUV</option>
              <option value="SUT" ${listeTipi==="SUT"?"selected":""}>SUT</option>
            </select>
          </div>
          <div class="field"><label>Kural tipi</label>
            <select name="ruleType"><option value="">Tümü</option>${optionList(opts.ruleTypes, ruleType)}</select>
          </div>
          <div class="field"><label>Öncelik</label>
            <select name="priority"><option value="">Tümü</option>${optionList(priorityOpts, priority)}</select>
          </div>
          <div class="field"><label>AI hipotezi</label>
            <select name="hasAi">
              <option value="">Tümü</option>
              <option value="1" ${hasAi==="1"?"selected":""}>Var</option>
              <option value="0" ${hasAi==="0"?"selected":""}>Yok</option>
            </select>
          </div>
          <button class="btn primary" type="submit">Filtrele</button>
        </form>
        <div class="table-wrap">
          <table class="data">
            <thead><tr>
              ${th("Öneri", "candidate")}
              ${th("Tip", "ruleType")}
              ${th("Öncelik", "priority")}
              ${th("Kanıt", "evidence")}
              ${th("Durum", "status")}
            </tr></thead>
            <tbody>${rows || `<tr><td colspan="5" class="empty">Kayıt bulunamadı</td></tr>`}</tbody>
          </table>
        </div>
        ${pagerHtml(data.page, data.totalPages, data.total)}
      </div>
    `;

    $("#proposal-filters").addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const next = new URLSearchParams();
      for (const [k, v] of fd.entries()) if (v) next.set(k, v);
      next.set("page", "1");
      location.hash = `#/proposals?${next}`;
    });
    views.proposals.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const next = new URLSearchParams(location.hash.split("?")[1] || "");
        const cur = Number(next.get("page") || 1);
        next.set("page", String(btn.dataset.act === "next" ? cur + 1 : cur - 1));
        location.hash = `#/proposals?${next}`;
      });
    });
  }

  function decisionForm(proposalId) {
    const d = getDecision(proposalId) || {};
    return `
      <div class="layer" style="background:#fff;border-style:dashed">
        <div class="layer-head">
          <h3>Not / karar</h3>
          <span class="pill warn">Yalnızca bu tarayıcıda saklanır</span>
        </div>
        <form id="decision-form">
          <div class="decision-box">
            ${[["approve","Uygun"],["edit","Düzenle"],["reject","Uygun değil"],["needs_more_evidence","Ek kanıt"]].map(([v,l]) => `
              <label><input type="radio" name="decision" value="${v}" ${d.decision===v?"checked":""} /> <span>${l}</span></label>
            `).join("")}
          </div>
          <div class="field" style="margin-bottom:10px">
            <label>Not</label>
            <textarea name="note" rows="2" placeholder="İsterseniz kısa not ekleyin">${esc(d.note || "")}</textarea>
          </div>
          <button class="btn primary" type="submit">Kaydet</button>
          <span id="decision-saved" class="muted" style="margin-left:10px"></span>
        </form>
      </div>
    `;
  }

  async function renderProposal(id) {
    const gen = routeGen;
    setTitle("Öneri detayı", "");
    setActiveNav("proposals");
    showView("proposal");
    views.proposal.innerHTML = `<div class="panel"><div class="muted">Yükleniyor…</div></div>`;
    const data = await api(`/api/proposals/${encodeURIComponent(id)}`);
    if (isRouteStale(gen)) return;
    const p = data.proposal;
    const proc = p.primaryProcedure || {};
    const comparison = p.existingRuleComparison || {};
    const title = data.displayTitle || proposalTitle({
      ...p,
      procedureKod: proc.kod,
      procedureAd: proc.ad,
      listeTipi: proc.listeTipi,
      targetRuleTypeLabel: data.ruleTypeLabel,
    });
    const flagLabels = data.qualityFlagLabels || (p.qualityFlags || []).map((f) => flagLabel(f));
    const relation = RELATION_LABELS[comparison.relation] || comparison.relation || "";
    const notes = (comparison.notes || []).filter(Boolean);
    const diagnoses = (p.diagnosisCodes || []).filter(Boolean);

    const summaryPairs = [
      ["İşlem", `${esc(proc.kod)} — ${esc(proc.ad)}`],
      ["Liste", esc(proc.listeTipi)],
      ["Kural tipi", esc(data.ruleTypeLabel || p.targetRuleType)],
      ["Öncelik", esc(p.priority || "—")],
      ["Durum", esc(completenessLabel(p.completeness, data.completenessLabel))],
    ];
    if (diagnoses.length) summaryPairs.push(["Tanılar", esc(diagnoses.join(", "))]);
    if (relation && relation !== "Yeni aday") summaryPairs.push(["Mevcut kural", esc(relation)]);
    if (notes.length) summaryPairs.push(["Not", esc(notes.join(" · "))]);

    const evidenceHtml = (data.officialEvidence || []).length
      ? data.officialEvidence.map((e) => {
          const meta = [];
          if (e.fileName) meta.push(["Kaynak", esc(e.fileName)]);
          if (e.sheetName) meta.push(["Sayfa", esc(e.sheetName)]);
          if (e.sourceRow != null && e.sourceRow !== "") meta.push(["Satır", esc(e.sourceRow)]);
          return `
          <div class="evidence-card">
            <div class="quote">${esc(e.quote || "Alıntı yok")}</div>
            ${meta.length ? kvRows(meta) : ""}
          </div>`;
        }).join("")
      : `<div class="empty">Resmî kanıt bulunamadı — dikkatli ilerleyin.</div>`;

    const existingHtml = (data.existingRules || []).length
      ? (data.existingRules || []).map((r) => `
          <div class="context-card">
            ${fieldsTable(r.businessFields || {})}
          </div>`).join("")
      : "";

    const aiHypotheses = data.aiHypotheses || [];
    const aiHtml = aiHypotheses.length
      ? aiHypotheses.map((h) => {
          const statusLabel = h.statusLabel || AI_STATUS_LABELS[h.status] || h.status || "—";
          const outcomeLabel = h.outcomeLabel || AI_OUTCOME_LABELS[h.outcome] || h.outcome || "";
          const statusClass = h.status === "accepted" ? "ok" : h.status === "blocked" ? "warn" : "danger";
          const gaps = (h.evidenceGaps || []).filter(Boolean);
          const questions = (h.expertQuestions || []).filter(Boolean);
          const errors = (h.errors || []).filter(Boolean);
          const hasFields = h.proposedFields && Object.keys(h.proposedFields).length;
          return `
          <div class="ai-card">
            <div class="chips" style="margin-bottom:8px">
              <span class="pill ${statusClass}">${esc(statusLabel)}</span>
              ${outcomeLabel ? `<span class="pill ai">${esc(outcomeLabel)}</span>` : ""}
              ${h.targetRuleTypeLabel ? `<span class="pill muted-pill">${esc(h.targetRuleTypeLabel)}</span>` : ""}
              ${h.hypothesisOnly !== false ? `<span class="pill warn">Hipotez</span>` : ""}
            </div>
            ${h.rationale ? `<p class="ai-rationale">${esc(h.rationale)}</p>` : `<p class="muted">Gerekçe yok.</p>`}
            ${hasFields ? `<h4>AI önerilen alanlar</h4>${fieldsTable(h.proposedFields)}` : ""}
            ${gaps.length ? `<h4>Kanıt boşlukları</h4><ul class="ai-list">${gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : ""}
            ${questions.length ? `<h4>Uzman soruları</h4><ul class="ai-list">${questions.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : ""}
            ${errors.length ? `<div class="banner danger" style="margin-top:10px">${errors.map((e) => esc(e)).join(" · ")}</div>` : ""}
          </div>`;
        }).join("")
      : `<div class="empty">Bu öneri için rule_synthesis hipotezi yok.</div>`;

    views.proposal.innerHTML = `
      <div class="chips" style="margin-bottom:12px">
        <a class="btn secondary" href="#/proposals">← Liste</a>
        <a class="btn secondary" href="#/oneri-ai?proposalId=${encodeURIComponent(p.proposalId || id)}">Öneri AI</a>
      </div>
      <div class="panel">
        <div class="proposal-hero">
          <div>
            <h2>${esc(title)}</h2>
            <div class="proposal-meta">
              <span class="pill liste">${esc(proc.listeTipi || "—")}</span>
              ${priorityPill(p.priority)}
              ${completenessPill(p.completeness, data.completenessLabel)}
              <span class="pill muted-pill">${esc((data.officialEvidence || []).length)} kanıt</span>
              ${aiHypotheses.length ? `<span class="pill ai">AI hipotezi</span>` : ""}
            </div>
          </div>
        </div>
        ${flagLabels.length ? `<div class="chips" style="margin-top:10px">${flagLabels.map((f) => `<span class="pill warn">${esc(f)}</span>`).join("")}</div>` : ""}
      </div>

      <div class="layer evidence">
        <div class="layer-head"><h3>1. Resmî kanıt</h3><span class="pill muted-pill">${(data.officialEvidence||[]).length}</span></div>
        ${evidenceHtml}
      </div>

      <div class="layer deterministic">
        <div class="layer-head"><h3>2. Önerilen alanlar</h3></div>
        ${kvRows(summaryPairs)}
        <div style="margin-top:12px">${fieldsTable(p.proposedFields || {})}</div>
        ${existingHtml ? `<h4>Mevcut kural</h4>${existingHtml}` : ""}
      </div>

      <div class="layer ai">
        <div class="layer-head">
          <h3>3. AI kural hipotezi</h3>
          <span class="pill ai">${aiHypotheses.length}</span>
        </div>
        <p class="muted" style="margin-top:0">Model çıktısıdır; canlı kural değildir. Resmî kanıt ve deterministik alanlarla birlikte okuyun.</p>
        ${aiHtml}
      </div>

      <div class="layer example-rules-box">
        <div class="layer-head">
          <h3>4. Örnek kural metni</h3>
          <span class="pill warn">Taslak</span>
        </div>
        <p class="muted" style="margin-top:0">Kanıtla uyumluysa örnek cümle üretin. Olduğu gibi yayınlamayın.</p>
        <button type="button" class="btn primary" id="btn-example-rules">Örnek metni göster</button>
        <div id="example-rules-panel" hidden></div>
      </div>

      ${decisionForm(p.proposalId)}
    `;

    const form = $("#decision-form");
    form?.addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const decision = fd.get("decision");
      if (!decision) {
        $("#decision-saved").textContent = "Bir seçenek işaretleyin.";
        return;
      }
      setDecision(p.proposalId, {
        decision: String(decision),
        note: String(fd.get("note") || ""),
        procedureKod: proc.kod,
        targetRuleType: p.targetRuleType,
        shortTitle: data.shortTitle || proposalTitle({
          procedureKod: proc.kod,
          procedureAd: proc.ad,
          listeTipi: proc.listeTipi,
          targetRuleTypeLabel: data.ruleTypeLabel,
          targetRuleType: p.targetRuleType,
        }),
        displayTitle: data.displayTitle || title,
      });
      $("#decision-saved").textContent = "Kaydedildi.";
    });

    $("#btn-example-rules")?.addEventListener("click", async () => {
      const panel = $("#example-rules-panel");
      const btn = $("#btn-example-rules");
      if (!panel || !btn) return;
      btn.disabled = true;
      panel.hidden = false;
      panel.innerHTML = `<div class="muted">Hazırlanıyor…</div>`;
      try {
        const ex = await api(`/api/proposals/${encodeURIComponent(p.proposalId)}/example-rules`);
        const c = ex.consistency || {};
        panel.innerHTML = `
          <div class="banner ${c.level === "high" ? "info" : c.level === "medium" ? "warn" : "danger"}" style="margin-top:12px">
            <strong>${esc(c.levelLabel || "")}</strong>
          </div>
          ${(ex.examples || []).map((item) => `
            <div class="example-card">
              <h4>${esc(item.title || "Örnek kural")}</h4>
              <p>${esc(item.text || "")}</p>
            </div>
          `).join("") || `<div class="empty">Örnek üretilemedi</div>`}
        `;
        btn.textContent = "Yenile";
      } catch (err) {
        panel.innerHTML = `<div class="banner danger">${esc(err.message || err)}</div>`;
      } finally {
        btn.disabled = false;
      }
    });
  }

  function renderDecisions() {
    setTitle("Kararlarım", "Bu tarayıcıda saklanan notlarınız");
    setActiveNav("decisions");
    showView("decisions");
    const all = loadDecisions();
    const entries = Object.values(all).sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));
    const decisionUi = {
      approve: "Uygun",
      edit: "Düzenle",
      reject: "Uygun değil",
      needs_more_evidence: "Ek kanıt",
    };
    views.decisions.innerHTML = `
      <div class="banner info">Buradaki kayıtlar canlı onaya dönüşmez; yalnızca sizin notlarınızdır.</div>
      <div class="panel">
        <div class="toolbar">
          <button type="button" class="btn secondary" id="btn-clear-decisions">Tümünü sil</button>
          <button type="button" class="btn primary" id="btn-export-2">Dışa aktar</button>
        </div>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>Öneri</th><th>Karar</th><th>Not</th><th>Zaman</th></tr></thead>
            <tbody>
              ${entries.map((d) => {
                const ruleLabel = ({ sure: "Süre / frekans", birlikteOdenmez: "Birlikte ödenmez", yas: "Yaş" }[d.targetRuleType] || d.targetRuleType || "Kural");
                const label = d.shortTitle || d.displayTitle || (d.procedureKod ? `${d.procedureKod} · ${ruleLabel}` : "Öneri");
                return `<tr>
                <td><a href="#/proposals/${esc(d.proposalId)}">${esc(label)}</a></td>
                <td>${esc(decisionUi[d.decision] || DECISION_LABELS[d.decision] || d.decision)}</td>
                <td>${esc(d.note || "—")}</td>
                <td>${esc(d.updatedAt ? new Date(d.updatedAt).toLocaleString("tr-TR") : "")}</td>
              </tr>`;
              }).join("") || `<tr><td colspan="4" class="empty">Henüz kayıt yok</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>`;
    $("#btn-clear-decisions")?.addEventListener("click", () => {
      if (confirm("Tüm notlar silinsin mi?")) {
        localStorage.removeItem(DECISIONS_KEY);
        renderDecisions();
      }
    });
    $("#btn-export-2")?.addEventListener("click", exportDecisions);
  }

  function exportDecisions() {
    const payload = {
      exportedAt: new Date().toISOString(),
      demoOnly: true,
      warning: "Demo taslağıdır; gerçek onay değildir.",
      decisions: loadDecisions(),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `dgx-demo-decisions-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function formatChatText(text) {
    const raw = String(text || "");
    const blocks = raw.replace(/\r\n/g, "\n").split(/\n{2,}/);
    return blocks
      .map((block) => {
        const lines = block.split("\n");
        const isUl = lines.every((l) => /^\s*[-*•]\s+/.test(l) || !l.trim());
        const isOl = lines.every((l) => /^\s*\d+[.)]\s+/.test(l) || !l.trim());
        const inline = (s) =>
          esc(s)
            .replace(/`([^`]+)`/g, "<code>$1</code>")
            .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        if (isUl) {
          const items = lines
            .filter((l) => l.trim())
            .map((l) => `<li>${inline(l.replace(/^\s*[-*•]\s+/, ""))}</li>`)
            .join("");
          return `<ul>${items}</ul>`;
        }
        if (isOl) {
          const items = lines
            .filter((l) => l.trim())
            .map((l) => `<li>${inline(l.replace(/^\s*\d+[.)]\s+/, ""))}</li>`)
            .join("");
          return `<ol>${items}</ol>`;
        }
        return `<p>${lines.map(inline).join("<br>")}</p>`;
      })
      .join("");
  }

  function bindOneriAiSuggestions(root) {
    root?.querySelectorAll("[data-q]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const input = $("#oneri-ai-input");
        if (!input || oneriAiState.busy) return;
        input.value = btn.getAttribute("data-q") || "";
        autosizeChatInput();
        sendOneriAi();
      });
    });
  }

  function renderOneriAiMessages() {
    const box = $("#oneri-ai-messages");
    if (!box) return;
    if (!oneriAiState.history.length) {
      box.innerHTML = `
        <div class="chat-thread-inner">
          <div class="chat-empty">
            <div class="chat-empty-mark" aria-hidden="true">AI</div>
            <h2>Bugün size nasıl yardımcı olabilirim?</h2>
            <p>Kural adayları, kanıt ve motor alanları hakkında sorun. Yanıtlar karar destek içindir.</p>
            <div class="chat-suggestions">
              <button type="button" class="chat-suggest" data-q="Öncelik A önerilerinde nelere dikkat etmeliyim?">
                <strong>Öncelik A</strong>
                <span>İnceleme sırası ve kritik noktalar</span>
              </button>
              <button type="button" class="chat-suggest" data-q="HUV ve SUT kurallarını neden ayrı değerlendirmeliyim?">
                <strong>HUV / SUT</strong>
                <span>Neden ayrı değerlendirilir?</span>
              </button>
              <button type="button" class="chat-suggest" data-q="Kanıt sayısı 0 olan adayları nasıl okumalıyım?">
                <strong>Kanıt yoksa</strong>
                <span>Ne yapmalı, ne beklemeli?</span>
              </button>
              <button type="button" class="chat-suggest" data-q="Süre / frekans kuralında hangi alanlar kritik?">
                <strong>Süre / frekans</strong>
                <span>Kritik alanlar ve okuma yolu</span>
              </button>
            </div>
          </div>
        </div>`;
      bindOneriAiSuggestions(box);
      return;
    }
    box.innerHTML = `<div class="chat-thread-inner">${oneriAiState.history
      .map((m) => {
        const role = m.role === "assistant" ? "assistant" : "user";
        const label = role === "assistant" ? "Öneri AI" : "Siz";
        const avatar = role === "assistant" ? "AI" : "SZ";
        const sources =
          role === "assistant" && m.sources && m.sources.length
            ? `<div class="chat-sources">${m.sources
                .map((s) => `<span class="pill ai">${esc(s)}</span>`)
                .join("")}</div>`
            : "";
        const badge =
          role === "assistant"
            ? m.usedModel
              ? `<span class="pill ai">Sistem + model</span>`
              : `<span class="pill muted-pill">Sistem özeti</span>`
            : "";
        let bodyHtml = "";
        if (role === "assistant" && m.sections && (m.sections.deterministic || m.sections.model)) {
          bodyHtml = `
            <div class="chat-section det">
              <div class="chat-section-title">Sistem özeti</div>
              <div class="chat-body">${formatChatText(m.sections.deterministic || "")}</div>
            </div>
            <div class="chat-section model">
              <div class="chat-section-title">Yorum</div>
              <div class="chat-body">${formatChatText(
                m.sections.model ||
                  "Ek yorum üretilemedi; yukarıdaki özetle devam edebilirsiniz."
              )}</div>
            </div>`;
        } else {
          const cardOpen = role === "user" ? '<div class="chat-bubble-card">' : "";
          const cardClose = role === "user" ? "</div>" : "";
          bodyHtml = `${cardOpen}<div class="chat-body">${formatChatText(m.content)}</div>${cardClose}`;
        }
        return `<article class="chat-turn ${role}">
          <div class="chat-avatar" aria-hidden="true">${avatar}</div>
          <div class="chat-bubble">
            <div class="chat-turn-head">
              <span class="chat-role">${label}</span>
              ${badge}
            </div>
            ${bodyHtml}
            ${sources}
          </div>
        </article>`;
      })
      .join("")}</div>`;
    box.scrollTop = box.scrollHeight;
  }

  async function sendOneriAi() {
    if (oneriAiState.busy) return;
    const input = $("#oneri-ai-input");
    const pidInput = $("#oneri-ai-proposal");
    const sendBtn = $("#oneri-ai-send");
    if (!input) return;
    const message = (input.value || "").trim();
    if (!message) return;

    oneriAiState.proposalId = (pidInput?.value || "").trim();
    oneriAiState.history.push({ role: "user", content: message });
    input.value = "";
    autosizeChatInput();
    oneriAiState.busy = true;
    if (sendBtn) sendBtn.disabled = true;
    renderOneriAiMessages();

    const inner = $("#oneri-ai-messages .chat-thread-inner") || $("#oneri-ai-messages");
    const typing = document.createElement("article");
    typing.className = "chat-turn assistant typing";
    typing.id = "oneri-ai-typing";
    typing.innerHTML = `
      <div class="chat-avatar" aria-hidden="true">AI</div>
      <div class="chat-bubble">
        <div class="chat-turn-head"><span class="chat-role">Öneri AI</span></div>
        <div class="chat-body"><span class="chat-typing-dots" aria-label="Yanıt hazırlanıyor"><i></i><i></i><i></i></span></div>
      </div>`;
    inner?.appendChild(typing);
    const box = $("#oneri-ai-messages");
    if (box) box.scrollTop = box.scrollHeight;

    try {
      const hist = oneriAiState.history
        .filter((m) => m.role === "user" || m.role === "assistant")
        .slice(0, -1)
        .slice(-8)
        .map((m) => ({ role: m.role, content: m.content }));
      const data = await api("/api/oneri-ai/chat", {
        method: "POST",
        body: {
          message,
          proposalId: oneriAiState.proposalId || null,
          history: hist,
        },
        signal: undefined,
      });
      oneriAiState.history.push({
        role: "assistant",
        content: data.reply || "Yanıt alınamadı.",
        sections: data.sections || null,
        sources: data.sources || [],
        usedModel: !!data.usedModel,
      });
    } catch (err) {
      oneriAiState.history.push({
        role: "assistant",
        content: `İstek başarısız: ${err.message || err}`,
        sections: null,
        sources: [],
        usedModel: false,
      });
    } finally {
      oneriAiState.busy = false;
      if (sendBtn) sendBtn.disabled = false;
      $("#oneri-ai-typing")?.remove();
      renderOneriAiMessages();
      input.focus();
    }
  }

  function autosizeChatInput() {
    const input = $("#oneri-ai-input");
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  }

  function renderOneriAi() {
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    const fromUrl = params.get("proposalId") || "";
    if (fromUrl) oneriAiState.proposalId = fromUrl;

    setTitle("Öneri AI", "");
    setActiveNav("oneri-ai");
    showView("oneri-ai");

    views["oneri-ai"].innerHTML = `
      <div class="chat-shell">
        <div class="chat-topbar">
          <button type="button" class="btn secondary" id="oneri-ai-context-toggle">Bağlam</button>
          <button type="button" class="btn secondary" id="oneri-ai-clear">Yeni sohbet</button>
        </div>
        <form class="chat-context" id="oneri-ai-context" hidden onsubmit="return false;">
          <div class="field">
            <label>Kural adayı (isteğe bağlı)</label>
            <input id="oneri-ai-proposal" value="${esc(oneriAiState.proposalId)}" placeholder="Detaydan gelen kimlik" />
          </div>
          <p class="muted" style="margin:8px 0 0;font-size:0.78rem">Boş bırakılabilir. Doldurulursa yanıt bu adaya göre özelleşir.</p>
        </form>
        <div id="oneri-ai-messages" class="chat-thread" aria-live="polite"></div>
        <div class="chat-composer">
          <div class="chat-composer-inner">
            <form id="oneri-ai-form">
              <div class="chat-input-box">
                <textarea id="oneri-ai-input" rows="1" placeholder="Mesajınızı yazın…"></textarea>
                <button type="submit" class="chat-send" id="oneri-ai-send" title="Gönder" aria-label="Gönder">
                  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M12 19V5M12 5l-6 6M12 5l6 6" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                </button>
              </div>
            </form>
            <p class="chat-disclaimer">Öneri AI karar destek sağlar; canlı kural yayınlamaz. HUV ve SUT ayrı değerlendirilir.</p>
          </div>
        </div>
      </div>`;

    renderOneriAiMessages();
    $("#oneri-ai-form")?.addEventListener("submit", (e) => {
      e.preventDefault();
      sendOneriAi();
    });
    $("#oneri-ai-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendOneriAi();
      }
    });
    $("#oneri-ai-input")?.addEventListener("input", autosizeChatInput);
    $("#oneri-ai-clear")?.addEventListener("click", () => {
      if (oneriAiState.busy) return;
      oneriAiState.history = [];
      renderOneriAiMessages();
      const input = $("#oneri-ai-input");
      if (input) {
        input.value = "";
        autosizeChatInput();
        input.focus();
      }
    });
    $("#oneri-ai-context-toggle")?.addEventListener("click", () => {
      const panel = $("#oneri-ai-context");
      if (!panel) return;
      panel.hidden = !panel.hidden;
    });
    $("#oneri-ai-proposal")?.addEventListener("change", (e) => {
      oneriAiState.proposalId = (e.target.value || "").trim();
    });
    autosizeChatInput();
    $("#oneri-ai-input")?.focus();
  }

  async function route() {
    const gen = ++routeGen;
    if (routeAbort) routeAbort.abort();
    routeAbort = new AbortController();
    try {
      if (!state.summary) {
        state.summary = await api("/api/summary");
        if (isRouteStale(gen)) return;
        state.labels = {
          not_processed: "Henüz işlenmedi",
          ...(state.summary.labels || {}),
        };
        const rawBadge = $("#raw-badge");
        if (rawBadge && state.summary.safety?.rawEnabled) {
          rawBadge.hidden = false;
          rawBadge.textContent = "Ham cevap açık";
          rawBadge.className = "pill warn";
        }
      }
      const r = parseRoute();
      if (isRouteStale(gen)) return;
      state.route = r;
      if (r.name === "dashboard" || r.name === "help") await renderDashboard();
      else if (r.name === "proposals") await renderProposals();
      else if (r.name === "proposal") await renderProposal(r.id);
      else if (r.name === "decisions") await renderDecisions();
      else if (r.name === "oneri-ai") renderOneriAi();
      else {
        location.hash = "#/dashboard";
        return;
      }
      if (isRouteStale(gen)) return;
    } catch (err) {
      if (err && err.name === "AbortError") return;
      if (isRouteStale(gen)) return;
      showView("error");
      setTitle("Hata", "");
      views.error.innerHTML = `<div class="banner danger">${esc(err.message || err)}</div>`;
    }
  }

  window.addEventListener("hashchange", route);
  $("#btn-export")?.addEventListener("click", exportDecisions);
  if (!location.hash) location.hash = "#/dashboard";
  else route();
})();
