/**
 * Mystery Search detail page — /api/page by doc_id.
 * Layout: nav | content (collapsible top-level sections) | image carousel + infobox
 */

async function fetchPage(docId) {
  const res = await fetch(apiUrl("/api/page", { doc_id: docId }));
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : detail || `Page load failed (${res.status})`;
    throw new Error(message);
  }
  return res.json();
}

function slugId(text) {
  return String(text || "section")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "") || "section";
}

function topLevelGroupKey(section) {
  if (section.is_lead || section.section_title === "Summary") {
    return { id: "summary", label: "Summary" };
  }
  const bc = section.breadcrumb || [];
  if (bc.length >= 2) {
    const label = bc[1];
    return { id: slugId(label), label };
  }
  return { id: section.section_id, label: section.section_title };
}

function sectionOrderKey(section, fallbackIndex) {
  if (section.chunk_order != null && section.chunk_order !== "") {
    const order = Number(section.chunk_order);
    if (!Number.isNaN(order)) {
      return order;
    }
  }
  return fallbackIndex;
}

function groupSections(sections) {
  const groupMap = new Map();
  const order = [];

  sections.forEach((section, index) => {
    const key = topLevelGroupKey(section);
    if (!groupMap.has(key.id)) {
      groupMap.set(key.id, {
        id: key.id,
        label: key.label,
        sections: [],
      });
      order.push(key.id);
    }
    groupMap.get(key.id).sections.push({ ...section, _origIndex: index });
  });

  const groups = order.map((id) => {
    const group = groupMap.get(id);
    group.sections.sort(
      (a, b) =>
        sectionOrderKey(a, a._origIndex) - sectionOrderKey(b, b._origIndex)
    );
    const groupOrder = group.sections.length
      ? sectionOrderKey(group.sections[0], group.sections[0]._origIndex)
      : 0;
    group.sections = group.sections.map(({ _origIndex, ...section }) => section);
    return { ...group, _groupOrder: groupOrder };
  });

  groups.sort((a, b) => a._groupOrder - b._groupOrder);
  return groups.map(({ _groupOrder, ...group }) => group);
}

function collectPageImages(sections) {
  const seen = new Set();
  const urls = [];
  for (const section of sections) {
    for (const url of section.images || []) {
      if (url && !seen.has(url)) {
        seen.add(url);
        urls.push(url);
      }
    }
  }
  return urls;
}

function renderBadges(data) {
  const chips = ['<span class="badge badge-wiki">Wikipedia</span>'];
  if (data.case_status) {
    chips.push(
      `<span class="badge badge-status">${escapeHtml(data.case_status)}</span>`
    );
  }
  if (data.country) {
    chips.push(
      `<span class="badge badge-status">${escapeHtml(data.country)}</span>`
    );
  }
  if (data.primary_location) {
    chips.push(
      `<span class="badge badge-status">${escapeHtml(data.primary_location)}</span>`
    );
  }
  if (data.category) {
    chips.push(
      `<span class="badge badge-status">${escapeHtml(data.category)}</span>`
    );
  }
  return chips.join("");
}

function renderContentParagraphs(text) {
  const parts = String(text || "")
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
  if (parts.length === 0) {
    return '<p class="section-empty">No text for this section.</p>';
  }
  return parts.map((p) => `<p>${escapeHtml(p)}</p>`).join("");
}

function renderBreadcrumbHeadings(section) {
  const bc = section.breadcrumb || [];
  if (bc.length <= 2) {
    return "";
  }

  const maxTag = 6;
  return bc
    .slice(2)
    .map((label, i) => {
      const tag = Math.min(3 + i, maxTag);
      return `<h${tag} class="subsection-heading subsection-depth-${i}">${escapeHtml(label)}</h${tag}>`;
    })
    .join("");
}

function renderGroupSection(section) {
  return `
    <article class="detail-subsection">
      ${renderBreadcrumbHeadings(section)}
      <div class="section-body">${renderContentParagraphs(section.content)}</div>
    </article>
  `;
}

function renderGroup(group) {
  const body = group.sections.map(renderGroupSection).join("");

  return `
    <details class="detail-group" id="${escapeHtml(group.id)}" open>
      <summary class="detail-group-summary">${escapeHtml(group.label)}</summary>
      <div class="detail-group-body">${body}</div>
    </details>
  `;
}

function renderNavLinks(groups) {
  return groups
    .map(
      (g) =>
        `<li><a class="detail-nav-link" href="#${escapeHtml(g.id)}" data-target="${escapeHtml(g.id)}">${escapeHtml(g.label)}</a></li>`
    )
    .join("");
}

function renderInfobox(data) {
  const rows = data.infobox_rows || [];
  if (rows.length === 0) {
    if (!data.infobox) return "";
    return `
      <aside class="detail-infobox">
        <h2 class="sidebar-heading">Key facts</h2>
        <p>${escapeHtml(data.infobox)}</p>
      </aside>`;
  }

  const items = rows
    .map((row) => {
      if (row.label) {
        return `<div class="infobox-row"><dt>${escapeHtml(row.label)}</dt><dd>${escapeHtml(row.value)}</dd></div>`;
      }
      return `<div class="infobox-row infobox-row-plain"><dd>${escapeHtml(row.value)}</dd></div>`;
    })
    .join("");

  return `
    <aside class="detail-infobox">
      <h2 class="sidebar-heading">Key facts</h2>
      <dl class="infobox-list">${items}</dl>
    </aside>`;
}

function renderCarousel(images) {
  if (!images.length) {
    return `
      <div class="detail-carousel detail-carousel-empty">
        <h2 class="sidebar-heading">Images</h2>
        <p class="section-empty">No images for this page.</p>
      </div>`;
  }

  return `
    <div class="detail-carousel" id="detail-carousel" data-count="${images.length}">
      <h2 class="sidebar-heading">Images</h2>
      <div class="carousel-viewport">
        <img class="carousel-image is-visible" src="${escapeHtml(images[0])}" alt="" referrerpolicy="no-referrer" />
      </div>
      <div class="carousel-controls">
        <button type="button" class="carousel-btn" id="carousel-prev" aria-label="Previous image" disabled>←</button>
        <span class="carousel-counter" id="carousel-counter">1 / ${images.length}</span>
        <button type="button" class="carousel-btn" id="carousel-next" aria-label="Next image"${images.length <= 1 ? " disabled" : ""}>→</button>
      </div>
    </div>`;
}

function renderRelatedLoading() {
  return `
    <aside class="detail-related detail-related-loading" id="detail-related">
      <h2 class="sidebar-heading">Related cases</h2>
      <p class="section-empty">Finding related cases…</p>
    </aside>`;
}

function renderRelatedPanel(hits) {
  if (!hits.length) {
    return "";
  }

  const items = hits
    .map(
      (hit) => `
    <li class="related-case">
      <a href="${detailUrl(hit.doc_id)}">${escapeHtml(hit.title)}</a>
      <p class="related-snippet">${escapeHtml(hit.snippet)}</p>
    </li>`
    )
    .join("");

  return `
    <aside class="detail-related" id="detail-related">
      <h2 class="sidebar-heading">Related cases</h2>
      <ul class="related-list">${items}</ul>
    </aside>`;
}

async function fetchRelated(docId) {
  const res = await fetch(apiUrl("/api/related", { doc_id: docId, size: 6 }));
  if (!res.ok) {
    return [];
  }
  const data = await res.json();
  return data.related || [];
}

async function loadRelated(root, docId) {
  const sidebar = root.querySelector(".detail-sidebar");
  if (!sidebar) {
    return;
  }

  sidebar.insertAdjacentHTML("beforeend", renderRelatedLoading());

  try {
    const related = await fetchRelated(docId);
    const panel = root.querySelector("#detail-related");
    if (!panel) {
      return;
    }
    if (related.length) {
      panel.outerHTML = renderRelatedPanel(related);
    } else {
      panel.remove();
    }
  } catch {
    root.querySelector("#detail-related")?.remove();
  }
}

function renderPage(data) {
  const groups = groupSections(data.sections || []);
  const images = collectPageImages(data.sections || []);

  const wikiLink = data.url
    ? `<p class="detail-external"><a href="${escapeHtml(data.url)}" target="_blank" rel="noopener noreferrer">View on Wikipedia</a></p>`
    : "";

  const locationNote =
    data.primary_location_explicit === false
      ? '<p class="detail-note">Location may be inferred from article text.</p>'
      : "";

  const navOptions = groups
    .map(
      (g) =>
        `<option value="${escapeHtml(g.id)}">${escapeHtml(g.label)}</option>`
    )
    .join("");

  return `
    <select class="detail-nav-mobile" id="detail-nav-mobile" aria-label="Jump to section">
      <option value="">Jump to section…</option>
      ${navOptions}
    </select>

    <div class="detail-layout">
      <aside class="detail-nav-col" aria-label="Section navigation">
        <nav class="detail-nav">
          <h2 class="nav-heading">Index</h2>
          <ul class="detail-nav-list">${renderNavLinks(groups)}</ul>
        </nav>
      </aside>

      <main class="detail-main">
        <header class="detail-header">
          <h1 class="detail-title">${escapeHtml(data.title)}</h1>
          <div class="row-badges">${renderBadges(data)}</div>
          ${wikiLink}
          ${locationNote}
        </header>
        <div class="detail-groups">
          ${groups.map((g) => renderGroup(g)).join("")}
        </div>
      </main>

      <aside class="detail-sidebar">
        ${renderCarousel(images)}
        ${renderInfobox(data)}
      </aside>
    </div>`;
}

function bindDetailSearch() {
  const form = document.getElementById("detail-search-form");
  const input = document.getElementById("detail-q");
  if (!form || !input) return;

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q) goToSearch(q);
  });
}

function bindNavigation(root) {
  const mobile = root.querySelector("#detail-nav-mobile");
  const links = root.querySelectorAll(".detail-nav-link");

  function jumpTo(id) {
    if (!id) return;
    const el = root.querySelector(`#${CSS.escape(id)}`);
    if (!el) return;
    el.open = true;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    links.forEach((a) => {
      a.classList.toggle("is-active", a.dataset.target === id);
    });
    if (mobile) mobile.value = id;
  }

  links.forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      jumpTo(a.dataset.target);
    });
  });

  if (mobile) {
    mobile.addEventListener("change", () => jumpTo(mobile.value));
  }

  if (links.length) {
    links[0].classList.add("is-active");
  }
}

function bindCarousel(root, images) {
  if (!images.length) return;

  const img = root.querySelector(".carousel-image");
  const prev = root.querySelector("#carousel-prev");
  const next = root.querySelector("#carousel-next");
  const counter = root.querySelector("#carousel-counter");
  if (!img || !prev || !next) return;

  let index = 0;

  function update() {
    counter.textContent = `${index + 1} / ${images.length}`;
    prev.disabled = index <= 0;
    next.disabled = index >= images.length - 1;
  }

  function show(nextIndex) {
    if (nextIndex === index || nextIndex < 0 || nextIndex >= images.length) {
      return;
    }
    img.classList.remove("is-visible");
    window.setTimeout(() => {
      index = nextIndex;
      img.src = images[index];
      img.classList.add("is-visible");
      update();
    }, 180);
  }

  prev.addEventListener("click", () => show(index - 1));
  next.addEventListener("click", () => show(index + 1));
  update();
}

async function initDetailPage() {
  const root = document.getElementById("detail-root");
  if (!root) return;

  bindDetailSearch();

  const params = new URLSearchParams(window.location.search);
  const docId = params.get("doc_id");

  if (!docId) {
    root.innerHTML =
      '<p class="alert alert-warn">Missing <code>doc_id</code> in URL.</p>';
    return;
  }

  root.innerHTML = '<p class="loading">Loading…</p>';

  try {
    const data = await fetchPage(docId);
    document.title = `${data.title} — Mystery Search`;
    const images = collectPageImages(data.sections || []);
    root.innerHTML = renderPage(data);
    bindNavigation(root);
    bindCarousel(root, images);
    loadRelated(root, docId);
  } catch (err) {
    root.innerHTML = `<div class="alert alert-warn"><strong>Could not load page.</strong> ${escapeHtml(
      err.message
    )}</div>`;
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initDetailPage);
} else {
  initDetailPage();
}
