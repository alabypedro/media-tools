"""Estado de progresso de um download e formatacao para exibicao."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass
class ProgressInfo:
    downloaded: int = 0
    total: int | None = None
    speed: float | None = None
    eta: float | None = None
    item: int = 1
    items: int | None = None
    files_done: int = 0
    stage_detail: str = ""  # merge, extract_audio, convert, move...

    @property
    def item_fraction(self) -> float | None:
        if self.total and self.total > 0:
            return max(0.0, min(1.0, self.downloaded / self.total))
        return None

    @property
    def fraction(self) -> float | None:
        """Fracao total do job (considera itens de playlist/galeria)."""
        item_frac = self.item_fraction
        if self.items and self.items > 1:
            done_items = max(0, min(self.items, self.item - 1))
            return max(0.0, min(1.0, (done_items + (item_frac or 0.0)) / self.items))
        return item_frac

    def copy(self) -> "ProgressInfo":
        return replace(self)


def human_size(value: float | int | None) -> str:
    if value is None or value < 0:
        return "?"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_speed(value: float | None) -> str:
    return f"{human_size(value)}/s" if value else ""


def human_duration(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
