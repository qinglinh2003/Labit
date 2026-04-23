from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DailyActivityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    summary: str = ""
    path: str = ""
    status: str = ""
    created_at: str = ""
    updated_at: str = ""
    refs: list[str] = Field(default_factory=list)

    @field_validator("title", "summary", "path", "status", "created_at", "updated_at", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("refs", mode="before")
    @classmethod
    def normalize_refs(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",")]
        if not isinstance(value, list):
            raise ValueError("refs must be a list of strings.")
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned


class DailyCommitItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha: str
    message: str
    authored_at: str = ""
    repo_label: str
    repo_path: str = ""

    @field_validator("sha", "message", "authored_at", "repo_label", "repo_path", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value


class DailyEventItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    summary: str
    created_at: str
    actor: str = ""
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("kind", "summary", "created_at", "actor", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def normalize_refs(cls, value: object) -> list[str]:
        return DailyActivityItem.normalize_refs(value)


class DailySummaryInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    date: str
    timezone: str
    event_counts: dict[str, int] = Field(default_factory=dict)
    events: list[DailyEventItem] = Field(default_factory=list)
    discussion_syntheses: list[DailyActivityItem] = Field(default_factory=list)
    hypotheses_created: list[DailyActivityItem] = Field(default_factory=list)
    hypotheses_updated: list[DailyActivityItem] = Field(default_factory=list)
    hypotheses_closed: list[DailyActivityItem] = Field(default_factory=list)
    experiments_created: list[DailyActivityItem] = Field(default_factory=list)
    experiments_updated: list[DailyActivityItem] = Field(default_factory=list)
    tasks_submitted: list[DailyActivityItem] = Field(default_factory=list)
    tasks_finished: list[DailyActivityItem] = Field(default_factory=list)
    reports: list[DailyActivityItem] = Field(default_factory=list)
    ideas: list[DailyActivityItem] = Field(default_factory=list)
    notes: list[DailyActivityItem] = Field(default_factory=list)
    todos: list[DailyActivityItem] = Field(default_factory=list)
    papers_pulled: list[DailyActivityItem] = Field(default_factory=list)
    papers_ingested: list[DailyActivityItem] = Field(default_factory=list)
    memory_updates: list[DailyActivityItem] = Field(default_factory=list)
    research_os_commits: list[DailyCommitItem] = Field(default_factory=list)
    project_code_commits: list[DailyCommitItem] = Field(default_factory=list)

    @field_validator("project", "date", "timezone", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value


class DailySummaryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bottom_line: str = ""
    evidence_gained: list[str] = Field(default_factory=list)
    belief_updates: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    next_action: list[str] = Field(default_factory=list)
    reflection: str = ""
    infra_notes: list[str] = Field(default_factory=list)

    @field_validator(
        "evidence_gained",
        "belief_updates",
        "blockers",
        "next_action",
        "infra_notes",
        mode="before",
    )
    @classmethod
    def normalize_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split("\n")]
        if not isinstance(value, list):
            raise ValueError("This field must be a list of strings.")
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    @field_validator("bottom_line", "reflection", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()


class DailySummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    date: str
    timezone: str
    markdown_path: str
    yaml_path: str
    markdown: str


class DailySummaryArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    markdown_path: str = ""
    yaml_path: str = ""
    markdown_excerpt: str = ""

    @field_validator("date", "markdown_path", "yaml_path", "markdown_excerpt", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value


class WeeklySummaryInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    week_label: str
    week_start: str
    week_end: str
    timezone: str
    day_count: int = 0
    daily_summaries: list[DailySummaryArtifact] = Field(default_factory=list)
    event_counts: dict[str, int] = Field(default_factory=dict)
    discussion_syntheses: list[DailyActivityItem] = Field(default_factory=list)
    hypotheses_created: list[DailyActivityItem] = Field(default_factory=list)
    hypotheses_updated: list[DailyActivityItem] = Field(default_factory=list)
    hypotheses_closed: list[DailyActivityItem] = Field(default_factory=list)
    experiments_created: list[DailyActivityItem] = Field(default_factory=list)
    experiments_updated: list[DailyActivityItem] = Field(default_factory=list)
    tasks_submitted: list[DailyActivityItem] = Field(default_factory=list)
    tasks_finished: list[DailyActivityItem] = Field(default_factory=list)
    reports: list[DailyActivityItem] = Field(default_factory=list)
    ideas: list[DailyActivityItem] = Field(default_factory=list)
    notes: list[DailyActivityItem] = Field(default_factory=list)
    todos: list[DailyActivityItem] = Field(default_factory=list)
    papers_pulled: list[DailyActivityItem] = Field(default_factory=list)
    papers_ingested: list[DailyActivityItem] = Field(default_factory=list)
    memory_updates: list[DailyActivityItem] = Field(default_factory=list)
    research_os_commits: list[DailyCommitItem] = Field(default_factory=list)
    project_code_commits: list[DailyCommitItem] = Field(default_factory=list)

    @field_validator("project", "week_label", "week_start", "week_end", "timezone", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value


class WeeklySummaryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekly_progress: list[str] = Field(default_factory=list)
    evidence_and_results: list[str] = Field(default_factory=list)
    hypothesis_evolution: list[str] = Field(default_factory=list)
    papers_reports_and_key_reads: list[str] = Field(default_factory=list)
    code_and_infrastructure: list[str] = Field(default_factory=list)
    carry_over_risks: list[str] = Field(default_factory=list)
    next_week_plan: list[str] = Field(default_factory=list)
    free_write: str = ""

    @field_validator(
        "weekly_progress",
        "evidence_and_results",
        "hypothesis_evolution",
        "papers_reports_and_key_reads",
        "code_and_infrastructure",
        "carry_over_risks",
        "next_week_plan",
        mode="before",
    )
    @classmethod
    def normalize_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split("\n")]
        if not isinstance(value, list):
            raise ValueError("This field must be a list of strings.")
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    @field_validator("free_write", mode="before")
    @classmethod
    def strip_free_write(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()


class WeeklySummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    week_label: str
    week_start: str
    week_end: str
    timezone: str
    markdown_path: str
    yaml_path: str
    markdown: str
