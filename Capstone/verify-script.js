// ===================== MOCK DATABASE =====================
// In production replace queryDatabase() with a real fetch() to your API.
// The "hash" field simulates a checksum stored in the DB for the file.
// When a real file is uploaded, you'd compute its hash and compare server-side.
const mockDatabase = [
  {
    fileName: "working_contract",
    title: "Working Contract",
    status: "authentic",
    changes: { textContent: true, embeddedImages: true, docStructure: true, metadata: true }
  },
  {
    fileName: "modified_contract",
    title: "Modified Contract",
    status: "modified",
    changes: { textContent: false, embeddedImages: false, docStructure: true, metadata: true }
  },
  {
    fileName: "corrupt_contract",
    title: "Corrupt Contract",
    status: "integrity",
    changes: { textContent: false, embeddedImages: false, docStructure: false, metadata: false }
  },
];

function queryDatabase(fileName) {
  const key = fileName.toLowerCase().replace(/\.[^/.]+$/, "").replace(/[\s\-]+/g, "_");
  return mockDatabase.find(r => key.includes(r.fileName)) || { status: "unknown", title: fileName, changes: {} };
}

// ===================== RESULT CONFIG =====================
const RESULT_CONFIG = {
  authentic: {
    badgeClass: "badge-authentic",
    badgeText:  "Authentic\nDocument",
    desc: "This document fully matches the official version stored in the barangay system.\n\nNo unauthorized modifications were detected.",
    srcResult: "Passed", srcIntegrity: "Complete", srcMatch: "Yes", showMatch: true, note: "",
    changes: [
      { label: "Text Content",       icon: "ok" },
      { label: "Embedded Images",    icon: "ok" },
      { label: "Document Structure", icon: "ok" },
      { label: "Metadata",           icon: "ok" },
    ]
  },
  modified: {
    badgeClass: "badge-modified",
    badgeText:  "Possible\nModification",
    desc: "Some parts of this document do not fully match the official record.\n\nThe document may have been altered after approval.",
    srcResult: "Passed", srcIntegrity: "Complete", srcMatch: "Partial", showMatch: true, note: "",
  },
  integrity: {
    badgeClass: "badge-integrity",
    badgeText:  "Document\nIntegrity\nIssue",
    desc: "This document does not match the official record stored in the barangay system.\n\nSignificant inconsistencies were detected.",
    srcResult: "Failed", srcIntegrity: "Failed", srcMatch: "No", showMatch: true,
    note: "This Document has been reported to the admin for further investigation.",
    changes: [
      { label: "Text Content",       icon: "bad" },
      { label: "Embedded Images",    icon: "bad" },
      { label: "Document Structure", icon: "bad" },
      { label: "Metadata",           icon: "bad" },
    ]
  },
  unknown: {
    badgeClass: "badge-unknown",
    badgeText:  "Unknown\nDocument",
    desc: "Document isn't found in our system.\n\nIt may not have been created or uploaded through this system.\n\nOnly contracts approved and sealed by the barangay are verifiable.",
    srcResult: "Failed", srcIntegrity: "Failed", srcMatch: "No", showMatch: false,
    note: "This Document has been reported to the admin for further investigation.",
    changes: []
  }
};

// ===================== STATE =====================
let uploadedFile     = null;
let uploadedFileName = "";
let isVerifying      = false;
let fileObjectURL    = null; // blob URL for the uploaded file

// ===================== UPLOAD SCREEN =====================
const dropZone      = document.getElementById("dropZone");
const fileInput     = document.getElementById("fileInput");
const selectFileBtn = document.getElementById("selectFileBtn");
const fileNameEl    = document.getElementById("fileName");
const btnContinue   = document.getElementById("btnContinue");

selectFileBtn.addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
dropZone.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (!file) return;
  handleFile(file);
});

dropZone.addEventListener("dragover",  (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault(); dropZone.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
});

function handleFile(file) {
  uploadedFile     = file;
  uploadedFileName = file.name;
  fileNameEl.textContent = file.name;
  fileNameEl.style.color = "#4caf82";
  btnContinue.disabled   = false;

  // Revoke previous blob URL to avoid memory leaks
  if (fileObjectURL) URL.revokeObjectURL(fileObjectURL);
  fileObjectURL = URL.createObjectURL(file);
}

btnContinue.addEventListener("click", () => {
  if (!uploadedFile) return;
  showViewerScreen();
});

// ===================== SCREEN TRANSITIONS =====================
function showViewerScreen() {
  document.getElementById("screenUpload").classList.add("hidden");
  document.getElementById("screenViewer").classList.remove("hidden");

  const record = queryDatabase(uploadedFileName);
  document.getElementById("viewerTitle").textContent = record.title || uploadedFileName;

  renderUploadedFile();
}

document.getElementById("btnBack").addEventListener("click", () => {
  document.getElementById("screenViewer").classList.add("hidden");
  document.getElementById("screenUpload").classList.remove("hidden");
  resetVerification();
});

// ===================== RENDER UPLOADED FILE =====================
function renderUploadedFile() {
  const previewArea = document.getElementById("docPreviewArea");
  previewArea.innerHTML = ""; // clear previous

  const ext = uploadedFileName.split(".").pop().toLowerCase();

  if (ext === "pdf") {
    // Render PDF in an iframe using blob URL
    const iframe = document.createElement("iframe");
    iframe.src    = fileObjectURL;
    iframe.className = "file-iframe";
    iframe.title  = uploadedFileName;
    previewArea.appendChild(iframe);

  } else if (["png","jpg","jpeg","gif","webp","bmp","svg"].includes(ext)) {
    // Render image
    const wrapper = document.createElement("div");
    wrapper.className = "file-img-wrapper";
    const img = document.createElement("img");
    img.src   = fileObjectURL;
    img.alt   = uploadedFileName;
    img.className = "file-img";
    wrapper.appendChild(img);
    previewArea.appendChild(wrapper);

  } else if (["txt","md","csv","json","xml","html"].includes(ext)) {
    // Render plain text
    const reader = new FileReader();
    reader.onload = (e) => {
      const pre = document.createElement("pre");
      pre.className = "file-text";
      pre.textContent = e.target.result;
      const wrapper = document.createElement("div");
      wrapper.className = "file-text-wrapper";
      wrapper.appendChild(pre);
      previewArea.appendChild(wrapper);
    };
    reader.readAsText(uploadedFile);

  } else if (["doc","docx","ppt","pptx","xls","xlsx"].includes(ext)) {
    // Use Google Docs Viewer for Office files (requires the file to be publicly accessible)
    // Since we have a local blob, fall back to a file info card + download
    const card = document.createElement("div");
    card.className = "file-office-card";
    card.innerHTML = `
      <div class="office-icon"><i class="fa-regular fa-file-word"></i></div>
      <p class="office-name">${uploadedFileName}</p>
      <p class="office-size">${formatFileSize(uploadedFile.size)}</p>
      <p class="office-note">Office documents cannot be previewed directly in the browser.<br/>The file has been received and will be verified.</p>
      <a class="office-download" href="${fileObjectURL}" download="${uploadedFileName}">
        <i class="fa-solid fa-download"></i> Download to view
      </a>
    `;
    previewArea.appendChild(card);

  } else {
    // Generic file card
    const card = document.createElement("div");
    card.className = "file-office-card";
    card.innerHTML = `
      <div class="office-icon"><i class="fa-regular fa-file"></i></div>
      <p class="office-name">${uploadedFileName}</p>
      <p class="office-size">${formatFileSize(uploadedFile.size)}</p>
      <p class="office-note">This file type cannot be previewed.<br/>It has been received and will be verified.</p>
      <a class="office-download" href="${fileObjectURL}" download="${uploadedFileName}">
        <i class="fa-solid fa-download"></i> Download to view
      </a>
    `;
    previewArea.appendChild(card);
  }

  // Update page count label
  document.getElementById("subbarPages").textContent = uploadedFileName;
}

// ===================== VERIFICATION FLOW =====================
document.getElementById("btnVerify").addEventListener("click", () => {
  if (isVerifying) return;
  startVerification();
});

function startVerification() {
  isVerifying = true;
  document.getElementById("btnVerify").classList.add("verifying");
  document.getElementById("verifyLoading").classList.remove("hidden");
  document.getElementById("verifyResult").classList.add("hidden");

  const steps  = ["vstep1","vstep2","vstep3","vstep4","vstep5"];
  const delays = [400, 950, 1600, 2250, 2900];

  steps.forEach(id => {
    const el = document.getElementById(id);
    el.classList.remove("active","done");
    el.querySelector(".loading-spinner")?.classList.remove("done");
  });

  steps.forEach((id, i) => {
    setTimeout(() => {
      if (i > 0) {
        const prev = document.getElementById(steps[i-1]);
        prev.classList.remove("active"); prev.classList.add("done");
        prev.querySelector(".loading-spinner")?.classList.add("done");
      }
      document.getElementById(id).classList.add("active");
    }, delays[i]);
  });

  setTimeout(() => {
    const last = document.getElementById(steps[steps.length-1]);
    last.classList.remove("active"); last.classList.add("done");
    last.querySelector(".loading-spinner")?.classList.add("done");
    setTimeout(showResult, 500);
  }, delays[delays.length-1] + 600);
}

function showResult() {
  const record = queryDatabase(uploadedFileName);
  const cfg    = RESULT_CONFIG[record.status] || RESULT_CONFIG.unknown;

  // Badge
  const badge = document.getElementById("statusBadge");
  badge.className = "status-badge " + cfg.badgeClass;
  badge.innerHTML = cfg.badgeText.replace(/\n/g, "<br/>");

  // Description
  document.getElementById("resultDesc").innerHTML = cfg.desc.replace(/\n/g, "<br/>");

  // Change list
  const changeList = document.getElementById("changeList");
  changeList.innerHTML = "";

  let changes = cfg.changes;
  if (record.status === "modified") {
    changes = [
      { label: "Text Content",          icon: record.changes.textContent    ? "ok" : "warn" },
      { label: "Embedded Images",       icon: record.changes.embeddedImages ? "ok" : "warn" },
      { label: "Document Structure",    icon: record.changes.docStructure   ? "ok" : "ok"   },
      { label: "Metadata (Unchanged)",  icon: record.changes.metadata       ? "ok" : "warn" },
    ];
  }

  if (changes && changes.length > 0) {
    const header = document.createElement("p");
    header.style.cssText = "font-size:12px;color:#555;font-weight:600;margin-bottom:2px;";
    header.textContent = "Changes were found in:";
    changeList.appendChild(header);

    const iconMap = { ok:"fa-check chk-ok", bad:"fa-xmark chk-bad", warn:"fa-triangle-exclamation chk-warn" };
    changes.forEach(c => {
      const li = document.createElement("li");
      li.innerHTML = `<i class="fa-solid ${iconMap[c.icon]}"></i> ${c.label}`;
      changeList.appendChild(li);
    });
  }

  // Source card
  document.getElementById("srcResult").textContent    = cfg.srcResult;
  document.getElementById("srcIntegrity").textContent = cfg.srcIntegrity;
  document.getElementById("srcMatch").textContent     = cfg.srcMatch;
  document.getElementById("srcMatchRow").style.display = cfg.showMatch ? "" : "none";
  document.getElementById("srcDate").textContent      = formatDate(new Date());
  const noteEl = document.getElementById("srcNote");
  noteEl.textContent  = cfg.note || "";
  noteEl.style.display = cfg.note ? "" : "none";

  // Highlight the preview if tampered
  const previewArea = document.getElementById("docPreviewArea");
  previewArea.classList.remove("preview-modified","preview-integrity");
  if (record.status === "modified")  previewArea.classList.add("preview-modified");
  if (record.status === "integrity") previewArea.classList.add("preview-integrity");

  document.getElementById("verifyLoading").classList.add("hidden");
  document.getElementById("verifyResult").classList.remove("hidden");
  isVerifying = false;
  document.getElementById("btnVerify").classList.remove("verifying");
}

function resetVerification() {
  isVerifying = false;
  document.getElementById("btnVerify").classList.remove("verifying");
  document.getElementById("verifyLoading").classList.add("hidden");
  document.getElementById("verifyResult").classList.add("hidden");
  document.getElementById("docPreviewArea").classList.remove("preview-modified","preview-integrity");
}

// ===================== HELPERS =====================
function formatDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")} `
       + `${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}:${String(d.getSeconds()).padStart(2,"0")}`;
}

function formatFileSize(bytes) {
  if (bytes < 1024)       return bytes + " B";
  if (bytes < 1048576)    return (bytes/1024).toFixed(1) + " KB";
  return (bytes/1048576).toFixed(1) + " MB";
}

// ===== HOW TO CONNECT TO A REAL DATABASE =====
// Replace queryDatabase() with a real API call:
//
// async function queryDatabase(fileName) {
//   const formData = new FormData();
//   formData.append("file", uploadedFile);           // send actual file for server-side hashing
//   const res = await fetch("https://your-api.com/verify", { method: "POST", body: formData });
//   if (!res.ok) return { status: "unknown" };
//   return await res.json();
//   // expects: { status: "authentic"|"modified"|"integrity"|"unknown", title, changes: {...} }
// }