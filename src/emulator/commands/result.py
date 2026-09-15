"""What a command reports back to its caller."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommandResult:
    """Vanilla's success flag and result value, plus a ``return`` marker."""

    success: bool = True
    value: int = 0
    #: set by ``return`` so the calling function stops after this line
    returned: bool = False

    @property
    def count(self) -> int:
        return self.value if self.success else 0

    @classmethod
    def failure(cls) -> CommandResult:
        return cls(success=False, value=0)
