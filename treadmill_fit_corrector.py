#!/usr/bin/env python3
"""
Soft treadmill correction for FIT files without GPS.

The utility adjusts per-lap distance using treadmill speeds provided by user,
then scales cumulative record distances inside each lap proportionally.
File structure is preserved; only selected field payload bytes are patched.
"""

from __future__ import annotations

import argparse
import csv
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any


# FIT global message numbers
MSG_SESSION = 18
MSG_LAP = 19
MSG_RECORD = 20

# Common field definitions for messages we patch
FIELD_TIMESTAMP = 253
FIELD_START_TIME = 2
FIELD_TOTAL_TIMER_TIME = 8
FIELD_TOTAL_ELAPSED_TIME = 7
FIELD_TOTAL_DISTANCE = 9
FIELD_RECORD_DISTANCE = 5
FIELD_RECORD_SPEED = 6
FIELD_RECORD_ENHANCED_SPEED = 73
FIELD_RECORD_COMPRESSED_SPEED_DISTANCE = 8


@dataclass
class FieldDef:
    num: int
    size: int


@dataclass
class MesgDef:
    global_num: int
    little_endian: bool
    fields: List[FieldDef]


@dataclass
class RecordMsg:
    timestamp: Optional[int]
    timestamp_offset: Optional[int]
    timestamp_size: Optional[int]
    header_offset: int
    is_compressed_header: bool
    distance_m: Optional[float]
    distance_offset: Optional[int]
    distance_size: Optional[int]
    speed_mps: Optional[float]
    speed_offset: Optional[int]
    speed_size: Optional[int]
    enhanced_speed_mps: Optional[float]
    enhanced_speed_offset: Optional[int]
    enhanced_speed_size: Optional[int]
    compressed_speed_distance_offset: Optional[int]
    compressed_speed_distance_size: Optional[int]
    little_endian: bool


@dataclass
class LapMsg:
    start_time: Optional[int]
    end_time: Optional[int]
    start_time_offset: Optional[int]
    start_time_size: Optional[int]
    end_time_offset: Optional[int]
    end_time_size: Optional[int]
    total_timer_s: Optional[float]
    total_timer_offset: Optional[int]
    total_timer_size: Optional[int]
    total_elapsed_offset: Optional[int]
    total_elapsed_size: Optional[int]
    total_distance_m: Optional[float]
    distance_offset: Optional[int]
    distance_size: Optional[int]
    little_endian: bool


@dataclass
class SessionMsg:
    total_distance_m: Optional[float]
    distance_offset: Optional[int]
    distance_size: Optional[int]
    little_endian: bool


def fit_crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def decode_u(raw: bytes, little_endian: bool) -> int:
    return int.from_bytes(raw, "little" if little_endian else "big", signed=False)


def encode_u(value: int, size: int, little_endian: bool) -> bytes:
    max_v = (1 << (8 * size)) - 1
    value = max(0, min(max_v, value))
    return value.to_bytes(size, "little" if little_endian else "big", signed=False)


def encode_invalid(size: int) -> bytes:
    return bytes([0xFF] * size)


def read_fit_payload_bytes(blob: bytes) -> Tuple[bytearray, int, int]:
    if len(blob) < 14:
        raise ValueError("File too short to be a FIT file")

    header_size = blob[0]
    if header_size not in (12, 14):
        raise ValueError(f"Unsupported FIT header size: {header_size}")

    data_size = struct.unpack_from("<I", blob, 4)[0]
    data_start = header_size
    data_end = data_start + data_size
    if data_end + 2 > len(blob):
        raise ValueError("Corrupted FIT: declared data_size exceeds file length")

    return bytearray(blob), data_start, data_end


def read_fit_payload(path: Path) -> Tuple[bytearray, int, int]:
    return read_fit_payload_bytes(path.read_bytes())


def parse_fit(blob: bytearray, data_start: int, data_end: int) -> Tuple[List[RecordMsg], List[LapMsg], Optional[SessionMsg]]:
    defs: Dict[int, MesgDef] = {}
    records: List[RecordMsg] = []
    laps: List[LapMsg] = []
    session: Optional[SessionMsg] = None
    last_timestamp: Optional[int] = None

    p = data_start
    while p < data_end:
        header_pos = p
        header = blob[p]
        p += 1

        is_compressed = (header & 0x80) != 0
        compressed_ts: Optional[int] = None
        if is_compressed:
            local_num = (header >> 5) & 0x03
            is_definition = False
            has_developer = False
            time_offset = header & 0x1F
            if last_timestamp is not None:
                # Rebuild absolute timestamp from 5-bit rolling offset.
                compressed_ts = (last_timestamp & 0xFFFFFFE0) + time_offset
                if compressed_ts < last_timestamp:
                    compressed_ts += 0x20
        else:
            is_definition = (header & 0x40) != 0
            has_developer = (header & 0x20) != 0
            local_num = header & 0x0F

        if is_definition:
            if p + 5 > data_end:
                raise ValueError("Corrupted FIT: truncated definition message")
            _reserved = blob[p]
            architecture = blob[p + 1]
            little_endian = architecture == 0
            global_num = int.from_bytes(blob[p + 2 : p + 4], "little" if little_endian else "big")
            num_fields = blob[p + 4]
            p += 5

            fields: List[FieldDef] = []
            for _ in range(num_fields):
                if p + 3 > data_end:
                    raise ValueError("Corrupted FIT: truncated field definition")
                field_num = blob[p]
                field_size = blob[p + 1]
                _base_type = blob[p + 2]
                fields.append(FieldDef(num=field_num, size=field_size))
                p += 3

            if has_developer:
                if p >= data_end:
                    raise ValueError("Corrupted FIT: truncated developer field count")
                num_dev_fields = blob[p]
                p += 1
                dev_bytes = 3 * num_dev_fields
                if p + dev_bytes > data_end:
                    raise ValueError("Corrupted FIT: truncated developer field definitions")
                p += dev_bytes

            defs[local_num] = MesgDef(global_num=global_num, little_endian=little_endian, fields=fields)
            continue

        if local_num not in defs:
            raise ValueError(f"Corrupted FIT: data message references unknown local def {local_num}")

        d = defs[local_num]
        timestamp = compressed_ts
        timestamp_offset = None
        timestamp_size = None
        start_time = None
        start_time_offset = None
        start_time_size = None
        total_timer = None
        total_timer_offset = None
        total_timer_size = None
        total_elapsed = None
        total_elapsed_offset = None
        total_elapsed_size = None
        total_dist = None
        end_time_offset = None
        end_time_size = None

        dist_offset = None
        dist_size = None
        speed = None
        speed_offset = None
        speed_size = None
        enhanced_speed = None
        enhanced_speed_offset = None
        enhanced_speed_size = None
        comp_sd_offset = None
        comp_sd_size = None

        for f in d.fields:
            if p + f.size > data_end:
                raise ValueError("Corrupted FIT: truncated data field")
            raw = bytes(blob[p : p + f.size])
            if f.num == FIELD_TIMESTAMP and f.size == 4:
                timestamp = decode_u(raw, d.little_endian)
                timestamp_offset = p
                timestamp_size = f.size

            if d.global_num == MSG_RECORD:
                if f.num == FIELD_RECORD_DISTANCE and f.size in (2, 4):
                    total_dist = decode_u(raw, d.little_endian) / 100.0
                    dist_offset = p
                    dist_size = f.size
                elif f.num == FIELD_RECORD_SPEED and f.size in (2, 4):
                    speed = decode_u(raw, d.little_endian) / 1000.0
                    speed_offset = p
                    speed_size = f.size
                elif f.num == FIELD_RECORD_ENHANCED_SPEED and f.size in (2, 4):
                    enhanced_speed = decode_u(raw, d.little_endian) / 1000.0
                    enhanced_speed_offset = p
                    enhanced_speed_size = f.size
                elif f.num == FIELD_RECORD_COMPRESSED_SPEED_DISTANCE:
                    comp_sd_offset = p
                    comp_sd_size = f.size

            elif d.global_num == MSG_LAP:
                if f.num == FIELD_START_TIME and f.size in (4,):
                    start_time = decode_u(raw, d.little_endian)
                    start_time_offset = p
                    start_time_size = f.size
                elif f.num == FIELD_TIMESTAMP and f.size in (4,):
                    timestamp = decode_u(raw, d.little_endian)
                    end_time_offset = p
                    end_time_size = f.size
                elif f.num == FIELD_TOTAL_TIMER_TIME and f.size in (2, 4):
                    total_timer = decode_u(raw, d.little_endian) / 1000.0
                    total_timer_offset = p
                    total_timer_size = f.size
                elif f.num == FIELD_TOTAL_ELAPSED_TIME and f.size in (2, 4) and total_timer is None:
                    total_elapsed = decode_u(raw, d.little_endian) / 1000.0
                    total_elapsed_offset = p
                    total_elapsed_size = f.size
                elif f.num == FIELD_TOTAL_DISTANCE and f.size in (2, 4):
                    total_dist = decode_u(raw, d.little_endian) / 100.0
                    dist_offset = p
                    dist_size = f.size

            elif d.global_num == MSG_SESSION:
                if f.num == FIELD_TOTAL_DISTANCE and f.size in (2, 4):
                    total_dist = decode_u(raw, d.little_endian) / 100.0
                    dist_offset = p
                    dist_size = f.size

            p += f.size

        if d.global_num == MSG_RECORD:
            records.append(
                RecordMsg(
                    timestamp=timestamp,
                    timestamp_offset=timestamp_offset,
                    timestamp_size=timestamp_size,
                    header_offset=header_pos,
                    is_compressed_header=is_compressed,
                    distance_m=total_dist,
                    distance_offset=dist_offset,
                    distance_size=dist_size,
                    speed_mps=speed,
                    speed_offset=speed_offset,
                    speed_size=speed_size,
                    enhanced_speed_mps=enhanced_speed,
                    enhanced_speed_offset=enhanced_speed_offset,
                    enhanced_speed_size=enhanced_speed_size,
                    compressed_speed_distance_offset=comp_sd_offset,
                    compressed_speed_distance_size=comp_sd_size,
                    little_endian=d.little_endian,
                )
            )
        elif d.global_num == MSG_LAP:
            laps.append(
                LapMsg(
                    start_time=start_time,
                    end_time=timestamp,
                    start_time_offset=start_time_offset,
                    start_time_size=start_time_size,
                    end_time_offset=end_time_offset,
                    end_time_size=end_time_size,
                    total_timer_s=total_timer if total_timer is not None else total_elapsed,
                    total_timer_offset=total_timer_offset,
                    total_timer_size=total_timer_size,
                    total_elapsed_offset=total_elapsed_offset,
                    total_elapsed_size=total_elapsed_size,
                    total_distance_m=total_dist,
                    distance_offset=dist_offset,
                    distance_size=dist_size,
                    little_endian=d.little_endian,
                )
            )
        elif d.global_num == MSG_SESSION:
            session = SessionMsg(
                total_distance_m=total_dist,
                distance_offset=dist_offset,
                distance_size=dist_size,
                little_endian=d.little_endian,
            )

        if timestamp is not None:
            last_timestamp = timestamp

    return records, laps, session


def kmh_to_mps(v_kmh: float) -> float:
    return v_kmh / 3.6


def assign_laps_to_records(records: List[RecordMsg], laps: List[LapMsg]) -> List[Optional[int]]:
    lap_ranges: List[Tuple[int, int, int]] = []
    for i, lap in enumerate(laps):
        if lap.start_time is None or lap.end_time is None:
            continue
        lap_ranges.append((lap.start_time, lap.end_time, i))

    lap_ranges.sort(key=lambda x: x[0])
    owners: List[Optional[int]] = [None] * len(records)

    if not lap_ranges:
        return owners

    j = 0
    for idx, rec in enumerate(records):
        if rec.timestamp is None:
            continue
        ts = rec.timestamp
        while j < len(lap_ranges) and ts > lap_ranges[j][1]:
            j += 1
        if j >= len(lap_ranges):
            break
        start, end, lap_i = lap_ranges[j]
        if start <= ts <= end:
            owners[idx] = lap_i

    # Fallback for records without timestamp: keep contiguous assignment so
    # early compressed segments are not left outside laps.
    first_known = None
    for i, o in enumerate(owners):
        if o is not None:
            first_known = i
            break
    if first_known is not None and owners[first_known] is not None:
        for i in range(first_known - 1, -1, -1):
            if records[i].distance_m is None:
                continue
            owners[i] = owners[first_known]

    prev_owner: Optional[int] = None
    for i, o in enumerate(owners):
        if o is not None:
            prev_owner = o
            continue
        if prev_owner is not None and records[i].distance_m is not None:
            owners[i] = prev_owner

    last_known = None
    for i in range(len(owners) - 1, -1, -1):
        if owners[i] is not None:
            last_known = i
            break
    if last_known is not None and owners[last_known] is not None:
        for i in range(last_known + 1, len(owners)):
            if records[i].distance_m is None:
                continue
            owners[i] = owners[last_known]

    return owners


def patch_distances(
    blob: bytearray,
    records: List[RecordMsg],
    laps: List[LapMsg],
    session: Optional[SessionMsg],
    speeds_kmh: List[float],
    blend: float,
    speed_strategy: str = "invalidate",
    trim_idle_start: bool = False,
    trim_idle_end: bool = False,
    lap_edge_stabilize_sec: int = 8,
    lap_edge_blend: float = 0.75,
    lap_uniform_blend: float = 0.0,
    lap_spike_blend: float = 0.2,
    debug_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, float]:
    if len(laps) != len(speeds_kmh):
        raise ValueError(f"Speed count ({len(speeds_kmh)}) must equal lap count ({len(laps)})")

    lap_ratios: List[float] = []
    corrected_lap_distances: List[Optional[float]] = []

    for i, lap in enumerate(laps):
        if lap.total_timer_s is None or lap.total_timer_s <= 0:
            lap_ratios.append(1.0)
            corrected_lap_distances.append(lap.total_distance_m)
            continue

        target = kmh_to_mps(speeds_kmh[i]) * lap.total_timer_s
        if lap.total_distance_m is None or lap.total_distance_m <= 0:
            ratio = 1.0
            corrected = target
        else:
            corrected = lap.total_distance_m + blend * (target - lap.total_distance_m)
            ratio = corrected / lap.total_distance_m

        lap_ratios.append(ratio)
        corrected_lap_distances.append(corrected)

    owners = assign_laps_to_records(records, laps)

    # Preserve \"roughness\": compute per-lap scale from record distance span
    # (not from lap field), so intra-lap fluctuations keep their original shape.
    lap_record_indices: List[List[int]] = [[] for _ in laps]
    for idx, owner in enumerate(owners):
        if owner is None:
            continue
        rec = records[idx]
        if rec.distance_m is None or rec.distance_offset is None or rec.distance_size is None:
            continue
        lap_record_indices[owner].append(idx)

    for lap_i, idxs in enumerate(lap_record_indices):
        if len(idxs) < 2:
            continue
        first = records[idxs[0]].distance_m
        last = records[idxs[-1]].distance_m
        desired = corrected_lap_distances[lap_i]
        if first is None or last is None or desired is None:
            continue
        span = last - first
        if span > 0:
            lap_ratios[lap_i] = desired / span

    last_lap_idx: Optional[int] = None
    lap_orig_anchor = 0.0
    lap_new_anchor = 0.0
    offset = 0.0
    records_patched = 0
    new_distances: List[Optional[float]] = [None] * len(records)
    new_speeds: List[Optional[float]] = [None] * len(records)

    for idx, rec in enumerate(records):
        if rec.distance_m is None:
            continue

        lap_idx = owners[idx]
        orig = rec.distance_m

        if lap_idx is None:
            new = orig + offset
            last_lap_idx = None
        else:
            ratio = lap_ratios[lap_idx]
            if last_lap_idx != lap_idx:
                lap_orig_anchor = orig
                lap_new_anchor = orig + offset
                last_lap_idx = lap_idx
            new = lap_new_anchor + (orig - lap_orig_anchor) * ratio

        new_distances[idx] = new
        offset = new - orig
        if rec.distance_offset is None or rec.distance_size is None:
            continue
        raw = int(round(new * 100.0))
        blob[rec.distance_offset : rec.distance_offset + rec.distance_size] = encode_u(raw, rec.distance_size, rec.little_endian)
        records_patched += 1

    # Trim idle start/end by compressing paused edge segments in timeline.
    new_timestamps: List[Optional[int]] = [rec.timestamp for rec in records]
    eps = 0.3
    if trim_idle_start and new_timestamps:
        first_d = next((d for d in new_distances if d is not None), None)
        first_idx = None
        if first_d is not None:
            for i, d in enumerate(new_distances):
                if d is not None and d > first_d + eps:
                    first_idx = i
                    break
        if first_idx is not None and first_idx > 0 and new_timestamps[0] is not None and new_timestamps[first_idx] is not None:
            t0 = new_timestamps[0]
            ti = new_timestamps[first_idx]
            shift = ti - t0
            if shift > 0:
                for i in range(first_idx):
                    new_timestamps[i] = t0
                for i in range(first_idx, len(new_timestamps)):
                    if new_timestamps[i] is not None:
                        new_timestamps[i] = new_timestamps[i] - shift  # type: ignore[operator]

    if trim_idle_end and new_timestamps:
        last_d = next((d for d in reversed(new_distances) if d is not None), None)
        last_move = None
        if last_d is not None:
            for i in range(len(new_distances) - 1, -1, -1):
                d = new_distances[i]
                if d is not None and d < last_d - eps:
                    last_move = i + 1
                    break
        if last_move is not None and 0 <= last_move < len(new_timestamps) and new_timestamps[last_move] is not None:
            tcut = new_timestamps[last_move]
            for i in range(last_move + 1, len(new_timestamps)):
                if new_timestamps[i] is not None:
                    new_timestamps[i] = tcut

    # Reduce lap-edge spikes (often rendered as triangles in Strava) by gently
    # blending first/last seconds of each lap toward treadmill target speed.
    if lap_edge_stabilize_sec > 0 or lap_uniform_blend > 0:
        lap_record_indices_timed: List[List[int]] = [[] for _ in laps]
        for idx, owner in enumerate(owners):
            if owner is None:
                continue
            if new_timestamps[idx] is None or new_distances[idx] is None:
                continue
            lap_record_indices_timed[owner].append(idx)

        for lap_i, idxs in enumerate(lap_record_indices_timed):
            if len(idxs) < 3:
                continue
            target_mps = kmh_to_mps(speeds_kmh[lap_i])
            lap_start = new_timestamps[idxs[0]]
            lap_end = new_timestamps[idxs[-1]]
            if lap_start is None or lap_end is None or lap_end <= lap_start:
                continue

            steps: List[Tuple[int, float, int]] = []  # (to_idx, dd, dt)
            total_dd = 0.0
            for j in range(1, len(idxs)):
                i0 = idxs[j - 1]
                i1 = idxs[j]
                t0 = new_timestamps[i0]
                t1 = new_timestamps[i1]
                d0 = new_distances[i0]
                d1 = new_distances[i1]
                if t0 is None or t1 is None or d0 is None or d1 is None:
                    continue
                dt = t1 - t0
                if dt <= 0:
                    continue
                dd = max(0.0, d1 - d0)
                steps.append((i1, dd, dt))
                total_dd += dd
            if not steps or total_dd <= 0:
                continue

            desired_span = (new_distances[idxs[-1]] or 0.0) - (new_distances[idxs[0]] or 0.0)
            if desired_span <= 0:
                continue

            blended: List[Tuple[int, float, int]] = []
            for to_idx, dd, dt in steps:
                t = new_timestamps[to_idx]
                if t is None:
                    blended.append((to_idx, dd, dt))
                    continue
                from_start = t - lap_start
                to_end = lap_end - t
                is_edge = from_start <= lap_edge_stabilize_sec or to_end <= lap_edge_stabilize_sec
                if trim_idle_start and lap_i == 0 and from_start <= 15:
                    # After trimming startup idle, keep first seconds steady to
                    # avoid synthetic acceleration spike in Strava.
                    dd = target_mps * dt
                if lap_uniform_blend > 0:
                    target_dd = target_mps * dt
                    dd = (1.0 - lap_uniform_blend) * dd + lap_uniform_blend * target_dd
                if is_edge and lap_edge_blend > 0:
                    target_dd = target_mps * dt
                    dd = (1.0 - lap_edge_blend) * dd + lap_edge_blend * target_dd
                blended.append((to_idx, max(0.0, dd), dt))

            blended_sum = sum(x[1] for x in blended)
            if blended_sum <= 0:
                continue
            scale = desired_span / blended_sum

            base_idx = idxs[0]
            base = new_distances[base_idx] or 0.0
            running = base
            for to_idx, dd, _dt in blended:
                running += dd * scale
                new_distances[to_idx] = running

    # Final per-lap normalization against effective lap duration after trims.
    lap_record_indices_timed: List[List[int]] = [[] for _ in laps]
    for idx, owner in enumerate(owners):
        if owner is None:
            continue
        if new_timestamps[idx] is None or new_distances[idx] is None:
            continue
        lap_record_indices_timed[owner].append(idx)

    effective_lap_durations: List[Optional[float]] = [None] * len(laps)
    for lap_i, idxs in enumerate(lap_record_indices_timed):
        if len(idxs) < 2:
            continue
        t0 = new_timestamps[idxs[0]]
        t1 = new_timestamps[idxs[-1]]
        if t0 is None or t1 is None or t1 <= t0:
            continue
        effective_lap_durations[lap_i] = float(t1 - t0)

    for lap_i, idxs in enumerate(lap_record_indices_timed):
        if len(idxs) < 2:
            continue
        d0 = new_distances[idxs[0]]
        d1 = new_distances[idxs[-1]]
        if d0 is None or d1 is None:
            continue
        span = d1 - d0
        if span <= 0:
            continue

        dur = effective_lap_durations[lap_i]
        if dur is None or dur <= 0:
            continue
        target = kmh_to_mps(speeds_kmh[lap_i]) * dur
        desired = span + blend * (target - span)
        ratio = desired / span
        for idx in idxs:
            d = new_distances[idx]
            if d is None:
                continue
            new_distances[idx] = d0 + (d - d0) * ratio

    # Enforce continuity between lap boundaries to avoid synthetic spikes in
    # cumulative distance at lap transitions (notably visible in Strava).
    prev_last: Optional[float] = None
    for idxs in lap_record_indices_timed:
        if not idxs:
            continue
        first_idx = idxs[0]
        last_idx = idxs[-1]
        first_d = new_distances[first_idx]
        if first_d is None:
            continue
        if prev_last is not None:
            shift = prev_last - first_d
            for idx in idxs:
                d = new_distances[idx]
                if d is not None:
                    new_distances[idx] = d + shift
        last_d = new_distances[last_idx]
        if last_d is not None:
            prev_last = last_d

    # Soft anti-spike smoothing inside each lap: blend each step speed toward
    # local median (window=5) and renormalize to keep lap distance unchanged.
    if lap_spike_blend > 0:
        for idxs in lap_record_indices_timed:
            if len(idxs) < 5:
                continue
            steps: List[Tuple[int, int, float]] = []  # (to_idx, dt, dd)
            for j in range(1, len(idxs)):
                i0 = idxs[j - 1]
                i1 = idxs[j]
                t0 = new_timestamps[i0]
                t1 = new_timestamps[i1]
                d0 = new_distances[i0]
                d1 = new_distances[i1]
                if t0 is None or t1 is None or d0 is None or d1 is None:
                    continue
                dt = t1 - t0
                if dt <= 0:
                    continue
                dd = max(0.0, d1 - d0)
                steps.append((i1, dt, dd))
            if len(steps) < 4:
                continue
            orig_sum = sum(dd for _to, _dt, dd in steps)
            if orig_sum <= 0:
                continue

            speeds = [dd / dt for _to, dt, dd in steps]
            smooth_speeds: List[float] = []
            n = len(speeds)
            for k, s in enumerate(speeds):
                lo = max(0, k - 2)
                hi = min(n, k + 3)
                w = sorted(speeds[lo:hi])
                med = w[len(w) // 2]
                smooth_speeds.append((1.0 - lap_spike_blend) * s + lap_spike_blend * med)

            smooth_dd = [max(0.0, smooth_speeds[k] * steps[k][1]) for k in range(len(steps))]
            smooth_sum = sum(smooth_dd)
            if smooth_sum <= 0:
                continue
            scale = orig_sum / smooth_sum

            base = new_distances[idxs[0]]
            if base is None:
                continue
            run = base
            for k, (to_idx, _dt, _dd) in enumerate(steps):
                run += smooth_dd[k] * scale
                new_distances[to_idx] = run

    # Apply final record distance bytes from adjusted new_distances.
    for i, rec in enumerate(records):
        if rec.distance_offset is None or rec.distance_size is None:
            continue
        d = new_distances[i]
        if d is None:
            continue
        raw = int(round(d * 100.0))
        blob[rec.distance_offset : rec.distance_offset + rec.distance_size] = encode_u(raw, rec.distance_size, rec.little_endian)

    # Apply record timestamps (supports both normal and compressed timestamp headers).
    for i, rec in enumerate(records):
        ts = new_timestamps[i]
        if ts is None:
            continue
        if rec.timestamp_offset is not None and rec.timestamp_size is not None:
            blob[rec.timestamp_offset : rec.timestamp_offset + rec.timestamp_size] = encode_u(
                ts, rec.timestamp_size, rec.little_endian
            )
        elif rec.is_compressed_header:
            hdr = blob[rec.header_offset]
            blob[rec.header_offset] = (hdr & 0xE0) | (ts & 0x1F)

    # Keep speed fields consistent with corrected distance. Compute speed
    # inside each lap only, so lap transitions do not inject boundary spikes.
    lap_record_indices_timed: List[List[int]] = [[] for _ in laps]
    for idx, owner in enumerate(owners):
        if owner is None:
            continue
        if new_timestamps[idx] is None or new_distances[idx] is None:
            continue
        lap_record_indices_timed[owner].append(idx)

    for idxs in lap_record_indices_timed:
        if not idxs:
            continue
        for pos, idx in enumerate(idxs):
            sp: Optional[float] = None
            if pos > 0:
                prev_idx = idxs[pos - 1]
                dt = new_timestamps[idx] - new_timestamps[prev_idx]  # type: ignore[operator]
                if dt > 0:
                    dd = new_distances[idx] - new_distances[prev_idx]  # type: ignore[operator]
                    sp = max(0.0, min(20.0, dd / dt))
            elif len(idxs) > 1:
                next_idx = idxs[1]
                dt = new_timestamps[next_idx] - new_timestamps[idx]  # type: ignore[operator]
                if dt > 0:
                    dd = new_distances[next_idx] - new_distances[idx]  # type: ignore[operator]
                    sp = max(0.0, min(20.0, dd / dt))
            if sp is not None:
                new_speeds[idx] = sp

    # Fill occasional gaps with nearest previous speed to avoid leaving stale values.
    last_speed_mps: Optional[float] = None
    for i, sp in enumerate(new_speeds):
        if sp is not None:
            last_speed_mps = sp
            continue
        if last_speed_mps is not None:
            new_speeds[i] = last_speed_mps

    for i, rec in enumerate(records):
        speed_mps = new_speeds[i]
        if speed_strategy == "recompute":
            if speed_mps is None:
                continue
            if rec.speed_offset is not None and rec.speed_size is not None:
                raw = int(round(speed_mps * 1000.0))
                blob[rec.speed_offset : rec.speed_offset + rec.speed_size] = encode_u(raw, rec.speed_size, rec.little_endian)

            if rec.enhanced_speed_offset is not None and rec.enhanced_speed_size is not None:
                raw = int(round(speed_mps * 1000.0))
                blob[rec.enhanced_speed_offset : rec.enhanced_speed_offset + rec.enhanced_speed_size] = encode_u(
                    raw, rec.enhanced_speed_size, rec.little_endian
                )
        else:
            if rec.speed_offset is not None and rec.speed_size is not None:
                blob[rec.speed_offset : rec.speed_offset + rec.speed_size] = encode_invalid(rec.speed_size)
            if rec.enhanced_speed_offset is not None and rec.enhanced_speed_size is not None:
                blob[rec.enhanced_speed_offset : rec.enhanced_speed_offset + rec.enhanced_speed_size] = encode_invalid(
                    rec.enhanced_speed_size
                )
        if rec.compressed_speed_distance_offset is not None and rec.compressed_speed_distance_size is not None:
            blob[
                rec.compressed_speed_distance_offset : rec.compressed_speed_distance_offset
                + rec.compressed_speed_distance_size
            ] = encode_invalid(rec.compressed_speed_distance_size)

    if debug_rows is not None:
        for i, rec in enumerate(records):
            if rec.distance_m is None and new_distances[i] is None:
                continue
            lap_idx = owners[i]
            debug_rows.append(
                {
                    "record_index": i,
                    "timestamp": rec.timestamp,
                    "timestamp_new": new_timestamps[i],
                    "lap_index_1based": (lap_idx + 1) if lap_idx is not None else None,
                    "distance_old_m": rec.distance_m,
                    "distance_new_m": new_distances[i],
                    "distance_delta_m": (new_distances[i] - rec.distance_m)
                    if rec.distance_m is not None and new_distances[i] is not None
                    else None,
                    "speed_old_mps": rec.speed_mps if rec.speed_mps is not None else rec.enhanced_speed_mps,
                    "speed_new_mps": new_speeds[i],
                }
            )

    final_lap_distances: List[Optional[float]] = [None] * len(laps)
    final_lap_durations: List[Optional[float]] = [None] * len(laps)
    for lap_i, idxs in enumerate(lap_record_indices_timed):
        if len(idxs) < 2:
            continue
        d0 = new_distances[idxs[0]]
        d1 = new_distances[idxs[-1]]
        t0 = new_timestamps[idxs[0]]
        t1 = new_timestamps[idxs[-1]]
        if d0 is not None and d1 is not None and d1 >= d0:
            final_lap_distances[lap_i] = d1 - d0
        if t0 is not None and t1 is not None and t1 >= t0:
            final_lap_durations[lap_i] = float(t1 - t0)

    laps_patched = 0
    lap_before_total = 0.0
    lap_after_total = 0.0

    for lap_i, lap in enumerate(laps):
        new_dist = final_lap_distances[lap_i]
        if lap.total_distance_m is not None:
            lap_before_total += lap.total_distance_m
        if new_dist is not None:
            lap_after_total += new_dist

        if new_dist is not None and lap.distance_offset is not None and lap.distance_size is not None:
            raw = int(round(new_dist * 100.0))
            blob[lap.distance_offset : lap.distance_offset + lap.distance_size] = encode_u(raw, lap.distance_size, lap.little_endian)
            laps_patched += 1

        new_dur = final_lap_durations[lap_i]
        if new_dur is not None:
            raw_ms = int(round(new_dur * 1000.0))
            if lap.total_timer_offset is not None and lap.total_timer_size is not None:
                blob[lap.total_timer_offset : lap.total_timer_offset + lap.total_timer_size] = encode_u(
                    raw_ms, lap.total_timer_size, lap.little_endian
                )
            if lap.total_elapsed_offset is not None and lap.total_elapsed_size is not None:
                blob[lap.total_elapsed_offset : lap.total_elapsed_offset + lap.total_elapsed_size] = encode_u(
                    raw_ms, lap.total_elapsed_size, lap.little_endian
                )

    session_patched = 0
    if session and session.total_distance_m is not None and session.distance_offset is not None and session.distance_size is not None:
        # If we changed record timeline, final delta is represented by `offset`.
        new_session_dist = max(0.0, session.total_distance_m + offset)
        raw = int(round(new_session_dist * 100.0))
        blob[session.distance_offset : session.distance_offset + session.distance_size] = encode_u(
            raw, session.distance_size, session.little_endian
        )
        session_patched = 1

    return {
        "records_patched": float(records_patched),
        "laps_patched": float(laps_patched),
        "session_patched": float(session_patched),
        "lap_total_before_m": lap_before_total,
        "lap_total_after_m": lap_after_total,
    }


def rewrite_crc(blob: bytearray, data_start: int, data_end: int) -> None:
    header_size = blob[0]

    if header_size == 14:
        header_crc = fit_crc16(bytes(blob[:12]))
        blob[12:14] = encode_u(header_crc, 2, True)

    data_crc = fit_crc16(bytes(blob[data_start:data_end]))
    blob[data_end : data_end + 2] = encode_u(data_crc, 2, True)


def parse_speeds(arg: str) -> List[float]:
    speeds = []
    for part in arg.split(","):
        part = part.strip()
        if not part:
            continue
        speeds.append(float(part))
    if not speeds:
        raise ValueError("No speeds provided")
    return speeds


def write_debug_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames = [
        "record_index",
        "timestamp",
        "timestamp_new",
        "lap_index_1based",
        "distance_old_m",
        "distance_new_m",
        "distance_delta_m",
        "speed_old_mps",
        "speed_new_mps",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Correct treadmill FIT distances by lap speeds")
    p.add_argument("input_fit", type=Path, help="Input FIT file")
    p.add_argument("output_fit", type=Path, help="Output FIT file")
    p.add_argument(
        "--speeds-kmh",
        required=True,
        help="Comma-separated treadmill speeds per lap in km/h (example: 9.5,10,10.5)",
    )
    p.add_argument(
        "--blend",
        type=float,
        default=1.0,
        help="Blend factor [0..1]. 1.0 = exact treadmill lap distances, default 1.0",
    )
    p.add_argument(
        "--debug-csv",
        type=Path,
        default=None,
        help="Optional CSV output for per-record diagnostics (timestamp, lap, distance/speed old/new)",
    )
    p.add_argument(
        "--speed-strategy",
        choices=("invalidate", "recompute"),
        default="invalidate",
        help="How to handle record speed fields: invalidate (Strava-friendly) or recompute",
    )
    p.add_argument(
        "--trim-idle-start",
        action="store_true",
        help="Trim idle pause at beginning by compressing record timeline",
    )
    p.add_argument(
        "--trim-idle-end",
        action="store_true",
        help="Trim idle pause at end by compressing record timeline",
    )
    p.add_argument(
        "--lap-edge-stabilize-sec",
        type=int,
        default=8,
        help="Seconds near each lap edge to stabilize against triangle artifacts (0 disables), default 8",
    )
    p.add_argument(
        "--lap-edge-blend",
        type=float,
        default=0.75,
        help="Blend [0..1] for lap-edge stabilization toward treadmill speed, default 0.75",
    )
    p.add_argument(
        "--lap-uniform-blend",
        type=float,
        default=0.0,
        help="Blend [0..1] to uniform lap speed (Strava anti-triangle), default 0.0",
    )
    p.add_argument(
        "--lap-spike-blend",
        type=float,
        default=0.2,
        help="Blend [0..1] to local median speed (soft anti-spike), default 0.2",
    )
    return p


def correct_fit_bytes(
    input_fit: bytes,
    speeds_kmh: List[float],
    blend: float = 1.0,
    speed_strategy: str = "invalidate",
    trim_idle_start: bool = False,
    trim_idle_end: bool = False,
    lap_edge_stabilize_sec: int = 8,
    lap_edge_blend: float = 0.75,
    lap_uniform_blend: float = 0.0,
    lap_spike_blend: float = 0.2,
) -> Tuple[bytes, Dict[str, float], int]:
    output, stats, lap_count, _ = correct_fit_bytes_debug(
        input_fit,
        speeds_kmh,
        blend,
        speed_strategy,
        trim_idle_start,
        trim_idle_end,
        lap_edge_stabilize_sec,
        lap_edge_blend,
        lap_uniform_blend,
        lap_spike_blend,
    )
    return output, stats, lap_count


def correct_fit_bytes_debug(
    input_fit: bytes,
    speeds_kmh: List[float],
    blend: float = 1.0,
    speed_strategy: str = "invalidate",
    trim_idle_start: bool = False,
    trim_idle_end: bool = False,
    lap_edge_stabilize_sec: int = 8,
    lap_edge_blend: float = 0.75,
    lap_uniform_blend: float = 0.0,
    lap_spike_blend: float = 0.2,
) -> Tuple[bytes, Dict[str, float], int, List[Dict[str, Any]]]:
    if not (0.0 <= blend <= 1.0):
        raise ValueError("--blend must be within [0, 1]")
    if speed_strategy not in ("invalidate", "recompute"):
        raise ValueError("--speed-strategy must be 'invalidate' or 'recompute'")
    if lap_edge_stabilize_sec < 0:
        raise ValueError("--lap-edge-stabilize-sec must be >= 0")
    if not (0.0 <= lap_edge_blend <= 1.0):
        raise ValueError("--lap-edge-blend must be within [0, 1]")
    if not (0.0 <= lap_uniform_blend <= 1.0):
        raise ValueError("--lap-uniform-blend must be within [0, 1]")
    if not (0.0 <= lap_spike_blend <= 1.0):
        raise ValueError("--lap-spike-blend must be within [0, 1]")

    blob, data_start, data_end = read_fit_payload_bytes(input_fit)
    records, laps, session = parse_fit(blob, data_start, data_end)
    debug_rows: List[Dict[str, Any]] = []
    stats = patch_distances(
        blob=blob,
        records=records,
        laps=laps,
        session=session,
        speeds_kmh=speeds_kmh,
        blend=blend,
        speed_strategy=speed_strategy,
        trim_idle_start=trim_idle_start,
        trim_idle_end=trim_idle_end,
        lap_edge_stabilize_sec=lap_edge_stabilize_sec,
        lap_edge_blend=lap_edge_blend,
        lap_uniform_blend=lap_uniform_blend,
        lap_spike_blend=lap_spike_blend,
        debug_rows=debug_rows,
    )
    rewrite_crc(blob, data_start, data_end)
    return bytes(blob), stats, len(laps), debug_rows


def inspect_laps(input_fit: bytes) -> List[LapMsg]:
    blob, data_start, data_end = read_fit_payload_bytes(input_fit)
    _, laps, _ = parse_fit(blob, data_start, data_end)
    return laps


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not (0.0 <= args.blend <= 1.0):
        raise ValueError("--blend must be within [0, 1]")

    speeds = parse_speeds(args.speeds_kmh)
    output, stats, lap_count, debug_rows = correct_fit_bytes_debug(
        args.input_fit.read_bytes(),
        speeds,
        args.blend,
        args.speed_strategy,
        args.trim_idle_start,
        args.trim_idle_end,
        args.lap_edge_stabilize_sec,
        args.lap_edge_blend,
        args.lap_uniform_blend,
        args.lap_spike_blend,
    )
    args.output_fit.write_bytes(output)
    if args.debug_csv is not None:
        write_debug_csv(args.debug_csv, debug_rows)

    print(f"Laps found: {lap_count}")
    print(f"Records patched: {int(stats['records_patched'])}")
    print(f"Laps patched: {int(stats['laps_patched'])}")
    print(f"Session patched: {int(stats['session_patched'])}")
    print(f"Distance (laps total): {stats['lap_total_before_m']:.1f} m -> {stats['lap_total_after_m']:.1f} m")
    if args.debug_csv is not None:
        print(f"Debug CSV: {args.debug_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
