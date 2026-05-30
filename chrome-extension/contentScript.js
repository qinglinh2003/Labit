function cleanText(value) {
  return (value || "").replace(/\s+/g, " ").trim();
}

function stripLabel(value, label) {
  return cleanText(value).replace(new RegExp(`^${label}:\\s*`, "i"), "").trim();
}

function parseArxivId() {
  const match = window.location.pathname.match(/^\/abs\/([^/?#]+)/);
  return match ? decodeURIComponent(match[1]).replace(/\.pdf$/i, "") : "";
}

function collectMetadata() {
  const arxivId = parseArxivId();
  if (!arxivId) {
    throw new Error("This page is not an arXiv abstract page.");
  }

  const title = stripLabel(document.querySelector("h1.title")?.textContent, "Title") || arxivId;
  const authors = Array.from(document.querySelectorAll(".authors a"))
    .map((item) => cleanText(item.textContent))
    .filter(Boolean);
  const abstract = stripLabel(document.querySelector("blockquote.abstract")?.textContent, "Abstract");

  return {
    arxiv_id: arxivId,
    title,
    authors,
    abstract,
    url: `https://arxiv.org/abs/${arxivId}`,
    pdf_url: `https://arxiv.org/pdf/${arxivId}`
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "LABIT_GET_ARXIV_METADATA") {
    return false;
  }

  try {
    sendResponse({ ok: true, metadata: collectMetadata() });
  } catch (error) {
    sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
  return true;
});
