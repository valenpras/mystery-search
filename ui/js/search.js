/**
 * Shared search helpers for Mystery Search UI.
 */

function apiUrl(path, params) {
  const base = typeof API_BASE === "string" ? API_BASE : "";
  const url = new URL(path, window.location.origin + base + "/");
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v != null && String(v).trim() !== "") {
        url.searchParams.set(k, v);
      }
    });
  }
  return url.toString();
}

async function runSearch(query, options = {}) {
  const params = {
    q: query.trim(),
    size: options.size || 20,
  };
  if (options.country) params.country = options.country;
  if (options.category) params.category = options.category;

  const res = await fetch(apiUrl("/api/search", params));
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : detail || `Search failed (${res.status})`;
    throw new Error(message);
  }
  return res.json();
}

function detailUrl(docId) {
  return `detail.html?doc_id=${encodeURIComponent(docId)}`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function renderResultCard(hit) {
  const chips = [];
  chips.push('<span class="badge badge-wiki">Wikipedia</span>');
  if (hit.case_status) {
    chips.push(
      `<span class="badge badge-status">${escapeHtml(hit.case_status)}</span>`
    );
  }
  if (hit.country) {
    chips.push(
      `<span class="badge badge-status">${escapeHtml(hit.country)}</span>`
    );
  }
  if (hit.category) {
    chips.push(
      `<span class="badge badge-status">${escapeHtml(hit.category)}</span>`
    );
  }
  chips.push(
    `<span class="badge-score">score ${Number(hit.score).toFixed(2)}</span>`
  );

  return `
    <li class="result-card">
      <h2><a href="${detailUrl(hit.doc_id)}">${escapeHtml(hit.title)}</a></h2>
      <p class="snippet">${escapeHtml(hit.snippet)}</p>
      <div class="row-badges">${chips.join("")}</div>
    </li>
  `;
}

function renderResultsList(data, container) {
  container.innerHTML = "";

  if (!data.results || data.results.length === 0) {
    const empty = document.createElement("div");
    empty.className = "alert alert-warn";
    empty.innerHTML =
      "<strong>No matches</strong> in the Wikipedia mystery archive for that query. " +
      "Try a shorter keyword or one of the example searches.";
    container.appendChild(empty);
    return;
  }

  if (data.low_confidence) {
    const warn = document.createElement("div");
    warn.className = "alert alert-warn";
    warn.innerHTML =
      "<strong>No strong match.</strong> Top results may be weakly related. " +
      "Try refining your query.";
    container.appendChild(warn);
  }

  const ul = document.createElement("ul");
  ul.className = "results";
  data.results.forEach((hit) => {
    ul.insertAdjacentHTML("beforeend", renderResultCard(hit));
  });
  container.appendChild(ul);
}

function goToSearch(query, extra = {}) {
  const url = new URL("search.html", window.location.href);
  url.searchParams.set("q", query);
  if (extra.country) url.searchParams.set("country", extra.country);
  if (extra.category) url.searchParams.set("category", extra.category);
  window.location.href = url.toString();
}
