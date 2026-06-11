// ===================== DATA =====================
const documents = [
  { name: "Test document ...",  tag: "Jobs",     tagClass: "orange", recipient: "Stiv Rogers",  date: "1/03/2023",  status: "Pending",  statusClass: "pending",  modified: "2 hours ago" },
  { name: "Job Offer",          tag: "Jobs",     tagClass: "orange", recipient: "Donna Prince", date: "3/03/2023",  status: "Sent",     statusClass: "sent",     modified: "a week ago" },
  { name: "Quote contract",     tag: "Quotes",   tagClass: "purple", recipient: "Stiv Rogers",  date: "4/03/2023",  status: "Approved", statusClass: "approved", modified: "15 minutes ago" },
  { name: "Work order contract",tag: "Jobs",     tagClass: "orange", recipient: "Donna Prince", date: "5/03/2022",  status: "Pending",  statusClass: "pending",  modified: "1 year ago" },
  { name: "Request contract",   tag: "Requests", tagClass: "teal",   recipient: "Stiv Rogers",  date: "7/03/2023",  status: "Sent",     statusClass: "sent",     modified: "15 minutes ago" },
  { name: "Quote contract",     tag: "Quotes",   tagClass: "purple", recipient: "Donna Prince", date: "7/03/2023",  status: "Approved", statusClass: "approved", modified: "10 minutes ago" },
  { name: "Work order contract",tag: "Jobs",     tagClass: "orange", recipient: "Stiv Rogers",  date: "10/03/2023", status: "Pending",  statusClass: "pending",  modified: "a week ago" },
  { name: "Request contract",   tag: "Requests", tagClass: "teal",   recipient: "Donna Prince", date: "11/03/2023", status: "Sent",     statusClass: "sent",     modified: "2 weeks ago" },
  { name: "Request contract",   tag: "Requests", tagClass: "teal",   recipient: "Stiv Rogers",  date: "12/03/2023", status: "Approved", statusClass: "approved", modified: "2 hours ago" },
  { name: "Quote contract",     tag: "Quotes",   tagClass: "purple", recipient: "Donna Prince", date: "14/03/2023", status: "Pending",  statusClass: "pending",  modified: "5 hours ago" },
  { name: "Quote contract",     tag: "Quotes",   tagClass: "purple", recipient: "Stiv Rogers",  date: "15/03/2023", status: "Sent",     statusClass: "sent",     modified: "10 minutes ago" },
  { name: "Work order contract",tag: "Jobs",     tagClass: "orange", recipient: "Donna Prince", date: "18/03/2023", status: "Approved", statusClass: "approved", modified: "40 minutes ago" },
];

// Active filter state
let activeStatusFilter = null;
let activeTagFilter    = null;

function getFilteredDocs() {
  return documents.filter(doc => {
    const statusOk = !activeStatusFilter || doc.status === activeStatusFilter;
    const tagOk    = !activeTagFilter    || doc.tag    === activeTagFilter;
    return statusOk && tagOk;
  });
}

// ===================== RENDER TABLE =====================
function renderTable(data) {
  const tbody = document.getElementById("tableBody");
  tbody.innerHTML = "";

  data.forEach((doc, idx) => {
    const realIdx = documents.indexOf(doc);
    const tr = document.createElement("tr");
    tr.dataset.docIdx = realIdx;
    tr.innerHTML = `
      <td><input type="checkbox" class="row-check" /></td>
      <td><i class="fa-solid fa-link link-icon"></i></td>
      <td>
        <div class="doc-name-cell">
          <span class="doc-name doc-name-link">${doc.name}</span>
          <span class="doc-tag ${doc.tagClass}">${doc.tag}</span>
        </div>
      </td>
      <td class="recipient">${doc.recipient}</td>
      <td>${doc.date}</td>
      <td>
        <div class="status-cell">
          <span class="status-dot ${doc.statusClass}"></span>
          ${doc.status}
        </div>
      </td>
      <td class="modified">${doc.modified}</td>
      <td>
        <div class="row-actions">
          <button class="row-btn btn-download" title="Download"><i class="fa-solid fa-download"></i></button>
          <button class="row-btn btn-edit"     title="Edit"><i class="fa-solid fa-pen"></i></button>
          <button class="row-btn btn-more"     title="More"><i class="fa-solid fa-grip-vertical"></i></button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

// ===================== SELECT ALL =====================
function initSelectAll() {
  const selectAll = document.getElementById("selectAll");
  // Clone to remove stale listeners
  const fresh = selectAll.cloneNode(true);
  selectAll.replaceWith(fresh);

  fresh.addEventListener("change", () => {
    document.querySelectorAll(".row-check").forEach(cb => cb.checked = fresh.checked);
  });
  document.getElementById("tableBody").addEventListener("change", (e) => {
    if (!e.target.classList.contains("row-check")) return;
    fresh.checked = [...document.querySelectorAll(".row-check")].every(cb => cb.checked);
  });
}

// ===================== SEARCH =====================
function initSearch() {
  document.querySelector(".search-input").addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase().trim();
    const base = getFilteredDocs();
    const filtered = base.filter(doc =>
      doc.name.toLowerCase().includes(q) ||
      doc.recipient.toLowerCase().includes(q) ||
      doc.tag.toLowerCase().includes(q) ||
      doc.status.toLowerCase().includes(q)
    );
    renderTable(filtered);
    initSelectAll();
  });
}

// ===================== LEFT PANEL FILTERS =====================
function clearAllPanelFilters() {
  activeStatusFilter = null;
  activeTagFilter    = null;
  document.querySelectorAll(".panel-item").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".tag").forEach(t => t.classList.remove("active-tag"));
  renderTable(documents);
  initSelectAll();
}

function initFilters() {
  // "All Documents" clears all filters
  const allDocsItem = document.querySelector(".panel-item[data-all]");
  if (allDocsItem) {
    allDocsItem.addEventListener("click", () => {
      clearAllPanelFilters();
      allDocsItem.classList.add("active");
    });
  }

  // Status dot filters
  document.querySelectorAll(".panel-item[data-status]").forEach(item => {
    item.addEventListener("click", () => {
      const status = item.dataset.status;
      const isActive = item.classList.contains("active");

      document.querySelectorAll(".panel-item").forEach(el => el.classList.remove("active"));

      if (isActive) {
        activeStatusFilter = null;
      } else {
        item.classList.add("active");
        activeStatusFilter = status;
      }

      renderTable(getFilteredDocs());
      initSelectAll();
    });
  });

  // Tag filters
  document.querySelectorAll(".tag").forEach(tag => {
    tag.addEventListener("click", () => {
      const label = tag.textContent.trim();
      const isActive = tag.classList.contains("active-tag");

      document.querySelectorAll(".tag").forEach(t => t.classList.remove("active-tag"));

      if (isActive) {
        activeTagFilter = null;
      } else {
        tag.classList.add("active-tag");
        activeTagFilter = label;
      }

      renderTable(getFilteredDocs());
      initSelectAll();
    });
  });
}

// ===================== SORT =====================
function initSort() {
  let sortState = { col: null, asc: true };
  document.querySelector(".doc-table thead").addEventListener("click", (e) => {
    const th = e.target.closest("th");
    if (!th) return;
    const headers = [...document.querySelectorAll(".doc-table th")];
    const sortMap = { 2:"name", 3:"recipient", 4:"date", 5:"status", 6:"modified" };
    const field = sortMap[headers.indexOf(th)];
    if (!field) return;

    sortState.asc = sortState.col === field ? !sortState.asc : true;
    sortState.col = field;

    const sorted = [...getFilteredDocs()].sort((a, b) =>
      sortState.asc ? a[field].toLowerCase().localeCompare(b[field].toLowerCase())
                    : b[field].toLowerCase().localeCompare(a[field].toLowerCase())
    );
    renderTable(sorted);
    initSelectAll();
  });
}

// ===================== MORE MENU (Delete) =====================
let activeMoreMenu = null;

function closeMoreMenu() {
  if (activeMoreMenu) {
    activeMoreMenu.remove();
    activeMoreMenu = null;
  }
}

function initMoreMenu() {
  document.getElementById("tableBody").addEventListener("click", (e) => {
    const moreBtn = e.target.closest(".btn-more");
    if (!moreBtn) { closeMoreMenu(); return; }
    e.stopPropagation();

    closeMoreMenu();

    const tr     = moreBtn.closest("tr");
    const docIdx = parseInt(tr.dataset.docIdx);

    const menu = document.createElement("div");
    menu.className = "more-menu";
    menu.innerHTML = `
      <button class="more-menu-item more-menu-delete" data-idx="${docIdx}">
        <i class="fa-regular fa-trash-can"></i> Delete
      </button>
    `;

    const rect = moreBtn.getBoundingClientRect();
    menu.style.top  = (rect.bottom + window.scrollY + 4) + "px";
    menu.style.left = (rect.left  + window.scrollX - 80) + "px";
    document.body.appendChild(menu);
    activeMoreMenu = menu;

    menu.querySelector(".more-menu-delete").addEventListener("click", () => {
      documents.splice(docIdx, 1);
      renderTable(getFilteredDocs());
      initSelectAll();
      closeMoreMenu();
    });
  });

  document.addEventListener("click", closeMoreMenu);
}

// ===================== DOCUMENT DETAIL PANEL (RIGHT) — read-only, opened by doc name =====================
let editingDocIdx = null; // track which doc is open in the edit modal

function openDetailPanel(docIdx) {
  const doc = documents[docIdx];
  if (!doc) return;
  const panel = document.getElementById("docDetailPanel");

  document.getElementById("ddpDocName").textContent   = doc.name;
  document.getElementById("ddpOwner").textContent     = "kate23@gmail.com";
  document.getElementById("ddpCreated").textContent   = doc.date;
  document.getElementById("ddpRecipient").textContent = doc.recipient.replace(" ", "").toLowerCase() + "@gmail.com";

  const tagEl = document.getElementById("ddpTag");
  tagEl.textContent = doc.status;
  tagEl.className   = "ddp-tag " + doc.statusClass;

  panel.classList.add("open");
}

function initDocDetailPanel() {
  const panel    = document.getElementById("docDetailPanel");
  const closeBtn = document.getElementById("docDetailClose");

  // Click doc NAME → open read-only detail panel
  document.getElementById("tableBody").addEventListener("click", (e) => {
    const nameEl = e.target.closest(".doc-name-link");
    if (!nameEl) return;
    const tr     = nameEl.closest("tr");
    const docIdx = parseInt(tr.dataset.docIdx);
    openDetailPanel(docIdx);
  });

  closeBtn.addEventListener("click", () => panel.classList.remove("open"));

  // Click EDIT button → open editable Document Details modal
  document.getElementById("tableBody").addEventListener("click", (e) => {
    const editBtn = e.target.closest(".btn-edit");
    if (!editBtn) return;
    const tr     = editBtn.closest("tr");
    const docIdx = parseInt(tr.dataset.docIdx);
    openDocDetailsModal(docIdx);
  });
}

// ===================== ADD DOCUMENT MODAL =====================
function initAddDocModal() {
  const overlay    = document.getElementById("addDocOverlay");
  const modal      = document.getElementById("addDocModal");
  const openBtn    = document.querySelector(".btn-add");
  const closeBtn   = document.getElementById("addDocClose");
  const dropZone   = document.getElementById("addDropZone");
  const fileInput  = document.getElementById("addFileInput");
  const selectBtn  = document.getElementById("addSelectBtn");
  const fileNameEl = document.getElementById("addFileName");

  function openModal()  { overlay.classList.add("open");    modal.classList.add("open"); }
  function closeModal() {
    overlay.classList.remove("open");
    modal.classList.remove("open");
    fileNameEl.textContent = "";
    fileInput.value = "";
    dropZone.classList.remove("drag-over");
  }

  openBtn.addEventListener("click",  openModal);
  closeBtn.addEventListener("click", closeModal);
  overlay.addEventListener("click",  closeModal);

  selectBtn.addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
  dropZone.addEventListener("click",  () => fileInput.click());

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (!file) return;
    fileNameEl.textContent = file.name;
    fileNameEl.style.color = "#4caf82";

    // After short delay close file picker modal and open Document Details modal
    setTimeout(() => {
      closeModal();
      openDocDetailsModal(file.name.replace(/\.[^/.]+$/, ""));
    }, 700);
  });

  dropZone.addEventListener("dragover",  (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (!file) return;
    fileNameEl.textContent = file.name;
    fileNameEl.style.color = "#4caf82";
    setTimeout(() => {
      closeModal();
      openDocDetailsModal(file.name.replace(/\.[^/.]+$/, ""));
    }, 700);
  });
}

// ===================== DOCUMENT DETAILS MODAL =====================
// docIdx = null means "new doc", a number means "edit existing"
function openDocDetailsModal(prefillNameOrIdx = "") {
  const overlay = document.getElementById("docDetailsOverlay");
  const modal   = document.getElementById("docDetailsModal");

  if (typeof prefillNameOrIdx === "number") {
    // Edit mode — pre-fill from existing doc
    editingDocIdx = prefillNameOrIdx;
    const doc = documents[prefillNameOrIdx];
    document.getElementById("ddmDocName").value    = doc.name;
    document.getElementById("ddmRecipient").value  = doc.recipient;
    document.getElementById("ddmContinue").textContent = "Save Changes";
  } else {
    // New doc mode
    editingDocIdx = null;
    if (prefillNameOrIdx) document.getElementById("ddmDocName").value = prefillNameOrIdx;
    document.getElementById("ddmContinue").textContent = "Continue";
  }

  overlay.classList.add("open");
  modal.classList.add("open");
}

function closeDocDetailsModal() {
  document.getElementById("docDetailsOverlay").classList.remove("open");
  document.getElementById("docDetailsModal").classList.remove("open");
  editingDocIdx = null;
  ["ddmDocName","ddmRecipient","ddmCompany","ddmFirstName","ddmLastName",
   "ddmPhone","ddmEmail","ddmAddress","ddmPostal","ddmCountry","ddmProvince"]
    .forEach(id => { const el = document.getElementById(id); if(el) el.value = ""; });
}

function initDocDetailsModal() {
  document.getElementById("docDetailsOverlay").addEventListener("click", closeDocDetailsModal);
  document.getElementById("ddmClose").addEventListener("click",          closeDocDetailsModal);

  document.getElementById("ddmSave").addEventListener("click", () => {
    // Save but stay open — just visual feedback
    const btn = document.getElementById("ddmSave");
    btn.textContent = "Saved!";
    btn.style.background = "#4caf82";
    setTimeout(() => { btn.textContent = "Save"; btn.style.background = ""; }, 1500);
  });

  document.getElementById("ddmContinue").addEventListener("click", () => {
    const name      = document.getElementById("ddmDocName").value.trim()   || "New Document";
    const recipient = document.getElementById("ddmRecipient").value.trim() || "Stiv Rogers";
    const today     = new Date();
    const dateStr   = `${String(today.getDate()).padStart(2,"0")}/${String(today.getMonth()+1).padStart(2,"0")}/${today.getFullYear()}`;

    if (editingDocIdx !== null) {
      // Edit existing document in place
      const doc = documents[editingDocIdx];
      doc.name      = name;
      doc.recipient = recipient;
      doc.modified  = "just now";
    } else {
      // Add new document
      documents.unshift({
        name, tag: "Jobs", tagClass: "orange", recipient, date: dateStr,
        status: "Pending", statusClass: "pending", modified: "just now",
      });
    }

    renderTable(getFilteredDocs());
    initSelectAll();
    closeDocDetailsModal();
  });
}

// ===================== FILTER PANEL (RIGHT SIDE) =====================
function initFilterPanel() {
  const overlay   = document.getElementById("filterOverlay");
  const panel     = document.getElementById("filterPanel");
  const openBtn   = document.querySelector(".btn-filter");
  const closeBtn  = document.getElementById("filterClose");
  const resetBtn  = document.getElementById("fpReset");
  const applyBtn  = document.getElementById("fpApply");

  function openPanel()  { overlay.classList.add("open");    panel.classList.add("open"); }
  function closePanel() { overlay.classList.remove("open"); panel.classList.remove("open"); }

  openBtn.addEventListener("click",  openPanel);
  closeBtn.addEventListener("click", closePanel);
  overlay.addEventListener("click",  closePanel);

  // DATE SELECT
  const dateSelect   = document.getElementById("fpDateSelect");
  const calendarWrap = document.getElementById("fpCalendarWrap");
  const fpDateInput  = document.getElementById("fpDateInput");
  const fpCalendar   = document.getElementById("fpCalendar");
  const fpCalToggle  = document.getElementById("fpCalToggle");

  dateSelect.addEventListener("change", () => {
    calendarWrap.style.display = dateSelect.value === "custom" ? "block" : "none";
  });

  // CALENDAR
  let calDate = new Date(), selectedDate = null;
  const MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
  const DAYS   = ["SU","MO","TU","WE","TH","FR","SA"];

  function renderCalendar() {
    const year = calDate.getFullYear(), month = calDate.getMonth();
    document.getElementById("calMonthLabel").textContent = `${MONTHS[month]} ${year}`;
    const grid = document.getElementById("calGrid");
    grid.innerHTML = "";
    DAYS.forEach(d => { const el = document.createElement("div"); el.className="cal-day-name"; el.textContent=d; grid.appendChild(el); });
    const firstDay = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month+1, 0).getDate();
    const today = new Date();
    for (let i=0; i<firstDay; i++) { const e=document.createElement("div"); e.className="cal-day other-month"; grid.appendChild(e); }
    for (let d=1; d<=daysInMonth; d++) {
      const el = document.createElement("div"); el.className="cal-day"; el.textContent=d;
      const thisDate = new Date(year, month, d);
      if (today.toDateString()===thisDate.toDateString()) el.classList.add("today");
      if (selectedDate && selectedDate.toDateString()===thisDate.toDateString()) el.classList.add("selected");
      el.addEventListener("click", () => {
        selectedDate = new Date(year, month, d);
        fpDateInput.value = `${String(d).padStart(2,"0")}/${String(month+1).padStart(2,"0")}/${year}`;
        renderCalendar();
      });
      grid.appendChild(el);
    }
  }
  document.getElementById("calPrev").addEventListener("click", () => { calDate.setMonth(calDate.getMonth()-1); renderCalendar(); });
  document.getElementById("calNext").addEventListener("click", () => { calDate.setMonth(calDate.getMonth()+1); renderCalendar(); });
  fpCalToggle.addEventListener("click", () => { fpCalendar.style.display = fpCalendar.style.display==="none"?"block":"none"; });
  renderCalendar();

  // RECIPIENT SEARCH
  const recipientSearch = document.getElementById("fpRecipientSearch");
  const radioItems      = document.querySelectorAll(".fp-radio-item");
  recipientSearch.addEventListener("input", () => {
    const q = recipientSearch.value.toLowerCase();
    radioItems.forEach(item => {
      const label = item.querySelector("label").textContent.toLowerCase();
      item.style.display = (label.includes(q) || label==="select all") ? "" : "none";
    });
  });

  // APPLY
  applyBtn.addEventListener("click", () => {
    const recipient = document.querySelector('input[name="recipient"]:checked')?.value || "";
    const dateVal   = dateSelect.value;
    let filtered = [...documents];
    if (recipient) filtered = filtered.filter(doc => doc.recipient === recipient);
    if (dateVal==="custom" && selectedDate) {
      filtered = filtered.filter(doc => {
        const [d,m,y] = doc.date.split("/");
        return new Date(+y, +m-1, +d).toDateString() === selectedDate.toDateString();
      });
    }
    renderTable(filtered);
    initSelectAll();
    closePanel();
  });

  // RESET
  resetBtn.addEventListener("click", () => {
    dateSelect.value = "today";
    calendarWrap.style.display = "none";
    selectedDate = null; fpDateInput.value = "";
    document.getElementById("rAll").checked = true;
    recipientSearch.value = "";
    radioItems.forEach(item => item.style.display = "");
    renderCalendar();
    renderTable(documents);
    initSelectAll();
  });
}

// ===================== INIT =====================
document.addEventListener("DOMContentLoaded", () => {
  renderTable(documents);
  initSelectAll();
  initSearch();
  initFilters();
  initSort();
  initMoreMenu();
  initDocDetailPanel();
  initAddDocModal();
  initDocDetailsModal();
  initFilterPanel();
});