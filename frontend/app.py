"""Streamlit dashboard for the Field Test Ingestion & Analytics solution.

Consumes the FastAPI backend: lists sessions, lets you upload a new
session (CSV + metadata), and visualizes telemetry + data-quality insights.

Run:  streamlit run app.py    (with the API running on API_BASE)
"""

import os

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")

# Palette ---------------------------------------------------------------------
BLUE = "#2563eb"
RED = "#dc2626"
GRID = "#eef2f7"
FLAG_COLORS = {
    "missing": "#9ca3af",       # gray  — empty cell
    "parse_error": "#f59e0b",   # amber — bad string
    "sentinel": "#8b5cf6",      # violet— -999 etc.
    "suspect_outlier": RED,     # red   — kept but flagged
}

st.set_page_config(page_title="Field Test Analytics", page_icon="🚗", layout="wide")


# --- API helpers -------------------------------------------------------------

def api_get(path: str, **params):
    r = requests.get(f"{API_BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def api_health() -> bool:
    try:
        return api_get("/health").get("status") == "ok"
    except Exception:
        return False


def style(fig: go.Figure, **layout) -> go.Figure:
    """Apply a consistent, clean theme to a Plotly figure."""
    fig.update_layout(
        template="plotly_white",
        font=dict(family="Inter, Segoe UI, system-ui, sans-serif", size=13),
        margin=dict(t=46, b=40, l=10, r=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=""),
        **layout,
    )
    fig.update_layout(title_font_size=16)  # separate: avoids clashing with title text
    fig.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False)
    return fig


def reverse_spans(frame: pd.DataFrame) -> list[tuple]:
    """Contiguous (start, end) time spans where the vehicle was in reverse."""
    spans, start, prev = [], None, None
    for _, row in frame.iterrows():
        rev = bool(row["reverse_state"]) if pd.notna(row["reverse_state"]) else False
        if rev and start is None:
            start = row["t"]
        elif not rev and start is not None:
            spans.append((start, prev))
            start = None
        prev = row["t"]
    if start is not None:
        spans.append((start, prev))
    return spans


# --- Sidebar: connection, upload, session picker -----------------------------

st.sidebar.title("🚗 Field Test Analytics")
st.sidebar.caption("Ingest · clean · visualize telemetry sessions")
st.sidebar.divider()

if not api_health():
    st.sidebar.error(f"API not reachable at {API_BASE}")
    st.error(
        f"Backend API is not reachable at **{API_BASE}**.\n\n"
        "Start it with `uvicorn app.main:app` (see README), then reload."
    )
    st.stop()

st.sidebar.success(f"Connected · {API_BASE}")

with st.sidebar.expander("➕  Upload a new session", expanded=False):
    meta_file = st.file_uploader("metadata JSON", type=["json"], key="meta")
    csv_file = st.file_uploader("telemetry CSV", type=["csv"], key="csv")
    if st.button("Ingest", disabled=not (meta_file and csv_file), type="primary"):
        resp = requests.post(
            f"{API_BASE}/ingest",
            files={
                "metadata": (meta_file.name, meta_file.getvalue(), "application/json"),
                "csv": (csv_file.name, csv_file.getvalue(), "text/csv"),
            },
            timeout=120,
        )
        if resp.ok:
            st.success(f"Ingested: {resp.json()['session_id']}")
        else:
            st.error(f"Ingest failed: {resp.status_code} — {resp.text}")

sessions = api_get("/sessions")
if not sessions:
    st.info("No sessions ingested yet. Upload one from the sidebar.")
    st.stop()

labels = {s["session_id"]: s for s in sessions}
selected = st.sidebar.selectbox("Session", list(labels.keys()))


# --- Load selected session ---------------------------------------------------

detail = api_get(f"/sessions/{selected}")
quality = api_get(f"/sessions/{selected}/quality")
samples = api_get(f"/sessions/{selected}/samples", limit=5000)["items"]

df = pd.DataFrame(samples)
if not df.empty:
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["quality_flags"] = df["quality_flags"].apply(lambda v: v or {})
    # A value is an outlier if its column's flag is suspect_outlier.
    df["speed_outlier"] = df["quality_flags"].apply(
        lambda f: f.get("speed") == "suspect_outlier"
    )
    df["angle_outlier"] = df["quality_flags"].apply(
        lambda f: f.get("wheel_angle") == "suspect_outlier"
    )
    # Source timestamps are only minute-resolution (all rows collapse onto a
    # couple of x-values), but the session is 1 Hz. Reconstruct a true per-sample
    # time axis from the session start + row_index / sample_rate_hz.
    rate = detail.get("sample_rate_hz") or 1
    start = pd.to_datetime(detail.get("start_time_utc"), errors="coerce")
    if pd.notna(start):
        df["t"] = start + pd.to_timedelta(df["row_index"] / rate, unit="s")
        x_title = "time (reconstructed @ %g Hz)" % rate
    else:
        df["t"] = df["row_index"]
        x_title = "sample # (≈ seconds @ %g Hz)" % rate


# --- Header & metadata -------------------------------------------------------

st.title(f"🚗 {detail['session_id']}")
subtitle = " · ".join(
    v for v in [
        detail.get("vehicle_id"),
        detail.get("test_location"),
        detail.get("recording_date"),
    ] if v
)
if subtitle:
    st.caption(subtitle)

if detail.get("notes"):
    st.warning(f"📝 **Field note** — {detail['notes']}")

with st.expander("Full session metadata"):
    st.json(detail)

st.divider()


# --- Key statistics / insights -----------------------------------------------

st.subheader("📊 Key statistics")

total = quality["total_samples"]
flagged = quality["flagged_samples"]

clean_speed = pd.Series(dtype=float)
reverse_share = None
if not df.empty:
    clean_speed = df.loc[~df["speed_outlier"], "speed"].dropna()
    if df["reverse_state"].notna().any():
        reverse_share = df["reverse_state"].fillna(False).mean()

start_dt = pd.to_datetime(detail.get("start_time_utc"), errors="coerce")
end_dt = pd.to_datetime(detail.get("end_time_utc"), errors="coerce")
duration = (
    (end_dt - start_dt).total_seconds()
    if pd.notna(start_dt) and pd.notna(end_dt) else None
)

with st.container(border=True):
    m = st.columns(6)
    m[0].metric("Samples", f"{total:,}")
    m[1].metric("Duration", f"{int(duration)} s" if duration else "—")
    m[2].metric(
        "Flagged",
        f"{flagged}",
        f"{flagged / total * 100:.0f}% of rows" if total else None,
        delta_color="inverse",
    )
    m[3].metric("Avg speed", f"{clean_speed.mean():.1f} km/h" if len(clean_speed) else "—")
    m[4].metric("Max speed", f"{clean_speed.max():.1f} km/h" if len(clean_speed) else "—")
    m[5].metric(
        "Time in reverse",
        f"{reverse_share * 100:.0f}%" if reverse_share is not None else "—",
    )
    st.caption("Speed stats exclude rows flagged as suspect outliers.")

st.divider()


# --- Data-quality breakdown --------------------------------------------------

st.subheader("🧪 Data quality")
q1, q2 = st.columns([3, 2])

flag_counts = quality["flag_counts"]
with q1:
    if flag_counts:
        order = [f for f in FLAG_COLORS if f in flag_counts] + [
            f for f in flag_counts if f not in FLAG_COLORS
        ]
        counts = [flag_counts[f] for f in order]
        fig = go.Figure(
            go.Bar(
                x=order, y=counts,
                marker_color=[FLAG_COLORS.get(f, BLUE) for f in order],
                text=counts, textposition="outside",
            )
        )
        style(fig, height=300, yaxis_title="rows affected", showlegend=False)
        fig.update_yaxes(rangemode="tozero")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.success("✅ No quality issues detected.")

with q2:
    st.markdown("**Per-field breakdown**")
    ff = quality["field_flag_counts"]
    if ff:
        ff_df = pd.DataFrame(ff).fillna(0).astype(int).T
        ff_df.index.name = "field"
        st.dataframe(ff_df, use_container_width=True)
    st.caption(
        "**missing / parse_error / sentinel** → value set to NULL. "
        "**suspect_outlier** → raw value kept but flagged."
    )

st.divider()


# --- Telemetry charts --------------------------------------------------------

st.subheader("📈 Telemetry")
stretch = st.checkbox(
    "Stretch Y-axis to include outliers",
    value=False,
    help="Off: scale to the clean signal so a single spike (e.g. 450) doesn't "
    "flatten the chart. On: show outliers on-scale.",
)


def telemetry_chart(value_col: str, outlier_col: str, title: str, unit: str, color: str):
    """Line vs time. The line is the *trustworthy* signal — NULLs and outliers
    both render as gaps; outliers are re-drawn as red ✕ markers. Reverse-gear
    spans are shaded. Y-axis scales to the clean signal unless 'stretch' is on."""
    clean = df.loc[~df[outlier_col], value_col].dropna()
    line_y = df[value_col].where(~df[outlier_col])  # drop outliers from the line

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["t"], y=line_y, mode="lines+markers", name=title,
            connectgaps=False,  # NULL/outlier -> visible gap, not interpolated
            line=dict(color=color, width=2), marker=dict(size=4),
        )
    )
    outliers = df[df[outlier_col]]
    if not outliers.empty:
        fig.add_trace(
            go.Scatter(
                x=outliers["t"], y=outliers[value_col], mode="markers",
                name="suspect outlier",
                marker=dict(color=RED, size=12, symbol="x", line=dict(width=2)),
                hovertemplate="%{y} " + unit + " (outlier)<extra></extra>",
            )
        )
    # Shade reverse-gear spans.
    for i, (s, e) in enumerate(reverse_spans(df)):
        fig.add_vrect(
            x0=s, x1=e, fillcolor="rgba(37,99,235,0.08)", line_width=0,
            layer="below",
            annotation_text="reverse" if i == 0 else None,
            annotation_position="top left",
            annotation=dict(font_size=11, font_color=BLUE),
        )
    if not stretch and len(clean):
        pad = (clean.max() - clean.min()) * 0.1 or 1.0
        fig.update_yaxes(range=[clean.min() - pad, clean.max() + pad])
    style(fig, height=330, title=title, yaxis_title=unit, xaxis_title=x_title)
    return fig


if df.empty:
    st.warning("No samples for this session.")
else:
    for col, ocol, title, unit, color in [
        ("speed", "speed_outlier", "Speed", "km/h", BLUE),
        ("wheel_angle", "angle_outlier", "Wheel angle", "deg", "#0891b2"),
    ]:
        st.plotly_chart(
            telemetry_chart(col, ocol, title, unit, color), use_container_width=True
        )
        offscale = df[df[ocol]][col].dropna().tolist()
        if offscale and not stretch:
            st.caption(
                f"⚠️ {len(offscale)} suspect outlier(s) beyond axis range: "
                f"{', '.join(f'{v:g}' for v in offscale)} {unit} — see Flagged rows."
            )

    st.divider()

    # --- Flagged rows table --------------------------------------------------
    st.subheader("🚩 Flagged rows")
    flagged_df = df[df["quality_flags"].apply(bool)][
        ["row_index", "timestamp", "wheel_angle", "speed", "reverse_state", "quality_flags"]
    ].copy()
    if flagged_df.empty:
        st.success("✅ No flagged rows.")
    else:
        flagged_df["quality_flags"] = flagged_df["quality_flags"].apply(
            lambda d: " · ".join(f"{k}: {v}" for k, v in d.items())
        )
        st.dataframe(
            flagged_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "row_index": st.column_config.NumberColumn("Row", width="small"),
                "timestamp": st.column_config.DatetimeColumn("Timestamp"),
                "wheel_angle": st.column_config.NumberColumn("Wheel angle", format="%.2f"),
                "speed": st.column_config.NumberColumn("Speed", format="%.2f"),
                "reverse_state": st.column_config.CheckboxColumn("Reverse"),
                "quality_flags": st.column_config.TextColumn("Quality flags", width="medium"),
            },
        )
