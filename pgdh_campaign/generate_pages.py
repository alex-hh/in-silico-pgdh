#!/usr/bin/env python3
"""Generate the GitHub Pages site from S3 design data.

Downloads designs/index.json and per-design structures from S3 (written by
sync_designs.py), caches them in docs/data/, and generates docs/index.html
with Evaluated and Unevaluated tabs, 3Dmol.js viewers, and metrics panels.

Falls back to cached docs/data/ if S3 is unreachable, or pgdh_campaign/out/
(legacy local data) if docs/data/ doesn't exist.

Usage:
    python pgdh_campaign/sync_designs.py    # ensure designs/ source of truth is current
    python pgdh_campaign/generate_pages.py  # sync from S3 + generate HTML

After running, commit and push docs/ to update the GitHub Pages site.
"""

import csv
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "projects" / "biolyceum" / "src" / "utils"))

BASE = Path(__file__).parent
DOCS_DATA = BASE.parent / "docs" / "data"
STRUCTURES_DIR = BASE / "structures"
OUTPUT_HTML = BASE.parent / "docs" / "index.html"

MAX_CIF_EMBEDS = 30  # Only embed 3D structures for top K designs by rank

STRATEGY_META = {
    "active_site": {"label": "Active Site", "color": "#FF6B6B"},
    "dimer_interface": {"label": "Dimer Interface", "color": "#9B59B6"},
    "surface": {"label": "Surface", "color": "#00CED1"},
    "unknown": {"label": "Unknown", "color": "#95A5A6"},
}

STRATEGY_INFO = [
    {
        "key": "active_site", "label": "Active Site", "color": "#FF6B6B",
        "length": "40\u201380 AA",
        "desc": "Target the NAD+-binding active site pocket.",
        "hotspots": [("Ser138", 138), ("Gln148", 148), ("Tyr151", 151),
                     ("Lys155", 155), ("Phe185", 185), ("Tyr217", 217)],
    },
    {
        "key": "dimer_interface", "label": "Dimer Interface", "color": "#9B59B6",
        "length": "90\u2013120 AA",
        "desc": "Disrupt the homodimer contact surface.",
        "hotspots": [("Ala146", 146), ("Ala153", 153), ("Phe161", 161), ("Leu167", 167),
                     ("Ala168", 168), ("Leu171", 171), ("Met172", 172), ("Tyr206", 206)],
    },
    {
        "key": "surface", "label": "Surface", "color": "#00CED1",
        "length": "60\u2013140 AA",
        "desc": "No specified hotspots \u2014 BoltzGen auto-detects binding site.",
        "hotspots": [],
    },
]

TOOL_COLORS = {
    "boltzgen": "#39ff14",
    "rfdiffusion3": "#ff6e1a",
    "custom": "#00e5ff",
}


def cls_low(val, good, warn):
    try:
        v = float(val)
        return "good" if v <= good else "warn" if v <= warn else "bad"
    except (ValueError, TypeError):
        return ""


def cls_high(val, good, warn):
    try:
        v = float(val)
        return "good" if v >= good else "warn" if v >= warn else "bad"
    except (ValueError, TypeError):
        return ""


def extract_sequence(cif_path, chain_id="B"):
    """Extract protein sequence for a chain from a CIF file."""
    three_to_one = {
        "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
        "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
        "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
        "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    }
    residues = {}
    with open(cif_path) as f:
        in_atom_site = False
        headers = []
        for line in f:
            line = line.strip()
            if line.startswith("_atom_site."):
                in_atom_site = True
                headers.append(line.split(".")[1])
                continue
            if in_atom_site and not line.startswith("_") and not line.startswith("#") and line:
                fields = line.split()
                if len(fields) < len(headers):
                    in_atom_site = False
                    continue
                row = dict(zip(headers, fields))
                chain = row.get("label_asym_id", row.get("auth_asym_id", ""))
                if chain != chain_id:
                    continue
                if row.get("group_PDB") != "ATOM":
                    continue
                resname = row.get("label_comp_id", "")
                resnum = int(row.get("label_seq_id", 0))
                if resname in three_to_one and resnum not in residues:
                    residues[resnum] = three_to_one[resname]
            elif in_atom_site and (line.startswith("#") or line == ""):
                if residues:
                    break
    if not residues:
        return ""
    return "".join(residues[k] for k in sorted(residues))


# ── S3 sync ───────────────────────────────────────────────────────────────

def sync_from_s3():
    """Download designs from S3 to docs/data/. Returns True if sync succeeded."""
    from client import LyceumClient

    DOCS_DATA.mkdir(parents=True, exist_ok=True)

    try:
        client = LyceumClient()
    except Exception as e:
        print(f"  Could not connect to Lyceum ({e})")
        return False

    # 1. Download index.json
    print("Syncing from S3...")
    try:
        index_data = client.download_bytes("designs/index.json")
        index = json.loads(index_data)
    except Exception as e:
        print(f"  Could not download index ({e})")
        return False

    (DOCS_DATA / "index.json").write_text(json.dumps(index, indent=2))
    print(f"  {len(index.get('designs', []))} designs in index")

    # 2. Download per-design metrics.json and CIF structures
    evaluated, unevaluated = [], []

    for entry in index.get("designs", []):
        did = entry["design_id"]
        tool = entry.get("tool", "unknown")
        prefix = f"designs/{tool}/{did}/"

        design_dir = DOCS_DATA / tool / did
        design_dir.mkdir(parents=True, exist_ok=True)

        # Download metrics.json
        try:
            metrics_data = client.download_bytes(f"{prefix}metrics.json")
            metrics = json.loads(metrics_data)
            (design_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
        except Exception:
            metrics = entry

        # Download designed.cif (try both plain and gzipped)
        cif_text = None
        for cif_name in ["designed.cif", "designed.cif.gz"]:
            try:
                cif_bytes = client.download_bytes(f"{prefix}{cif_name}")
                if cif_name.endswith(".gz"):
                    cif_bytes = gzip.decompress(cif_bytes)
                cif_text = cif_bytes.decode()
                (design_dir / "designed.cif").write_text(cif_text)
                break
            except Exception:
                continue

        # Download refolded.cif if it exists
        try:
            refolded = client.download_bytes(f"{prefix}refolded.cif")
            (design_dir / "refolded.cif").write_text(refolded.decode())
        except Exception:
            pass

        # Classify: evaluated = has validation or scoring or refolding
        has_eval = (
            metrics.get("validation") is not None
            or metrics.get("scoring") is not None
            or metrics.get("refolding") is not None
            or (entry.get("composite_score") is not None and entry.get("has_validation"))
        )
        record = {**metrics, "design_id": did, "has_structure": cif_text is not None}
        (evaluated if has_eval else unevaluated).append(record)

        status = "evaluated" if has_eval else "unevaluated"
        struct = "+" if cif_text else "-"
        print(f"  [{struct}] {did} ({status})")

    # 3. Write summary JSONs
    (DOCS_DATA / "evaluated.json").write_text(json.dumps(evaluated, indent=2))
    (DOCS_DATA / "unevaluated.json").write_text(json.dumps(unevaluated, indent=2))

    print(f"  Synced: {len(evaluated)} evaluated, {len(unevaluated)} unevaluated")
    return True


# ── Data loading ──────────────────────────────────────────────────────────

def load_from_docs_data():
    """Load designs from docs/data/ (synced from S3)."""
    evaluated, unevaluated = [], []
    for json_file, dest in [
        (DOCS_DATA / "evaluated.json", evaluated),
        (DOCS_DATA / "unevaluated.json", unevaluated),
    ]:
        if not json_file.exists():
            continue
        for entry in json.loads(json_file.read_text()):
            did = entry.get("design_id", "")
            tool = entry.get("tool", "unknown")
            cif_path = DOCS_DATA / tool / did / "designed.cif"
            if not cif_path.exists():
                continue

            seq = entry.get("sequence", "")
            if not seq:
                for chain in ["B", "A"]:
                    seq = extract_sequence(cif_path, chain)
                    if seq:
                        break

            dm = entry.get("design_metrics", entry.get("metrics", {}))
            dest.append({
                "design_id": did, "name": did, "tool": tool,
                "strategy": entry.get("strategy", "unknown"),
                "round": entry.get("round"),
                "cif_path": cif_path, "cif": "", "sequence": seq,
                "length": len(seq) if seq else entry.get("num_residues", 0),
                "binder_chain": "A",
                "metrics": dm, "status": entry.get("status", "designed"),
                "composite_score": entry.get("composite_score"),
                "rank": entry.get("rank"),
                "validation": entry.get("validation"),
                "scoring": entry.get("scoring"),
                "refolding": entry.get("refolding"),
            })
    return evaluated, unevaluated


def load_from_local():
    """Load from pgdh_campaign/out/ (legacy fallback)."""
    designs = []
    out_dir = BASE / "out"

    # BoltzGen
    bg_csv = out_dir / "boltzgen" / "all_designs_metrics.csv"
    metrics_by_id = {}
    if bg_csv.exists():
        with open(bg_csv) as f:
            for row in csv.DictReader(f):
                metrics_by_id[row["id"]] = row

    designs_dir = out_dir / "boltzgen" / "designs"
    if designs_dir.exists():
        for cif in sorted(designs_dir.glob("*.cif")):
            matched = next((r for mid, r in metrics_by_id.items() if mid in cif.stem), None)
            seq = extract_sequence(cif, "B")
            designs.append({
                "design_id": f"boltzgen_local_{cif.stem}", "name": cif.stem,
                "tool": "boltzgen", "strategy": "surface",
                "cif": cif.read_text(), "sequence": seq, "length": len(seq),
                "binder_chain": "A", "metrics": matched or {}, "status": "designed",
            })

    # RFD3
    rfd3_dir = out_dir / "rfd3"
    if rfd3_dir.exists():
        for jf in sorted(rfd3_dir.glob("*.json")):
            data = json.loads(jf.read_text())
            stem = jf.stem
            cif_path = rfd3_dir / f"{stem}.cif"
            cif_gz = rfd3_dir / f"{stem}.cif.gz"
            if not cif_path.exists() and cif_gz.exists():
                with gzip.open(cif_gz, "rb") as f_in:
                    cif_path.write_bytes(f_in.read())
            if not cif_path.exists():
                continue
            seq = extract_sequence(cif_path, "A") or extract_sequence(cif_path, "B")
            designs.append({
                "design_id": f"rfd3_local_{stem}", "name": stem,
                "tool": "rfdiffusion3", "strategy": "active_site",
                "cif": cif_path.read_text(), "sequence": seq,
                "length": len(seq) if seq else len(data.get("diffused_index_map", {})),
                "binder_chain": "A", "metrics": data.get("metrics", {}), "status": "designed",
            })

    return [], designs


# ── HTML generation ───────────────────────────────────────────────────────

def metric_row(label, value, css_class=""):
    c = f' class="metric-value {css_class}"' if css_class else ' class="metric-value"'
    return f'<div class="metric-row"><span class="metric-label">{label}</span><span{c}>{value}</span></div>'


def design_card_html(d, idx):
    tool = d.get("tool", "unknown")
    strategy = d.get("strategy", "unknown")
    tool_color = TOOL_COLORS.get(tool, "#95A5A6")
    strat_meta = STRATEGY_META.get(strategy, STRATEGY_META["unknown"])
    rank = d.get("rank", "")
    composite = d.get("composite_score")
    rank_html = f'<span class="rank-badge">#{rank}</span>' if rank else ""
    has_validation = bool(d.get("validation"))
    score_cls = "score-badge" if has_validation else "score-badge score-partial"
    score_label = f"Score: {composite:.3f}" if composite else ""
    score_html = f'<span class="{score_cls}">{score_label} <span class="score-help" onclick="event.stopPropagation();document.getElementById(\'score-tooltip\').classList.toggle(\'visible\')">?</span></span>' if composite else ""
    rnd = d.get("round")
    round_html = f'<span class="tool-badge" style="background:#2ECC71">R{rnd}</span>' if rnd is not None else ""

    card = f"""
<div class="design-card">
  <div class="card-header">
    <h2>{d['name']}</h2>
    <div>{rank_html} {score_html}
      <span class="tool-badge" style="background:{tool_color}">{tool}</span>
      <span class="tool-badge" style="background:{strat_meta['color']}">{strat_meta['label']}</span>
      {round_html}
    </div>
  </div>
  <div class="card-body">
    <div class="viewer-container" id="viewer_{idx}">
      <div class="seq-legend" id="seqleg_{idx}">
        <span class="seq-legend-item"><span class="seq-legend-dot" style="background:#E8860C"></span>Hydrophobic</span>
        <span class="seq-legend-item"><span class="seq-legend-dot" style="background:#2ECC71"></span>Polar</span>
        <span class="seq-legend-item"><span class="seq-legend-dot" style="background:#3498DB"></span>Positive</span>
        <span class="seq-legend-item"><span class="seq-legend-dot" style="background:#E74C3C"></span>Negative</span>
        <span class="seq-legend-item"><span class="seq-legend-dot" style="background:#95A5A6"></span>Gly</span>
      </div>
      <div class="viewer-controls">
        <button onclick="resetView({idx})">Reset</button>
        <button onclick="toggleStyle({idx},'cartoon')" class="active" id="btn_cartoon_{idx}">Cartoon</button>
        <button onclick="toggleStyle({idx},'surface')" id="btn_surface_{idx}">Surface</button>
        <button onclick="toggleStyle({idx},'sticks')" id="btn_sticks_{idx}">Interface</button>
        <button onclick="toggleStyle({idx},'sequence')" id="btn_sequence_{idx}">Sequence</button>
      </div>
    </div>
    <div class="metrics-panel">
"""
    dm = d.get("metrics", {})
    if dm.get("iptm") or dm.get("design_to_target_iptm"):
        v = dm.get("iptm", dm.get("design_to_target_iptm", "N/A"))
        card += metric_row("ipTM", v, cls_high(v, 0.7, 0.5))
    if dm.get("ptm") or dm.get("design_ptm"):
        v = dm.get("ptm", dm.get("design_ptm", "N/A"))
        card += metric_row("pTM", v, cls_high(v, 0.8, 0.7))
    if dm.get("filter_rmsd"):
        card += metric_row("Design RMSD", f"{dm['filter_rmsd']} &Aring;", cls_low(dm["filter_rmsd"], 2.0, 2.5))
    if dm.get("min_pae") or dm.get("min_design_to_target_pae"):
        v = dm.get("min_pae", dm.get("min_design_to_target_pae", "N/A"))
        card += metric_row("Min PAE", v, cls_low(v, 3.0, 5.0))

    val = d.get("validation") or {}
    if val:
        card += '<div class="section-label">Validation (Boltz-2)</div>'
        if val.get("iptm"):
            card += metric_row("Val. ipTM", val["iptm"], cls_high(val["iptm"], 0.7, 0.5))
        if val.get("plddt"):
            card += metric_row("Val. pLDDT", val["plddt"], cls_high(val["plddt"], 85, 70))

    scr = d.get("scoring") or {}
    if scr:
        card += '<div class="section-label">Scoring</div>'
        if scr.get("min_interaction_pae"):
            card += metric_row("Min iPAE", scr["min_interaction_pae"], cls_low(scr["min_interaction_pae"], 3.0, 5.0))
        if scr.get("pdockq"):
            card += metric_row("pDockQ", scr["pdockq"], cls_high(scr["pdockq"], 0.50, 0.23))

    refold = d.get("refolding") or {}
    if refold and (refold.get("boltzgen_rmsd") or refold.get("min_interaction_pae")):
        card += '<div class="section-label">Designability</div>'
        if refold.get("boltzgen_rmsd"):
            card += metric_row("BoltzGen RMSD", f"{refold['boltzgen_rmsd']} &Aring;", cls_low(refold["boltzgen_rmsd"], 2.0, 2.5))
        if refold.get("min_interaction_pae") and not scr.get("min_interaction_pae"):
            card += metric_row("Min iPAE", refold["min_interaction_pae"], cls_low(refold["min_interaction_pae"], 3.0, 5.0))

    if tool == "boltzgen":
        if dm.get("plip_hbonds"):
            card += metric_row("H-bonds", dm["plip_hbonds"])
        if dm.get("delta_sasa"):
            card += metric_row("&Delta;SASA", f"{dm['delta_sasa']} &Aring;&sup2;")
    elif tool == "rfdiffusion3":
        if dm.get("radius_of_gyration"):
            card += metric_row("R<sub>g</sub>", f"{dm['radius_of_gyration']} &Aring;")
        if dm.get("n_chainbreaks") is not None:
            card += metric_row("Chain breaks", dm["n_chainbreaks"], cls_low(dm["n_chainbreaks"], 0, 0))

    helix = float(dm.get("helix", dm.get("helix_fraction", 0)) or 0) * 100
    sheet = float(dm.get("sheet", dm.get("sheet_fraction", 0)) or 0) * 100
    loop = float(dm.get("loop", dm.get("loop_fraction", 0)) or 0) * 100
    if helix + sheet + loop > 0:
        card += f"""
      <div class="section-label">Secondary Structure</div>
      <div class="ss-bar"><div class="helix" style="width:{helix:.1f}%"></div><div class="sheet" style="width:{sheet:.1f}%"></div><div class="loop" style="width:{loop:.1f}%"></div></div>
      <div class="legend"><span class="h">Helix {helix:.0f}%</span><span class="s">Sheet {sheet:.0f}%</span><span class="l">Loop {loop:.0f}%</span></div>
"""

    seq = d.get("sequence", "")
    if seq:
        card += f'<div class="section-label">Sequence ({d.get("length", len(seq))} AA)</div><div class="sequence-box">{seq}</div>'
    else:
        card += '<div class="section-label">Backbone Only</div><div class="sequence-box" style="color:#8b949e">Use ProteinMPNN for inverse folding.</div>'

    card += "</div></div></div>\n"
    return card


def build_table_data(all_designs):
    """Build JSON-serializable table data for all designs."""
    rows = []
    for d in all_designs:
        dm = d.get("metrics", {})
        val = d.get("validation") or {}
        scr = d.get("scoring") or {}
        refold = d.get("refolding") or {}
        rows.append({
            "rank": d.get("rank") or "",
            "design_id": d.get("design_id", ""),
            "tool": d.get("tool", ""),
            "strategy": d.get("strategy", ""),
            "round": d.get("round") if d.get("round") is not None else "",
            "composite": d.get("composite_score") or "",
            "score_partial": not bool(val),
            "iptm": dm.get("iptm", dm.get("design_to_target_iptm", "")),
            "ptm": dm.get("ptm", dm.get("design_ptm", "")),
            "filter_rmsd": dm.get("filter_rmsd", ""),
            "min_pae": dm.get("min_pae", dm.get("min_design_to_target_pae", "")),
            "bg_rmsd": refold.get("boltzgen_rmsd", ""),
            "bg_plddt": refold.get("boltzgen_plddt", ""),
            "bg_iptm": refold.get("boltzgen_iptm", ""),
            "val_iptm": val.get("iptm", ""),
            "val_plddt": val.get("plddt", ""),
            "min_ipae": scr.get("min_interaction_pae", "") or refold.get("min_interaction_pae", ""),
            "pdockq": scr.get("pdockq", ""),
            "length": d.get("length", ""),
            "status": d.get("status", ""),
        })
    return rows


TABLE_COLUMNS = [
    ("rank", "#", "low"),
    ("design_id", "Design ID", None),
    ("tool", "Tool", None),
    ("strategy", "Strategy", None),
    ("round", "Round", None),
    ("composite", "Score", "high"),
    # Designer metrics (single-seq, from BoltzGen/RFD3)
    ("iptm", "Design ipTM", "high"),
    ("ptm", "Design pTM", "high"),
    ("filter_rmsd", "Design RMSD", "low"),
    ("min_pae", "Design PAE", "low"),
    # Refolding (BoltzGen single-seq folding — does the sequence fold back?)
    ("bg_rmsd", "Refold RMSD (BZG)", "low"),
    ("bg_plddt", "Refold pLDDT (BZG)", "high"),
    ("bg_iptm", "Refold ipTM (BZG)", "high"),
    # Cross-validation (Boltz-2 + MSA — independent structure prediction)
    ("val_iptm", "Xval ipTM (BZ2+MSA)", "high"),
    ("val_plddt", "Xval pLDDT (BZ2+MSA)", "high"),
    # Scoring
    ("min_ipae", "Min iPAE", "low"),
    ("pdockq", "pDockQ", "high"),
    ("length", "Length", None),
    ("status", "Status", None),
]


def build_html(skip_sync=False):
    # Step 1: Sync from S3 (downloads to docs/data/)
    if not skip_sync:
        sync_from_s3()

    # Step 2: Load data
    if (DOCS_DATA / "index.json").exists():
        print("Loading from docs/data/...")
        evaluated, unevaluated = load_from_docs_data()
    else:
        print("No docs/data/. Loading from pgdh_campaign/out/ (local)...")
        evaluated, unevaluated = load_from_local()

    all_designs = evaluated + unevaluated
    print(f"  {len(evaluated)} evaluated, {len(unevaluated)} unevaluated")

    if not all_designs:
        print("No designs found. Run sync_designs.py first.")
        return

    # Load CIF text only for top-ranked designs to keep HTML size manageable
    top_ids = set()
    ranked = sorted(all_designs, key=lambda d: (d.get("rank") or 9999))
    for d in ranked[:MAX_CIF_EMBEDS]:
        top_ids.add(d["design_id"])
    n_embedded = 0
    for d in all_designs:
        if d["design_id"] in top_ids and d.get("cif_path"):
            try:
                d["cif"] = d["cif_path"].read_text()
                n_embedded += 1
            except Exception:
                d["cif"] = ""
    print(f"  Embedded 3D structures for top {n_embedded} designs (of {len(all_designs)} total)")

    target_cif = STRUCTURES_DIR / "2GDZ.cif"
    target_cif_text = target_cif.read_text() if target_cif.exists() else ""

    # Build cards
    idx = 0
    eval_cards = ""
    for d in evaluated:
        eval_cards += design_card_html(d, idx); idx += 1
    uneval_cards = ""
    for d in unevaluated:
        uneval_cards += design_card_html(d, idx); idx += 1
    all_cards = ""
    for d in all_designs:
        all_cards += design_card_html(d, idx); idx += 1

    # Viewer data for JS — only include CIF text for embedded designs
    viewer_data = []
    for d in evaluated + unevaluated + all_designs:
        viewer_data.append({
            "cif": d.get("cif", ""), "name": d["name"],
            "binderChain": d.get("binder_chain", "B"),
            "tool": d.get("tool", "unknown"),
        })

    # Table data for all designs
    table_rows = build_table_data(all_designs)

    n_bg = len([d for d in all_designs if d.get("tool") == "boltzgen"])
    n_rfd = len([d for d in all_designs if d.get("tool") == "rfdiffusion3"])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>In Silico PGDH — Binder Design Campaign</title>
<script src="https://3Dmol.org/build/3Dmol-min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root{{--bg:#0d1117;--surface:#161b22;--surface2:#21262d;--border:#30363d;--text:#e6edf3;--text2:#8b949e;--accent:#58a6ff;--green:#3fb950;--yellow:#d29922;--red:#f85149;--pink:#e94560;--blue:#4361ee}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text)}}
.header{{background:var(--surface);border-bottom:1px solid var(--border);padding:24px 32px}}
.header h1{{font-size:24px;font-weight:600}}
.header p{{color:var(--text2);margin-top:4px;font-size:14px}}
.header a{{color:var(--accent);text-decoration:none}}
.container{{max-width:1400px;margin:0 auto;padding:24px}}
.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:16px;margin-bottom:24px}}
.stat{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}}
.stat .value{{font-size:28px;font-weight:700;color:var(--accent)}}
.stat .label{{font-size:13px;color:var(--text2);margin-top:4px}}
.tabs{{display:flex;gap:0;margin-bottom:24px;border-bottom:2px solid var(--border)}}
.tab{{padding:12px 24px;cursor:pointer;font-size:14px;font-weight:600;color:var(--text2);border-bottom:3px solid transparent;margin-bottom:-2px;transition:all 0.2s}}
.tab:hover{{color:var(--text)}}
.tab.active{{color:var(--text);border-bottom-color:var(--pink)}}
.tab .count{{background:var(--surface2);color:var(--text2);padding:1px 8px;border-radius:10px;font-size:12px;margin-left:6px}}
.tab.active .count{{background:var(--pink);color:white}}
.tab-panel{{display:none}}
.tab-panel.active{{display:block}}
.design-card{{background:var(--surface);border:1px solid var(--border);border-radius:12px;margin-bottom:24px;overflow:hidden}}
.card-header{{background:var(--surface2);color:var(--text);padding:16px 24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
.card-header h2{{font-size:16px;font-weight:600}}
.rank-badge{{background:var(--pink);color:white;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600}}
.score-badge{{background:var(--blue);color:white;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600}}
.score-partial{{background:#e67e22;border:2px dashed rgba(255,255,255,0.5)}}
.score-help{{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:rgba(255,255,255,0.3);font-size:10px;font-weight:700;cursor:pointer;margin-left:4px;vertical-align:middle}}
.score-help:hover{{background:rgba(255,255,255,0.5)}}
#score-tooltip{{display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:var(--surface);color:var(--text);padding:24px;border-radius:12px;border:1px solid var(--border);box-shadow:0 8px 32px rgba(0,0,0,0.6);z-index:10000;max-width:480px;font-size:13px;line-height:1.6}}
#score-tooltip.visible{{display:block}}
#score-tooltip h3{{color:var(--text);margin:0 0 12px 0;font-size:15px}}
#score-tooltip table{{width:100%;border-collapse:collapse;margin:8px 0}}
#score-tooltip th,#score-tooltip td{{text-align:left;padding:4px 8px;border-bottom:1px solid var(--border)}}
#score-tooltip th{{color:var(--text2);font-weight:400;font-size:12px}}
#score-tooltip td{{color:var(--text)}}
#score-tooltip .formula{{color:var(--accent);font-weight:600}}
#score-tooltip .note{{color:var(--text2);font-size:11px;margin-top:8px}}
#score-tooltip .close-btn{{position:absolute;top:8px;right:12px;color:var(--text2);cursor:pointer;font-size:18px;background:none;border:none}}
#score-tooltip .close-btn:hover{{color:var(--text)}}
.tool-badge{{color:white;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;margin-left:4px}}
.card-body{{display:grid;grid-template-columns:1fr 1fr;gap:0}}
.viewer-container{{height:500px;position:relative;border-right:1px solid var(--border)}}
.viewer-controls{{position:absolute;bottom:12px;left:12px;z-index:10;display:flex;gap:6px}}
.viewer-controls button{{background:rgba(22,27,34,0.9);border:1px solid var(--border);border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer;color:var(--text2)}}
.viewer-controls button:hover{{background:var(--pink);color:white;border-color:var(--pink)}}
.viewer-controls button.active{{background:var(--pink);color:white;border-color:var(--pink)}}
.metrics-panel{{padding:20px;overflow-y:auto;max-height:500px}}
.metric-row{{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--border)}}
.metric-label{{color:var(--text2);font-size:13px}}
.metric-value{{font-weight:600;font-size:13px}}
.metric-value.good{{color:var(--green)}}
.metric-value.warn{{color:var(--yellow)}}
.metric-value.bad{{color:var(--red)}}
.sequence-box{{background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:12px;margin-top:12px;font-family:'Courier New',monospace;font-size:12px;word-break:break-all;line-height:1.6;max-height:120px;overflow-y:auto;color:var(--text)}}
.section-label{{font-size:14px;font-weight:600;color:var(--text);margin:14px 0 6px}}
.ss-bar{{display:flex;height:18px;border-radius:4px;overflow:hidden;margin-top:4px}}
.ss-bar .helix{{background:var(--pink)}}.ss-bar .sheet{{background:var(--blue)}}.ss-bar .loop{{background:var(--surface2)}}
.legend{{display:flex;gap:16px;margin-top:6px;font-size:12px;color:var(--text2)}}
.legend span::before{{content:'';display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle}}
.legend .h::before{{background:var(--pink)}}.legend .s::before{{background:var(--blue)}}.legend .l::before{{background:var(--surface2)}}
.seq-legend{{position:absolute;top:12px;left:12px;z-index:10;background:rgba(22,27,34,0.95);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:11px;display:none;box-shadow:0 1px 4px rgba(0,0,0,0.4)}}
.seq-legend.visible{{display:block}}
.seq-legend-item{{display:inline-block;margin-right:10px}}
.seq-legend-dot{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:3px;vertical-align:middle}}
.tab-quote{{font-style:italic;color:var(--text);font-size:15px;line-height:1.6;padding:16px 0 20px;border-bottom:1px solid var(--border);margin-bottom:20px;max-width:720px}}
.tab-quote .tq-attr{{color:var(--text2);font-size:13px;margin-top:4px;display:block}}
.empty{{text-align:center;padding:48px;color:var(--text2);font-size:16px}}
.nav-links{{margin-top:8px;font-size:13px}}
.nav-links a{{color:var(--accent);margin-right:16px}}
/* Table view */
.table-wrap{{overflow-x:auto;margin-top:8px}}
.design-table{{width:100%;border-collapse:collapse;font-size:13px;background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden}}
.design-table th{{background:var(--surface2);color:var(--text);padding:10px 12px;text-align:left;font-weight:600;cursor:pointer;white-space:nowrap;user-select:none;position:sticky;top:0;border-bottom:1px solid var(--border)}}
.design-table th:hover{{background:#2d333b}}
.design-table th .sort-arrow{{margin-left:4px;font-size:10px;opacity:0.5}}
.design-table th.sorted .sort-arrow{{opacity:1}}
.design-table td{{padding:8px 12px;border-bottom:1px solid var(--border);white-space:nowrap}}
.design-table tr:hover td{{background:rgba(88,166,255,0.06)}}
.design-table td.good{{color:var(--green);font-weight:600}}
.design-table td.warn{{color:var(--yellow);font-weight:600}}
.design-table td.bad{{color:var(--red);font-weight:600}}
.design-table td.partial{{background:rgba(219,109,40,0.1);border-left:3px solid #e67e22}}
.table-filter{{margin-bottom:12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
.table-filter input{{padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:13px;width:240px;background:var(--surface2);color:var(--text)}}
.table-filter select{{padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:13px;background:var(--surface2);color:var(--text)}}
.strat-card{{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:12px}}
.strat-toggle{{background:rgba(22,27,34,0.9);border:1px solid var(--border);border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer;color:var(--text2)}}
.strat-toggle:hover{{opacity:0.8}}
.strat-toggle.active{{font-weight:700;color:var(--text)}}
.strat-toggle:not(.active){{opacity:0.5}}
.hotspot-tag{{display:inline-block;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:600}}
/* Progress chart */
.progress-controls{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:16px}}
.progress-controls select{{padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:13px;background:var(--surface2);color:var(--text)}}
.progress-controls label{{font-size:13px;color:var(--text2);font-weight:600}}
.chart-container{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px;position:relative;height:420px}}
.progress-stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:16px}}
.progress-stat{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 16px}}
.progress-stat .ps-label{{font-size:12px;color:var(--text2)}}
.progress-stat .ps-value{{font-size:20px;font-weight:700;color:var(--accent)}}
/* Workflow tab — cyberpunk flowchart */
.wf-wrap{{padding:24px 0;max-width:880px;margin:0 auto;position:relative}}
.wf-title{{text-align:center;margin-bottom:32px}}
.wf-title h2{{font-size:20px;font-weight:700;background:linear-gradient(90deg,#39ff14,#00e5ff,#f54880);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;letter-spacing:3px;text-transform:uppercase}}
.wf-title p{{color:var(--text2);font-size:12px;margin-top:4px;font-family:'SF Mono',Monaco,monospace}}
.wf-canvas{{position:relative;min-height:520px}}
.wf-canvas svg{{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:0}}
.wf-box{{background:var(--surface);border:2px solid;border-radius:6px;padding:12px 16px;position:absolute;width:280px;transition:all 0.2s;cursor:default;z-index:1}}
.wf-box:hover{{box-shadow:0 0 28px currentColor;transform:scale(1.02)}}
.wf-box .wf-label{{font-size:14px;font-weight:700;letter-spacing:1px;text-transform:uppercase}}
.wf-box .wf-desc{{font-size:11px;color:var(--text2);margin-top:4px;line-height:1.5}}
.wf-box .wf-skills{{margin-top:6px;display:flex;gap:4px;flex-wrap:wrap}}
.wf-box .wf-skill{{background:var(--surface2);border:1px solid var(--border);border-radius:4px;padding:1px 6px;font-size:9px;font-family:'SF Mono',Monaco,monospace;color:var(--text2)}}
.wf-box .wf-tag{{position:absolute;top:-8px;right:-8px;background:var(--bg);border:1px solid;border-radius:8px;padding:1px 6px;font-size:9px;font-weight:700}}
.wf-g{{color:#39ff14;border-color:#39ff14}}.wf-c{{color:#00e5ff;border-color:#00e5ff}}.wf-p{{color:#f54880;border-color:#f54880}}.wf-o{{color:#f0883e;border-color:#f0883e}}.wf-v{{color:#bc8cff;border-color:#bc8cff}}.wf-y{{color:#d29922;border-color:#d29922}}
.wf-glow-g{{filter:drop-shadow(0 0 4px #39ff1480)}}.wf-glow-o{{filter:drop-shadow(0 0 4px #f0883e80)}}.wf-glow-p{{filter:drop-shadow(0 0 4px #f5488080)}}.wf-glow-c{{filter:drop-shadow(0 0 4px #00e5ff80)}}
@media(max-width:700px){{.wf-box{{position:relative;width:100%;margin-bottom:16px;left:0!important;top:0!important}}.wf-canvas{{min-height:auto}}.wf-canvas svg{{display:none}}}}
@media(max-width:900px){{.card-body{{grid-template-columns:1fr}}.viewer-container{{border-right:none;border-bottom:1px solid var(--border)}}}}
</style>
</head>
<body>
<div id="score-tooltip">
  <button class="close-btn" onclick="this.parentElement.classList.remove('visible')">&times;</button>
  <h3>Composite Score</h3>
  <p>Weighted average of available metrics, normalised to [0, 1]. Only metrics present for a design contribute; weights are re-normalised accordingly.</p>
  <table>
    <tr><th>Metric</th><th>Weight</th><th>Transform</th></tr>
    <tr><td>Design ipTM</td><td class="formula">0.25</td><td>raw (0&ndash;1)</td></tr>
    <tr><td>Min iPAE</td><td class="formula">0.25</td><td>1 &minus; PAE/10 (lower better)</td></tr>
    <tr><td>Xval ipTM (Boltz-2)</td><td class="formula">0.20</td><td>raw (0&ndash;1)</td></tr>
    <tr><td>Refold RMSD</td><td class="formula">0.15</td><td>1 &minus; RMSD/5 (lower better)</td></tr>
    <tr><td>Design pTM</td><td class="formula">0.10</td><td>raw (0&ndash;1)</td></tr>
    <tr><td>Xval pLDDT (Boltz-2)</td><td class="formula">0.10</td><td>pLDDT/100 (0&ndash;1)</td></tr>
    <tr><td>Design RMSD</td><td class="formula">0.05</td><td>1 &minus; RMSD/5 (lower better)</td></tr>
  </table>
  <p class="note">Weights sum to 1.10 when all metrics are available. The score is normalised by the sum of weights of metrics actually present for each design.</p>
  <p class="note"><span style="display:inline-block;background:#e67e22;color:white;padding:2px 8px;border-radius:10px;border:2px dashed rgba(255,255,255,0.5);font-size:11px;margin-right:4px">orange</span> = partial score (no Boltz-2 cross-validation). Not directly comparable to fully evaluated designs.</p>
</div>
<div class="header">
  <h1>In Silico PGDH</h1>
  <p>Protein binder design campaign &mdash; Target: 2GDZ (15-hydroxyprostaglandin dehydrogenase)</p>
  <div class="nav-links">
    <a href="production_runs.html">Production Runs (legacy)</a>
    <a href="designs_viewer.html">Initial Designs (legacy)</a>
  </div>
  <p style="font-style:italic;color:var(--text);margin-top:12px;font-size:15px;max-width:640px;line-height:1.6">&ldquo;There is no such thing as either man or nature now, only a process that produces the one within the other and couples the machines together.&rdquo;<br><span style="color:var(--text2);font-size:13px">&mdash; Gilles Deleuze &amp; F&eacute;lix Guattari, <em>Anti-Oedipus</em></span></p>
</div>
<div class="container">
<div class="summary">
  <div class="stat"><div class="value">{len(all_designs)}</div><div class="label">Total Designs</div></div>
  <div class="stat"><div class="value">{len(evaluated)}</div><div class="label">Evaluated</div></div>
  <div class="stat"><div class="value">{len(unevaluated)}</div><div class="label">Unevaluated</div></div>
  <div class="stat"><div class="value">{n_bg}</div><div class="label">BoltzGen</div></div>
  <div class="stat"><div class="value">{n_rfd}</div><div class="label">RFdiffusion3</div></div>
</div>
<div class="tabs">
  <div class="tab active" onclick="switchTab('evaluated')">Evaluated<span class="count">{len(evaluated)}</span></div>
  <div class="tab" onclick="switchTab('unevaluated')">Unevaluated<span class="count">{len(unevaluated)}</span></div>
  <div class="tab" onclick="switchTab('all')">All<span class="count">{len(all_designs)}</span></div>
  <div class="tab" onclick="switchTab('table')">Table<span class="count">{len(all_designs)}</span></div>
  <div class="tab" onclick="switchTab('progress')">Progress</div>
  <div class="tab" onclick="switchTab('strategies')">Strategies</div>
  <div class="tab" onclick="switchTab('target')">Target (2GDZ)</div>
  <div class="tab" onclick="switchTab('workflow')">Workflow</div>
</div>
<div class="tab-panel active" id="panel_evaluated">
<div class="tab-quote">&ldquo;What is great in man is that he is a bridge and not an end.&rdquo;<span class="tq-attr">&mdash; Friedrich Nietzsche, <em>Thus Spoke Zarathustra</em></span></div>
{eval_cards if eval_cards else '<div class="empty">No evaluated designs yet. Run evaluate_designs.py then generate_pages.py.</div>'}
</div>
<div class="tab-panel" id="panel_unevaluated">
<div class="tab-quote">&ldquo;The simulacrum is never that which conceals the truth &mdash; it is the truth which conceals that there is none.&rdquo;<span class="tq-attr">&mdash; Jean Baudrillard, <em>Simulacra and Simulation</em></span></div>
{uneval_cards if uneval_cards else '<div class="empty">No unevaluated designs.</div>'}
</div>
<div class="tab-panel" id="panel_all">
<div class="tab-quote">&ldquo;A self does not amount to much, but no self is an island; each exists in a fabric of relations that is now more complex and mobile than ever before.&rdquo;<span class="tq-attr">&mdash; Jean-Fran&ccedil;ois Lyotard, <em>The Postmodern Condition</em></span></div>
{all_cards}
</div>
<div class="tab-panel" id="panel_table">
  <div class="tab-quote">&ldquo;Information is information, not matter or energy. No materialism which does not admit this can survive at the present day.&rdquo;<span class="tq-attr">&mdash; Norbert Wiener, <em>Cybernetics</em></span></div>
  <div class="table-filter">
    <input type="text" id="table-search" placeholder="Filter by design ID..." oninput="filterTable()">
    <select id="table-tool-filter" onchange="filterTable()">
      <option value="">All tools</option>
      <option value="boltzgen">BoltzGen</option>
      <option value="rfdiffusion3">RFdiffusion3</option>
    </select>
    <select id="table-strategy-filter" onchange="filterTable()">
      <option value="">All strategies</option>
      <option value="active_site">Active Site</option>
      <option value="dimer_interface">Dimer Interface</option>
      <option value="surface">Surface</option>
    </select>
    <select id="table-round-filter" onchange="filterTable()">
      <option value="">All rounds</option>
    </select>
  </div>
  <div class="table-wrap">
    <table class="design-table" id="design-table">
      <thead><tr>{"".join(f'<th data-col="{col}" data-dir="{d or ""}" onclick="sortTable(this)">{label}<span class="sort-arrow">&#9650;</span></th>' for col, label, d in TABLE_COLUMNS)}</tr></thead>
      <tbody id="table-body"></tbody>
    </table>
  </div>
</div>
"""

    # Progress chart metrics
    PROGRESS_METRICS = [
        ("composite", "Composite Score", "high"),
        ("iptm", "Design ipTM", "high"),
        ("val_iptm", "Xval ipTM (Boltz-2)", "high"),
        ("min_ipae", "Min iPAE", "low"),
        ("bg_rmsd", "Refold RMSD", "low"),
        ("filter_rmsd", "Design RMSD", "low"),
        ("pdockq", "pDockQ", "high"),
        ("bg_iptm", "Refold ipTM", "high"),
        ("val_plddt", "Xval pLDDT", "high"),
    ]
    progress_options = "".join(
        f'<option value="{k}">{label}</option>' for k, label, _ in PROGRESS_METRICS
    )

    html += f"""<div class="tab-panel" id="panel_progress">
  <div class="tab-quote">&ldquo;The organism is not the environment of the genome: it is the genome&rsquo;s way of making more genomes.&rdquo;<span class="tq-attr">&mdash; Richard Dawkins, <em>The Extended Phenotype</em></span></div>
  <div class="progress-controls">
    <label>Metric:</label>
    <select id="progress-metric" onchange="updateProgressChart()">{progress_options}</select>
    <label style="margin-left:12px">Aggregation:</label>
    <select id="progress-agg" onchange="updateProgressChart()">
      <option value="mean">Mean</option>
      <option value="best">Best</option>
      <option value="median">Median</option>
      <option value="count">Count (designs)</option>
    </select>
  </div>
  <div class="chart-container">
    <canvas id="progress-canvas"></canvas>
  </div>
  <div class="progress-stats" id="progress-stats"></div>
</div>
"""

    # Strategy design counts
    strat_counts = {}
    for d in all_designs:
        s = d.get("strategy", "unknown")
        strat_counts[s] = strat_counts.get(s, 0) + 1

    # Strategy cards HTML
    strat_cards_html = ""
    for si in STRATEGY_INFO:
        count = strat_counts.get(si["key"], 0)
        if si["hotspots"]:
            hotspot_tags = " ".join(
                f'<span class="hotspot-tag" style="background:{si["color"]}20;color:{si["color"]};border:1px solid {si["color"]}40">{name}</span>'
                for name, _ in si["hotspots"]
            )
        else:
            hotspot_tags = '<span class="hotspot-tag" style="background:#00CED120;color:#00CED1;border:1px solid #00CED140">Auto-detected</span>'
        strat_cards_html += f"""
        <div class="strat-card" style="border-left:4px solid {si['color']}">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <span class="tool-badge" style="background:{si['color']}">{si['label']}</span>
            <span style="font-size:13px;color:#8b949e">{count} design{"s" if count != 1 else ""}</span>
          </div>
          <p style="font-size:13px;color:#c9d1d9;margin:0 0 8px">{si['desc']}</p>
          <p style="font-size:12px;color:#8b949e;margin:0 0 8px">Binder length: {si['length']}</p>
          <div style="display:flex;flex-wrap:wrap;gap:6px">{hotspot_tags}</div>
        </div>"""

    html += f"""<div class="tab-panel" id="panel_strategies">
<div class="tab-quote">&ldquo;The essence of technology is by no means anything technological.&rdquo;<span class="tq-attr">&mdash; Martin Heidegger, <em>The Question Concerning Technology</em></span></div>
<div class="design-card">
  <div class="card-header"><h2>Binding Strategies</h2><span class="tool-badge" style="background:#4361ee">3 strategies</span></div>
  <div class="card-body">
    <div class="viewer-container" id="viewer_strategies">
      <div class="viewer-controls">
        <button onclick="resetStratView()">Reset</button>
        <button class="strat-toggle active" id="strat_btn_active_site" onclick="toggleStrat('active_site')" style="border-left:3px solid #FF6B6B">Active Site</button>
        <button class="strat-toggle active" id="strat_btn_dimer_interface" onclick="toggleStrat('dimer_interface')" style="border-left:3px solid #9B59B6">Dimer Interface</button>
        <button class="strat-toggle active" id="strat_btn_surface" onclick="toggleStrat('surface')" style="border-left:3px solid #00CED1">Surface</button>
      </div>
    </div>
    <div class="metrics-panel" style="overflow-y:auto;max-height:500px">
      <div class="section-label">Strategies Overview</div>
      {strat_cards_html}
    </div>
  </div>
</div>
</div>
"""

    if target_cif_text:
        html += f"""<div class="tab-panel" id="panel_target">
<div class="tab-quote">&ldquo;The will to power is not a being, not a becoming, but a pathos.&rdquo;<span class="tq-attr">&mdash; Friedrich Nietzsche, <em>The Will to Power</em></span></div>
<div class="design-card">
  <div class="card-header"><h2>15-PGDH Target (2GDZ)</h2><span class="rank-badge" style="background:#4361ee">Reference</span></div>
  <div class="card-body">
    <div class="viewer-container" id="viewer_target"><div class="viewer-controls"><button onclick="resetTargetView()">Reset</button></div></div>
    <div class="metrics-panel">
      <div class="section-label">Hotspots</div>
      {metric_row("Active Site (Red)", "Ser138, Gln148, Tyr151, Lys155, Phe185, Tyr217")}
      {metric_row("Dimer Interface (Blue)", "Ala146, Ala153, Phe161, Leu167, Ala168, Tyr206")}
    </div>
  </div>
</div>
</div>
"""

    html += """<div class="tab-panel" id="panel_workflow">
<div class="tab-quote">&ldquo;We have to go beyond the machine, beyond the mechanistic, to arrive at the machinic.&rdquo;<span class="tq-attr">&mdash; F&eacute;lix Guattari, <em>Chaosmosis</em></span></div>
<div class="wf-wrap">
  <div class="wf-title">
    <h2>// Design Pipeline //</h2>
    <p>&gt; autonomous binder campaign &mdash; human sets strategy, agents execute</p>
  </div>
  <div class="wf-canvas">
    <svg viewBox="0 0 880 520" fill="none" xmlns="http://www.w3.org/2000/svg">
      <!-- Design → Evaluate (top-right to mid-left) -->
      <path d="M 420 75 L 480 75 Q 520 75 520 115 L 520 155 Q 520 195 480 195 L 460 195" stroke="#39ff14" stroke-width="2" class="wf-glow-g"/>
      <polygon points="462,190 462,200 450,195" fill="#39ff14" class="wf-glow-g"/>
      <!-- Evaluate → Analyse (mid-left to bottom-right) -->
      <path d="M 420 195 Q 440 195 440 215 L 440 300 Q 440 330 470 330 L 490 330" stroke="#f0883e" stroke-width="2" class="wf-glow-o"/>
      <polygon points="488,325 488,335 500,330" fill="#f0883e" class="wf-glow-o"/>
      <!-- Analyse → Publish (bottom-right to bottom-center) -->
      <path d="M 500 365 Q 470 365 470 395 L 470 430 Q 470 460 430 460 L 380 460" stroke="#f54880" stroke-width="2" class="wf-glow-p"/>
      <polygon points="382,455 382,465 370,460" fill="#f54880" class="wf-glow-p"/>
      <!-- Analyse → Design (loop back, dashed) -->
      <path d="M 780 340 Q 830 340 830 290 L 830 60 Q 830 20 790 20 L 200 20 Q 160 20 160 55 L 160 60" stroke="#00e5ff" stroke-width="1.5" stroke-dasharray="6 4" class="wf-glow-c"/>
      <polygon points="155,58 165,58 160,70" fill="#00e5ff" class="wf-glow-c"/>
      <text x="450" y="16" fill="#00e5ff" font-size="10" font-family="monospace" text-anchor="middle" opacity="0.7">next round</text>
    </svg>

    <!-- Box 1: DESIGN — top left -->
    <div class="wf-box wf-g" style="left:40px;top:40px">
      <span class="wf-tag wf-g">GPU</span>
      <div class="wf-label">Design</div>
      <div class="wf-desc">Claude invokes <code>/design-round-modal</code> &mdash; submits up to 3 jobs per round via Modal. BoltzGen generates binders with self-consistency filtering; RFdiffusion3 does atomic-level diffusion with hotspot constraints.</div>
      <div class="wf-skills"><span class="wf-skill">BoltzGen</span><span class="wf-skill">RFdiffusion3</span><span class="wf-skill">Modal GPUs</span></div>
    </div>

    <!-- Box 2: EVALUATE — middle, offset right -->
    <div class="wf-box wf-o" style="left:140px;top:155px">
      <span class="wf-tag wf-o">GPU</span>
      <div class="wf-label">Evaluate</div>
      <div class="wf-desc">Three-stage filter. BoltzGen refolds each sequence to check designability (RMSD). Boltz-2 + MSA cross-validates binding independently. ipSAE scores the interface PAE and pDockQ.</div>
      <div class="wf-skills"><span class="wf-skill">BoltzGen fold</span><span class="wf-skill">Boltz-2+MSA</span><span class="wf-skill">ipSAE</span></div>
    </div>

    <!-- Box 3: ANALYSE — bottom right -->
    <div class="wf-box wf-p" style="left:500px;top:290px">
      <span class="wf-tag wf-p">AI</span>
      <div class="wf-label">Analyse</div>
      <div class="wf-desc">Claude runs <code>/propose-designs</code> &mdash; reads all metrics, compares strategies and tools, identifies which hotspots and binder lengths are working, writes a concrete plan for the next round.</div>
      <div class="wf-skills"><span class="wf-skill">/propose-designs</span><span class="wf-skill">composite score</span></div>
    </div>

    <!-- Box 4: PUBLISH — bottom left -->
    <div class="wf-box wf-c" style="left:80px;top:425px">
      <span class="wf-tag wf-c">CPU</span>
      <div class="wf-label">Publish</div>
      <div class="wf-desc">Regenerate this site from ranked designs, push to GitHub Pages. Advance round counter.</div>
      <div class="wf-skills"><span class="wf-skill">generate_pages.py</span><span class="wf-skill">git push</span></div>
    </div>

  </div>
</div>
</div>
"""

    html += f"""
</div>
<script>
var vd={json.dumps(viewer_data)};
var tc={json.dumps(target_cif_text)};
var as={json.dumps([138,148,151,155,185,217])};
var di={json.dumps([146,153,161,167,168,206])};
var tclr={json.dumps(TOOL_COLORS)};
var tableData={json.dumps(table_rows)};
var tableCols={json.dumps([c[0] for c in TABLE_COLUMNS])};
var vs={{}},tv=null,init={{}};
var tableInit=false,curSort=null,curDir=1,progressInit=false,progressChart=null;
function iv(i){{if(init[i])return;var e=document.getElementById('viewer_'+i);if(!e||e.offsetParent===null)return;var d=vd[i];if(!d)return;if(!d.cif){{e.innerHTML='<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#8b949e;font-size:14px">3D view available for top {MAX_CIF_EMBEDS} designs</div>';init[i]=true;return;}}var v=$3Dmol.createViewer(e,{{backgroundColor:'#0d1117'}});v.addModel(d.cif,'cif');var bc=d.binderChain,tc2=bc==='A'?'B':'A',c=tclr[d.tool]||'#00CED1';v.setStyle({{chain:tc2}},{{cartoon:{{color:'#f54880',opacity:0.6}}}});v.setStyle({{chain:bc}},{{cartoon:{{color:c}}}});v.zoomTo();v.render();vs[i]={{viewer:v,binderChain:bc,targetChain:tc2,color:c}};init[i]=true;}}
function itv(){{if(tv)return;var e=document.getElementById('viewer_target');if(!e||e.offsetParent===null)return;tv=$3Dmol.createViewer('viewer_target',{{backgroundColor:'#0d1117'}});tv.addModel(tc,'cif');tv.setStyle({{}},{{cartoon:{{color:'#6e7681',opacity:0.7}}}});tv.setStyle({{chain:'A',resi:as}},{{cartoon:{{color:'#FF4500'}},stick:{{color:'#FF4500'}}}});tv.setStyle({{chain:'A',resi:di}},{{cartoon:{{color:'#1E90FF'}},stick:{{color:'#1E90FF'}}}});tv.zoomTo();tv.render();}}
function ivv(){{for(var i=0;i<vd.length;i++){{var e=document.getElementById('viewer_'+i);if(e&&e.offsetParent!==null&&!init[i])iv(i);}}if(document.getElementById('viewer_target')&&document.getElementById('viewer_target').offsetParent!==null)itv();if(document.getElementById('viewer_strategies')&&document.getElementById('viewer_strategies').offsetParent!==null)initStratViewer();}}
function switchTab(t){{var ts=['evaluated','unevaluated','all','table','progress','strategies','target','workflow'];document.querySelectorAll('.tab').forEach(function(e,i){{e.classList.toggle('active',ts[i]===t);}});document.querySelectorAll('.tab-panel').forEach(function(p){{p.classList.remove('active');}});var p=document.getElementById('panel_'+t);if(p)p.classList.add('active');if(t==='table'&&!tableInit){{renderTable();tableInit=true;}}if(t==='progress'&&!progressInit){{initProgressChart();progressInit=true;}}setTimeout(ivv,100);}}
function clsCell(val,dir){{if(val===''||val===null||val===undefined)return'';var v=parseFloat(val);if(isNaN(v))return'';if(dir==='high')return v>=0.7?'good':v>=0.5?'warn':'bad';if(dir==='low')return v<=2.0?'good':v<=3.0?'warn':'bad';return'';}}
var colDirs={{{",".join(f'"{c[0]}":"{c[2] or ""}"' for c in TABLE_COLUMNS)}}};
function renderTable(){{var tb=document.getElementById('table-body');if(!tb)return;var q=(document.getElementById('table-search')||{{}}).value||'';q=q.toLowerCase();var tf=(document.getElementById('table-tool-filter')||{{}}).value||'';var sf=(document.getElementById('table-strategy-filter')||{{}}).value||'';var rf=(document.getElementById('table-round-filter')||{{}}).value||'';var rows=tableData.filter(function(r){{if(q&&r.design_id.toLowerCase().indexOf(q)<0)return false;if(tf&&r.tool!==tf)return false;if(sf&&r.strategy!==sf)return false;if(rf&&String(r.round||'')!==rf)return false;return true;}});if(curSort){{rows.sort(function(a,b){{var av=a[curSort],bv=b[curSort];var an=parseFloat(av),bn=parseFloat(bv);if(!isNaN(an)&&!isNaN(bn))return(an-bn)*curDir;av=String(av||'');bv=String(bv||'');return av.localeCompare(bv)*curDir;}});}}var h='';rows.forEach(function(r){{h+='<tr>';tableCols.forEach(function(c){{var v=r[c];if(v===''||v===null||v===undefined)v='—';else if(typeof v==='number')v=Number.isInteger(v)?v:parseFloat(v).toFixed(3);var cls=clsCell(r[c],colDirs[c]);if(c==='composite'&&r.score_partial)cls=(cls?cls+' ':'')+'partial';h+='<td'+(cls?' class="'+cls+'"':'')+'>'+(c==='design_id'?'<span title="'+v+'">'+v+'</span>':v)+'</td>';}});h+='</tr>';}});tb.innerHTML=h;}}
function filterTable(){{renderTable();}}
function sortTable(th){{var col=th.getAttribute('data-col');if(curSort===col){{curDir*=-1;}}else{{curSort=col;curDir=1;}}document.querySelectorAll('.design-table th').forEach(function(h){{h.classList.remove('sorted');h.querySelector('.sort-arrow').innerHTML='&#9650;';}});th.classList.add('sorted');th.querySelector('.sort-arrow').innerHTML=curDir>0?'&#9650;':'&#9660;';renderTable();}}
function toggleStyle(i,m){{var v=vs[i];if(!v)return;var vw=v.viewer;vw.removeAllSurfaces();var bc=v.binderChain,tc2=v.targetChain,c=v.color;if(m==='cartoon'){{vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#f54880',opacity:0.6}}}});vw.setStyle({{chain:bc}},{{cartoon:{{color:c}}}});}}else if(m==='surface'){{vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#f54880',opacity:0.5}}}});vw.setStyle({{chain:bc}},{{cartoon:{{color:c,opacity:0.5}}}});vw.addSurface($3Dmol.SurfaceType.VDW,{{opacity:0.6,color:'#f54880'}},{{chain:tc2}});vw.addSurface($3Dmol.SurfaceType.VDW,{{opacity:0.6,color:c}},{{chain:bc}});}}else if(m==='sticks'){{vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#f54880',opacity:0.6}}}});vw.setStyle({{chain:bc}},{{cartoon:{{color:c}}}});vw.addStyle({{chain:bc,within:{{distance:5,sel:{{chain:tc2}}}}}},{{stick:{{color:c}}}});vw.addStyle({{chain:tc2,within:{{distance:5,sel:{{chain:bc}}}}}},{{stick:{{color:'#FF6347'}}}});}}else if(m==='sequence'){{var ac={{'ALA':'#E8860C','VAL':'#E8860C','LEU':'#E8860C','ILE':'#E8860C','MET':'#E8860C','PHE':'#E8860C','TRP':'#E8860C','PRO':'#E8860C','SER':'#2ECC71','THR':'#2ECC71','ASN':'#2ECC71','GLN':'#2ECC71','TYR':'#2ECC71','CYS':'#2ECC71','LYS':'#3498DB','ARG':'#3498DB','HIS':'#3498DB','ASP':'#E74C3C','GLU':'#E74C3C','GLY':'#95A5A6'}};vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#484f58',opacity:0.5}}}});Object.keys(ac).forEach(function(r){{vw.setStyle({{chain:bc,resn:r}},{{cartoon:{{color:ac[r]}},stick:{{color:ac[r]}}}});}});}}vw.render();['cartoon','surface','sticks','sequence'].forEach(function(mm){{var b=document.getElementById('btn_'+mm+'_'+i);if(b)b.classList.toggle('active',mm===m);}});var l=document.getElementById('seqleg_'+i);if(l)l.classList.toggle('visible',m==='sequence');}}
function resetView(i){{if(vs[i]){{vs[i].viewer.zoomTo();vs[i].viewer.render();}}}}
function resetTargetView(){{if(tv){{tv.zoomTo();tv.render();}}}}
var sv=null,stratVis={{active_site:true,dimer_interface:true,surface:true}};
var stratData={{active_site:{{color:'#FF6B6B',resi:[138,148,151,155,185,217]}},dimer_interface:{{color:'#9B59B6',resi:[146,153,161,167,168,171,172,206]}},surface:{{color:'#00CED1',resi:[]}}}};
function initStratViewer(){{if(sv)return;var e=document.getElementById('viewer_strategies');if(!e||e.offsetParent===null)return;if(!tc)return;sv=$3Dmol.createViewer('viewer_strategies',{{backgroundColor:'#0d1117'}});sv.addModel(tc,'cif');applyStratStyles();sv.zoomTo();sv.render();}}
function applyStratStyles(){{if(!sv)return;sv.setStyle({{}},{{cartoon:{{color:'#6e7681',opacity:0.7}}}});for(var k in stratData){{var s=stratData[k];if(s.resi.length>0&&stratVis[k]){{sv.setStyle({{chain:'A',resi:s.resi}},{{cartoon:{{color:s.color}},stick:{{color:s.color}}}});}}}}sv.render();}}
function toggleStrat(k){{stratVis[k]=!stratVis[k];var b=document.getElementById('strat_btn_'+k);if(b)b.classList.toggle('active');applyStratStyles();}}
function resetStratView(){{if(sv){{sv.zoomTo();sv.render();}}}}
var progressMetricDirs={{{",".join(f'"{k}":"{d}"' for k, _, d in PROGRESS_METRICS)}}};
function buildRoundData(){{var byRound={{}};tableData.forEach(function(r){{var rnd=r.round;if(rnd===''||rnd===null||rnd===undefined)return;if(!byRound[rnd])byRound[rnd]=[];byRound[rnd].push(r);}});return byRound;}}
function aggValues(vals,agg,dir){{if(agg==='count')return vals.length;var nums=vals.map(parseFloat).filter(function(v){{return!isNaN(v);}});if(nums.length===0)return null;if(agg==='mean')return nums.reduce(function(a,b){{return a+b;}},0)/nums.length;if(agg==='median'){{nums.sort(function(a,b){{return a-b;}});var m=Math.floor(nums.length/2);return nums.length%2?nums[m]:(nums[m-1]+nums[m])/2;}}if(agg==='best')return dir==='low'?Math.min.apply(null,nums):Math.max.apply(null,nums);return null;}}
function initProgressChart(){{updateProgressChart();}}
function updateProgressChart(){{var metric=document.getElementById('progress-metric').value;var agg=document.getElementById('progress-agg').value;var dir=progressMetricDirs[metric]||'high';var byRound=buildRoundData();var rounds=Object.keys(byRound).map(Number).sort(function(a,b){{return a-b;}});var values=rounds.map(function(r){{var rows=byRound[r];if(agg==='count')return rows.length;var vals=rows.map(function(row){{return row[metric];}}).filter(function(v){{return v!==''&&v!==null&&v!==undefined;}});return aggValues(vals,agg,dir);}});var labels=rounds.map(function(r){{return'Round '+r;}});var ctx=document.getElementById('progress-canvas');if(progressChart)progressChart.destroy();var color='#58a6ff';progressChart=new Chart(ctx,{{type:'line',data:{{labels:labels,datasets:[{{label:agg.charAt(0).toUpperCase()+agg.slice(1)+' '+document.getElementById('progress-metric').selectedOptions[0].text,data:values,borderColor:color,backgroundColor:color+'20',fill:true,tension:0.3,pointRadius:6,pointHoverRadius:9,pointBackgroundColor:color,borderWidth:2.5}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:true,position:'top',labels:{{color:'#e6edf3'}}}}}},scales:{{x:{{grid:{{display:false}},ticks:{{color:'#8b949e'}}}},y:{{beginAtZero:agg==='count',grid:{{color:'#21262d'}},ticks:{{color:'#8b949e'}},title:{{display:true,text:agg==='count'?'Number of designs':document.getElementById('progress-metric').selectedOptions[0].text,color:'#8b949e'}}}}}}}}}});var statsDiv=document.getElementById('progress-stats');if(statsDiv){{var sh='';rounds.forEach(function(r,i){{var v=values[i];var vt=v===null?'—':(agg==='count'?v:parseFloat(v).toFixed(3));sh+='<div class="progress-stat"><div class="ps-label">Round '+r+'</div><div class="ps-value">'+vt+'</div></div>';}});statsDiv.innerHTML=sh;}}}}
(function(){{var rs={{}};tableData.forEach(function(r){{var v=r.round;if(v!==''&&v!==null&&v!==undefined)rs[v]=1;}});var sel=document.getElementById('table-round-filter');if(sel){{Object.keys(rs).sort(function(a,b){{return parseInt(a)-parseInt(b);}}).forEach(function(r){{var o=document.createElement('option');o.value=r;o.textContent='Round '+r;sel.appendChild(o);}});}}}})();
document.addEventListener('DOMContentLoaded',ivv);
document.addEventListener('click',function(e){{var tt=document.getElementById('score-tooltip');if(tt&&tt.classList.contains('visible')&&!tt.contains(e.target)&&!e.target.classList.contains('score-help'))tt.classList.remove('visible');}});
</script>
</body>
</html>"""

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html)
    print(f"\nWrote {OUTPUT_HTML} ({OUTPUT_HTML.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser(description="Generate GitHub Pages site from design data")
    _parser.add_argument("--no-sync", action="store_true",
                         help="Skip S3 sync, use cached docs/data/")
    _parser.add_argument("--designs-dir", type=str, default=None,
                         help="Load designs from a local directory instead of S3 (e.g. pgdh_modal/out/designs/)")
    _args = _parser.parse_args()

    if _args.designs_dir:
        # Override DOCS_DATA to point at the local designs directory
        # and load data from there without S3 sync
        local_dir = Path(_args.designs_dir)
        if not local_dir.exists():
            print(f"Error: {local_dir} does not exist")
            sys.exit(1)
        # Copy local designs into docs/data/ so load_from_docs_data() works
        DOCS_DATA.mkdir(parents=True, exist_ok=True)
        local_index = local_dir / "index.json"
        if local_index.exists():
            index = json.loads(local_index.read_text())
            (DOCS_DATA / "index.json").write_text(json.dumps(index, indent=2))
            evaluated, unevaluated = [], []
            for entry in index.get("designs", []):
                did = entry["design_id"]
                tool = entry.get("tool", "unknown")
                src_dir = local_dir / tool / did
                dest_dir = DOCS_DATA / tool / did
                dest_dir.mkdir(parents=True, exist_ok=True)
                # Copy metrics.json
                src_metrics = src_dir / "metrics.json"
                if src_metrics.exists():
                    metrics = json.loads(src_metrics.read_text())
                    (dest_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
                else:
                    metrics = entry
                # Copy CIF files
                for cif_name in ["designed.cif", "refolded.cif"]:
                    src_cif = src_dir / cif_name
                    if src_cif.exists():
                        (dest_dir / cif_name).write_bytes(src_cif.read_bytes())
                # Classify
                has_eval = (
                    metrics.get("validation") is not None
                    or metrics.get("scoring") is not None
                    or metrics.get("refolding") is not None
                )
                (evaluated if has_eval else unevaluated).append(metrics)
            (DOCS_DATA / "evaluated.json").write_text(json.dumps(evaluated, indent=2))
            (DOCS_DATA / "unevaluated.json").write_text(json.dumps(unevaluated, indent=2))
            print(f"Loaded {len(index.get('designs', []))} designs from {local_dir}")
        build_html(skip_sync=True)
    else:
        build_html(skip_sync=_args.no_sync)
