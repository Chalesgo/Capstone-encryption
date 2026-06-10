// ===================== DATA =====================
const documents = [
  { name: "Test document ...", tag: "Jobs",     tagClass: "orange", recipient: "Stiv Rogers",   date: "1/03/2023",  status: "Pending",  statusClass: "pending",  modified: "2 hours ago" },
  { name: "Job Offer",         tag: "Jobs",     tagClass: "orange", recipient: "Donna Prince",  date: "3/03/2023",  status: "Sent",     statusClass: "sent",     modified: "a week ago" },
  { name: "Quote contract",    tag: "Quotes",   tagClass: "purple", recipient: "Stiv Rogers",   date: "4/03/2023",  status: "Approved", statusClass: "approved", modified: "15 minutes ago" },
  { name: "Work order contract",tag: "Jobs",    tagClass: "orange", recipient: "Donna Prince",  date: "5/03/2022",  status: "Pending",  statusClass: "pending",  modified: "1 year ago" },
  { name: "Request contract",  tag: "Requests", tagClass: "teal",   recipient: "Stiv Rogers",   date: "7/03/2023",  status: "Sent",     statusClass: "sent",     modified: "15 minutes ago" },
  { name: "Quote contract",    tag: "Quotes",   tagClass: "purple", recipient: "Donna Prince",  date: "7/03/2023",  status: "Approved", statusClass: "approved", modified: "10 minutes ago" },
  { name: "Work order contract",tag: "Jobs",    tagClass: "orange", recipient: "Stiv Rogers",   date: "10/03/2023", status: "Pending",  statusClass: "pending",  modified: "a week ago" },
  { name: "Request contract",  tag: "Requests", tagClass: "teal",   recipient: "Donna Prince",  date: "11/03/2023", status: "Sent",     statusClass: "sent",     modified: "2 weeks ago" },
  { name: "Request contract",  tag: "Requests", tagClass: "teal",   recipient: "Stiv Rogers",   date: "12/03/2023", status: "Approved", statusClass: "approved", modified: "2 hours ago" },
  { name: "Quote contract",    tag: "Quotes",   tagClass: "purple", recipient: "Donna Prince",  date: "14/03/2023", status: "Pending",  statusClass: "pending",  modified: "5 hours ago" },
  { name: "Quote contract",    tag: "Quotes",   tagClass: "purple", recipient: "Stiv Rogers",   date: "15/03/2023", status: "Sent",     statusClass: "sent",     modified: "10 minutes ago" },
  { name: "Work order contract",tag: "Jobs",    tagClass: "orange", recipient: "Donna Prince",  date: "18/03/2023", status: "Approved", statusClass: "approved", modified: "40 minutes ago" },
];

// ===================== RENDER TABLE =====================
function renderTable(data) {
  const tbody = document.getElementById("tableBody");
  tbody.innerHTML = "";

  data.forEach((doc) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" class="row-check" /></td>
      <td><i class="fa-solid fa-link link-icon"></i></td>
      <td>
        <div class="doc-name-cell">
          <span class="doc-name">${doc.name}</span>
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
          <button class="row-btn" title="Download"><i class="fa-solid fa-download"></i></button>
          <button class="row-btn" title="Edit"><i class="fa-solid fa-pen"></i></button>
          <button class="row-btn" title="More"><i class="fa-solid fa-grip-vertical"></i></button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

// ===================== SELECT ALL =====================
function initSelectAll() {
  const selectAll = document.getElementById("selectAll");
  selectAll.addEventListener("change", () => {
    document.querySelectorAll(".row-check").forEach((cb) => {
      cb.checked = selectAll.checked;
    });
  });

  document.getElementById("tableBody").addEventListener("change", (e) => {
    if (e.target.classList.contains("row-check")) {
      const allChecks = document.querySelectorAll(".row-check");
      const allChecked = [...allChecks].every((cb) => cb.checked);
      selectAll.checked = allChecked;
    }
  });
}

// ===================== SEARCH =====================
function initSearch() {
  const searchInput = document.querySelector(".search-input");
  searchInput.addEventListener("input", () => {
    const query = searchInput.value.toLowerCase().trim();
    const filtered = documents.filter(
      (doc) =>
        doc.name.toLowerCase().includes(query) ||
        doc.recipient.toLowerCase().includes(query) ||
        doc.tag.toLowerCase().includes(query) ||
        doc.status.toLowerCase().includes(query)
    );
    renderTable(filtered);
    initSelectAll();
  });
}

// ===================== FILTER PANEL =====================
function initFilters() {
  // Status filter dots in panel
  document.querySelectorAll(".panel-item").forEach((item) => {
    item.addEventListener("click", () => {
      const dot = item.querySelector(".dot");
      if (!dot) return;

      const statusMap = { gray: "Pending", yellow: "Sent", green: "Approved" };
      const colorClass = [...dot.classList].find((c) => statusMap[c]);
      if (!colorClass) return;

      // Toggle active state
      const isActive = item.classList.contains("active");
      document.querySelectorAll(".panel-item").forEach((el) => el.classList.remove("active"));

      if (isActive) {
        renderTable(documents);
      } else {
        item.classList.add("active");
        const filtered = documents.filter(
          (doc) => doc.status === statusMap[colorClass]
        );
        renderTable(filtered);
      }
      initSelectAll();
    });
  });

  // Tag filter
  document.querySelectorAll(".tag").forEach((tag) => {
    tag.addEventListener("click", () => {
      const label = tag.textContent.trim();
      const isActive = tag.classList.contains("active-tag");

      document.querySelectorAll(".tag").forEach((t) => t.classList.remove("active-tag"));

      if (isActive) {
        renderTable(documents);
      } else {
        tag.classList.add("active-tag");
        const filtered = documents.filter((doc) => doc.tag === label);
        renderTable(filtered);
      }
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
    const idx = headers.indexOf(th);

    // Only sortable columns: Name (2), Recipient (3), Date (4), Status (5), Modified (6)
    const sortMap = {
      2: "name",
      3: "recipient",
      4: "date",
      5: "status",
      6: "modified",
    };
    const field = sortMap[idx];
    if (!field) return;

    if (sortState.col === field) {
      sortState.asc = !sortState.asc;
    } else {
      sortState.col = field;
      sortState.asc = true;
    }

    const sorted = [...documents].sort((a, b) => {
      const va = a[field].toLowerCase();
      const vb = b[field].toLowerCase();
      return sortState.asc ? va.localeCompare(vb) : vb.localeCompare(va);
    });

    renderTable(sorted);
    initSelectAll();
  });
}

// ===================== ADD DOCUMENT BUTTON =====================
function initAddDocument() {}

























// ===================== FILTER PANEL =====================
function initFilterPanel() {
  const overlay   = document.getElementById("filterOverlay");
  const panel     = document.getElementById("filterPanel");
  const openBtn   = document.querySelector(".btn-filter");
  const closeBtn  = document.getElementById("filterClose");
  const resetBtn  = document.getElementById("fpReset");
  const applyBtn  = document.getElementById("fpApply");

  // Open / close helpers
  function openPanel() {
    overlay.classList.add("open");
    panel.classList.add("open");
  }
  function closePanel() {
    overlay.classList.remove("open");
    panel.classList.remove("open");
  }

  openBtn.addEventListener("click", openPanel);
  closeBtn.addEventListener("click", closePanel);
  overlay.addEventListener("click", closePanel);

  // ---- DATE SELECT ----
  const dateSelect      = document.getElementById("fpDateSelect");
  const calendarWrap    = document.getElementById("fpCalendarWrap");
  const fpDateInput     = document.getElementById("fpDateInput");
  const fpCalToggle     = document.getElementById("fpCalToggle");
  const fpCalendar      = document.getElementById("fpCalendar");

  dateSelect.addEventListener("change", () => {
    if (dateSelect.value === "custom") {
      calendarWrap.style.display = "block";
      fpCalendar.style.display = "block";
    } else {
      calendarWrap.style.display = "none";
    }
  });

  // ---- CALENDAR ----
  let calDate = new Date();
  let selectedDate = null;

  const MONTHS = ["January","February","March","April","May","June",
                  "July","August","September","October","November","December"];
  const DAYS   = ["SU","MO","TU","WE","TH","FR","SA"];

  function renderCalendar() {
    const year  = calDate.getFullYear();
    const month = calDate.getMonth();
    document.getElementById("calMonthLabel").textContent = `${MONTHS[month]} ${year}`;

    const grid = document.getElementById("calGrid");
    grid.innerHTML = "";

    // Day names
    DAYS.forEach(d => {
      const el = document.createElement("div");
      el.className = "cal-day-name";
      el.textContent = d;
      grid.appendChild(el);
    });

    const firstDay = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const today = new Date();

    // Empty cells before first day
    for (let i = 0; i < firstDay; i++) {
      const empty = document.createElement("div");
      empty.className = "cal-day other-month";
      grid.appendChild(empty);
    }

    for (let d = 1; d <= daysInMonth; d++) {
      const el = document.createElement("div");
      el.className = "cal-day";
      el.textContent = d;

      const thisDate = new Date(year, month, d);
      if (today.toDateString() === thisDate.toDateString()) el.classList.add("today");
      if (selectedDate && selectedDate.toDateString() === thisDate.toDateString()) el.classList.add("selected");

      el.addEventListener("click", () => {
        selectedDate = new Date(year, month, d);
        fpDateInput.value = `${String(d).padStart(2,"0")}/${String(month+1).padStart(2,"0")}/${year}`;
        renderCalendar();
      });

      grid.appendChild(el);
    }
  }

  document.getElementById("calPrev").addEventListener("click", () => {
    calDate.setMonth(calDate.getMonth() - 1);
    renderCalendar();
  });
  document.getElementById("calNext").addEventListener("click", () => {
    calDate.setMonth(calDate.getMonth() + 1);
    renderCalendar();
  });
  fpCalToggle.addEventListener("click", () => {
    const vis = fpCalendar.style.display === "none" ? "block" : "none";
    fpCalendar.style.display = vis;
  });

  renderCalendar();

  // ---- RECIPIENT SEARCH ----
  const recipientSearch = document.getElementById("fpRecipientSearch");
  const radioItems      = document.querySelectorAll(".fp-radio-item");

  recipientSearch.addEventListener("input", () => {
    const q = recipientSearch.value.toLowerCase();
    radioItems.forEach(item => {
      const label = item.querySelector("label").textContent.toLowerCase();
      item.style.display = (label.includes(q) || label === "select all") ? "" : "none";
    });
  });

  // ---- APPLY ----
  applyBtn.addEventListener("click", () => {
    const recipient = document.querySelector('input[name="recipient"]:checked')?.value || "";
    const dateVal   = dateSelect.value;

    let filtered = [...documents];

    if (recipient) {
      filtered = filtered.filter(doc => doc.recipient === recipient);
    }

    if (dateVal === "today") {
      // keep all (demo data is not real-time)
    } else if (dateVal === "custom" && selectedDate) {
      const sel = selectedDate;
      filtered = filtered.filter(doc => {
        const parts = doc.date.split("/");
        const d = new Date(parseInt(parts[2]), parseInt(parts[1])-1, parseInt(parts[0]));
        return d.toDateString() === sel.toDateString();
      });
    }

    renderTable(filtered);
    initSelectAll();
    closePanel();
  });

  // ---- RESET ----
  resetBtn.addEventListener("click", () => {
    dateSelect.value = "today";
    calendarWrap.style.display = "none";
    selectedDate = null;
    fpDateInput.value = "";
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
  initAddDocument();
  initFilterPanel();
  initDocDetailPanel();
  initAddDocModal();
});

// ===================== DOCUMENT DETAIL PANEL =====================
function initDocDetailPanel() {
  const panel    = document.getElementById("docDetailPanel");
  const closeBtn = document.getElementById("docDetailClose");

  // Click pencil (edit) icon on any row to open detail
  document.getElementById("tableBody").addEventListener("click", (e) => {
    const btn = e.target.closest(".row-btn");
    if (!btn) return;

    // Only open on the pencil (edit) button — second .row-btn
    const allBtns = [...btn.closest("tr").querySelectorAll(".row-btn")];
    const idx = allBtns.indexOf(btn);
    if (idx !== 1) return; // 0=download, 1=edit, 2=more

    const row = btn.closest("tr");
    const cells = row.querySelectorAll("td");

    // Populate panel
    const docName  = cells[2].querySelector(".doc-name")?.textContent || "";
    const tagText  = cells[2].querySelector(".doc-tag")?.textContent || "";
    const tagClass = cells[2].querySelector(".doc-tag")?.className.split(" ")[1] || "";
    const recipient = cells[3]?.textContent.trim() || "";
    const date      = cells[4]?.textContent.trim() || "";
    const status    = cells[5]?.querySelector("div")?.textContent.trim() || "";

    document.getElementById("ddpDocName").textContent   = docName;
    document.getElementById("ddpOwner").textContent     = "kate23@gmail.com";
    document.getElementById("ddpCreated").textContent   = date;
    document.getElementById("ddpRecipient").textContent = recipient + "@gmail.com";

    const tagEl = document.getElementById("ddpTag");
    tagEl.textContent = status;
    tagEl.className = "ddp-tag " + status.toLowerCase();

    panel.classList.add("open");
  });

  closeBtn.addEventListener("click", () => panel.classList.remove("open"));
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

  function openModal() {
    overlay.classList.add("open");
    modal.classList.add("open");
  }
  function closeModal() {
    overlay.classList.remove("open");
    modal.classList.remove("open");
    fileNameEl.textContent = "";
    fileInput.value = "";
    dropZone.classList.remove("drag-over");
  }

  // Override the old btn-add prompt behaviour
  openBtn.addEventListener("click", openModal, true);

  closeBtn.addEventListener("click", closeModal);
  overlay.addEventListener("click", closeModal);

  selectBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    fileInput.click();
  });

  dropZone.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (file) {
      fileNameEl.textContent = file.name;
      fileNameEl.style.color = "#4caf82";

      // After a short delay, add a placeholder row to the table and close
      setTimeout(() => {
        const tagOptions = ["Jobs", "Quotes", "Requests"];
        const tagMap     = { Jobs: "orange", Quotes: "purple", Requests: "teal" };
        const tag        = tagOptions[Math.floor(Math.random() * tagOptions.length)];
        const today      = new Date();
        const dateStr    = `${String(today.getDate()).padStart(2,"0")}/${String(today.getMonth()+1).padStart(2,"0")}/${today.getFullYear()}`;

        documents.unshift({
          name: file.name.replace(/\.[^/.]+$/, ""),
          tag,
          tagClass: tagMap[tag],
          recipient: "Stiv Rogers",
          date: dateStr,
          status: "Pending",
          statusClass: "pending",
          modified: "just now",
        });

        renderTable(documents);
        initSelectAll();
        closeModal();
      }, 900);
    }
  });

  // Drag & drop
  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file) {
      fileInput._customFile = file;
      fileNameEl.textContent = file.name;
      fileNameEl.style.color = "#4caf82";
    }
  });
}