"""Parse BoltzGen CSVs and RFdiffusion3 JSONs into unified design dicts.

Reuses metric threshold logic from pgdh_campaign/generate_viewer.py.
"""

import csv
import io
import json
import re
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Metric classification (higher/lower is better) ──────────────────────

def cls_high(val, good, warn) -> str:
    """CSS class for higher-is-better metrics."""
    try:
        v = float(val)
        return "good" if v >= good else "warn" if v >= warn else "bad"
    except (ValueError, TypeError):
        return ""


def cls_low(val, good, warn) -> str:
    """CSS class for lower-is-better metrics."""
    try:
        v = float(val)
        return "good" if v <= good else "warn" if v <= warn else "bad"
    except (ValueError, TypeError):
        return ""


# ── Metric threshold definitions ─────────────────────────────────────────

METRIC_THRESHOLDS = {
    # BoltzGen metrics
    "iptm": {"fn": cls_high, "good": 0.7, "warn": 0.5},
    "design_to_target_iptm": {"fn": cls_high, "good": 0.7, "warn": 0.5},
    "ptm": {"fn": cls_high, "good": 0.8, "warn": 0.7},
    "design_ptm": {"fn": cls_high, "good": 0.8, "warn": 0.7},
    "filter_rmsd": {"fn": cls_low, "good": 2.0, "warn": 2.5},
    "min_design_to_target_pae": {"fn": cls_low, "good": 3.0, "warn": 5.0},
    # RFD3 metrics
    "max_ca_deviation": {"fn": cls_low, "good": 0.5, "warn": 1.0},
    "n_chainbreaks": {"fn": cls_low, "good": 0, "warn": 0},
}


def classify_metric(name: str, value) -> str:
    """Return 'good', 'warn', 'bad', or '' for a metric value."""
    thresh = METRIC_THRESHOLDS.get(name)
    if not thresh:
        return ""
    return thresh["fn"](value, thresh["good"], thresh["warn"])


# ── Strategy detection ───────────────────────────────────────────────────

def _detect_strategy(key: str) -> str:
    """Detect strategy from S3 key path."""
    key_lower = key.lower()
    if "active_site" in key_lower or "s1" in key_lower:
        return "active_site"
    if "dimer" in key_lower or "s2" in key_lower:
        return "dimer_interface"
    if "surface" in key_lower or "s3" in key_lower:
        return "surface"
    return "unknown"


# ── BoltzGen CSV parsing ────────────────────────────────────────────────

def parse_boltzgen_csv(csv_text: str, source_key: str = "") -> list[dict]:
    """Parse a BoltzGen metrics CSV into unified design dicts."""
    reader = csv.DictReader(io.StringIO(csv_text))
    strategy = _detect_strategy(source_key)
    designs = []

    for row in reader:
        design_id = row.get("id", row.get("design_id", ""))
        if not design_id:
            continue

        metrics = {}
        for field in ["design_to_target_iptm", "min_design_to_target_pae",
                       "design_ptm", "filter_rmsd", "plip_hbonds_refolded",
                       "delta_sasa_refolded", "helix", "sheet", "loop",
                       "num_design", "num_filters_passed", "quality_score"]:
            val = row.get(field, "")
            if val:
                try:
                    metrics[field] = float(val)
                except ValueError:
                    metrics[field] = val

        # Convenience aliases
        if "design_to_target_iptm" in metrics:
            metrics["iptm"] = metrics["design_to_target_iptm"]
        if "design_ptm" in metrics:
            metrics["ptm"] = metrics["design_ptm"]

        seq = row.get("sequence", row.get("binder_sequence", ""))
        designs.append({
            "id": f"boltzgen_{design_id}",
            "tool": "boltzgen",
            "strategy": strategy,
            "status": "designed",
            "sequence": seq,
            "num_residues": len(seq) if seq else int(row.get("num_residues", 0) or 0),
            "metrics": metrics,
            "rank": row.get("final_rank", ""),
            "source_key": source_key,
            "notes": "",
            "created_at": _now(),
        })

    return designs


# ── RFdiffusion3 JSON parsing ───────────────────────────────────────────

def parse_rfd3_json(json_text: str, source_key: str = "") -> list[dict]:
    """Parse an RFdiffusion3 output JSON into unified design dicts."""
    data = json.loads(json_text)
    strategy = _detect_strategy(source_key)

    metrics_raw = data.get("metrics", {})
    spec = data.get("specification", {})

    metrics = {}
    for field in ["helix_fraction", "sheet_fraction", "loop_fraction",
                   "radius_of_gyration", "max_ca_deviation", "n_chainbreaks",
                   "num_ss_elements", "alanine_content", "glycine_content"]:
        val = metrics_raw.get(field)
        if val is not None:
            metrics[field] = val

    # Rename fractions for consistency
    if "helix_fraction" in metrics:
        metrics["helix"] = metrics.pop("helix_fraction")
    if "sheet_fraction" in metrics:
        metrics["sheet"] = metrics.pop("sheet_fraction")
    if "loop_fraction" in metrics:
        metrics["loop"] = metrics.pop("loop_fraction")

    hotspots = list(spec.get("select_hotspots", {}).keys())
    num_diffused = len(data.get("diffused_index_map", {}))

    # Derive design_id from source key filename
    stem = source_key.rsplit("/", 1)[-1].rsplit(".", 1)[0] if source_key else "rfd3_design"

    return [{
        "id": f"rfd3_{stem}",
        "tool": "rfdiffusion3",
        "strategy": strategy,
        "status": "designed",
        "sequence": "",  # RFD3 outputs are backbone-only
        "num_residues": num_diffused,
        "metrics": metrics,
        "hotspots": ", ".join(hotspots),
        "source_key": source_key,
        "notes": "",
        "created_at": _now(),
    }]


# ── Unified loader ───────────────────────────────────────────────────────

def load_all_designs_from_state(state: dict) -> list[dict]:
    """Return all designs from state, ready for display."""
    return state.get("designs", [])
