"""Segments held in memory for every TV, numbered locally so numbering never goes backwards."""
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class BufferedSegment:
    seq: int
    duration: float
    data: bytes
    discontinuity: bool
    disc_seq: int


class SegmentBuffer:
    def __init__(self, max_seconds: float = 120.0):
        self.max_seconds = max_seconds
        self._segments: OrderedDict[int, BufferedSegment] = OrderedDict()
        self._next_seq = 0
        self._disc_seq = 0
        self._pending_discontinuity = False

    def __len__(self) -> int:
        return len(self._segments)

    def append(self, duration: float, data: bytes, discontinuity: bool = False) -> int:
        discontinuity = discontinuity or self._pending_discontinuity
        self._pending_discontinuity = False
        if discontinuity:
            self._disc_seq += 1
        seq = self._next_seq
        self._next_seq += 1
        self._segments[seq] = BufferedSegment(seq, duration, data, discontinuity, self._disc_seq)
        self._prune()
        return seq

    def get(self, seq: int) -> bytes | None:
        segment = self._segments.get(seq)
        return segment.data if segment else None

    def window(self, count: int) -> list[BufferedSegment]:
        return list(self._segments.values())[-count:]

    def reset(self) -> None:
        """Drop everything; the next segment starts a discontinuity, numbering continues."""
        self._segments.clear()
        self._pending_discontinuity = self._next_seq > 0

    def _prune(self) -> None:
        total = sum(s.duration for s in self._segments.values())
        while len(self._segments) > 1 and total > self.max_seconds:
            _, oldest = self._segments.popitem(last=False)
            total -= oldest.duration
