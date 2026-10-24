"""Plotly figures: upstream-dependency chain, restoration curves, crew Gantt."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from .scheduling import Instance, POLICY_LABELS, ScheduleResult

STATE_COLORS = {"no damage": "#6c9a6c", "repaired before service returns": "#e0a030", "blocking (last on your path)": "#c0392b"}
POLICY_COLORS = {"fifo": "#888888", "customers_first": "#e0a030", "dependency_aware": "#1f77b4"}


def dependency_chain(path: pd.DataFrame, service_h: float | None, affected: bool) -> go.Figure:
    """Substation → … → customer, one node per protective zone, colored by state in the current scenario."""
    fig = go.Figure()
    n = len(path)
    xs = list(range(n + 1))
    fig.add_trace(go.Scatter(x=xs, y=[0] * (n + 1), mode="lines", line=dict(color="#999", width=3), hoverinfo="skip", showlegend=False))
    for i, r in path.iterrows():
        color = STATE_COLORS.get(r["state"], "#999")
        hours = f"repair done {r['repair_done_h']:.0f} h" if r["repair_done_h"] is not None and r["tasks"] else "no repair needed"
        text = f"{r['kind']}<br>{r['tasks']} task{'s' if r['tasks'] != 1 else ''}"
        hover = (f"{r['label']}<br>{r['customers_downstream']:,} customers downstream<br>{r['oh_miles']} mi overhead<br>"
                 f"{hours}<br>state: {r['state']}" + (f"<br>risk share {r['risk_share']:.0%}" if r.get("risk_share") is not None else ""))
        fig.add_trace(go.Scatter(x=[i], y=[0], mode="markers+text", marker=dict(size=34 if r["kind"] == "breaker" else 26, color=color, line=dict(width=2, color="#333")),
                                 text=[text], textposition="bottom center", hovertext=[hover], hoverinfo="text", showlegend=False))
    cust_color = "#c0392b" if affected else "#6c9a6c"
    label = f"you<br>service back {service_h:.0f} h" if affected and service_h is not None else "you<br>no interruption"
    fig.add_trace(go.Scatter(x=[n], y=[0], mode="markers+text", marker=dict(size=28, color=cust_color, symbol="star", line=dict(width=2, color="#333")),
                             text=[label], textposition="bottom center", hoverinfo="skip", showlegend=False))
    fig.add_annotation(x=0, y=0.25, text="substation", showarrow=False, font=dict(size=11, color="#555"))
    for state, color in STATE_COLORS.items():
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", marker=dict(size=10, color=color), name=state))
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10), xaxis=dict(visible=False), yaxis=dict(visible=False, range=[-0.9, 0.5]),
                      legend=dict(orientation="h", y=1.15, x=0), plot_bgcolor="rgba(0,0,0,0)")
    return fig


def restoration_curves(results: dict[str, ScheduleResult], inst: Instance, highlight_h: float | None = None) -> go.Figure:
    fig = go.Figure()
    for policy, res in results.items():
        pts = sorted((T, n) for (g, n, _, blocking) in inst.groups if blocking for T in [res.group_restored[g]])
        xs, ys, cum = [0.0], [0], 0
        for T, n in pts:
            cum += n
            xs.append(T)
            ys.append(cum)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line_shape="hv", name=POLICY_LABELS.get(policy, policy),
                                 line=dict(color=POLICY_COLORS.get(policy), width=3 if policy == "dependency_aware" else 2)))
    if highlight_h is not None:
        fig.add_vline(x=highlight_h, line_dash="dot", line_color="#c0392b", annotation_text="your service", annotation_position="top")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), xaxis_title="hours after crews start",
                      yaxis_title="customers restored", legend=dict(orientation="h", y=-0.25))
    return fig


def gantt(res: ScheduleResult, inst: Instance, path_zones: set[str] | None = None, max_tasks: int = 400) -> go.Figure:
    fig = go.Figure()
    path_zones = path_zones or set()
    for a in res.assignments[:max_tasks]:
        t = inst.task_by_id[a.task_id]
        on_path = t.zone_id in path_zones
        fig.add_trace(go.Bar(x=[a.end - a.start], y=[f"crew {a.crew + 1}"], base=[a.start], orientation="h",
                             marker=dict(color="#c0392b" if on_path else "#7fa6c9", line=dict(color="#fff", width=0.5)),
                             hovertext=f"{t.asset_type} / {t.damage_type}<br>zone {t.zone_id}<br>{a.start:.1f}–{a.end:.1f} h"
                                       + ("<br>on your upstream path" if on_path else ""), hoverinfo="text", showlegend=False))
    fig.update_layout(barmode="stack", height=max(200, 40 + 28 * inst.crews), margin=dict(l=10, r=10, t=10, b=10),
                      xaxis_title="hours after crews start", yaxis=dict(autorange="reversed"))
    return fig
