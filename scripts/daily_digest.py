#!/usr/bin/env python3
"""
daily_digest.py — Fetch new papers, generate Obsidian notes, auto-link references.

Usage:
    python daily_digest.py                    # Fetch today's papers
    python daily_digest.py --days 3           # Fetch last 3 days
    python daily_digest.py --score            # Also score relevance via claude -p
    python daily_digest.py --query "slot attention"  # Custom search query

Requires:
    pip install arxiv requests

Environment:
    SEMANTIC_SCHOLAR_API_KEY (optional, higher rate limits)
"""

import arxiv
import requests
import json
import os
import re
import subprocess
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep

sys.path.insert(0, str(Path(__file__).parent))
from project_config import (
    load_project, load_all_projects, list_project_names,
    ensure_project_dirs, project_dir, update_project_papers_index,
)

# ---------------------------------------------------------------------------
# Config — edit these to match your research interests
# ---------------------------------------------------------------------------

ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CV", "cs.CL"]

KEYWORDS = [
    "VLM", "vision language model", "multimodal",
    "exploration", "curiosity", "intrinsic reward",
    "world model", "model-based reinforcement learning",
    "hallucination", "grounding",
    "slot attention", "object-centric",
    "belief state", "POMDP", "partial observability",
]

# Minimum keyword matches to include a paper (lower = more papers)
MIN_KEYWORD_HITS = 1

VAULT_DIR = Path(__file__).parent.parent / "vault"
PAPERS_DIR = VAULT_DIR / "papers"
DIGEST_DIR = VAULT_DIR / "digests"

S2_API_BASE = "https://api.semanticscholar.org/graph/v1"
S2_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

# ---------------------------------------------------------------------------
# Semantic Scholar helpers
# ---------------------------------------------------------------------------

def s2_headers():
    h = {}
    if S2_API_KEY:
        h["x-api-key"] = S2_API_KEY
    return h


def s2_paper_details(arxiv_id: str) -> dict | None:
    """Fetch paper details from Semantic Scholar by arXiv ID."""
    url = f"{S2_API_BASE}/paper/ARXIV:{arxiv_id}"
    fields = "paperId,title,authors,year,venue,tldr,fieldsOfStudy,references,citations,externalIds"
    try:
        r = requests.get(url, params={"fields": fields}, headers=s2_headers(), timeout=15)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            print(f"  S2 rate limited, sleeping 5s...")
            sleep(5)
            return s2_paper_details(arxiv_id)
    except Exception as e:
        print(f"  S2 error for {arxiv_id}: {e}")
    return None


def s2_search(query: str, limit: int = 20) -> list[dict]:
    """Search Semantic Scholar for papers matching a query."""
    url = f"{S2_API_BASE}/paper/search"
    fields = "paperId,title,authors,year,venue,tldr,fieldsOfStudy,externalIds"
    try:
        r = requests.get(
            url,
            params={"query": query, "limit": limit, "fields": fields},
            headers=s2_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception as e:
        print(f"  S2 search error: {e}")
    return []


# ---------------------------------------------------------------------------
# arXiv fetching
# ---------------------------------------------------------------------------

def fetch_arxiv_papers(days: int = 1, max_results: int = 200, keywords: list[str] = None) -> list:
    """Fetch recent arXiv papers matching our categories and keywords."""
    kw_list = keywords or KEYWORDS
    cat_query = " OR ".join(f"cat:{c}" for c in ARXIV_CATEGORIES)

    client = arxiv.Client()
    search = arxiv.Search(
        query=cat_query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    cutoff = datetime.now().astimezone() - timedelta(days=days)
    papers = []

    for result in client.results(search):
        if result.published < cutoff:
            break

        # Keyword filtering
        text = f"{result.title} {result.summary}".lower()
        hits = sum(1 for kw in kw_list if kw.lower() in text)
        if hits >= MIN_KEYWORD_HITS:
            papers.append({
                "arxiv_id": result.entry_id.split("/")[-1],
                "title": result.title.replace("\n", " "),
                "authors": [a.name for a in result.authors],
                "abstract": result.summary.replace("\n", " "),
                "categories": [c for c in result.categories],
                "published": result.published.strftime("%Y-%m-%d"),
                "url": result.entry_id,
                "pdf_url": result.pdf_url,
                "keyword_hits": hits,
            })

    return papers


# ---------------------------------------------------------------------------
# Project relevance
# ---------------------------------------------------------------------------

def compute_project_relevance(paper: dict, project_configs: list[dict]) -> list[str]:
    """Return list of project names this paper is relevant to."""
    text = f"{paper['title']} {paper['abstract']}".lower()
    relevant = []
    for pc in project_configs:
        hits = sum(1 for kw in pc.get("keywords", []) if kw.lower() in text)
        if hits >= 1:
            relevant.append(pc["name"])
    return relevant


def backfill_relevance(project_configs: list[dict]):
    """Re-scan existing paper notes and update their relevance_to field."""
    updated = 0
    for f in PAPERS_DIR.glob("*.md"):
        content = f.read_text()
        fm_match = re.match(r'^---\n(.+?)\n---', content, re.DOTALL)
        if not fm_match:
            continue
        # Build text from title + tags + body for matching
        title_match = re.search(r'title:\s*"([^"]*)"', fm_match.group(1))
        title = title_match.group(1) if title_match else ""
        tags_match = re.search(r'tags:\s*\[([^\]]*)\]', fm_match.group(1))
        tags_text = tags_match.group(1).replace(",", " ") if tags_match else ""
        body = content[fm_match.end():]
        text = f"{title} {tags_text} {body}".lower()

        relevant = []
        for pc in project_configs:
            hits = sum(1 for kw in pc.get("keywords", []) if kw.lower() in text)
            if hits >= 1:
                relevant.append(pc["name"])

        relevance_str = ", ".join(relevant)
        # Update relevance_to in frontmatter
        new_content = re.sub(
            r'relevance_to:\s*\[.*?\]',
            f'relevance_to: [{relevance_str}]',
            content,
        )
        if new_content != content:
            f.write_text(new_content)
            updated += 1
            print(f"  Backfilled: {f.stem} -> [{relevance_str}]")

    print(f"Backfill done: {updated} papers updated.")

    # Update per-project papers index
    for pc in project_configs:
        count = update_project_papers_index(pc["name"], PAPERS_DIR)
        print(f"  {pc['name']} papers index: {count} papers")


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

PDF_DIR = VAULT_DIR / "papers" / "pdf"


def download_pdf(arxiv_id: str, pdf_url: str) -> bool:
    """Download paper PDF to vault/papers/pdf/{arxiv_id}.pdf."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = PDF_DIR / f"{arxiv_id.replace('/', '_')}.pdf"
    if pdf_path.exists():
        return True
    url = pdf_url or f"https://arxiv.org/pdf/{arxiv_id}"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200 and len(r.content) > 1000:
            pdf_path.write_bytes(r.content)
            return True
    except Exception as e:
        print(f"  PDF download failed for {arxiv_id}: {e}")
    return False


# ---------------------------------------------------------------------------
# Note generation
# ---------------------------------------------------------------------------

def sanitize_filename(title: str) -> str:
    """Convert paper title to a safe filename."""
    clean = re.sub(r'[^\w\s-]', '', title)
    clean = re.sub(r'\s+', '_', clean.strip())
    return clean[:80]


def existing_paper_ids() -> dict[str, str]:
    """Map arxiv IDs (and S2 paper IDs) to note filenames in vault."""
    mapping = {}
    for f in PAPERS_DIR.glob("*.md"):
        content = f.read_text()
        # Extract paper_id from frontmatter
        m = re.search(r'paper_id:\s*"([^"]+)"', content)
        if m:
            mapping[m.group(1)] = f.stem
    return mapping


def generate_note(paper: dict, s2_data: dict | None, known_ids: dict) -> str:
    """Generate an Obsidian-compatible markdown note for a paper."""
    # Extract S2 metadata
    tldr = ""
    tags = list(set(paper.get("categories", [])))
    refs_section_lines = []

    if s2_data:
        if s2_data.get("tldr"):
            tldr = s2_data["tldr"].get("text", "")

        # Auto-tag from fieldsOfStudy
        for field in s2_data.get("fieldsOfStudy") or []:
            tag = field.replace(" ", "-")
            if tag not in tags:
                tags.append(tag)

        # Auto-link: find references that are already in our vault
        for ref in s2_data.get("references") or []:
            ref_ids = ref.get("externalIds") or {}
            ref_arxiv = ref_ids.get("ArXiv", "")
            if ref_arxiv in known_ids:
                refs_section_lines.append(f"- [[{known_ids[ref_arxiv]}]]")

    # Add keyword-based tags
    text = f"{paper['title']} {paper['abstract']}".lower()
    for kw in KEYWORDS:
        tag = kw.lower().replace(" ", "-")
        if kw.lower() in text and tag not in tags:
            tags.append(tag)

    refs_section = "\n".join(refs_section_lines) if refs_section_lines else "(none yet)"
    authors_str = ", ".join(f'"{a}"' for a in paper["authors"][:5])
    if len(paper["authors"]) > 5:
        authors_str += ", ..."
    tags_str = ", ".join(tags)
    relevance_str = ", ".join(paper.get("relevant_projects", []))

    pdf_path = paper.get("pdf_path", "")

    note = f"""---
paper_id: "{paper['arxiv_id']}"
title: "{paper['title']}"
authors: [{authors_str}]
venue: ""
year: {paper['published'][:4]}
url: "{paper['url']}"
pdf: "{pdf_path}"
tags: [{tags_str}]
relevance_to: [{relevance_str}]
status: "to-read"
added: "{datetime.now().strftime('%Y-%m-%d')}"
---

## Core idea

{tldr if tldr else '(read paper to fill in)'}

## Method summary

## Key equations / algorithms

## Limitations & gaps

## Potential for my work

## References in my vault

{refs_section}

## Cited by (in my vault)

"""
    return note


def update_back_references(paper_id: str, note_filename: str, s2_data: dict | None, known_ids: dict):
    """Add 'cited by' back-links in existing notes that this paper references."""
    if not s2_data:
        return

    for ref in s2_data.get("references") or []:
        ref_ids = ref.get("externalIds") or {}
        ref_arxiv = ref_ids.get("ArXiv", "")
        if ref_arxiv in known_ids:
            ref_note_path = PAPERS_DIR / f"{known_ids[ref_arxiv]}.md"
            if ref_note_path.exists():
                content = ref_note_path.read_text()
                back_link = f"- [[{note_filename}]]"
                if back_link not in content:
                    # Append under "Cited by" section
                    content = content.replace(
                        "## Cited by (in my vault)\n",
                        f"## Cited by (in my vault)\n\n{back_link}\n"
                    )
                    ref_note_path.write_text(content)
                    print(f"  Back-linked: {known_ids[ref_arxiv]} <- {note_filename}")


# ---------------------------------------------------------------------------
# Relevance scoring via Claude Code
# ---------------------------------------------------------------------------

def score_papers_with_claude(papers: list[dict]) -> list[dict]:
    """Use `claude -p` to score relevance of papers to current research."""
    if not papers:
        return papers

    papers_text = ""
    for i, p in enumerate(papers):
        papers_text += f"\n[{i}] {p['title']}\n    {p['abstract'][:300]}...\n"

    prompt = f"""I work on VLM agents for exploration in partially observable environments.
Current projects: GLANCE (curiosity-driven VLM exploration), SemBelief-WM (slot-based latent belief states for model-based RL).

Score each paper 0-5 on relevance to my work. Be strict: 5 = directly applicable, 3 = related technique, 1 = tangentially related, 0 = irrelevant.

Papers:
{papers_text}

Respond ONLY as JSON array: [{{"index": 0, "score": 3, "reason": "one sentence"}}]"""

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=120,
        )
        # Parse JSON from response (handle possible markdown wrapping)
        output = result.stdout.strip()
        output = re.sub(r'^```json\s*', '', output)
        output = re.sub(r'\s*```$', '', output)
        scores = json.loads(output)

        for item in scores:
            idx = item["index"]
            if 0 <= idx < len(papers):
                papers[idx]["relevance_score"] = item.get("score", 0)
                papers[idx]["relevance_reason"] = item.get("reason", "")
    except Exception as e:
        print(f"  Claude scoring failed: {e}")
        # Fall back to keyword count as proxy
        for p in papers:
            p["relevance_score"] = min(p.get("keyword_hits", 0), 5)
            p["relevance_reason"] = f"{p.get('keyword_hits', 0)} keyword matches"

    return papers


# ---------------------------------------------------------------------------
# Digest generation
# ---------------------------------------------------------------------------

def generate_digest(papers: list[dict], date_str: str, project_name: str = None) -> str:
    """Generate a daily digest markdown file."""
    papers_sorted = sorted(papers, key=lambda p: p.get("relevance_score", 0), reverse=True)

    header = f"# Paper digest — {date_str}"
    if project_name:
        header += f" [{project_name}]"

    lines = [
        header,
        f"",
        f"Found {len(papers)} papers matching filters.",
        f"",
    ]

    for p in papers_sorted:
        score = p.get("relevance_score", "?")
        reason = p.get("relevance_reason", "")
        filename = sanitize_filename(p["title"])
        note_exists = (PAPERS_DIR / f"{filename}.md").exists()
        link = f"[[{filename}]]" if note_exists else p["title"]

        lines.append(f"### [{score}/5] {link}")
        if reason:
            lines.append(f"*{reason}*")
        lines.append(f"")
        lines.append(f"{p['abstract'][:200]}...")
        lines.append(f"")
        lines.append(f"[arXiv]({p['url']}) | {', '.join(p['authors'][:3])}")
        lines.append(f"")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch papers and generate Obsidian notes")
    parser.add_argument("--days", type=int, default=1, help="How many days back to look")
    parser.add_argument("--max-results", type=int, default=200, help="Max arXiv results to scan")
    parser.add_argument("--score", action="store_true", help="Score relevance via claude -p")
    parser.add_argument("--query", type=str, default=None, help="Custom S2 search query instead of arXiv")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be created, don't write")
    parser.add_argument("--project", type=str, nargs="*", default=None,
                        help='Project name(s) for filtered digests. Use "all" for all projects.')
    parser.add_argument("--backfill", action="store_true",
                        help="Re-scan existing papers and update relevance_to fields")
    args = parser.parse_args()

    # Resolve project configs
    project_configs = []
    if args.project:
        names = list_project_names() if "all" in args.project else args.project
        project_configs = [load_project(n) for n in names]

    # Handle backfill mode
    if args.backfill:
        if not project_configs:
            print("--backfill requires --project. Use: --backfill --project all")
            return
        PAPERS_DIR.mkdir(parents=True, exist_ok=True)
        backfill_relevance(project_configs)
        return

    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)

    # Merge project keywords with global keywords for wider fetch
    all_keywords = list(KEYWORDS)
    for pc in project_configs:
        for kw in pc.get("keywords", []):
            if kw not in all_keywords:
                all_keywords.append(kw)

    # Fetch papers
    if args.query:
        print(f"Searching Semantic Scholar: '{args.query}'")
        s2_results = s2_search(args.query, limit=20)
        papers = []
        for r in s2_results:
            ext_ids = r.get("externalIds") or {}
            arxiv_id = ext_ids.get("ArXiv", r.get("paperId", "unknown"))
            papers.append({
                "arxiv_id": arxiv_id,
                "title": r.get("title", ""),
                "authors": [a["name"] for a in (r.get("authors") or [])],
                "abstract": (r.get("tldr") or {}).get("text", ""),
                "categories": r.get("fieldsOfStudy") or [],
                "published": str(r.get("year", "")),
                "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id != "unknown" else "",
                "pdf_url": "",
                "keyword_hits": 0,
            })
    else:
        print(f"Fetching arXiv papers from last {args.days} day(s)...")
        papers = fetch_arxiv_papers(days=args.days, max_results=args.max_results, keywords=all_keywords)

    print(f"Found {len(papers)} papers matching keywords.")

    # Compute per-paper project relevance
    if project_configs:
        for p in papers:
            p["relevant_projects"] = compute_project_relevance(p, project_configs)

    if not papers:
        print("No papers found. Try increasing --days or lowering MIN_KEYWORD_HITS.")
        return

    # Score if requested
    if args.score:
        print("Scoring relevance via Claude...")
        papers = score_papers_with_claude(papers)

    # Load existing vault state
    known_ids = existing_paper_ids()
    created = 0
    skipped = 0

    # Generate notes
    for p in papers:
        filename = sanitize_filename(p["title"])
        note_path = PAPERS_DIR / f"{filename}.md"

        if note_path.exists():
            skipped += 1
            continue

        if args.dry_run:
            print(f"  [DRY RUN] Would create: {filename}.md")
            created += 1
            continue

        # Fetch S2 details for references
        print(f"  Processing: {p['title'][:60]}...")
        s2_data = s2_paper_details(p["arxiv_id"])
        sleep(0.5)  # Be nice to S2 API

        # Download PDF
        if download_pdf(p["arxiv_id"], p.get("pdf_url", "")):
            p["pdf_path"] = f"vault/papers/pdf/{p['arxiv_id'].replace('/', '_')}.pdf"
        else:
            p["pdf_path"] = ""

        # Generate and write note
        note_content = generate_note(p, s2_data, known_ids)
        note_path.write_text(note_content)

        # Update back-references in existing notes
        update_back_references(p["arxiv_id"], filename, s2_data, known_ids)

        # Register this paper for future linking
        known_ids[p["arxiv_id"]] = filename
        created += 1

    # Generate digest
    date_str = datetime.now().strftime("%Y-%m-%d")
    digest = generate_digest(papers, date_str)

    if args.dry_run:
        print(f"\n--- Digest preview ---\n{digest[:500]}...")
    else:
        digest_path = DIGEST_DIR / f"{date_str}.md"
        digest_path.write_text(digest)
        print(f"\nDigest written to: {digest_path}")

    # Per-project filtered digests
    if project_configs:
        for pc in project_configs:
            pname = pc["name"]
            proj_papers = [p for p in papers if pname in p.get("relevant_projects", [])]
            if not proj_papers:
                print(f"  {pname}: 0 relevant papers, skipping digest.")
                continue
            proj_digest = generate_digest(proj_papers, date_str, project_name=pname)
            if args.dry_run:
                print(f"\n  [DRY RUN] {pname}: {len(proj_papers)} relevant papers")
            else:
                ensure_project_dirs(pname)
                proj_digest_path = project_dir(pname) / "digests" / f"{date_str}.md"
                proj_digest_path.write_text(proj_digest)
                print(f"  {pname} digest: {len(proj_papers)} papers -> {proj_digest_path}")

    # Update per-project papers index
    if project_configs and not args.dry_run:
        for pc in project_configs:
            count = update_project_papers_index(pc["name"], PAPERS_DIR)
            print(f"  {pc['name']} papers index: {count} papers")

    print(f"\nDone: {created} created, {skipped} already existed.")


if __name__ == "__main__":
    main()
