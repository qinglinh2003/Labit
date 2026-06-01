// Background service worker: caches arXiv metadata and runs import jobs outside
// the popup lifecycle.

const CACHE_KEY = "labitArxivMetadataCache";

// In-memory cache of metadata from recently visited arXiv pages: { [tabId]: metadata }.
// Mirrored into chrome.storage.session so it survives service worker restarts.
const metadataCache = {};

// Listen for metadata pushed from content scripts on arXiv pages.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "LABIT_CACHE_METADATA" && sender.tab?.id) {
    cacheMetadata(sender.tab.id, message.metadata)
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }

  if (message?.type === "LABIT_GET_CACHED_METADATA") {
    getCachedMetadata(message.tabId)
      .then((metadata) => sendResponse({ ok: true, metadata }))
      .catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }

  if (message?.type === "LABIT_IMPORT_PAPER") {
    // Keep the message event alive while the import runs. This makes the service
    // worker much less likely to be suspended mid-fetch/mid-upload.
    handleImport(message.metadata, message.apiBase, message.project)
      .then((result) => sendResponse(result))
      .catch((error) => {
        console.error("[Labit] Import error:", error);
        setBadge("ERR", "#d32f2f");
        sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
      });
    return true;
  }

  return false;
});

async function handleImport(metadata, apiBase, project) {
  try {
    // Fetch PDF from arXiv
    const pdfResponse = await fetch(metadata.pdf_url);
    if (!pdfResponse.ok) {
      console.error(`[Labit] PDF fetch failed: ${pdfResponse.status}`);
      setBadge("ERR", "#d32f2f");
      return { ok: false, error: `PDF fetch failed: ${pdfResponse.status}` };
    }

    const pdfBlob = await pdfResponse.blob();
    const form = new FormData();
    form.append("metadata", JSON.stringify(metadata));
    form.append("pdf", new File([pdfBlob], `${metadata.arxiv_id}.pdf`, { type: "application/pdf" }));

    // Upload to Labit API
    const importResponse = await fetch(
      `${apiBase}/api/projects/${encodeURIComponent(project)}/papers/import/arxiv`,
      { method: "POST", body: form }
    );

    if (!importResponse.ok) {
      const errorText = await importResponse.text();
      console.error(`[Labit] Import failed: ${errorText}`);
      setBadge("ERR", "#d32f2f");
      return { ok: false, error: errorText || `Import failed: ${importResponse.status}` };
    }

    console.log(`[Labit] Imported ${metadata.arxiv_id} into ${project}`);
    setBadge("✓", "#388e3c");
    setTimeout(() => chrome.action.setBadgeText({ text: "" }), 5000);
    return { ok: true };
  } catch (error) {
    console.error(`[Labit] Import error:`, error);
    setBadge("ERR", "#d32f2f");
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

function setBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

async function cacheMetadata(tabId, metadata) {
  metadataCache[tabId] = metadata;
  const stored = await chrome.storage.session.get(CACHE_KEY);
  const cache = stored[CACHE_KEY] || {};
  cache[String(tabId)] = { metadata, cachedAt: Date.now() };
  await chrome.storage.session.set({ [CACHE_KEY]: cache });
}

async function getCachedMetadata(tabId) {
  const stored = await chrome.storage.session.get(CACHE_KEY);
  const cache = stored[CACHE_KEY] || {};

  Object.entries(cache).forEach(([cachedTabId, entry]) => {
    if (entry?.metadata) {
      metadataCache[cachedTabId] = entry.metadata;
    }
  });

  const direct = tabId ? metadataCache[tabId] || cache[String(tabId)]?.metadata : null;
  if (direct) {
    return direct;
  }

  const entries = Object.values(cache)
    .filter((entry) => entry?.metadata)
    .sort((left, right) => (right.cachedAt || 0) - (left.cachedAt || 0));
  return entries[0]?.metadata || Object.values(metadataCache).at(-1) || null;
}
