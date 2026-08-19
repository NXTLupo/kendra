from __future__ import annotations

import os
import shutil
from pathlib import Path


class SlotManager:
    """Small A/B application-slot helper.

    This does not update the operating system. It only manages verified application
    directories below slots_root and a `current` symlink.
    """

    def __init__(self, slots_root: Path):
        self.root = slots_root
        self.slot_a = self.root / "slot-a"
        self.slot_b = self.root / "slot-b"
        self.current = self.root / "current"

    def ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.slot_a.mkdir(parents=True, exist_ok=True)
        self.slot_b.mkdir(parents=True, exist_ok=True)

    def active_slot(self) -> Path | None:
        if not self.current.is_symlink():
            return None
        target = self.current.resolve()
        if target not in {self.slot_a.resolve(), self.slot_b.resolve()}:
            raise RuntimeError("current symlink points outside Kendra A/B slots")
        return target

    def inactive_slot(self) -> Path:
        active = self.active_slot()
        return self.slot_b if active == self.slot_a.resolve() else self.slot_a

    def clear_inactive(self) -> Path:
        target = self.inactive_slot()
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        return target

    def activate(self, slot: Path) -> None:
        slot = slot.resolve()
        allowed = {self.slot_a.resolve(), self.slot_b.resolve()}
        if slot not in allowed:
            raise ValueError("Can only activate slot-a or slot-b")
        temp = self.root / ".current.new"
        if temp.exists() or temp.is_symlink():
            temp.unlink()
        os.symlink(slot, temp)
        os.replace(temp, self.current)
