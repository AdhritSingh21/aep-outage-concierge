"""Outage-risk model: circuit-level damage rates aggregated along each customer's upstream path.

What is Observed: damage counts per circuit in the Feb-2025 reference storm, overhead miles, device counts, pole
ages, the customer's upstream path. What is Assumed: the storm footprint covered every circuit (zero counts are
informative), damage arrivals are Poisson, a storm of severity *s* scales every rate by *s*, underground service
lines have no storm damage hazard. What is Simulated: nothing here — the output is an analytic probability.

    μ_zone  = rate_line(circuit features) · overhead miles in zone        (prior expected line-damage count)
    λ_zone  = μ · (α + n_observed) / (α + μ)                              (gamma-Poisson posterior mean)
            + the same for equipment units
    Λ(s)    = s · ( Σ_{zones on the customer's path} λ_zone + λ_service·[overhead service] )
    P(outage | storm s) = 1 − exp(−Λ(s))

A customer is affected by damage anywhere on their upstream path because any fault inside a protective zone trips
that zone's device and everything downstream of it. The circuit rate model is a Poisson regression with an
exposure offset (fit as weighted rates with scikit-learn) and is validated leave-one-circuit-out; it serves as the
prior mean. The dispersion α is estimated by negative-binomial maximum likelihood over zones, so zones with observed
Feb-2025 damage move toward their own count and zones without damage shrink toward the prior.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor

from . import config

FEATURES = ["oh_share", "lateral_share", "pole_year_centered", "pole_year_known"]
LINE_ASSETS = ("primary", "secondary", "Pole")  # wire + pole records count as line damage


# ----------------------------------------------------------------------------- model
@dataclass
class RiskModel:
    intercept: float
    coef: dict[str, float]
    feature_mean: dict[str, float]
    feature_std: dict[str, float]
    pole_year_reference: float
    rate_equipment_per_unit: float
    rate_service_per_overhead_customer: float
    territory_rate_per_oh_mile: float
    territory_median_rate_per_oh_mile: float
    alpha: float
    dispersion_line: float = 1.0        # gamma shape α for line damage (NB MLE over zones)
    dispersion_equipment: float = 1.0   # gamma shape α for equipment damage
    mu_floor: float = 0.02              # Assumed: floor on the prior mean so zones without exposure still shrink sanely
    reference_storm: str = f"{config.REFERENCE_STORM_START}..{config.REFERENCE_STORM_END}"
    n_circuits_fit: int = 0
    validation: dict = field(default_factory=dict)

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf8")

    @classmethod
    def from_json(cls, path: Path) -> "RiskModel":
        return cls(**json.loads(Path(path).read_text(encoding="utf8")))

    def _design(self, feats: pd.DataFrame) -> np.ndarray:
        cols = []
        for f in FEATURES:
            x = feats[f].to_numpy(dtype=float)
            cols.append((x - self.feature_mean[f]) / (self.feature_std[f] or 1.0))
        return np.column_stack(cols)

    def line_rate_per_oh_mile(self, feats: pd.DataFrame) -> np.ndarray:
        """Expected line-damage records per overhead mile in a reference-severity storm."""
        X = self._design(feats)
        eta = self.intercept + X @ np.array([self.coef[f] for f in FEATURES])
        return np.exp(eta)


def circuit_features(circuits: pd.DataFrame, pole_year_reference: float | None = None) -> pd.DataFrame:
    c = circuits.copy()
    total = (c["oh_miles"] + c["ug_miles"]).replace(0, np.nan)
    c["oh_share"] = (c["oh_miles"] / total).fillna(0.0)
    c["lateral_share"] = (c["lateral_miles"] / total).fillna(0.0)
    ref = pole_year_reference if pole_year_reference is not None else float(c["pole_year_mean"].dropna().mean())
    c["pole_year_centered"] = (c["pole_year_mean"].fillna(ref) - ref) / 10.0  # decades, missing → territory mean
    c["pole_year_known"] = c["pole_year_known_pct"].fillna(0.0) / 100.0
    c["line_damage_ref"] = c["n_damage_ref_wire"] + c["n_damage_ref_pole"]
    return c


def _fit(feats: pd.DataFrame, alpha: float) -> tuple[PoissonRegressor, dict, dict]:
    fit = feats[feats["oh_miles"] > 0]
    mean = {f: float(fit[f].mean()) for f in FEATURES}
    std = {f: float(fit[f].std(ddof=0)) or 1.0 for f in FEATURES}
    X = np.column_stack([(fit[f].to_numpy(float) - mean[f]) / std[f] for f in FEATURES])
    y = fit["line_damage_ref"].to_numpy(float) / fit["oh_miles"].to_numpy(float)
    reg = PoissonRegressor(alpha=alpha, max_iter=1000)
    reg.fit(X, y, sample_weight=fit["oh_miles"].to_numpy(float))
    return reg, mean, std


def fit_rate_model(circuits: pd.DataFrame, damage: pd.DataFrame, zones: pd.DataFrame, customers: pd.DataFrame,
                   alpha: float = 0.05) -> RiskModel:
    feats = circuit_features(circuits)
    pole_ref = float(circuits["pole_year_mean"].dropna().mean())
    reg, mean, std = _fit(feats, alpha)
    fit = feats[feats["oh_miles"] > 0]

    # equipment and service rates (territory-wide constants)
    ref = damage[damage["in_reference_storm"] & damage["circuit_id"].isin(set(circuits["circuit_id"]))]
    equipment_records = int((~ref["asset_type"].isin(LINE_ASSETS)).sum())
    equipment_units = float((zones["n_transformers_overhead"] + (zones["kind"] != "breaker").astype(int)).sum())
    service_records = int((ref["asset_type"] == "secondary").sum())
    overhead_customers = int((customers["service_kind"] == "Overhead").sum())

    rates = fit["line_damage_ref"] / fit["oh_miles"]
    model = RiskModel(
        intercept=float(reg.intercept_),
        coef={f: float(c) for f, c in zip(FEATURES, reg.coef_)},
        feature_mean=mean, feature_std=std, pole_year_reference=pole_ref,
        rate_equipment_per_unit=equipment_records / equipment_units if equipment_units else 0.0,
        rate_service_per_overhead_customer=service_records / overhead_customers if overhead_customers else 0.0,
        territory_rate_per_oh_mile=float(fit["line_damage_ref"].sum() / fit["oh_miles"].sum()),
        territory_median_rate_per_oh_mile=float(rates.median()),
        alpha=alpha, n_circuits_fit=int(len(fit)),
    )
    # dispersion of observed zone counts around the prior means (negative-binomial MLE)
    prior = zone_hazards(zones, circuits, model, shrink=False)
    model.dispersion_line = estimate_dispersion(prior["n_damage_ref_line"].to_numpy(float), prior["mu_line"].to_numpy(float))
    model.dispersion_equipment = estimate_dispersion(prior["n_damage_ref_equipment"].to_numpy(float), prior["mu_equipment"].to_numpy(float))
    model.validation = validate_loo(feats, alpha)
    model.validation.update({
        "equipment_records_ref": equipment_records, "equipment_units": equipment_units,
        "service_records_ref": service_records, "overhead_customers": overhead_customers,
        "line_records_ref": int(fit["line_damage_ref"].sum()), "oh_miles_fit": float(fit["oh_miles"].sum()),
    })
    return model


def validate_loo(feats: pd.DataFrame, alpha: float) -> dict:
    """Leave-one-circuit-out: predict each circuit's line-damage count from a model fit on the others."""
    fit = feats[feats["oh_miles"] > 0].reset_index(drop=True)
    pred = np.zeros(len(fit))
    for i in range(len(fit)):
        train = fit.drop(index=i)
        reg, mean, std = _fit(train, alpha)
        x = np.array([[(fit.at[i, f] - mean[f]) / std[f] for f in FEATURES]])
        pred[i] = reg.predict(x)[0] * fit.at[i, "oh_miles"]
    obs = fit["line_damage_ref"].to_numpy(float)
    naive = fit["oh_miles"].to_numpy(float) * (obs.sum() / fit["oh_miles"].sum())  # exposure-only baseline
    from scipy.stats import spearmanr
    rho = float(spearmanr(pred, obs).correlation) if len(obs) > 2 else float("nan")
    rho_naive = float(spearmanr(naive, obs).correlation) if len(obs) > 2 else float("nan")
    order = np.argsort(pred)
    bins = np.array_split(order, 5)
    calibration = [{"quintile": k + 1, "n_circuits": int(len(b)), "predicted": float(pred[b].sum()),
                    "observed": float(obs[b].sum())} for k, b in enumerate(bins)]
    return {
        "loo_spearman_model": round(rho, 3), "loo_spearman_exposure_only": round(rho_naive, 3),
        "loo_mae_model": round(float(np.abs(pred - obs).mean()), 2),
        "loo_mae_exposure_only": round(float(np.abs(naive - obs).mean()), 2),
        "n_circuits": int(len(fit)), "calibration_quintiles": calibration,
    }


# ----------------------------------------------------------------------------- hazards
def estimate_dispersion(n: np.ndarray, mu: np.ndarray) -> float:
    """Gamma shape α maximising the negative-binomial likelihood of counts n with means mu (mu > 0 only)."""
    from scipy.optimize import minimize_scalar
    from scipy.special import gammaln

    ok = mu > 0
    n, mu = n[ok], mu[ok]
    if len(n) == 0:
        return 1.0

    def nll(log_a: float) -> float:
        a = math.exp(log_a)
        ll = (gammaln(n + a) - gammaln(a) - gammaln(n + 1) + a * np.log(a / (a + mu)) + n * np.log(mu / (a + mu)))
        return float(-ll.sum())

    res = minimize_scalar(nll, bounds=(-7, 7), method="bounded")
    return float(math.exp(res.x))


def zone_hazards(zones: pd.DataFrame, circuits: pd.DataFrame, model: RiskModel, shrink: bool = True) -> pd.DataFrame:
    """Per-zone expected damage counts in a reference-severity storm.

    mu_* are the prior means from the circuit model (exposure × rate); lam_* are the gamma-Poisson posterior means
    that also use the zone's own Observed Feb-2025 count (or equal mu_* when shrink=False).
    """
    feats = circuit_features(circuits, model.pole_year_reference)
    rate = pd.Series(model.line_rate_per_oh_mile(feats), index=feats["circuit_id"])
    cols = ["zone_id", "circuit_id", "kind", "depth", "parent_zone_id", "oh_miles", "ug_miles", "n_transformers_overhead",
            "n_customers_direct", "n_customers_downstream", "pole_year_mean", "pole_year_known_pct", "n_damage_ref",
            "n_damage_ref_line", "n_damage_ref_equipment"]
    z = zones[[c for c in cols if c in zones.columns]].copy()
    for c in ("n_damage_ref_line", "n_damage_ref_equipment"):
        if c not in z.columns:
            z[c] = 0
    z["rate_line_per_oh_mile"] = z["circuit_id"].map(rate).fillna(model.territory_rate_per_oh_mile)
    z["n_equipment"] = z["n_transformers_overhead"] + (z["kind"] != "breaker").astype(int)
    z["mu_line"] = z["rate_line_per_oh_mile"] * z["oh_miles"]
    z["mu_equipment"] = model.rate_equipment_per_unit * z["n_equipment"]
    if shrink:
        a_l, a_e = model.dispersion_line, model.dispersion_equipment
        mu_l = z["mu_line"].clip(lower=model.mu_floor)
        mu_e = z["mu_equipment"].clip(lower=model.mu_floor)
        z["lam_line"] = mu_l * (a_l + z["n_damage_ref_line"]) / (a_l + mu_l)
        z["lam_equipment"] = mu_e * (a_e + z["n_damage_ref_equipment"]) / (a_e + mu_e)
    else:
        z["lam_line"] = z["mu_line"]
        z["lam_equipment"] = z["mu_equipment"]
    z["lam_total"] = z["lam_line"] + z["lam_equipment"]
    return z


def customer_path_hazard(customers: pd.DataFrame, zh: pd.DataFrame) -> pd.Series:
    """Σ λ over the zones on each pathed customer's upstream path (index: premise_nu)."""
    lam = zh.set_index("zone_id")["lam_total"]
    c = customers[customers["has_path"]]
    exploded = c.set_index("premise_nu")["path_zone_ids"].str.split("|").explode()
    return exploded.map(lam).fillna(0.0).groupby(level=0).sum()


def probability(hazard: float) -> float:
    return float(1.0 - math.exp(-max(hazard, 0.0)))


def band_for(p: float) -> str:
    for upper, name in config.RISK_BANDS:
        if p < upper:
            return name
    return config.RISK_BANDS[-1][1]


# ----------------------------------------------------------------------------- scoring
@dataclass
class RiskResult:
    probability: float
    band: str
    confidence: str
    severity: float
    hazard: float
    components: dict
    factors: list[dict]
    flags: list[str]
    method: str  # "path" | "circuit_average" | "unavailable"
    sensitivity: dict = field(default_factory=dict)   # severity multiplier → probability (what moves this risk)

    @property
    def percent(self) -> int:
        return int(round(100 * self.probability))

    @property
    def reliable(self) -> bool:
        """False for the fallback paths: the number is shown as 'being assessed', never sent as a figure (E6)."""
        return self.method == "path" and self.confidence in ("High", "Medium")

    @property
    def percent_text(self) -> str:
        """Never show a false '100%': the model is a probability, not a certainty."""
        if self.probability >= 0.995:
            return ">99%"
        if 0 < self.probability < 0.005:
            return "<1%"
        return f"{self.percent}%"


def _zone_label(row: pd.Series) -> str:
    kind = row["kind"]
    if kind == "breaker":
        return "the substation breaker zone (the whole circuit)"
    return f"a {kind} zone {int(row['depth'])} device{'s' if row['depth'] != 1 else ''} below the substation"


def score_customer(cust: pd.Series, zh: pd.DataFrame, circuit: pd.Series, model: RiskModel, severity: float,
                   circuit_avg_hazard: float | None = None) -> RiskResult:
    """Risk for one customer row (from customers.parquet joined with its circuit row)."""
    flags: list[str] = []
    overhead_service = cust.get("service_kind", "Unknown") == "Overhead"
    unknown_service = cust.get("service_kind", "Unknown") == "Unknown"
    lam_service = model.rate_service_per_overhead_customer if overhead_service else 0.0
    if unknown_service:
        lam_service = 0.5 * model.rate_service_per_overhead_customer
        flags.append("Service line type unknown; half of the overhead service-line hazard was assumed.")

    zids = cust["path_zone_ids"].split("|") if cust.get("has_path", False) and isinstance(cust.get("path_zone_ids"), str) else []
    path = zh.set_index("zone_id").reindex(zids).dropna(subset=["lam_total"]) if zids else zh.iloc[0:0]
    if len(path):
        lam_path = float(path["lam_total"].sum())
        method = "path"
        if len(path) < len(zids):
            flags.append(f"{len(zids) - len(path)} of the {len(zids)} zones on your upstream path are missing from the zone table; "
                         "the estimate uses the zones on file.")
    elif circuit_avg_hazard is not None and circuit_avg_hazard > 0:
        path = zh.iloc[0:0]
        lam_path = float(circuit_avg_hazard)
        method = "circuit_average"
        flags.append(("The zones on your upstream path are missing from the zone table" if zids else "No upstream path on file for this premise")
                     + "; the circuit-average exposure was used instead.")
    else:
        return RiskResult(0.0, "Unavailable", "None", severity, 0.0, {}, [],
                          ["No upstream path and no circuit exposure available: a reliable estimate cannot be made."],
                          "unavailable")

    hazard = severity * (lam_path + lam_service)
    p = probability(hazard)
    components = {
        "line": float(severity * path["lam_line"].sum()) if len(path) else float(severity * lam_path),
        "equipment": float(severity * path["lam_equipment"].sum()) if len(path) else 0.0,
        "service": float(severity * lam_service),
        "oh_miles_upstream": float(path["oh_miles"].sum()) if len(path) else float("nan"),
        "equipment_upstream": int(path["n_equipment"].sum()) if len(path) else 0,
        "zones_upstream": int(len(path)),
    }

    # confidence
    n_ref = int(circuit.get("n_damage_ref", 0))
    pole_known = float(circuit.get("pole_year_known_pct", 0.0) or 0.0)
    if method != "path":
        confidence = "Low"
    elif n_ref >= config.CONFIDENCE_MIN_CIRCUIT_DAMAGE and pole_known >= config.CONFIDENCE_MIN_POLE_YEAR_KNOWN_PCT:
        confidence = "High"
    else:
        confidence = "Medium"
        if n_ref < config.CONFIDENCE_MIN_CIRCUIT_DAMAGE:
            flags.append(("No damage reports on this circuit in the Feb-2025 reference storm" if n_ref == 0 else
                          f"Only {n_ref} damage report{'s' if n_ref != 1 else ''} on this circuit in the Feb-2025 reference storm")
                         + "; the rate leans on the territory-wide model.")
        if pole_known < config.CONFIDENCE_MIN_POLE_YEAR_KNOWN_PCT:
            flags.append(f"Pole installation year is known for only {pole_known:.0f}% of poles on this circuit.")

    # factors (largest hazard contributions first)
    factors: list[dict] = []
    if len(path):
        total = max(hazard, 1e-9)
        top = path.sort_values("lam_total", ascending=False).head(2)
        for zid, row in top.iterrows():
            share = severity * row["lam_total"] / total
            factors.append({
                "name": "Upstream line exposure", "share": round(float(share), 2),
                "detail": f"{row['oh_miles']:.1f} mi of overhead line and {int(row['n_equipment'])} equipment unit(s) in "
                          f"{_zone_label(row)}, serving {int(row['n_customers_downstream']):,} customers.",
            })
        factors.append({"name": "Upstream path", "share": None,
                        "detail": f"{len(path)} protective zone(s) between you and the substation; "
                                  f"{components['oh_miles_upstream']:.1f} mi of overhead primary line in total."})
    rate = float(circuit.get("n_damage_ref", 0)) / float(circuit["oh_miles"]) if circuit.get("oh_miles", 0) else float("nan")
    med = model.territory_median_rate_per_oh_mile
    affected = circuit.get("affected_share_ref", None)
    hist = f"{n_ref} damage report(s) on your circuit in the Feb-2025 storm"
    if not math.isnan(rate):
        hist += f" ({rate:.2f} per overhead mile vs. a territory median of {med:.2f})"
    if affected is not None and not pd.isna(affected):
        hist += f"; damage sat on the upstream path of {100 * float(affected):.0f}% of the circuit's customers"
    factors.append({"name": "Circuit history (Observed)", "share": None, "detail": hist + "."})
    if len(path) and int(path["n_damage_ref"].sum()) > 0:
        factors.append({"name": "Your upstream path in Feb-2025 (Observed)", "share": None,
                        "detail": f"{int(path['n_damage_ref'].sum())} damage report(s) were attributed to zones on your own "
                                  f"upstream path in that storm; the estimate leans on this."})
    factors.append({"name": "Your service line", "share": round(components["service"] / max(hazard, 1e-9), 2),
                    "detail": ("Overhead service drop: exposed to falling limbs and wind." if overhead_service else
                               "Underground service: no storm hazard assumed on your own service line." if not unknown_service else
                               "Service type unknown.")})
    if method == "path" and len(path) and path["pole_year_mean"].notna().any():
        yr = float(np.average(path["pole_year_mean"].fillna(model.pole_year_reference), weights=np.maximum(path["oh_miles"], 1e-3)))
        factors.append({"name": "Pole age upstream", "share": None,
                        "detail": f"Average known pole installation year upstream ≈ {yr:.0f} (territory average {model.pole_year_reference:.0f})."})

    # the band is decided on the rounded percent so the word and the number on the card never disagree
    return RiskResult(p, band_for(round(p, 2)), confidence, severity, hazard, components, factors, flags, method)


def severity_sensitivity(cust: pd.Series, zh: pd.DataFrame, circuit: pd.Series, model: RiskModel,
                         circuit_avg_hazard: float | None = None) -> dict[float, float]:
    """Probability at every configured severity multiplier (what moves this risk)."""
    out = {}
    for s in sorted(set(config.SEVERITY_OPTIONS.values())):
        out[float(s)] = score_customer(cust, zh, circuit, model, float(s), circuit_avg_hazard=circuit_avg_hazard).probability
    return out


# ----------------------------------------------------------------------------- artifacts
def build_risk_artifacts(processed: Path, results: Path) -> RiskModel:
    circuits = pd.read_parquet(processed / "circuits.parquet")
    zones = pd.read_parquet(processed / "zones.parquet")
    damage = pd.read_parquet(processed / "damage.parquet")
    customers = pd.read_parquet(processed / "customers.parquet")
    model = fit_rate_model(circuits, damage, zones, customers)
    zh = zone_hazards(zones, circuits, model)
    zh.to_parquet(processed / "zone_hazard.parquet", index=False)
    results.mkdir(parents=True, exist_ok=True)
    model.to_json(results / "risk_model.json")
    (results / "risk_validation.md").write_text(validation_report(model, circuits, zh, customers), encoding="utf8")
    return model


def validation_report(model: RiskModel, circuits: pd.DataFrame, zh: pd.DataFrame, customers: pd.DataFrame) -> str:
    v = model.validation
    feats = circuit_features(circuits, model.pole_year_reference)
    lines = ["# Risk model validation (generated by concierge/risk.py)", "",
             f"Reference storm: {model.reference_storm} (**Observed** damage records). Model: Poisson regression of "
             f"line-damage records (primary, secondary, pole) per circuit with overhead miles as exposure, "
             f"L2 alpha = {model.alpha}, {model.n_circuits_fit} circuits with overhead line.", "",
             "## Coefficients (per 1 standard deviation of the feature; log-rate scale)", "",
             "| feature | coefficient | mean | sd |", "|---|---|---|---|"]
    for f in FEATURES:
        lines.append(f"| {f} | {model.coef[f]:+.3f} | {model.feature_mean[f]:.3f} | {model.feature_std[f]:.3f} |")
    lines += [f"| intercept | {model.intercept:+.3f} | | |", "",
              f"Territory line-damage rate: {model.territory_rate_per_oh_mile:.3f} per overhead mile (median circuit "
              f"{model.territory_median_rate_per_oh_mile:.3f}). Equipment rate: {model.rate_equipment_per_unit:.4f} per unit "
              f"({v.get('equipment_records_ref')} records / {v.get('equipment_units'):.0f} units). Service-line rate: "
              f"{model.rate_service_per_overhead_customer:.5f} per overhead customer ({v.get('service_records_ref')} records / "
              f"{v.get('overhead_customers')} customers).", "",
              "## Zone-level shrinkage (empirical Bayes)", "",
              f"The circuit model is the prior mean μ for each zone; the zone's own Feb-2025 count n moves it to the "
              f"gamma-Poisson posterior λ = μ·(α+n)/(α+μ). Negative-binomial MLE over {len(zh):,} zones gives "
              f"α_line = {model.dispersion_line:.3f} and α_equipment = {model.dispersion_equipment:.3f} "
              f"(small α = damage was highly concentrated, so observed counts dominate where they exist).", "",
              f"Zones with Feb-2025 line damage: {int((zh['n_damage_ref_line'] > 0).sum()):,}; "
              f"sum of posterior λ_line = {zh['lam_line'].sum():.0f} vs observed {int(zh['n_damage_ref_line'].sum())} "
              f"and prior {zh['mu_line'].sum():.0f}.", "",
              "## Leave-one-circuit-out validation of the prior (circuit model)", "",
              "| metric | model | exposure-only baseline |", "|---|---|---|",
              f"| Spearman rank correlation (predicted vs observed counts) | {v['loo_spearman_model']} | {v['loo_spearman_exposure_only']} |",
              f"| Mean absolute error (records per circuit) | {v['loo_mae_model']} | {v['loo_mae_exposure_only']} |", "",
              "Calibration by predicted quintile (sums of records):", "",
              "| quintile | circuits | predicted | observed |", "|---|---|---|---|"]
    for q in v["calibration_quintiles"]:
        lines.append(f"| {q['quintile']} | {q['n_circuits']} | {q['predicted']:.1f} | {q['observed']:.0f} |")
    pathed = customers[customers["has_path"]]
    ph = customer_path_hazard(pathed, zh)
    p = 1 - np.exp(-ph)
    bands = pd.Series([band_for(x) for x in p]).value_counts()
    lines += ["", "## Customer risk distribution at severity 1x (Feb-2025-like storm)", "",
              "| band | customers |", "|---|---|"]
    for name in [b for _, b in config.RISK_BANDS]:
        lines.append(f"| {name} | {int(bands.get(name, 0)):,} |")
    circ_idx = circuits.set_index("circuit_id")
    zero_circuits = set(circ_idx.index[circ_idx["n_damage_ref"] == 0])
    on_zero = pathed["circuit_id"].isin(zero_circuits).to_numpy()
    p_by_prem = pd.Series(p, index=ph.index).reindex(pathed["premise_nu"]).to_numpy()
    affected = circ_idx["affected_share_ref"] if "affected_share_ref" in circ_idx.columns else None
    lines += ["", f"Median customer hazard Λ = {float(np.median(ph)):.3f} → P = {float(np.median(p)):.1%}; "
              f"90th percentile P = {float(np.percentile(p, 90)):.1%}.",
              f"Customers on the {len(zero_circuits)} circuits with no Feb-2025 damage: median P = "
              f"{float(np.nanmedian(p_by_prem[on_zero])):.1%}; on circuits with damage: median P = "
              f"{float(np.nanmedian(p_by_prem[~on_zero])):.1%}.", ""]
    if affected is not None:
        w = circ_idx["n_with_path"].astype(float)
        lines += [f"Observed anchor: in Feb-2025, damage sat on the upstream path of "
                  f"{float((affected * w).sum() / w.sum()):.1%} of pathed customers territory-wide "
                  f"(per-circuit share ranges {affected.min():.0%}–{affected.max():.0%}).", ""]
    lines += ["## Limitations", "",
              "- One reference storm: the model learns *where* damage concentrated in that event, not how any future storm will behave; "
              "where the storm hit and how vulnerable the lines are cannot be separated with one event.",
              "- The storm footprint is assumed to have covered every circuit in the extract (zero-damage circuits count as zeros).",
              "- No per-customer outage records exist; the probability is a damage-intensity estimate, not a calibrated outage forecast.",
              "- Tie switches / back-feeds are not modelled; any damage on the upstream path is assumed to interrupt service."]
    return "\n".join(lines) + "\n"
