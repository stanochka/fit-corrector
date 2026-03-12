#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import timedelta

import streamlit as st

from treadmill_fit_corrector import correct_fit_bytes_debug, inspect_laps


def parse_segments_text(text: str) -> list[tuple[float, float]]:
    """
    Parse segments like: "15m@7.8, 1m@6, 14m@7.5"
    Allowed formats:
    - 15m@7.8
    - 90s@6
    - 15:7.8 (minutes:speed)
    - 1@6 (minutes:speed)
    """
    out: list[tuple[float, float]] = []
    if not text.strip():
        return out
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "@" in part:
            dur_raw, speed_raw = part.split("@", 1)
        elif ":" in part:
            dur_raw, speed_raw = part.split(":", 1)
        else:
            raise ValueError(f"Invalid segment '{part}'. Use 15m@7.8 or 15:7.8")
        dur_raw = dur_raw.strip().lower()
        speed = float(speed_raw.strip())
        if dur_raw.endswith("m"):
            dur_s = float(dur_raw[:-1]) * 60.0
        elif dur_raw.endswith("s"):
            dur_s = float(dur_raw[:-1])
        else:
            # default minutes
            dur_s = float(dur_raw) * 60.0
        out.append((dur_s, speed))
    return out

st.set_page_config(page_title="FIT Treadmill Corrector", page_icon="🏃", layout="centered")
st.title("FIT Treadmill Corrector")
st.caption("Коррекция дистанции FIT-файла по скоростям беговой дорожки на отрезках")

uploaded = st.file_uploader("Загрузи исходный FIT-файл", type=["fit"])

if uploaded is None:
    st.info("Загрузи файл, и появятся поля скорости для каждого отрезка.")
    st.stop()

input_bytes = uploaded.getvalue()

try:
    laps = inspect_laps(input_bytes)
except Exception as exc:
    st.error(f"Не удалось прочитать FIT: {exc}")
    st.stop()

if not laps:
    st.error("В файле не найдено lap-сообщений.")
    st.stop()

st.success(f"Найдено lap: {len(laps)}")

if "blend" not in st.session_state:
    st.session_state["blend"] = 1.0
if "invalidate_speed" not in st.session_state:
    st.session_state["invalidate_speed"] = True
if "trim_idle_start" not in st.session_state:
    st.session_state["trim_idle_start"] = True
if "lap_edge_stabilize_sec" not in st.session_state:
    st.session_state["lap_edge_stabilize_sec"] = 8
if "lap_edge_blend" not in st.session_state:
    st.session_state["lap_edge_blend"] = 0.7
if "lap_uniform_blend" not in st.session_state:
    st.session_state["lap_uniform_blend"] = 0.3
if "lap_spike_blend" not in st.session_state:
    st.session_state["lap_spike_blend"] = 0.2

blend = st.slider(
    "Степень подгонки к дорожке (blend)",
    min_value=0.0,
    max_value=1.0,
    key="blend",
    step=0.05,
    help="1.0 = точное совпадение дистанции каждого lap с целью от скорости дорожки",
)
invalidate_speed = st.checkbox(
    "Strava-режим (очистить speed-поля)",
    key="invalidate_speed",
    help="Рекомендуется: Strava считает темп по distance/time, а не по встроенным speed-полям часов",
)
speed_strategy = "invalidate" if invalidate_speed else "recompute"
trim_idle_start = st.checkbox("Обрезать паузу в начале", key="trim_idle_start")

with st.expander("Тонкие настройки Strava", expanded=False):
    lap_edge_stabilize_sec = st.number_input(
        "Стабилизация краев lap, сек",
        min_value=0,
        max_value=30,
        key="lap_edge_stabilize_sec",
        step=1,
        help="Снижает «треугольники» на медленных отрезках в Strava",
    )
    lap_edge_blend = st.slider(
        "Сила стабилизации краев lap",
        min_value=0.0,
        max_value=1.0,
        key="lap_edge_blend",
        step=0.05,
    )
    lap_uniform_blend = st.slider(
        "Выравнивание внутри lap (Strava anti-triangle)",
        min_value=0.0,
        max_value=1.0,
        key="lap_uniform_blend",
        step=0.05,
        help="Больше = меньше треугольников в Strava, но меньше «рваности»",
    )
    lap_spike_blend = st.slider(
        "Сглаживание точечных артефактов",
        min_value=0.0,
        max_value=1.0,
        key="lap_spike_blend",
        step=0.05,
        help="Мягко подавляет одиночные пики/провалы скорости внутри lap",
    )

st.subheader("Скорости по lap (км/ч)")
st.caption("Опционально: задай сегменты внутри lap, формат 15m@7.8, 1m@6, 14m@7.5")
speeds = []
segments_texts = []
for i, lap in enumerate(laps):
    default_speed = 10.0
    if lap.total_distance_m and lap.total_timer_s and lap.total_timer_s > 0:
        default_speed = (lap.total_distance_m / lap.total_timer_s) * 3.6

    speed = st.number_input(
        f"Lap {i + 1}",
        min_value=1.0,
        max_value=30.0,
        value=float(round(default_speed, 2)),
        step=0.1,
        key=f"speed_{i}",
    )
    speeds.append(float(speed))
    seg_text = st.text_input(
        f"Lap {i + 1} сегменты (опц.)",
        value="",
        placeholder="15m@7.8, 1m@6, 14m@7.5",
        key=f"seg_{i}",
    )
    segments_texts.append(seg_text)

signature = (
    f"{uploaded.name}|{blend:.2f}|{speed_strategy}|{int(trim_idle_start)}|"
    f"{lap_edge_stabilize_sec}|{lap_edge_blend:.2f}|{lap_uniform_blend:.2f}|{lap_spike_blend:.2f}|"
    f"{'|'.join(segments_texts)}|"
    f"{','.join(f'{s:.2f}' for s in speeds)}"
)
process_clicked = st.button("Скорректировать файл", type="primary")

if process_clicked:
    try:
        parsed_segments = []
        for text in segments_texts:
            segs = parse_segments_text(text)
            parsed_segments.append(segs if segs else None)
        output_bytes, stats, lap_count, debug_rows = correct_fit_bytes_debug(
            input_bytes,
            speeds,
            parsed_segments,
            blend,
            speed_strategy=speed_strategy,
            trim_idle_start=bool(trim_idle_start),
            trim_idle_end=False,
            lap_edge_stabilize_sec=int(lap_edge_stabilize_sec),
            lap_edge_blend=float(lap_edge_blend),
            lap_uniform_blend=float(lap_uniform_blend),
            lap_spike_blend=float(lap_spike_blend),
        )
    except Exception as exc:
        st.error(f"Ошибка обработки: {exc}")
    else:
        in_name = Path(uploaded.name)
        st.session_state["result_bytes"] = output_bytes
        st.session_state["result_stats"] = stats
        st.session_state["result_lap_count"] = lap_count
        st.session_state["result_name"] = f"{in_name.stem}_corrected.fit"
        st.session_state["result_signature"] = signature
        st.session_state["result_debug_rows"] = debug_rows

has_result = "result_signature" in st.session_state
is_actual = has_result and st.session_state["result_signature"] == signature

if is_actual:
    st.success("Готово. Можно скачать скорректированный FIT.")
    st.write(
        f"Laps: {st.session_state['result_lap_count']}, дистанция по отрезкам: "
        f"{st.session_state['result_stats']['lap_total_before_m']:.1f} m -> "
        f"{st.session_state['result_stats']['lap_total_after_m']:.1f} m"
    )
    st.download_button(
        label="Скачать результат",
        data=st.session_state["result_bytes"],
        file_name=st.session_state["result_name"],
        mime="application/octet-stream",
    )
    debug_rows = st.session_state.get("result_debug_rows", [])
    min_ts = None
    for row in debug_rows:
        ts = row.get("timestamp_new") or row.get("timestamp")
        if ts is None:
            continue
        min_ts = ts if min_ts is None else min(min_ts, ts)
    chart_values = []
    for row in debug_rows:
        ts = row.get("timestamp_new") or row.get("timestamp")
        lap_i = row.get("lap_index_1based")
        s_old = row.get("speed_old_mps")
        s_new = row.get("speed_new_mps")
        if ts is None or lap_i is None or min_ts is None:
            continue
        sec = ts - min_ts
        td = timedelta(seconds=int(sec))
        total = int(td.total_seconds())
        hh = total // 3600
        mm = (total % 3600) // 60
        ss = total % 60
        t_hms = f"{hh}:{mm:02d}:{ss:02d}" if hh > 0 else f"{mm:02d}:{ss:02d}"
        if s_old is not None:
            chart_values.append({"sec": sec, "t_hms": t_hms, "lap": lap_i, "series": "До", "kmh": s_old * 3.6})
        if s_new is not None:
            chart_values.append({"sec": sec, "t_hms": t_hms, "lap": lap_i, "series": "После", "kmh": s_new * 3.6})

    if chart_values:
        st.subheader("Предпросмотр графика скорости")
        st.vega_lite_chart(
            {
                "width": "container",
                "height": 280,
                "data": {"values": chart_values},
                "mark": {"type": "line", "interpolate": "linear"},
                "encoding": {
                    "x": {
                        "field": "sec",
                        "type": "quantitative",
                        "title": "Время (ч:м:с)",
                        "axis": {
                            "labelExpr": "datum.value >= 3600 ? floor(datum.value/3600)+':' + format(floor((datum.value%3600)/60), '02') + ':' + format(floor(datum.value%60), '02') : format(floor(datum.value/60), '02') + ':' + format(floor(datum.value%60), '02')"
                        },
                    },
                    "y": {"field": "kmh", "type": "quantitative", "title": "Скорость, км/ч"},
                    "color": {"field": "series", "type": "nominal", "title": " "},
                    "tooltip": [
                        {"field": "t_hms", "type": "nominal", "title": "Время"},
                        {"field": "lap", "type": "quantitative", "title": "Lap"},
                        {"field": "kmh", "type": "quantitative", "format": ".2f", "title": "км/ч"},
                        {"field": "series", "type": "nominal", "title": "Файл"},
                    ],
                },
            },
            use_container_width=True,
        )
elif has_result:
    st.info("Параметры изменены. Нажми кнопку «Скорректировать файл», чтобы пересчитать.")
