"""Detection modules. Every detector returns a ModuleResult."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModuleResult:
    """Common output of every detector.

    score       0..1, where 1 means "looks live / genuine" and 0 means "looks spoofed".
    confidence  0..1, how much this module trusts its own score right now (0 = unavailable).
    """

    name: str
    score: float = 0.5
    confidence: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    flags: list[dict[str, str]] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.confidence > 0.0

    def flag(self, code: str, severity: str, message: str) -> None:
        self.flags.append({"module": self.name, "code": code, "severity": severity, "message": message})

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(float(self.score), 4),
            "confidence": round(float(self.confidence), 4),
            "available": self.available,
            "details": self.details,
        }


def unavailable(name: str, reason: str) -> ModuleResult:
    return ModuleResult(name=name, score=0.5, confidence=0.0, details={"reason": reason})
