/**
 * Shared search helpers for Mystery Search UI.
 */

const SEARCH_PAGE_SIZE = 20;

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
    size: options.size || SEARCH_PAGE_SIZE,
  };
  if (options.from != null && options.from > 0) {
    params.from = options.from;
  }
  if (options.country) params.country = options.country;
  if (options.category) params.category = options.category;
  if (options.case_status) params.case_status = options.case_status;

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
  // doc_id from API already encodes the page title (wikipedia:Title%20Here)
  return `detail.html?doc_id=${docId}`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function populateCountrySelect(selectEl, selectedValue) {
  if (!selectEl || selectEl.dataset.countriesPopulated === "1") {
    if (selectEl && selectedValue) {
      selectEl.value = selectedValue;
    }
    return;
  }
  FILTER_COUNTRIES.forEach((country) => {
    const opt = document.createElement("option");
    opt.value = country;
    opt.textContent = country;
    selectEl.appendChild(opt);
  });
  selectEl.dataset.countriesPopulated = "1";
  if (selectedValue) {
    selectEl.value = selectedValue;
  }
  bindScrollableCountrySelect(selectEl);
}

/** Expand to a scrollable list on focus; collapse back to one row when done. */
function bindScrollableCountrySelect(selectEl) {
  const openRows = 10;
  const collapse = () => {
    selectEl.size = 1;
  };
  selectEl.size = 1;
  selectEl.addEventListener("focus", () => {
    selectEl.size = Math.min(openRows, selectEl.options.length);
  });
  selectEl.addEventListener("blur", collapse);
  selectEl.addEventListener("change", collapse);
}

function populateCaseStatusSelect(selectEl, selectedValue) {
  if (!selectEl || selectEl.dataset.caseStatusPopulated === "1") {
    if (selectEl && selectedValue) {
      selectEl.value = selectedValue;
    }
    return;
  }
  FILTER_CASE_STATUSES.forEach(({ label, value }) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    selectEl.appendChild(opt);
  });
  selectEl.dataset.caseStatusPopulated = "1";
  if (selectedValue) {
    selectEl.value = selectedValue;
  }
}

function renderResultCard(hit) {
  const chips = [];
  chips.push('<span class="badge badge-wiki">Wikipedia</span>');
  if (hit.case_status && hit.case_status !== "unknown") {
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

  return `
    <li class="result-card">
      <h2><a href="${detailUrl(hit.doc_id)}">${escapeHtml(hit.title)}</a></h2>
      <p class="snippet">${escapeHtml(hit.snippet)}</p>
      <div class="row-badges">${chips.join("")}</div>
    </li>
  `;
}

function appendResultCards(hits, ul) {
  hits.forEach((hit) => {
    ul.insertAdjacentHTML("beforeend", renderResultCard(hit));
  });
}

function renderResultsList(data, container, { append = false } = {}) {
  if (!append) {
    container.innerHTML = "";
  }

  if (!data.results || data.results.length === 0) {
    if (!append) {
      const empty = document.createElement("div");
      empty.className = "alert alert-warn";
      empty.innerHTML =
        "<strong>No matches</strong> in the Wikipedia mystery archive for that query. " +
        "Try a shorter keyword or one of the example searches.";
      container.appendChild(empty);
    }
    return { hasMore: false };
  }

  if (!append) {
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
    container.appendChild(ul);
  }

  const ul = container.querySelector("ul.results");
  if (ul) {
    appendResultCards(data.results, ul);
  }

  const hasMore =
    data.has_more != null
      ? data.has_more
      : data.results.length === (data.size || SEARCH_PAGE_SIZE);

  return { hasMore };
}

function goToSearch(query, extra = {}) {
  const url = new URL("search.html", window.location.href);
  url.searchParams.set("q", query);
  if (extra.country) url.searchParams.set("country", extra.country);
  if (extra.category) url.searchParams.set("category", extra.category);
  if (extra.case_status) url.searchParams.set("case_status", extra.case_status);
  window.location.href = url.toString();
}
