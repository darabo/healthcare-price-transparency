const messages = document.querySelector("#messages");
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const sendButton = document.querySelector("#sendButton");
const details = document.querySelector("#details");
const detailContent = document.querySelector("#detailContent");
const factsPanel = document.querySelector("#caseFacts");
const fileInput = document.querySelector("#fileInput");

let caseId = null;
let lastResponse = null;
let activeTab = "comparison";

document.querySelectorAll(".preset").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.dataset.prompt;
    input.focus();
  });
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    activeTab = tab.dataset.tab;
    document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === tab));
    renderDetails();
  });
});

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) return;
  const text = await file.text();
  input.value = `${input.value.trim()}\n\nUploaded bill/estimate text:\n${text}`.trim();
  fileInput.value = "";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;

  appendMessage("user", message);
  input.value = "";
  sendButton.disabled = true;

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, case_id: caseId }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Request failed");
    caseId = payload.case_id;
    lastResponse = payload;
    appendMessage("assistant", payload.answer, payload.guardrails);
    updateFacts(payload);
    details.hidden = false;
    renderDetails();
  } catch (error) {
    appendMessage("assistant", `Request failed: ${error.message}`);
  } finally {
    sendButton.disabled = false;
    input.focus();
  }
});

function appendMessage(role, text, guardrails = []) {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = `<p>${escapeHtml(text)}</p>`;
  if (guardrails.length) {
    const list = document.createElement("ul");
    guardrails.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      list.appendChild(li);
    });
    bubble.appendChild(list);
  }
  article.appendChild(bubble);
  messages.appendChild(article);
  messages.scrollTop = messages.scrollHeight;
}

function updateFacts(payload) {
  const facts = payload.facts;
  factsPanel.innerHTML = `
    <div><dt>Type</dt><dd>${escapeHtml(payload.case_type)}</dd></div>
    <div><dt>Confidence</dt><dd>${escapeHtml(facts.confidence)}</dd></div>
    <div><dt>CPT</dt><dd>${escapeHtml(facts.cpt_candidates.join(", ") || "-")}</dd></div>
    <div><dt>Amount</dt><dd>${facts.amount ? formatMoney(facts.amount) : "-"}</dd></div>
    <div><dt>Missing</dt><dd>${escapeHtml(facts.missing.join(", ") || "None")}</dd></div>
  `;
}

function renderDetails() {
  if (!lastResponse) return;
  if (activeTab === "comparison") {
    renderComparison(lastResponse);
  } else if (activeTab === "artifact") {
    renderArtifact(lastResponse.artifact);
  } else {
    detailContent.innerHTML = `<pre>${escapeHtml(JSON.stringify(lastResponse.tool_trace, null, 2))}</pre>`;
  }
}

function renderComparison(payload) {
  const cards = payload.cards;
  const rate = cards.rate_distribution;
  const cms = cards.cms_benchmark;
  const hospitals = cards.cms_hospitals || [];
  const fairness = cards.fairness;
  const options = cards.care_options || [];
  const evidence = cards.public_evidence || [];
  const mrfMatches = cards.mrf_matches || [];
  const ai = cards.ai_explanation;
  const optionCards = options.map((option) => `
    <div class="card">
      <h3>${escapeHtml(option.provider)}</h3>
      <p><strong>${formatMoney(option.estimated_allowed)}</strong> estimated allowed</p>
      <p>${escapeHtml(option.facility_type)} · ${escapeHtml(option.location)}</p>
      <p>${escapeHtml(option.network_status)}</p>
    </div>
  `).join("");

  detailContent.innerHTML = `
    <div class="cards">
      <div class="card">
        <h3>Fairness</h3>
        <div class="metric status-${escapeHtml(fairness.status)}">${escapeHtml(fairness.status.replaceAll("_", " "))}</div>
        <p>${escapeHtml(fairness.summary)}</p>
      </div>
      <div class="card">
        <h3>AI Explanation</h3>
        ${ai ? renderAi(ai) : "<p>No AI explanation was run.</p>"}
      </div>
      <div class="card">
        <h3>Benchmark</h3>
        ${rate ? `
          <p>P25 ${formatMoney(rate.p25)} · Median ${formatMoney(rate.median)} · P75 ${formatMoney(rate.p75)}</p>
          <p>${escapeHtml(rate.sample_size)} sample observations · ${escapeHtml(rate.source)}</p>
        ` : "<p>No benchmark found.</p>"}
      </div>
      <div class="card">
        <h3>CMS PPL</h3>
        ${cms ? renderCms(cms) : "<p>No CMS lookup was run.</p>"}
      </div>
      <div class="card">
        <h3>CMS Hospital Data</h3>
        ${renderHospitals(hospitals)}
      </div>
      <div class="card">
        <h3>Hospital MRF</h3>
        ${renderMrfMatches(mrfMatches)}
      </div>
      ${optionCards || `<div class="card"><h3>Care Options</h3><p>No options found in the sample index.</p></div>`}
      <div class="card">
        <h3>Public Evidence</h3>
        ${renderEvidence(evidence)}
      </div>
    </div>
  `;
}

function renderAi(ai) {
  const model = ai.model ? ` · ${ai.model}` : "";
  const detail = ai.status === "success"
    ? "Generated from structured CPT, benchmark, CMS, MRF, and care-option evidence."
    : (ai.error || "AI response generation is not configured.");
  return `<p><strong>${escapeHtml(ai.status)}</strong>${escapeHtml(model)}</p><p>${escapeHtml(detail)}</p>`;
}

function renderHospitals(hospitals) {
  if (!hospitals.length) return "<p>No CMS open hospital lookup was run.</p>";
  return hospitals.map((hospital) => {
    if (hospital.status !== "found") {
      return `<p>${escapeHtml(hospital.status)}</p><p>${escapeHtml(hospital.message || "No hospital match returned.")}</p>`;
    }
    return `
      <p><strong>${escapeHtml(hospital.facility_name)}</strong></p>
      <p>${escapeHtml([hospital.city, hospital.state, hospital.zip_code].filter(Boolean).join(", "))}</p>
      <p>${escapeHtml(hospital.hospital_type || "")} · Rating ${escapeHtml(hospital.rating || "n/a")}</p>
    `;
  }).join("");
}

function renderMrfMatches(matches) {
  if (!matches.length) return "<p>No hospital MRF parse was run.</p>";
  return matches.map((match) => {
    if (match.status !== "found") {
      return `<p>${escapeHtml(match.status)}</p><p>${escapeHtml(match.message || "No MRF match returned.")}</p>`;
    }
    const priceParts = [];
    if (match.negotiated_dollar) priceParts.push(`Negotiated ${formatMoney(match.negotiated_dollar)}`);
    if (match.discounted_cash) priceParts.push(`Cash ${formatMoney(match.discounted_cash)}`);
    if (match.gross_charge) priceParts.push(`Gross ${formatMoney(match.gross_charge)}`);
    if (match.median_allowed) priceParts.push(`Median allowed ${formatMoney(match.median_allowed)}`);
    return `
      <p><strong>${escapeHtml(match.description || match.code)}</strong></p>
      <p>${escapeHtml([match.payer_name, match.plan_name].filter(Boolean).join(" · ") || "All payers / not specified")}</p>
      <p>${escapeHtml(priceParts.join(" · ") || "No dollar amount in matched row")}</p>
      <p>${escapeHtml(match.hospital_name || match.source)}</p>
    `;
  }).join("");
}

function renderCms(cms) {
  if (cms.status !== "found") {
    return `<p>${escapeHtml(cms.status)}</p><p>${escapeHtml(cms.message || "No CMS benchmark returned.")}</p>`;
  }
  const parts = [];
  if (cms.hospital_outpatient_payment) parts.push(`HOPD ${formatMoney(cms.hospital_outpatient_payment)}`);
  if (cms.ambulatory_surgical_center_payment) parts.push(`ASC ${formatMoney(cms.ambulatory_surgical_center_payment)}`);
  if (cms.beneficiary_copay) parts.push(`Copay ${formatMoney(cms.beneficiary_copay)}`);
  return `<p>${escapeHtml(cms.title || cms.code)}</p><p>${escapeHtml(parts.join(" · ") || "Benchmark returned")}</p>`;
}

function renderEvidence(evidence) {
  if (!evidence.length) return "<p>No public evidence returned.</p>";
  return evidence.map((item) => `
    <p><strong>${escapeHtml(item.source)}</strong> · ${escapeHtml(item.status)}</p>
    <p>${escapeHtml(item.summary || item.title)}</p>
  `).join("");
}

function renderArtifact(artifact) {
  if (!artifact) {
    detailContent.innerHTML = "<p>No artifact generated yet. Ask for help negotiating, disputing, or appealing a bill.</p>";
    return;
  }
  detailContent.innerHTML = `
    <div class="cards">
      <div class="card"><h3>Phone Script</h3><p>${escapeHtml(artifact.phone_script)}</p></div>
      <div class="card"><h3>Email</h3><pre>${escapeHtml(artifact.email)}</pre></div>
      <div class="card"><h3>Checklist</h3><pre>${escapeHtml(artifact.checklist)}</pre></div>
    </div>
  `;
}

function formatMoney(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
