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

TOOL_COLORS = {
    "boltzgen": "#3498DB",
    "rfdiffusion3": "#E67E22",
    "custom": "#2ECC71",
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
                "binder_chain": "B" if tool == "boltzgen" else "A",
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
                "binder_chain": "B", "metrics": matched or {}, "status": "designed",
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
    score_html = f'<span class="score-badge">Score: {composite:.3f} <span class="score-help" onclick="event.stopPropagation();document.getElementById(\'score-tooltip\').classList.toggle(\'visible\')">?</span></span>' if composite else ""
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
        card += '<div class="section-label">Backbone Only</div><div class="sequence-box" style="color:#999">Use ProteinMPNN for inverse folding.</div>'

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
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;color:#333}}
.header{{background:#1a1a2e;color:white;padding:24px 32px}}
.header h1{{font-size:24px;font-weight:600}}
.header p{{color:#aaa;margin-top:4px;font-size:14px}}
.header a{{color:#4361ee;text-decoration:none}}
.container{{max-width:1400px;margin:0 auto;padding:24px}}
.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:16px;margin-bottom:24px}}
.stat{{background:white;border-radius:8px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,0.1)}}
.stat .value{{font-size:28px;font-weight:700;color:#1a1a2e}}
.stat .label{{font-size:13px;color:#888;margin-top:4px}}
.tabs{{display:flex;gap:0;margin-bottom:24px;border-bottom:2px solid #ddd}}
.tab{{padding:12px 24px;cursor:pointer;font-size:14px;font-weight:600;color:#666;border-bottom:3px solid transparent;margin-bottom:-2px;transition:all 0.2s}}
.tab:hover{{color:#333}}
.tab.active{{color:#1a1a2e;border-bottom-color:#e94560}}
.tab .count{{background:#eee;color:#666;padding:1px 8px;border-radius:10px;font-size:12px;margin-left:6px}}
.tab.active .count{{background:#e94560;color:white}}
.tab-panel{{display:none}}
.tab-panel.active{{display:block}}
.design-card{{background:white;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.08);margin-bottom:24px;overflow:hidden}}
.card-header{{background:#16213e;color:white;padding:16px 24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
.card-header h2{{font-size:16px;font-weight:600}}
.rank-badge{{background:#e94560;color:white;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600}}
.score-badge{{background:#4361ee;color:white;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600}}
.score-help{{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:rgba(255,255,255,0.3);font-size:10px;font-weight:700;cursor:pointer;margin-left:4px;vertical-align:middle}}
.score-help:hover{{background:rgba(255,255,255,0.5)}}
#score-tooltip{{display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#1a1a2e;color:#e0e0e0;padding:24px;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,0.4);z-index:10000;max-width:480px;font-size:13px;line-height:1.6}}
#score-tooltip.visible{{display:block}}
#score-tooltip h3{{color:white;margin:0 0 12px 0;font-size:15px}}
#score-tooltip table{{width:100%;border-collapse:collapse;margin:8px 0}}
#score-tooltip th,#score-tooltip td{{text-align:left;padding:4px 8px;border-bottom:1px solid #333}}
#score-tooltip th{{color:#a0a0a0;font-weight:400;font-size:12px}}
#score-tooltip td{{color:white}}
#score-tooltip .formula{{color:#4361ee;font-weight:600}}
#score-tooltip .note{{color:#a0a0a0;font-size:11px;margin-top:8px}}
#score-tooltip .close-btn{{position:absolute;top:8px;right:12px;color:#888;cursor:pointer;font-size:18px;background:none;border:none}}
#score-tooltip .close-btn:hover{{color:white}}
.tool-badge{{color:white;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;margin-left:4px}}
.card-body{{display:grid;grid-template-columns:1fr 1fr;gap:0}}
.viewer-container{{height:500px;position:relative;border-right:1px solid #eee}}
.viewer-controls{{position:absolute;bottom:12px;left:12px;z-index:10;display:flex;gap:6px}}
.viewer-controls button{{background:rgba(255,255,255,0.9);border:1px solid #ddd;border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer}}
.viewer-controls button:hover{{background:#e94560;color:white;border-color:#e94560}}
.viewer-controls button.active{{background:#e94560;color:white;border-color:#e94560}}
.metrics-panel{{padding:20px;overflow-y:auto;max-height:500px}}
.metric-row{{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #f0f0f0}}
.metric-label{{color:#666;font-size:13px}}
.metric-value{{font-weight:600;font-size:13px}}
.metric-value.good{{color:#27ae60}}
.metric-value.warn{{color:#f39c12}}
.metric-value.bad{{color:#e74c3c}}
.sequence-box{{background:#f8f9fa;border:1px solid #e9ecef;border-radius:6px;padding:12px;margin-top:12px;font-family:'Courier New',monospace;font-size:12px;word-break:break-all;line-height:1.6;max-height:120px;overflow-y:auto}}
.section-label{{font-size:14px;font-weight:600;color:#1a1a2e;margin:14px 0 6px}}
.ss-bar{{display:flex;height:18px;border-radius:4px;overflow:hidden;margin-top:4px}}
.ss-bar .helix{{background:#e94560}}.ss-bar .sheet{{background:#4361ee}}.ss-bar .loop{{background:#ddd}}
.legend{{display:flex;gap:16px;margin-top:6px;font-size:12px;color:#888}}
.legend span::before{{content:'';display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle}}
.legend .h::before{{background:#e94560}}.legend .s::before{{background:#4361ee}}.legend .l::before{{background:#ddd}}
.seq-legend{{position:absolute;top:12px;left:12px;z-index:10;background:rgba(255,255,255,0.92);border-radius:6px;padding:8px 12px;font-size:11px;display:none;box-shadow:0 1px 4px rgba(0,0,0,0.15)}}
.seq-legend.visible{{display:block}}
.seq-legend-item{{display:inline-block;margin-right:10px}}
.seq-legend-dot{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:3px;vertical-align:middle}}
.empty{{text-align:center;padding:48px;color:#999;font-size:16px}}
.nav-links{{margin-top:8px;font-size:13px}}
.nav-links a{{color:#4361ee;margin-right:16px}}
/* Table view */
.table-wrap{{overflow-x:auto;margin-top:8px}}
.design-table{{width:100%;border-collapse:collapse;font-size:13px;background:white;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1)}}
.design-table th{{background:#16213e;color:white;padding:10px 12px;text-align:left;font-weight:600;cursor:pointer;white-space:nowrap;user-select:none;position:sticky;top:0}}
.design-table th:hover{{background:#1a2744}}
.design-table th .sort-arrow{{margin-left:4px;font-size:10px;opacity:0.5}}
.design-table th.sorted .sort-arrow{{opacity:1}}
.design-table td{{padding:8px 12px;border-bottom:1px solid #f0f0f0;white-space:nowrap}}
.design-table tr:hover td{{background:#f8f9ff}}
.design-table td.good{{color:#27ae60;font-weight:600}}
.design-table td.warn{{color:#f39c12;font-weight:600}}
.design-table td.bad{{color:#e74c3c;font-weight:600}}
.table-filter{{margin-bottom:12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
.table-filter input{{padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:13px;width:240px}}
.table-filter select{{padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:13px}}
@media(max-width:900px){{.card-body{{grid-template-columns:1fr}}.viewer-container{{border-right:none;border-bottom:1px solid #eee}}}}
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
</div>
<div class="header">
  <h1>In Silico PGDH</h1>
  <p>Protein binder design campaign &mdash; Target: 2GDZ (15-hydroxyprostaglandin dehydrogenase)</p>
  <div class="nav-links">
    <a href="production_runs.html">Production Runs (legacy)</a>
    <a href="designs_viewer.html">Initial Designs (legacy)</a>
  </div>
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
  <div class="tab" onclick="switchTab('target')">Target (2GDZ)</div>
</div>
<div class="tab-panel active" id="panel_evaluated">
{eval_cards if eval_cards else '<div class="empty">No evaluated designs yet. Run evaluate_designs.py then generate_pages.py.</div>'}
</div>
<div class="tab-panel" id="panel_unevaluated">
{uneval_cards if uneval_cards else '<div class="empty">No unevaluated designs.</div>'}
</div>
<div class="tab-panel" id="panel_all">
{all_cards}
</div>
<div class="tab-panel" id="panel_table">
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

    if target_cif_text:
        html += f"""<div class="tab-panel" id="panel_target">
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
var tableInit=false,curSort=null,curDir=1;
function iv(i){{if(init[i])return;var e=document.getElementById('viewer_'+i);if(!e||e.offsetParent===null)return;var d=vd[i];if(!d)return;if(!d.cif){{e.innerHTML='<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#999;font-size:14px">3D view available for top {MAX_CIF_EMBEDS} designs</div>';init[i]=true;return;}}var v=$3Dmol.createViewer(e,{{backgroundColor:'white'}});v.addModel(d.cif,'cif');var bc=d.binderChain,tc2=bc==='A'?'B':'A',c=tclr[d.tool]||'#00CED1';v.setStyle({{chain:tc2}},{{cartoon:{{color:'#999',opacity:0.8}}}});v.setStyle({{chain:bc}},{{cartoon:{{color:c}}}});v.zoomTo();v.render();vs[i]={{viewer:v,binderChain:bc,targetChain:tc2,color:c}};init[i]=true;}}
function itv(){{if(tv)return;var e=document.getElementById('viewer_target');if(!e||e.offsetParent===null)return;tv=$3Dmol.createViewer('viewer_target',{{backgroundColor:'white'}});tv.addModel(tc,'cif');tv.setStyle({{}},{{cartoon:{{color:'#CCC',opacity:0.7}}}});tv.setStyle({{chain:'A',resi:as}},{{cartoon:{{color:'#FF4500'}},stick:{{color:'#FF4500'}}}});tv.setStyle({{chain:'A',resi:di}},{{cartoon:{{color:'#1E90FF'}},stick:{{color:'#1E90FF'}}}});tv.zoomTo();tv.render();}}
function ivv(){{for(var i=0;i<vd.length;i++){{var e=document.getElementById('viewer_'+i);if(e&&e.offsetParent!==null&&!init[i])iv(i);}}if(document.getElementById('viewer_target')&&document.getElementById('viewer_target').offsetParent!==null)itv();}}
function switchTab(t){{var ts=['evaluated','unevaluated','all','table','target'];document.querySelectorAll('.tab').forEach(function(e,i){{e.classList.toggle('active',ts[i]===t);}});document.querySelectorAll('.tab-panel').forEach(function(p){{p.classList.remove('active');}});var p=document.getElementById('panel_'+t);if(p)p.classList.add('active');if(t==='table'&&!tableInit){{renderTable();tableInit=true;}}setTimeout(ivv,100);}}
function clsCell(val,dir){{if(val===''||val===null||val===undefined)return'';var v=parseFloat(val);if(isNaN(v))return'';if(dir==='high')return v>=0.7?'good':v>=0.5?'warn':'bad';if(dir==='low')return v<=2.0?'good':v<=3.0?'warn':'bad';return'';}}
var colDirs={{{",".join(f'"{c[0]}":"{c[2] or ""}"' for c in TABLE_COLUMNS)}}};
function renderTable(){{var tb=document.getElementById('table-body');if(!tb)return;var q=(document.getElementById('table-search')||{{}}).value||'';q=q.toLowerCase();var tf=(document.getElementById('table-tool-filter')||{{}}).value||'';var sf=(document.getElementById('table-strategy-filter')||{{}}).value||'';var rf=(document.getElementById('table-round-filter')||{{}}).value||'';var rows=tableData.filter(function(r){{if(q&&r.design_id.toLowerCase().indexOf(q)<0)return false;if(tf&&r.tool!==tf)return false;if(sf&&r.strategy!==sf)return false;if(rf&&String(r.round||'')!==rf)return false;return true;}});if(curSort){{rows.sort(function(a,b){{var av=a[curSort],bv=b[curSort];var an=parseFloat(av),bn=parseFloat(bv);if(!isNaN(an)&&!isNaN(bn))return(an-bn)*curDir;av=String(av||'');bv=String(bv||'');return av.localeCompare(bv)*curDir;}});}}var h='';rows.forEach(function(r){{h+='<tr>';tableCols.forEach(function(c){{var v=r[c];if(v===''||v===null||v===undefined)v='—';else if(typeof v==='number')v=Number.isInteger(v)?v:parseFloat(v).toFixed(3);var cls=clsCell(r[c],colDirs[c]);h+='<td'+(cls?' class="'+cls+'"':'')+'>'+(c==='design_id'?'<span title="'+v+'">'+v+'</span>':v)+'</td>';}});h+='</tr>';}});tb.innerHTML=h;}}
function filterTable(){{renderTable();}}
function sortTable(th){{var col=th.getAttribute('data-col');if(curSort===col){{curDir*=-1;}}else{{curSort=col;curDir=1;}}document.querySelectorAll('.design-table th').forEach(function(h){{h.classList.remove('sorted');h.querySelector('.sort-arrow').innerHTML='&#9650;';}});th.classList.add('sorted');th.querySelector('.sort-arrow').innerHTML=curDir>0?'&#9650;':'&#9660;';renderTable();}}
function toggleStyle(i,m){{var v=vs[i];if(!v)return;var vw=v.viewer;vw.removeAllSurfaces();var bc=v.binderChain,tc2=v.targetChain,c=v.color;if(m==='cartoon'){{vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#999',opacity:0.8}}}});vw.setStyle({{chain:bc}},{{cartoon:{{color:c}}}});}}else if(m==='surface'){{vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#999',opacity:0.5}}}});vw.setStyle({{chain:bc}},{{cartoon:{{color:c,opacity:0.5}}}});vw.addSurface($3Dmol.SurfaceType.VDW,{{opacity:0.6,color:'#999'}},{{chain:tc2}});vw.addSurface($3Dmol.SurfaceType.VDW,{{opacity:0.6,color:c}},{{chain:bc}});}}else if(m==='sticks'){{vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#999',opacity:0.8}}}});vw.setStyle({{chain:bc}},{{cartoon:{{color:c}}}});vw.addStyle({{chain:bc,within:{{distance:5,sel:{{chain:tc2}}}}}},{{stick:{{color:c}}}});vw.addStyle({{chain:tc2,within:{{distance:5,sel:{{chain:bc}}}}}},{{stick:{{color:'#FF6347'}}}});}}else if(m==='sequence'){{var ac={{'ALA':'#E8860C','VAL':'#E8860C','LEU':'#E8860C','ILE':'#E8860C','MET':'#E8860C','PHE':'#E8860C','TRP':'#E8860C','PRO':'#E8860C','SER':'#2ECC71','THR':'#2ECC71','ASN':'#2ECC71','GLN':'#2ECC71','TYR':'#2ECC71','CYS':'#2ECC71','LYS':'#3498DB','ARG':'#3498DB','HIS':'#3498DB','ASP':'#E74C3C','GLU':'#E74C3C','GLY':'#95A5A6'}};vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#DDD',opacity:0.5}}}});Object.keys(ac).forEach(function(r){{vw.setStyle({{chain:bc,resn:r}},{{cartoon:{{color:ac[r]}},stick:{{color:ac[r]}}}});}});}}vw.render();['cartoon','surface','sticks','sequence'].forEach(function(mm){{var b=document.getElementById('btn_'+mm+'_'+i);if(b)b.classList.toggle('active',mm===m);}});var l=document.getElementById('seqleg_'+i);if(l)l.classList.toggle('visible',m==='sequence');}}
function resetView(i){{if(vs[i]){{vs[i].viewer.zoomTo();vs[i].viewer.render();}}}}
function resetTargetView(){{if(tv){{tv.zoomTo();tv.render();}}}}
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
