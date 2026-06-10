// ===================== CONFIG =====================
// Set mode: "staff" shows username/password fields, "public" hides them
// This can be toggled programmatically or set via URL param: ?mode=public
const MODE = getMode(); // "staff" | "public"

function getMode() {
  const params = new URLSearchParams(window.location.search);
  const m = params.get("mode");
  if (m === "public") return "public";
  return "staff"; // default
}

// ===================== DOM REFS =====================
const loginFields   = document.getElementById("loginFields");
const modalSubtitle = document.getElementById("modalSubtitle");
const inputUsername = document.getElementById("inputUsername");
const inputPassword = document.getElementById("inputPassword");
const dropZone      = document.getElementById("dropZone");
const fileInput     = document.getElementById("fileInput");
const selectFileBtn = document.getElementById("selectFileBtn");
const fileName      = document.getElementById("fileName");
const btnContinue   = document.getElementById("btnContinue");
const modalClose    = document.getElementById("modalClose");

// ===================== APPLY MODE =====================
function applyMode() {
  if (MODE === "public") {
    loginFields.classList.add("hidden");
    modalSubtitle.textContent = "Public Use";
  } else {
    loginFields.classList.remove("hidden");
    modalSubtitle.textContent = "Staff Only";
  }
}

// ===================== FILE UPLOAD =====================
let uploadedFile = null;

selectFileBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  fileInput.click();
});

dropZone.addEventListener("click", () => {
  fileInput.click();
});

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (file) {
    uploadedFile = file;
    fileName.textContent = file.name;
  }
});

// Drag & drop
dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("drag-over");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("drag-over");
});

dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) {
    uploadedFile = file;
    fileName.textContent = file.name;
  }
});

// ===================== VALIDATION =====================
function clearErrors() {
  document.querySelectorAll(".field-error").forEach(el => el.remove());
  document.querySelectorAll(".field-input.error").forEach(el => el.classList.remove("error"));
}

function showError(input, message) {
  input.classList.add("error");
  const err = document.createElement("p");
  err.className = "field-error";
  err.textContent = message;
  input.parentElement.appendChild(err);
}

function validate() {
  clearErrors();
  let valid = true;

  if (MODE === "staff") {
    if (!inputUsername.value.trim()) {
      showError(inputUsername, "Username is required.");
      valid = false;
    }
    if (!inputPassword.value.trim()) {
      showError(inputPassword, "Password is required.");
      valid = false;
    }
  }

  if (!uploadedFile) {
    dropZone.style.borderColor = "#e05a5a";
    fileName.textContent = "Please select a document.";
    fileName.style.color = "#e05a5a";
    valid = false;
  } else {
    dropZone.style.borderColor = "";
    fileName.style.color = "#4caf82";
  }

  return valid;
}

// ===================== CONTINUE =====================
btnContinue.addEventListener("click", () => {
  if (!validate()) return;

  // Redirect to main dashboard (index.html) or handle as needed
  alert("Login successful! Redirecting to dashboard...");
  // window.location.href = "index.html";
});

// ===================== CLOSE =====================
modalClose.addEventListener("click", () => {
  // Optionally navigate away or close a parent modal context
  window.history.back();
});

// ===================== INIT =====================
document.addEventListener("DOMContentLoaded", () => {
  applyMode();
});