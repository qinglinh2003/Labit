from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenBudget:
    total_tokens: int = 120000
    reserve_tokens: int = 20000

    @property
    def usable_tokens(self) -> int:
        return max(0, self.total_tokens - self.reserve_tokens)


@dataclass(frozen=True)
class TokenBudgetDecision:
    included_tokens: int
    truncated: bool
    reason: str
