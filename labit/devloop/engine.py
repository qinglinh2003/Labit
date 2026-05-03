from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from rich.console import Console

from labit.chat.service import ChatService
from labit.commands.rendering import render_dev_decision
from labit.devloop.git_ops import (
    dev_auto_commit,
    get_last_commit_diff,
    get_scope_diff,
    list_scope_dirty_files,
)
from labit.devloop.models import DevDecision, DevLoopSession, DevRound
from labit.paths import RepoPaths

RunStreamingTurn = Callable[..., object]


def parse_dev_decision(text: str) -> DevDecision | None:
    """Parse DECISION_NEEDED block from agent output."""
    if "DECISION_NEEDED" not in text:
        return None
    lines = text[text.index("DECISION_NEEDED"):].splitlines()
    question = ""
    options: list[str] = []
    recommended: int | None = None
    rationale: str | None = None
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("question:"):
            question = line.split(":", 1)[1].strip()
        elif re.match(r"option_[a-z]:", low):
            options.append(line.split(":", 1)[1].strip())
        elif low.startswith("recommended:"):
            letter = line.split(":", 1)[1].strip().lower()
            idx = ord(letter[0]) - ord("a") if letter else None
            recommended = idx
        elif low.startswith("reason:"):
            rationale = line.split(":", 1)[1].strip()
    if not question and not options:
        return None
    return DevDecision(
        question=question or "(agent requests a decision)",
        options=options if options else ["(see agent output above)"],
        recommended=recommended,
        rationale=rationale,
    )


def parse_review_findings(text: str) -> tuple[bool, list[str], str]:
    """Parse reviewer output. Returns (is_approved, findings, summary)."""
    lines = text.strip().splitlines()
    findings: list[str] = []
    summary_parts: list[str] = []
    is_lgtm = False

    for line in lines:
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("lgtm"):
            is_lgtm = True
        elif low.startswith("finding:"):
            findings.append(stripped.split(":", 1)[1].strip())
        elif low.startswith("summary:"):
            summary_parts.append(stripped.split(":", 1)[1].strip())

    if not is_lgtm and re.search(r"\bLGTM\b", text):
        is_lgtm = True

    summary = " ".join(summary_parts) if summary_parts else text[:200]
    approved = not findings and (is_lgtm or bool(text.strip()))
    return approved, findings, summary


def run_dev_loop(
    *,
    service: ChatService,
    session,
    dev_session: DevLoopSession,
    console: Console,
    run_streaming_turn: RunStreamingTurn,
) -> DevLoopSession:
    """Run the writer/reviewer auto-iteration loop."""
    participants = session.participants
    writer = next((p for p in participants if p.name == dev_session.writer_name), participants[0])
    reviewer = next((p for p in participants if p.name == dev_session.reviewer_name), participants[-1])

    skip_for_writer = {p.name for p in participants if p.name != writer.name}
    skip_for_reviewer = {p.name for p in participants if p.name != reviewer.name}

    scope_label = dev_session.scope_label or "repository"
    scope_pathspecs = dev_session.scope_pathspecs or ["."]
    scope_git_root = Path(dev_session.scope_git_root) if dev_session.scope_git_root else RepoPaths.discover().root
    turn_cwd = str(scope_git_root)

    for round_num in range(dev_session.current_round + 1, dev_session.max_rounds + 1):
        dev_session.current_round = round_num
        dev_round = DevRound(round_index=round_num)

        console.print(f"\n[bold]── Dev Round {round_num}/{dev_session.max_rounds} ──[/bold]")

        writer_query_parts = [f"[Dev Loop — Round {round_num}/{dev_session.max_rounds}]"]
        writer_query_parts.append(f"Task: {dev_session.task}")
        writer_query_parts.append(f"Scope: {scope_label}")
        writer_query_parts.append(f"Editable workspace: {turn_cwd}")
        if dev_session.worktree_path:
            writer_query_parts.append(
                "This /dev loop is running in an isolated git worktree. "
                "Use the editable workspace path above, not the original checkout, for all file edits and commands."
            )

        if dev_session.user_decision:
            writer_query_parts.append(f"User decided: {dev_session.user_decision}")
            dev_session.user_decision = None
            dev_session.pending_decision = None

        if dev_session.history:
            last = dev_session.history[-1]
            if last.findings:
                writer_query_parts.append("Reviewer findings from last round:")
                for finding in last.findings:
                    writer_query_parts.append(f"- {finding}")
            elif last.reviewer_summary:
                writer_query_parts.append(f"Reviewer feedback: {last.reviewer_summary}")

        writer_query_parts.append(
            "\nYou are the WRITER. Read the codebase, implement the change, and summarize what you did."
            "\nFollow the session's project boundary rules from the system prompt."
            f"\nWork only within this /dev scope: {scope_label}."
            "\nIMPORTANT: Do NOT run `git commit` yourself. Labit handles all commits automatically."
            "\nIf you hit a genuine architecture/design fork requiring user input, output:\n"
            "DECISION_NEEDED\nquestion: ...\noption_a: ...\noption_b: ...\nrecommended: a\nreason: ..."
        )

        try:
            writer_result = run_streaming_turn(
                service=service,
                session=session,
                query="\n".join(writer_query_parts),
                skip_participants=skip_for_writer,
                cwd_override=turn_cwd,
            )
        except KeyboardInterrupt:
            console.print(f"\n[bold yellow]Dev loop interrupted at round {round_num} (writer phase).[/bold yellow]")
            dev_session.status = "stopped"
            return dev_session

        if writer_result is None or not writer_result.replies:
            console.print("[bold red]Writer produced no output. Stopping dev loop.[/bold red]")
            dev_session.status = "stopped"
            return dev_session

        writer_text = writer_result.replies[0].message.content
        dev_round.writer_summary = writer_text[:500]
        changed_files = list_scope_dirty_files(scope_pathspecs, scope_git_root)
        if dev_session.initial_dirty_files:
            changed_files = [item for item in changed_files if item not in dev_session.initial_dirty_files] or changed_files
        dev_round.changed_files = changed_files[:20]

        commit_hash = dev_auto_commit(round_num, scope_pathspecs, dev_session.task, scope_git_root)
        if commit_hash:
            console.print(f"[dim]Auto-committed: {commit_hash}[/dim]")

        decision = parse_dev_decision(writer_text)
        if decision:
            decision.asked_by = "writer"
            dev_session.pending_decision = decision
            dev_session.status = "waiting_decision"
            dev_session.history.append(dev_round)
            render_dev_decision(console, dev_session)
            return dev_session

        console.print(f"\n[dim]── Reviewer ({reviewer.name}) ──[/dim]")

        if commit_hash:
            diff_text = get_last_commit_diff(scope_git_root)
        else:
            diff_text = get_scope_diff(scope_pathspecs, scope_git_root)

        reviewer_query_parts = [f"[Dev Loop — Review Round {round_num}/{dev_session.max_rounds}]"]
        reviewer_query_parts.append(f"Task: {dev_session.task}")
        reviewer_query_parts.append(f"Scope: {scope_label}")
        reviewer_query_parts.append(f"Editable workspace: {turn_cwd}")
        if dev_round.changed_files:
            reviewer_query_parts.append(
                "Files changed this round:\n" + "\n".join(f"- {item}" for item in dev_round.changed_files[:20])
            )
        reviewer_query_parts.append(f"\nWriter's changes (this round only):\n{diff_text}")

        if dev_session.test_mode == "on":
            reviewer_query_parts.append("\nRun targeted tests relevant to the changes if test infrastructure exists.")
        elif dev_session.test_mode == "auto":
            reviewer_query_parts.append(
                "\nIf you see obvious test infrastructure (pytest, unittest), run a quick targeted check. "
                "Otherwise skip testing and focus on code review."
            )

        reviewer_query_parts.append(
            "\nYou are the REVIEWER. Review the diff above."
            "\n- If the changes look correct and complete, output: LGTM\\nsummary: ..."
            "\n- If there are issues, output: FINDING: <issue>\\n for each issue, then summary: ..."
            "\n- If a real design fork needs user input, output DECISION_NEEDED (same format as writer)."
            "\n- Be concrete and actionable. Don't nitpick style — focus on bugs, regressions, missing logic."
        )

        try:
            reviewer_result = run_streaming_turn(
                service=service,
                session=session,
                query="\n".join(reviewer_query_parts),
                skip_participants=skip_for_reviewer,
                cwd_override=turn_cwd,
            )
        except KeyboardInterrupt:
            console.print(f"\n[bold yellow]Dev loop interrupted at round {round_num} (reviewer phase).[/bold yellow]")
            dev_session.status = "stopped"
            dev_session.history.append(dev_round)
            return dev_session

        if reviewer_result is None or not reviewer_result.replies:
            console.print("[bold red]Reviewer produced no output. Stopping dev loop.[/bold red]")
            dev_session.status = "stopped"
            dev_session.history.append(dev_round)
            return dev_session

        reviewer_text = reviewer_result.replies[0].message.content
        dev_round.reviewer_summary = reviewer_text[:500]

        decision = parse_dev_decision(reviewer_text)
        if decision:
            decision.asked_by = "reviewer"
            dev_session.pending_decision = decision
            dev_session.status = "waiting_decision"
            dev_session.history.append(dev_round)
            render_dev_decision(console, dev_session)
            return dev_session

        approved, findings, summary = parse_review_findings(reviewer_text)
        dev_round.findings = findings
        dev_round.reviewer_summary = summary
        dev_round.status = "approved" if approved else "review_failed"
        dev_session.history.append(dev_round)

        if approved:
            console.print(f"[bold green]Approved at round {round_num}. Dev loop complete.[/bold green]")
            dev_session.status = "completed"
            return dev_session

        console.print(f"[dim]{len(findings)} finding(s) — continuing to next round[/dim]")

    console.print(f"[bold yellow]Reached max rounds ({dev_session.max_rounds}). Dev loop finished.[/bold yellow]")
    dev_session.status = "completed"
    return dev_session
