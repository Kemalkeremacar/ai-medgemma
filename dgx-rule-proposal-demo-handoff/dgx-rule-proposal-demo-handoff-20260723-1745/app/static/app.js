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
    help: $("#view-help"),
    error: $("#view-error"),
  };

  const COL_HELP = {
    proposalId: "Bu kural adayının benzersiz kimliği. Takip ve not için kullanın.",
    procedure: "Kuralın bağlandığı işlem kodu ve adı.",
    listeTipi: "Listenin türü: HUV veya SUT. Kurallar liste tipine göre ayrı değerlendirilir; HUV↔SUT eşleştirmesi yoktur.",
    ruleType: "Kuralın türü: Süre/frekans, Birlikte ödenmez veya Yaş.",
    priority: "A=önce bakın, B=normal, C=düşük öncelik. Doğruluk garantisi değildir.",
    completeness: "complete=alanlar görece tam; partial=eksik/kısmi — önce boşluklara bakın.",
    evidence: "Bağlı resmî kaynak alıntısı sayısı. 0 ise dayanak zayıf; 1+ ise detayda alıntıyı okuyun.",
    qualityFlags: "Motor dikkat bayrakları. Doluysa kaydı şüpheli kabul edin; Yardım’da örnekler var.",
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

  async function api(path) {
    const url = `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
    const res = await fetch(url, {
      headers: { Accept: "application/json" },
      signal: routeAbort ? routeAbort.signal : undefined,
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
    return `<span class="pill ${cls}">Öncelik ${esc(p || "—")}</span>`;
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
    document.querySelectorAll(".nav a").forEach((a) => {
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
  }

  function setTitle(title, sub) {
    $("#page-title").textContent = title;
    $("#page-sub").textContent = sub || "";
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
          `<tr><td class="mono">${esc(k)}</td><td>${esc(
            typeof v === "object" ? JSON.stringify(v) : fmt(v)
          )}</td></tr>`
      )
      .join("");
    return `<table class="data"><thead><tr><th>Alan</th><th>Değer</th></tr></thead><tbody>${rows}</tbody></table>`;
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
    setTitle("Dashboard", "Partial snapshot — HUV ve SUT kuralları ayrı");
    setActiveNav("dashboard");
    showView("dashboard");
    const s = state.summary;
    const c = s.counts || {};
    views.dashboard.innerHTML = `
      <div class="banner warn">
        <strong>Partial snapshot:</strong>
        Bu ekran dondurulmuş bir kesittir. HUV↔SUT crosswalk karşılaştırması bu demoda kullanılmaz;
        HUV kuralları ile SUT kuralları ayrı adaylar olarak incelenir.
      </div>
      <div class="grid-stats">
        <div class="stat"><div class="label">Deterministik öneri</div><div class="value">${esc(c.deterministicProposals)}</div></div>
        <div class="stat"><div class="label">İşlem coverage</div><div class="value">${esc(c.procedureCoverage)}</div></div>
        <div class="stat"><div class="label">Resmî evidence</div><div class="value">${esc(c.officialEvidence)}</div></div>
        <div class="stat"><div class="label">Engine signals</div><div class="value">${esc(c.engineSignals)}</div></div>
      </div>
      <div class="panel">
        <h2>Nasıl bakmalıyım?</h2>
        <ul class="list-plain">
          <li>Liste tipine (HUV / SUT) göre kural adaylarını ayrı değerlendirin.</li>
          <li>Önce resmî evidence, sonra deterministik alanlar, sonra örnek kural taslağı.</li>
          <li>Demo kararı gerçek yayın onayı değildir.</li>
        </ul>
        ${(s.limitations || []).length ? `<h3>Snapshot sınırları</h3><ul class="list-plain">${(s.limitations || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      </div>
      <div class="panel">
        <h2>Hızlı gezinme</h2>
        <div class="chips">
          <a class="btn primary" href="#/proposals">Kural önerilerine git</a>
          <a class="btn secondary" href="#/help">Uzman yardım rehberi</a>
          <a class="btn secondary" href="#/decisions">Uzman kararları</a>
        </div>
        <p class="muted" style="margin-top:12px">Snapshot: ${esc(s.snapshotCreatedAt)} · kaynak durumu: ${esc(s.sourceState)}</p>
      </div>
    `;
  }

  async function renderProposals() {
    const gen = routeGen;
    setTitle("Kural önerileri", "Deterministik paket — arama, filtre ve sayfalama");
    setActiveNav("proposals");
    showView("proposals");
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    const q = params.get("q") || "";
    const ruleType = params.get("ruleType") || "";
    const priority = params.get("priority") || "";
    const qualityFlag = params.get("qualityFlag") || "";
    const completeness = params.get("completeness") || "";
    const listeTipi = params.get("listeTipi") || "";
    const page = Number(params.get("page") || 1);
    const opts = state.summary.filterOptions || {};

    views.proposals.innerHTML = `<div class="panel"><div class="muted">Yükleniyor…</div></div>`;
    const data = await api(
      `/api/proposals?${new URLSearchParams({
        q, ruleType, priority, qualityFlag, completeness, listeTipi, page: String(page), pageSize: "25",
      })}`
    );
    if (isRouteStale(gen)) return;

    const rows = (data.items || [])
      .map((p) => {
        return `<tr>
          <td><a href="#/proposals/${esc(p.proposalId)}">${esc(p.proposalId)}</a></td>
          <td><div class="mono">${esc(p.procedureKod)}</div><div>${esc(p.procedureAd)}</div></td>
          <td><span class="pill muted-pill">${esc(p.listeTipi || "—")}</span></td>
          <td>${esc(p.targetRuleTypeLabel || p.targetRuleType)}</td>
          <td>${priorityPill(p.priority)}</td>
          <td>${esc(p.completeness)}</td>
          <td>${esc(p.evidenceCount)}</td>
          <td class="mono" style="max-width:220px">${esc((p.qualityFlags || []).slice(0, 2).join(", ") || "—")}</td>
        </tr>`;
      })
      .join("");

    views.proposals.innerHTML = `
      <div class="panel">
        <form class="toolbar" id="proposal-filters">
          <div class="field grow"><label>Ara</label><input name="q" value="${esc(q)}" placeholder="ID, işlem kodu veya adı" /></div>
          <div class="field"><label>Kural tipi</label>
            <select name="ruleType"><option value="">Tümü</option>${(opts.ruleTypes || []).map((r) => `<option value="${esc(r.value)}" ${r.value===ruleType?"selected":""}>${esc(r.label)}</option>`).join("")}</select>
          </div>
          <div class="field"><label>Öncelik</label>
            <select name="priority"><option value="">Tümü</option>${(opts.priorities || []).map((p) => `<option ${p===priority?"selected":""}>${esc(p)}</option>`).join("")}</select>
          </div>
          <div class="field"><label>Completeness</label>
            <select name="completeness"><option value="">Tümü</option>${(opts.completeness || []).map((p) => `<option ${p===completeness?"selected":""}>${esc(p)}</option>`).join("")}</select>
          </div>
          <div class="field grow"><label>Quality flag</label>
            <select name="qualityFlag"><option value="">Tümü</option>${(opts.qualityFlags || []).map((p) => `<option value="${esc(p)}" ${p===qualityFlag?"selected":""}>${esc(p)}</option>`).join("")}</select>
          </div>
          <div class="field"><label>Liste tipi</label>
            <select name="listeTipi">
              <option value="">Tümü</option>
              <option value="HUV" ${listeTipi==="HUV"?"selected":""}>HUV</option>
              <option value="SUT" ${listeTipi==="SUT"?"selected":""}>SUT</option>
            </select>
          </div>
          <button class="btn primary" type="submit">Filtrele</button>
        </form>
        <div class="banner info" style="margin-bottom:12px">
          HUV ve SUT kuralları ayrı incelenir; HUV↔SUT eşleştirme ekranı yoktur.
          Kolon açıklamaları için başlığın üzerine gelin veya <a href="#/help">Yardım</a>.
        </div>
        <table class="data">
          <thead><tr>
            ${th("Proposal ID", "proposalId")}
            ${th("İşlem", "procedure")}
            ${th("Liste", "listeTipi")}
            ${th("Kural tipi", "ruleType")}
            ${th("Öncelik", "priority")}
            ${th("Completeness", "completeness")}
            ${th("Evidence", "evidence")}
            ${th("Quality flags", "qualityFlags")}
          </tr></thead>
          <tbody>${rows || `<tr><td colspan="8" class="empty">Kayıt bulunamadı</td></tr>`}</tbody>
        </table>
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
          <h3>Uzman demo kararı</h3>
          <span class="pill warn">Demo taslağıdır; gerçek onay değildir</span>
        </div>
        <p class="muted">Kaynak dosyalar değiştirilmez. Karar yalnızca local storage ve JSON export ile tutulur.</p>
        <form id="decision-form">
          <div class="decision-box">
            ${[["approve","Onayla"],["edit","Düzenle"],["reject","Reddet"],["needs_more_evidence","Ek kanıt gerekli"]].map(([v,l]) => `
              <label><input type="radio" name="decision" value="${v}" ${d.decision===v?"checked":""} /> <span>${l}</span></label>
            `).join("")}
          </div>
          <div class="field" style="margin-bottom:10px">
            <label>Not</label>
            <textarea name="note" rows="3" placeholder="Uzman notu (demo)">${esc(d.note || "")}</textarea>
          </div>
          <button class="btn primary" type="submit">Demo kararını kaydet</button>
          <span id="decision-saved" class="muted" style="margin-left:10px"></span>
        </form>
      </div>
    `;
  }

  async function renderProposal(id) {
    const gen = routeGen;
    setTitle("Kural önerisi detayı", id);
    setActiveNav("proposals");
    showView("proposal");
    views.proposal.innerHTML = `<div class="panel"><div class="muted">Yükleniyor…</div></div>`;
    const data = await api(`/api/proposals/${encodeURIComponent(id)}`);
    if (isRouteStale(gen)) return;
    const p = data.proposal;
    const proc = p.primaryProcedure || {};
    const comparison = p.existingRuleComparison || {};

    const evidenceHtml = (data.officialEvidence || []).length
      ? data.officialEvidence.map((e) => `
          <div style="margin-bottom:12px">
            <div class="chips" style="margin-bottom:6px">
              <span class="pill muted-pill">${esc(e.sourceKind || "evidence")}</span>
              <span class="pill muted-pill mono">${esc(e.fileName || "")}</span>
            </div>
            <div class="quote">${esc(e.quote || "")}</div>
            ${kvRows([
              ["Locator", `<span class="mono">${esc(e.locator)}</span>`],
              ["Satır", esc(e.sourceRow)],
              ["Sheet", esc(e.sheetName)],
              ["Doğrulama", esc(e.verificationMethod)],
              ["Alıntı doğrulandı", fmt(e.quoteVerified)],
            ])}
          </div>`).join("")
      : `<div class="empty">Resmî evidence yok</div>`;


    views.proposal.innerHTML = `
      <div class="banner info"><a href="#/proposals">← Listeye dön</a> · Owner: <span class="mono">${esc(data.ownerId || "—")}</span></div>
      <div class="layer deterministic">
        <div class="layer-head"><h3>Deterministik öneri</h3>${priorityPill(p.priority)}</div>
        ${kvRows([
          ["Proposal ID", `<span class="mono">${esc(p.proposalId)}</span>`],
          ["İşlem", `${esc(proc.kod)} — ${esc(proc.ad)}`],
          ["Liste", esc(proc.listeTipi)],
          ["Kural tipi", esc(data.ruleTypeLabel || p.targetRuleType)],
          ["Completeness", esc(p.completeness)],
          ["Human review", fmt(p.humanReviewRequired)],
          ["Tanılar", esc((p.diagnosisCodes || []).join(", ") || "—")],
          ["Mevcut kural ilişkisi", esc(comparison.relation || "—")],
          ["Karşılaştırma notları", esc((comparison.notes || []).join(" · ") || "—")],
        ])}
        <h4>Önerilen alanlar</h4>
        ${fieldsTable(p.proposedFields || {})}
        <h4>Engine signals</h4>
        ${(data.engineSignals || []).map((s) => `
          <div style="margin:8px 0;padding:8px;background:rgba(255,255,255,0.55);border-radius:8px">
            <div class="mono">${esc(s.signalId)}</div>
            <div>${esc(s.engineRuleType)} → ${esc(s.targetRuleType)} · confidence ${esc(s.confidence)}</div>
            ${fieldsTable(s.fields || {})}
          </div>`).join("") || `<div class="empty">Signal yok</div>`}
        <h4>Mevcut kural bağlamı</h4>
        ${(data.existingRules || []).map((r) => `
          <div style="margin:8px 0">
            <div class="mono">${esc(r.contextId)}</div>
            ${fieldsTable(r.businessFields || {})}
          </div>`).join("") || `<div class="empty">Bağlam yok (${esc(comparison.relation || "new")})</div>`}
      </div>

      <div class="layer evidence">
        <div class="layer-head"><h3>Resmî evidence</h3><span class="pill muted-pill">${(data.officialEvidence||[]).length} kayıt</span></div>
        ${evidenceHtml}
      </div>

      <div class="layer example-rules-box">
        <div class="layer-head">
          <h3>Örnek kural önerileri</h3>
          <span class="pill warn">Otomatik taslak — yayın değildir</span>
        </div>
        <p class="muted" style="margin-top:0">
          Bu kayıttaki işlem, kural tipi ve alanlardan Türkçe örnek kural cümleleri üretir.
          Önce evidence’ı okuyun; metni olduğu gibi canlı kurala yapıştırmayın.
        </p>
        <button type="button" class="btn primary" id="btn-example-rules">Bu kayıt için örnek kural önerilerini göster</button>
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
      });
      $("#decision-saved").textContent = "Kaydedildi (local storage).";
    });

    $("#btn-example-rules")?.addEventListener("click", async () => {
      const panel = $("#example-rules-panel");
      const btn = $("#btn-example-rules");
      if (!panel || !btn) return;
      btn.disabled = true;
      panel.hidden = false;
      panel.innerHTML = `<div class="muted">Üretiliyor…</div>`;
      try {
        const ex = await api(`/api/proposals/${encodeURIComponent(p.proposalId)}/example-rules`);
        const c = ex.consistency || {};
        const levelCls =
          c.level === "high" ? "ok" : c.level === "medium" ? "warn" : "danger";
        panel.innerHTML = `
          <div class="banner ${c.level === "high" ? "info" : c.level === "medium" ? "warn" : "danger"}" style="margin-top:12px">
            <strong>${esc(c.levelLabel || "")}</strong>
            · skor ${esc(c.score)}/${esc(c.maxScore)}
            <div style="margin-top:6px">${esc(ex.disclaimer || "")}</div>
          </div>
          <div class="chips" style="margin:10px 0">
            <span class="pill ${levelCls}">${esc(c.levelLabel || c.level || "")}</span>
            ${(c.reasons || []).slice(0, 6).map((r) => `<span class="pill muted-pill">${esc(r)}</span>`).join("")}
          </div>
          ${(ex.examples || []).map((item) => `
            <div class="example-card">
              <h4>${esc(item.title || "Örnek kural")}</h4>
              <p>${esc(item.text || "")}</p>
            </div>
          `).join("") || `<div class="empty">Örnek üretilemedi</div>`}
          <details style="margin-top:10px">
            <summary class="muted">Kullanılan alanlar / üretim notu</summary>
            <p class="muted">${esc(ex.howGenerated || "")}</p>
            ${fieldsTable(ex.usedFields || {})}
          </details>
        `;
        btn.textContent = "Örnek kural önerilerini yenile";
      } catch (err) {
        panel.innerHTML = `<div class="banner danger">${esc(err.message || err)}</div>`;
      } finally {
        btn.disabled = false;
      }
    });
  }

  function renderDecisions() {
    setTitle("Uzman demo kararları", "Local storage — gerçek onay değildir");
    setActiveNav("decisions");
    showView("decisions");
    const all = loadDecisions();
    const entries = Object.values(all).sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));
    views.decisions.innerHTML = `
      <div class="banner warn">Demo taslağıdır; gerçek onay değildir. Kaynak JSON/CSV dosyaları değiştirilmez.</div>
      <div class="panel">
        <div class="toolbar">
          <button type="button" class="btn secondary" id="btn-clear-decisions">Tüm demo kararlarını sil</button>
          <button type="button" class="btn primary" id="btn-export-2">JSON dışa aktar</button>
        </div>
        <table class="data">
          <thead><tr><th>Proposal</th><th>Karar</th><th>İşlem</th><th>Not</th><th>Zaman</th></tr></thead>
          <tbody>
            ${entries.map((d) => `<tr>
              <td><a href="#/proposals/${esc(d.proposalId)}">${esc(d.proposalId)}</a></td>
              <td>${esc(d.decision)}</td>
              <td class="mono">${esc(d.procedureKod || "—")}</td>
              <td>${esc(d.note || "—")}</td>
              <td class="mono">${esc(d.updatedAt || "")}</td>
            </tr>`).join("") || `<tr><td colspan="5" class="empty">Henüz demo kararı yok</td></tr>`}
          </tbody>
        </table>
      </div>`;
    $("#btn-clear-decisions")?.addEventListener("click", () => {
      if (confirm("Tüm demo kararları silinsin mi?")) {
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

  async function renderHelp() {
    const gen = routeGen;
    setTitle("Yardım", "Uzman inceleme ve kural adayı değerlendirme rehberi");
    setActiveNav("help");
    showView("help");
    views.help.innerHTML = `<div class="panel"><div class="muted">Yardım yükleniyor…</div></div>`;
    const data = await api("/api/help");
    if (isRouteStale(gen)) return;
    views.help.innerHTML = `
      <div class="banner warn">
        Bu ekran canlı kural yayınlamaz. Rehber; adayları nasıl okuyacağınızı ve resmi kurala geçmeden önce neye bakacağınızı anlatır.
      </div>
      <div class="panel help-doc">
        ${mdToHtml(data.markdown || "")}
      </div>
      <div class="panel">
        <a class="btn primary" href="#/proposals">Kural önerileri listesine dön</a>
      </div>
    `;
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
        if (state.summary.safety?.rawEnabled) {
          rawBadge.textContent = "Ham cevap açık";
          rawBadge.className = "pill warn";
        }
      }
      const r = parseRoute();
      if (isRouteStale(gen)) return;
      state.route = r;
      if (r.name === "dashboard") await renderDashboard();
      else if (r.name === "proposals") await renderProposals();
      else if (r.name === "proposal") await renderProposal(r.id);
      else if (r.name === "decisions") await renderDecisions();
      else if (r.name === "help") await renderHelp();
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
