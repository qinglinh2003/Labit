#!/usr/bin/env python3
"""
spark_ideas.py — Cross-analyze paper notes to find innovation opportunities.

Usage:
    python spark_ideas.py                # Analyze all papers
    python spark_ideas.py --tag VLM      # Only papers with specific tag
    python spark_ideas.py --recent 14    # Only papers added in last N days

Requires:
    claude CLI (Claude Code) installed and authenticated
"""

import argparse
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from itertools import combinations

import yaml

VAULT_DIR = Path(__file__).parent.parent / "vault"
PAPERS_DIR = VAULT_DIR / "papers"
SPARKS_DIR = VAULT_DIR / "sparks"

# Your research context — edit this
RESEARCH_CONTEXT = """I work on VLM (Vision-Language Model) agents for exploration in partially observable environments.

Current projects:
- GLANCE: Curiosity-driven exploration for VLM agents using visual prediction error as intrinsic reward.
- SemBelief-WM: Slot-based latent belief states with a VLM backbone for model-based RL.

I'm especially interested in combinations that could:
1. Improve exploration in environments with visual distractors
2. Build better world models from partial observations  
3. Reduce hallucination in VLM-based decision making
4. Bridge object-centric representations with RL"""


def load_paper_notes(tag_filter: str = None, recent_days: int = None) -> list[dict]:
    """Load paper notes from vault with optional filtering."""
    papers = []
    cutoff = None
    if recent_days:
        cutoff = (datetime.now() - timedelta(days=recent_days)).strftime("%Y-%m-%d")

    for f in PAPERS_DIR.glob("*.md"):
        content = f.read_text()

        # Parse frontmatter
        fm_match = re.match(r'^---\n(.+?)\n---', content, re.DOTALL)
        if not fm_match:
            continue
        try:
            fm = yaml.safe_load(fm_match.group(1))
        except yaml.YAMLError:
            continue

        # Filter by date
        if cutoff and fm.get("added", "9999") < cutoff:
            continue

        # Filter by tag
        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        if tag_filter and tag_filter.lower() not in [t.lower() for t in tags]:
            continue

        papers.append({
            "filename": f.stem,
            "title": fm.get("title", f.stem),
            "tags": tags,
            "content": content,
            "frontmatter": fm,
        })

    return papers


def find_clusters(papers: list[dict], max_groups: int = 5, group_size: int = 3) -> list[list[dict]]:
    """Group papers by tag overlap for cross-analysis."""
    if len(papers) <= group_size:
        return [papers] if papers else []

    # Score all pairs by tag overlap
    pair_scores = []
    for a, b in combinations(papers, 2):
        tags_a = set(t.lower() for t in a["tags"])
        tags_b = set(t.lower() for t in b["tags"])
        overlap = len(tags_a & tags_b)
        has_diff = len(tags_a ^ tags_b) > 0  # They should differ on something
        if overlap >= 1 and has_diff:
            pair_scores.append((overlap, a, b))

    pair_scores.sort(key=lambda x: x[0], reverse=True)

    # Greedy clustering: pick top pairs, expand to group_size
    used = set()
    groups = []
    for _, a, b in pair_scores:
        if a["filename"] in used or b["filename"] in used:
            continue
        group = [a, b]
        used.add(a["filename"])
        used.add(b["filename"])

        # Try to add a third paper that shares tags with both
        tags_ab = set(t.lower() for t in a["tags"]) | set(t.lower() for t in b["tags"])
        for p in papers:
            if p["filename"] in used:
                continue
            p_tags = set(t.lower() for t in p["tags"])
            if len(p_tags & tags_ab) >= 1:
                group.append(p)
                used.add(p["filename"])
                if len(group) >= group_size:
                    break

        groups.append(group)
        if len(groups) >= max_groups:
            break

    return groups


def extract_summary(content: str) -> str:
    """Extract the key sections from a paper note (skip empty template fields)."""
    sections = ["## Core idea", "## Method summary", "## Limitations & gaps", "## Potential for my work"]
    summary_parts = []
    for section in sections:
        idx = content.find(section)
        if idx == -1:
            continue
        # Find content until next ## or end
        next_section = content.find("\n## ", idx + len(section))
        if next_section == -1:
            text = content[idx + len(section):]
        else:
            text = content[idx + len(section):next_section]
        text = text.strip()
        if text and text != "(read paper to fill in)":
            summary_parts.append(f"{section}\n{text}")
    return "\n\n".join(summary_parts)


def analyze_group(group: list[dict]) -> str:
    """Send a group of papers to Claude for cross-analysis."""
    papers_text = ""
    for i, p in enumerate(group):
        summary = extract_summary(p["content"])
        tags_str = ", ".join(p["tags"])
        papers_text += f"\n{'='*60}\nPaper {i+1}: {p['title']}\nTags: {tags_str}\n{summary}\n"

    prompt = f"""{RESEARCH_CONTEXT}

Here are {len(group)} related papers from my reading notes:
{papers_text}

Analyze these papers together and identify:

1. CROSS-POLLINATION: Could a method from paper X solve a limitation mentioned in paper Y? Be specific about which method and which limitation.

2. UNEXPLORED COMBINATIONS: Is there a combination of techniques from these papers that nobody has tried? What would the hypothesis be?

3. SETTING TRANSFER: Could the experimental setting of one paper reveal interesting failure modes of another paper's method?

For each actionable idea, output it as:

### Spark: [short title]
- **Papers**: [which papers]
- **Hypothesis**: [specific, testable hypothesis]  
- **Why it could work**: [1-2 sentences]
- **First experiment**: [what you'd try first]
- **Risk**: [main reason it might not work]

Only output genuinely novel combinations. Skip obvious ideas."""

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=180,
        )
        return result.stdout.strip()
    except FileNotFoundError:
        return "(Claude Code not installed — install with: npm install -g @anthropic-ai/claude-code)"
    except subprocess.TimeoutExpired:
        return "(Claude timed out on this group)"
    except Exception as e:
        return f"(Error: {e})"


def main():
    parser = argparse.ArgumentParser(description="Cross-analyze papers for research sparks")
    parser.add_argument("--tag", type=str, help="Filter papers by tag")
    parser.add_argument("--recent", type=int, help="Only papers added in last N days")
    parser.add_argument("--max-groups", type=int, default=5, help="Max number of groups to analyze")
    parser.add_argument("--dry-run", action="store_true", help="Show groups without calling Claude")
    args = parser.parse_args()

    SPARKS_DIR.mkdir(parents=True, exist_ok=True)

    # Load papers
    papers = load_paper_notes(tag_filter=args.tag, recent_days=args.recent)
    print(f"Loaded {len(papers)} paper notes.")

    if len(papers) < 2:
        print("Need at least 2 papers to cross-analyze. Read more papers first!")
        return

    # Cluster
    groups = find_clusters(papers, max_groups=args.max_groups)
    print(f"Found {len(groups)} analysis groups.\n")

    if args.dry_run:
        for i, group in enumerate(groups):
            titles = [p["title"][:50] for p in group]
            print(f"Group {i+1}: {titles}")
        return

    # Analyze each group
    all_sparks = []
    for i, group in enumerate(groups):
        titles = [p["title"][:40] for p in group]
        print(f"Analyzing group {i+1}/{len(groups)}: {titles}...")
        result = analyze_group(group)
        all_sparks.append(f"## Group {i+1}\n\nPapers: {', '.join(p['title'] for p in group)}\n\n{result}")

    # Write output
    week = datetime.now().strftime("%Y-W%V")
    output = f"# Research sparks — {week}\n\n" + "\n\n---\n\n".join(all_sparks)

    spark_path = SPARKS_DIR / f"{week}.md"
    spark_path.write_text(output)
    print(f"\nSparks written to: {spark_path}")


if __name__ == "__main__":
    main()
