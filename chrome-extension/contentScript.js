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

function parseSubmittedDate() {
  const dateline = cleanText(document.querySelector(".dateline")?.textContent);
  const match = dateline.match(/(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})/);
  if (!match) {
    return "";
  }

  const monthByName = {
    Jan: "01",
    Feb: "02",
    Mar: "03",
    Apr: "04",
    May: "05",
    Jun: "06",
    Jul: "07",
    Aug: "08",
    Sep: "09",
    Oct: "10",
    Nov: "11",
    Dec: "12"
  };
  const month = monthByName[match[2]];
  if (!month) {
    return "";
  }

  return `${match[3]}-${month}-${match[1].padStart(2, "0")}`;
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
    pdf_url: `https://arxiv.org/pdf/${arxivId}`,
    submitted_date: parseSubmittedDate()
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

// Proactively cache metadata in background worker so it persists after navigating away.
try {
  const metadata = collectMetadata();
  chrome.runtime.sendMessage({ type: "LABIT_CACHE_METADATA", metadata });
} catch (_) {
  // Not an arXiv abstract page — ignore.
}
