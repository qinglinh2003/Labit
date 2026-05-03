from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DevDecision:
    question: str
    options: list[str]
    recommended: int | None = None
    rationale: str | None = None
    asked_by: str = "writer"  # writer|reviewer


@dataclass
class DevRound:
    round_index: int
    writer_summary: str = ""
    reviewer_summary: str = ""
    findings: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    status: str = "pending"  # pending|writer_done|approved|decision_needed


@dataclass
class DevLoopSession:
    task: str
    writer_name: str
    reviewer_name: str
    max_rounds: int = 6
    test_mode: str = "auto"  # off|auto|on
    current_round: int = 0
    history: list[DevRound] = field(default_factory=list)
    pending_decision: DevDecision | None = None
    user_decision: str | None = None  # answer to pending decision
    status: str = "active"  # active|waiting_decision|completed|stopped
    scope_label: str = ""
    scope_pathspecs: list[str] = field(default_factory=list)
    scope_git_root: str = ""  # git root for the dev scope
    branch_repo_root: str = ""  # repo that owns dev_branch and worktree metadata
    worktree_path: str = ""  # isolated worktree used by this dev loop, if any
    initial_dirty_files: list[str] = field(default_factory=list)
    dev_branch: str = ""  # branch created for this dev loop
    original_branch: str = ""  # branch to return to after dev loop
