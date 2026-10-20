"""Compiled, vectorized DBC extraction plans; no Python loop over CAN samples.

Channel numbers are the 1-based numbers stored in BLF, not python-can's
zero-based Message.channel values. Numeric values are retained for enums.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cantools
import numpy as np


@dataclass(frozen=True)
class DBCBinding:
    path: Path | str
    channel: int | None = None


@dataclass(frozen=True)
class DecodedSignal:
    name: str
    unit: str
    samples: np.ndarray
    comment: str = ""


@dataclass(frozen=True)
class DecodedGroup:
    timestamps: np.ndarray
    signals: tuple[DecodedSignal, ...]
    message_name: str
    channel: int
    frame_id: int
    is_extended_id: bool


@dataclass(frozen=True)
class _SignalPlan:
    signal: object
    byte_start: int
    span: int
    view_width: int
    shift: int
    segments: tuple[tuple[int, int, int, int], ...]
    conditions: tuple[tuple[str, tuple[int, ...]], ...]

    def raw(self, payloads: np.ndarray) -> np.ndarray:
        """Extract integers with one strided NumPy view for common layouts."""
        signal = self.signal
        width = self.view_width
        if width and self.byte_start + width <= payloads.shape[1]:
            order = "<" if signal.byte_order == "little_endian" else ">"
            values = payloads[:, self.byte_start:self.byte_start + width].view(
                np.dtype(f"{order}u{width}")
            ).reshape(-1)
            if self.shift:
                values = np.right_shift(values, self.shift)
            if signal.length < width * 8:
                values = np.bitwise_and(values, (1 << signal.length) - 1)
            return values
        # Unaligned 64-bit signals may span nine bytes. Work per byte segment,
        # never per frame; use uint64 so shifts cannot overflow a smaller dtype.
        values = np.zeros(len(payloads), dtype=np.uint64)
        for byte, source_shift, bits, target_shift in self.segments:
            part = payloads[:, byte].astype(np.uint64)
            if source_shift:
                part >>= source_shift
            if bits < 8:
                part &= (1 << bits) - 1
            if target_shift:
                part <<= target_shift
            values |= part
        return values

    def physical(self, raw: np.ndarray) -> np.ndarray:
        signal = self.signal
        if signal.is_float:
            if signal.length not in (32, 64):
                raise ValueError(f"Float {signal.name}: only 32/64-bit IEEE supported")
            values = raw.astype(f"u{signal.length // 8}", copy=False).view(
                f"f{signal.length // 8}"
            )
        elif signal.is_signed:
            values = self.integer(raw)
        else:
            values = raw
        if signal.scale != 1 or signal.offset != 0:
            values = values.astype(np.float64) * signal.scale + signal.offset
        return values

    def integer(self, raw: np.ndarray) -> np.ndarray:
        """Unscaled integer value, also used for signed multiplexer selection."""
        signal = self.signal
        if not signal.is_signed:
            return raw
        if signal.length == 64:
            return raw.astype(np.uint64, copy=False).view(np.int64)
        # Sign extension in unsigned arithmetic preserves exact int64 values.
        bit = np.uint64(1 << (signal.length - 1))
        return ((raw.astype(np.uint64) ^ bit) - bit).view(np.int64)


def _compile_signal(signal: object, conditions: tuple) -> _SignalPlan:
    length = signal.length
    if not 1 <= length <= 64:
        raise ValueError(f"Signal {signal.name}: numeric size must be 1..64 bits")
    if signal.is_float and length not in (32, 64):
        raise ValueError(f"Float {signal.name}: only 32/64-bit IEEE supported")
    little = signal.byte_order == "little_endian"
    byte_start = signal.start // 8
    bit_offset = signal.start % 8 if little else 7 - signal.start % 8
    span = (bit_offset + length + 7) // 8
    width = next((n for n in (1, 2, 4, 8) if n >= span), 0)
    shift = bit_offset if little else width * 8 - bit_offset - length
    remaining, position, consumed = length, bit_offset, 0
    segments = []
    byte = byte_start
    while remaining:
        bits = min(8 - position, remaining)
        source_shift = position if little else 8 - position - bits
        target_shift = consumed if little else remaining - bits
        segments.append((byte, source_shift, bits, target_shift))
        consumed += bits
        remaining -= bits
        byte += 1
        position = 0
    return _SignalPlan(signal, byte_start, span, width, shift, tuple(segments), conditions)


class _MessagePlan:
    def __init__(self, message: object):
        self.message = message
        self.output_name = message.name
        signals = {s.name: s for s in message.signals}
        conditions: dict[str, tuple] = {}

        def resolve(name: str, visiting: frozenset[str] = frozenset()) -> tuple:
            if name in conditions:
                return conditions[name]
            if name in visiting:
                raise ValueError(f"Cyclic multiplexer dependency: {message.name}.{name}")
            signal = signals[name]
            parent = signal.multiplexer_signal
            if parent is None:
                value = ()
            else:
                if parent not in signals:
                    raise ValueError(f"Missing multiplexer {parent} in {message.name}")
                value = resolve(parent, visiting | {name}) + (
                    (parent, tuple(sorted(signal.multiplexer_ids or ()))),
                )
            conditions[name] = value
            return value

        self.plans = {s.name: _compile_signal(s, resolve(s.name)) for s in message.signals}
        self.groups: dict[tuple, list[_SignalPlan]] = {}
        for plan in self.plans.values():
            self.groups.setdefault(plan.conditions, []).append(plan)

    def decode(self, channel: int, timestamps: np.ndarray,
               payloads: np.ndarray) -> list[DecodedGroup]:
        raw_cache: dict[str, np.ndarray] = {}

        def raw(name: str) -> np.ndarray:
            if name not in raw_cache:
                raw_cache[name] = self.plans[name].raw(payloads)
            return raw_cache[name]

        result = []
        message = self.message
        for conditions, plans in self.groups.items():
            mask = None
            for parent, accepted in conditions:
                parent_values = self.plans[parent].integer(raw(parent))
                condition = (parent_values == accepted[0]) if len(accepted) == 1 else np.isin(parent_values, accepted)
                mask = condition if mask is None else mask & condition
            if mask is not None and not np.any(mask):
                continue
            # Slice rows once for a mux branch, then decode all its signals.
            data = payloads if mask is None else payloads[mask]
            times = timestamps if mask is None else timestamps[mask]
            decoded = []
            for plan in plans:
                signal = plan.signal
                if mask is None:
                    values = raw(signal.name)
                elif signal.name in raw_cache:
                    values = raw_cache[signal.name][mask]
                else:
                    values = plan.raw(data)
                comment = signal.comment or ""
                if signal.choices:
                    choices = "; ".join(f"{k}={v}" for k, v in signal.choices.items())
                    comment = f"{comment}\nValues: {choices}".strip()
                decoded.append(DecodedSignal(
                    f"CAN{channel}.{self.output_name}.{signal.name}",
                    signal.unit or "", plan.physical(values), comment,
                ))
            if decoded:
                result.append(DecodedGroup(
                    times, tuple(decoded), self.output_name, channel,
                    message.frame_id, message.is_extended_frame,
                ))
        return result


class DatabaseRegistry:
    """Load DBCs once and cache extraction plans by CAN identifier and format.

    An explicit bus binding takes precedence over a wildcard binding. Conflicting
    identifiers within the same scope fail fast, rather than silently choosing a
    database. Short frames are counted and dropped; unknown frames return [].
    """

    def __init__(self, bindings: Sequence[DBCBinding]):
        if not bindings:
            raise ValueError("Select at least one DBC file")
        self.bindings = tuple(bindings)
        self._plans: dict[tuple[int | None, int, bool], _MessagePlan] = {}
        self.truncated_frames = 0
        self.unknown_frames = 0
        self.decoded_frames = 0
        self.warnings: list[str] = []
        seen = set()
        loaded: dict[Path, list[_MessagePlan]] = {}
        for binding in bindings:
            if binding.channel is not None and binding.channel < 1:
                raise ValueError("DBC channel must be 1-based (CAN1, CAN2, ...)")
            path = Path(binding.path).expanduser().resolve()
            identity = (path, binding.channel)
            if identity in seen:
                continue
            seen.add(identity)
            if path not in loaded:
                database = cantools.database.load_file(str(path), strict=True)
                plans = []
                for message in database.messages:
                    if message.is_container:
                        raise ValueError(f"Container messages unsupported: {message.name}")
                    plans.append(_MessagePlan(message))
                loaded[path] = plans
            for plan in loaded[path]:
                message = plan.message
                key = (binding.channel, message.frame_id, message.is_extended_frame)
                if key in self._plans:
                    scope = "all channels" if binding.channel is None else f"CAN{binding.channel}"
                    raise ValueError(
                        f"Conflicting DBC ID 0x{message.frame_id:X} "
                        f"({'extended' if message.is_extended_frame else 'standard'}) on {scope}; "
                        "assign each DBC to its CAN channel"
                    )
                self._plans[key] = plan
        # DBC message names need only be unique within one source database.
        # Qualify collisions across all bindings so both wildcard and explicit
        # bus assignments produce unambiguous MDF channel and Excel group names.
        identities: dict[str, set[tuple[int, bool]]] = {}
        for plan in self._plans.values():
            message = plan.message
            identities.setdefault(message.name, set()).add(
                (message.frame_id, message.is_extended_frame)
            )
        collisions = {name for name, ids in identities.items() if len(ids) > 1}
        for plan in self._plans.values():
            message = plan.message
            if message.name in collisions:
                kind = "EXT" if message.is_extended_frame else "STD"
                plan.output_name = f"{message.name}_0x{message.frame_id:X}_{kind}"
        for name in sorted(collisions):
            self.warnings.append(
                f"Duplicate DBC message name '{name}': output names include CAN ID and STD/EXT."
            )

    def resolve(self, channel: int, frame_id: int, is_extended: bool) -> _MessagePlan | None:
        return self._plans.get((channel, frame_id, bool(is_extended))) or self._plans.get(
            (None, frame_id, bool(is_extended))
        )

    def decode(self, channel: int, frame_id: int, is_extended: bool,
               timestamps: np.ndarray, payloads: np.ndarray,
               lengths: np.ndarray | None = None) -> list[DecodedGroup]:
        plan = self.resolve(channel, frame_id, is_extended)
        if plan is None:
            self.unknown_frames += len(timestamps)
            return []
        timestamps = np.asarray(timestamps, dtype=np.float64)
        payloads = np.asarray(payloads, dtype=np.uint8)
        if payloads.ndim != 2 or len(payloads) != len(timestamps):
            raise ValueError("payloads must be an N x bytes uint8 array matching timestamps")
        if payloads.shape[1] < plan.message.length:
            raise ValueError("payload array is narrower than the DBC message length")
        if lengths is not None:
            lengths = np.asarray(lengths)
            if lengths.shape != timestamps.shape:
                raise ValueError("lengths must match timestamps")
            valid = lengths >= plan.message.length
            invalid = len(valid) - int(np.count_nonzero(valid))
            self.truncated_frames += invalid
            if invalid:
                timestamps = timestamps[valid]
                payloads = payloads[valid]
        self.decoded_frames += len(timestamps)
        if not len(timestamps):
            return []
        return plan.decode(channel, timestamps, payloads)
