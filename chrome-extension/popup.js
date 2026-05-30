const DEFAULT_API_BASE = "http://127.0.0.1:8787";

const state = {
  apiBase: DEFAULT_API_BASE,
  metadata: null,
  projects: []
};

const elements = {
  apiBase: document.querySelector("#apiBase"),
  importButton: document.querySelector("#importButton"),
  message: document.querySelector("#message"),
  paper: document.querySelector("#paper"),
  paperAuthors: document.querySelector("#paperAuthors"),
  paperId: document.querySelector("#paperId"),
  paperTitle: document.querySelector("#paperTitle"),
  projectSelect: document.querySelector("#projectSelect"),
  status: document.querySelector("#status")
};

function setStatus(value) {
  elements.status.textContent = value;
}

function setMessage(value) {
  elements.message.textContent = value;
}

function normalizeApiBase(value) {
  return (value || DEFAULT_API_BASE).trim().replace(/\/+$/, "");
}

async function loadSettings() {
  const saved = await chrome.storage.sync.get(["apiBase", "project"]);
  state.apiBase = normalizeApiBase(saved.apiBase);
  elements.apiBase.value = state.apiBase;
  return saved.project || "";
}

async function saveSettings() {
  await chrome.storage.sync.set({
    apiBase: normalizeApiBase(elements.apiBase.value),
    project: elements.projectSelect.value
  });
}

async function getActiveTabMetadata() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    throw new Error("No active tab found.");
  }

  const response = await chrome.tabs.sendMessage(tab.id, { type: "LABIT_GET_ARXIV_METADATA" });
  if (!response?.ok) {
    throw new Error(response?.error || "Open an arXiv abstract page first.");
  }
  return response.metadata;
}

async function fetchProjects(preferredProject) {
  const response = await fetch(`${state.apiBase}/api/projects`);
  if (!response.ok) {
    throw new Error(`Labit API returned ${response.status}.`);
  }

  const payload = await response.json();
  state.projects = payload.projects || [];
  elements.projectSelect.replaceChildren(
    ...state.projects.map((project) => {
      const option = document.createElement("option");
      option.value = project;
      option.textContent = project;
      return option;
    })
  );

  const selected = preferredProject || payload.active_project || state.projects[0] || "";
  if (selected) {
    elements.projectSelect.value = selected;
  }
}

function renderPaper(metadata) {
  elements.paper.classList.remove("hidden");
  elements.paperId.textContent = metadata.arxiv_id;
  elements.paperTitle.textContent = metadata.title;
  elements.paperAuthors.textContent = metadata.authors?.join(", ") || "No authors found";
}

function updateImportState() {
  elements.importButton.disabled = !state.metadata || !elements.projectSelect.value;
}

async function importPaper() {
  const project = elements.projectSelect.value;
  if (!state.metadata || !project) {
    return;
  }

  setStatus("Importing");
  setMessage("Fetching PDF from arXiv...");
  elements.importButton.disabled = true;

  try {
    await saveSettings();
    const pdfResponse = await fetch(state.metadata.pdf_url);
    if (!pdfResponse.ok) {
      throw new Error(`PDF fetch returned ${pdfResponse.status}.`);
    }

    const pdfBlob = await pdfResponse.blob();
    const form = new FormData();
    form.append("metadata", JSON.stringify(state.metadata));
    form.append("pdf", new File([pdfBlob], `${state.metadata.arxiv_id}.pdf`, { type: "application/pdf" }));

    setMessage("Uploading to Labit...");
    const importResponse = await fetch(
      `${state.apiBase}/api/projects/${encodeURIComponent(project)}/papers/import/arxiv`,
      { method: "POST", body: form }
    );
    if (!importResponse.ok) {
      throw new Error(await importResponse.text());
    }

    setStatus("Done");
    setMessage(`Imported ${state.metadata.arxiv_id} into ${project}.`);
  } catch (error) {
    setStatus("Error");
    setMessage(error instanceof Error ? error.message : String(error));
  } finally {
    updateImportState();
  }
}

async function reloadProjects() {
  state.apiBase = normalizeApiBase(elements.apiBase.value);
  setStatus("Loading");
  try {
    await fetchProjects(elements.projectSelect.value);
    await saveSettings();
    setStatus("Ready");
  } catch (error) {
    setStatus("Error");
    setMessage(error instanceof Error ? error.message : String(error));
  } finally {
    updateImportState();
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  elements.importButton.addEventListener("click", importPaper);
  elements.projectSelect.addEventListener("change", () => {
    saveSettings();
    updateImportState();
  });
  elements.apiBase.addEventListener("change", reloadProjects);

  try {
    const preferredProject = await loadSettings();
    const [metadata] = await Promise.all([getActiveTabMetadata(), fetchProjects(preferredProject)]);
    state.metadata = metadata;
    renderPaper(metadata);
    setStatus("Ready");
    setMessage("");
  } catch (error) {
    setStatus("Error");
    setMessage(error instanceof Error ? error.message : String(error));
  } finally {
    updateImportState();
  }
});
