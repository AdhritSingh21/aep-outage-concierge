"""Plotly figures: upstream-dependency chain, zone map, restoration curves, crew Gantt.

Accessibility: every state is encoded by colour AND by marker shape AND by a word in the node label, so the figures
read without colour; the chain carries a plain-language caption in the app.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from .restoration import fmt_hours
from .scheduling import Instance, POLICY_LABELS, ScheduleResult

STATE_COLORS = {"no damage": "#4c8c4a", "repaired before service returns": "#e0a030", "blocking (last on your path)": "#c0392b"}
STATE_SYMBOLS = {"no damage": "circle", "repaired before service returns": "diamond", "blocking (last on your path)": "x"}
STATE_SHORT = {"no damage": "no damage", "repaired before service returns": "repaired", "blocking (last on your path)": "blocking"}
POLICY_COLORS = {"fifo": "#888888", "customers_first": "#e0a030", "dependency_aware": "#1f77b4"}


def dependency_chain(path: pd.DataFrame, service_h: float | None, affected: bool) -> go.Figure:
    """Substation → … → customer, one node per protective zone, colored and shaped by state in the current scenario."""
    fig = go.Figure()
    n = len(path)
    xs = list(range(n + 1))
    fig.add_trace(go.Scatter(x=xs, y=[0] * (n + 1), mode="lines", line=dict(color="#999", width=3), hoverinfo="skip", showlegend=False))
    for i, r in path.iterrows():
        state = r["state"]
        color = STATE_COLORS.get(state, "#999")
        done = r["repair_done_h"]
        hours = f"repair done {fmt_hours(done)}" if (done is not None and done == done and r["tasks"]) else "no repair needed"
        text = f"{r['kind']} · {r['tasks']} task{'s' if r['tasks'] != 1 else ''}<br>{STATE_SHORT.get(state, state)}"
        hover = (f"{r['label']}<br>{r['customers_downstream']:,} customers downstream<br>{r['oh_miles']} mi overhead<br>"
                 f"{hours}<br>state: {state}" + (f"<br>risk share {r['risk_share']:.0%}" if r.get("risk_share") is not None else ""))
        fig.add_trace(go.Scatter(x=[i], y=[0], mode="markers+text",
                                 marker=dict(size=34 if r["kind"] == "breaker" else 26, color=color, symbol=STATE_SYMBOLS.get(state, "circle"),
                                             line=dict(width=2, color="#333")),
                                 text=[text], textposition="bottom center", hovertext=[hover], hoverinfo="text", showlegend=False))
    cust_color = "#c0392b" if affected else "#4c8c4a"
    label = f"you<br>service back {fmt_hours(service_h)}" if affected and service_h is not None else "you<br>no interruption"
    fig.add_trace(go.Scatter(x=[n], y=[0], mode="markers+text", marker=dict(size=28, color=cust_color, symbol="star", line=dict(width=2, color="#333")),
                             text=[label], textposition="bottom center", hoverinfo="skip", showlegend=False))
    fig.add_annotation(x=0, y=0.3, text="substation", showarrow=False, font=dict(size=11, color="#555"))
    for state, color in STATE_COLORS.items():
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", marker=dict(size=10, color=color, symbol=STATE_SYMBOLS[state]), name=state))
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=10, b=10), xaxis=dict(visible=False), yaxis=dict(visible=False, range=[-1.0, 0.6]),
                      legend=dict(orientation="h", y=1.15, x=0), plot_bgcolor="rgba(0,0,0,0)", font=dict(size=13))
    return fig


def zone_map(zones: pd.DataFrame, path_zone_ids: list[str], states: dict[str, str], customer_zone: str | None) -> go.Figure | None:
    """Zone centroids of the customer's circuit on an OpenStreetMap base (no premise or facility points; the customer's
    own zone is marked at its centroid, not at the home). Returns None when the circuit has no zone coordinates."""
    z = zones[zones["lon"].notna() & zones["lat"].notna()] if "lon" in zones.columns else zones.iloc[0:0]
    if len(z) == 0:
        return None
    on_path = z["zone_id"].isin(path_zone_ids)
    scatter = getattr(go, "Scattermap", None) or go.Scattermapbox
    use_map = scatter is not go.Scattermapbox
    fig = go.Figure()
    base = z[~on_path]
    fig.add_trace(scatter(lon=base["lon"], lat=base["lat"], mode="markers",
                          marker=dict(size=7, color="#b0b0b0"), name="other zones on the circuit",
                          hovertext=[f"{k} zone · {int(c):,} customers downstream" for k, c in zip(base["kind"], base["n_customers_downstream"])],
                          hoverinfo="text"))
    for state, color in STATE_COLORS.items():
        sel = z[on_path & z["zone_id"].map(lambda i: states.get(i, "no damage") == state)]
        if len(sel) == 0:
            continue
        fig.add_trace(scatter(lon=sel["lon"], lat=sel["lat"], mode="markers",
                              marker=dict(size=16 if use_map else 14, color=color), name=f"your path: {state}",
                              hovertext=[f"{k} zone · {int(c):,} customers downstream · {state}" for k, c in zip(sel["kind"], sel["n_customers_downstream"])],
                              hoverinfo="text"))
    if customer_zone in set(z["zone_id"]):
        cz = z[z["zone_id"] == customer_zone].iloc[0]
        fig.add_trace(scatter(lon=[cz["lon"]], lat=[cz["lat"]], mode="markers", marker=dict(size=20, color="#1f77b4", symbol="circle"),
                              name="your zone (centroid)", hoverinfo="name"))
    center = dict(lon=float(z["lon"].mean()), lat=float(z["lat"].mean()))
    layout = dict(height=380, margin=dict(l=0, r=0, t=0, b=0), legend=dict(orientation="h", y=1.08, x=0))
    if use_map:
        layout["map"] = dict(style="open-street-map", center=center, zoom=11)
    else:
        layout["mapbox"] = dict(style="open-street-map", center=center, zoom=11)
    fig.update_layout(**layout)
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
                                 line=dict(color=POLICY_COLORS.get(policy), width=3 if policy == "dependency_aware" else 2,
                                           dash={"fifo": "dot", "customers_first": "dash"}.get(policy, "solid"))))
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
                             marker=dict(color="#c0392b" if on_path else "#7fa6c9", line=dict(color="#fff", width=0.5),
                                         pattern_shape="/" if on_path else ""),
                             hovertext=f"{t.asset_type} / {t.damage_type}<br>{a.start:.1f}–{a.end:.1f} h after crews start"
                                       + ("<br>on your upstream path" if on_path else ""), hoverinfo="text", showlegend=False))
    fig.update_layout(barmode="stack", height=max(200, 40 + 28 * inst.crews), margin=dict(l=10, r=10, t=10, b=10),
                      xaxis_title="hours after crews start", yaxis=dict(autorange="reversed"))
    return fig
