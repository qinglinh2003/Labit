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

  // Try content script on the active tab first (if on an arXiv page).
  if (tab?.id) {
    try {
      const response = await chrome.tabs.sendMessage(tab.id, { type: "LABIT_GET_ARXIV_METADATA" });
      if (response?.ok) {
        return response.metadata;
      }
    } catch (_) {
      // Content script not available — tab is not an arXiv page.
    }
  }

  // Fall back to cached metadata from background worker.
  const cached = await chrome.runtime.sendMessage({ type: "LABIT_GET_CACHED_METADATA", tabId: tab?.id });
  if (cached?.ok && cached.metadata) {
    return cached.metadata;
  }

  throw new Error("No paper found. Visit an arXiv abstract page first.");
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
  setMessage("Import running in background. You can close this popup.");
  elements.importButton.disabled = true;

  try {
    await saveSettings();

    // Delegate to background service worker. If this popup stays open, the
    // response updates the status when the background import completes.
    const response = await chrome.runtime.sendMessage({
      type: "LABIT_IMPORT_PAPER",
      metadata: state.metadata,
      apiBase: state.apiBase,
      project
    });

    if (response?.ok) {
      setStatus("Done");
      setMessage(`Imported ${state.metadata.arxiv_id}.`);
    } else {
      throw new Error(response?.error || "Failed to import paper.");
    }
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
