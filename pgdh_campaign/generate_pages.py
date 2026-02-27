#!/usr/bin/env python3
"""Generate the GitHub Pages site from S3-synced data.

Reads from docs/data/ (written by sync_to_pages.py) and generates docs/index.html
with Evaluated and Unevaluated tabs, 3Dmol.js viewers, and metrics panels.

Falls back to pgdh_campaign/out/ (legacy local data) if docs/data/ doesn't exist.

The original generate_viewer.py is preserved as a fallback. This script replaces
docs/index.html (the landing page) with the full design viewer.

Usage:
    python pgdh_campaign/sync_to_pages.py   # sync from S3 first
    python pgdh_campaign/generate_pages.py  # then regenerate HTML
"""

import csv
import gzip
import json
from pathlib import Path

BASE = Path(__file__).parent
DOCS_DATA = BASE.parent / "docs" / "data"
STRUCTURES_DIR = BASE / "structures"
OUTPUT_HTML = BASE.parent / "docs" / "index.html"

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
                "cif": cif_path.read_text(), "sequence": seq,
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
    score_html = f'<span class="score-badge">{composite:.3f}</span>' if composite else ""

    card = f"""
<div class="design-card">
  <div class="card-header">
    <h2>{d['name']}</h2>
    <div>{rank_html} {score_html}
      <span class="tool-badge" style="background:{tool_color}">{tool}</span>
      <span class="tool-badge" style="background:{strat_meta['color']}">{strat_meta['label']}</span>
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
        card += '<div class="section-label">Scoring (ipSAE)</div>'
        if scr.get("ipsae"):
            card += metric_row("ipSAE", scr["ipsae"], cls_high(scr["ipsae"], 0.70, 0.61))
        if scr.get("pdockq"):
            card += metric_row("pDockQ", scr["pdockq"], cls_high(scr["pdockq"], 0.50, 0.23))

    refold = d.get("refolding") or {}
    if refold and refold.get("rmsd"):
        card += '<div class="section-label">Designability</div>'
        card += metric_row("Refold RMSD", f"{refold['rmsd']} &Aring;", cls_low(refold["rmsd"], 2.0, 2.5))

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


def build_html():
    if (DOCS_DATA / "index.json").exists():
        print("Loading from docs/data/ (synced from S3)...")
        evaluated, unevaluated = load_from_docs_data()
    else:
        print("No docs/data/. Loading from pgdh_campaign/out/ (local)...")
        evaluated, unevaluated = load_from_local()

    all_designs = evaluated + unevaluated
    print(f"  {len(evaluated)} evaluated, {len(unevaluated)} unevaluated")

    if not all_designs:
        print("No designs found. Run sync_to_pages.py first.")
        return

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

    # Viewer data for JS
    viewer_data = []
    for d in evaluated + unevaluated + all_designs:
        viewer_data.append({
            "cif": d["cif"], "name": d["name"],
            "binderChain": d.get("binder_chain", "B"),
            "tool": d.get("tool", "unknown"),
        })

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
@media(max-width:900px){{.card-body{{grid-template-columns:1fr}}.viewer-container{{border-right:none;border-bottom:1px solid #eee}}}}
</style>
</head>
<body>
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
  <div class="tab" onclick="switchTab('target')">Target (2GDZ)</div>
</div>
<div class="tab-panel active" id="panel_evaluated">
{eval_cards if eval_cards else '<div class="empty">No evaluated designs yet. Run evaluate_designs.py then sync_to_pages.py.</div>'}
</div>
<div class="tab-panel" id="panel_unevaluated">
{uneval_cards if uneval_cards else '<div class="empty">No unevaluated designs.</div>'}
</div>
<div class="tab-panel" id="panel_all">
{all_cards}
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
var vs={{}},tv=null,init={{}};
function iv(i){{if(init[i])return;var e=document.getElementById('viewer_'+i);if(!e||e.offsetParent===null)return;var d=vd[i];if(!d)return;var v=$3Dmol.createViewer(e,{{backgroundColor:'white'}});v.addModel(d.cif,'cif');var bc=d.binderChain,tc2=bc==='A'?'B':'A',c=tclr[d.tool]||'#00CED1';v.setStyle({{chain:tc2}},{{cartoon:{{color:'#999',opacity:0.8}}}});v.setStyle({{chain:bc}},{{cartoon:{{color:c}}}});v.zoomTo();v.render();vs[i]={{viewer:v,binderChain:bc,targetChain:tc2,color:c}};init[i]=true;}}
function itv(){{if(tv)return;var e=document.getElementById('viewer_target');if(!e||e.offsetParent===null)return;tv=$3Dmol.createViewer('viewer_target',{{backgroundColor:'white'}});tv.addModel(tc,'cif');tv.setStyle({{}},{{cartoon:{{color:'#CCC',opacity:0.7}}}});tv.setStyle({{chain:'A',resi:as}},{{cartoon:{{color:'#FF4500'}},stick:{{color:'#FF4500'}}}});tv.setStyle({{chain:'A',resi:di}},{{cartoon:{{color:'#1E90FF'}},stick:{{color:'#1E90FF'}}}});tv.zoomTo();tv.render();}}
function ivv(){{for(var i=0;i<vd.length;i++){{var e=document.getElementById('viewer_'+i);if(e&&e.offsetParent!==null&&!init[i])iv(i);}}if(document.getElementById('viewer_target')&&document.getElementById('viewer_target').offsetParent!==null)itv();}}
function switchTab(t){{var ts=['evaluated','unevaluated','all','target'];document.querySelectorAll('.tab').forEach(function(e,i){{e.classList.toggle('active',ts[i]===t);}});document.querySelectorAll('.tab-panel').forEach(function(p){{p.classList.remove('active');}});var p=document.getElementById('panel_'+t);if(p)p.classList.add('active');setTimeout(ivv,100);}}
function toggleStyle(i,m){{var v=vs[i];if(!v)return;var vw=v.viewer;vw.removeAllSurfaces();var bc=v.binderChain,tc2=v.targetChain,c=v.color;if(m==='cartoon'){{vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#999',opacity:0.8}}}});vw.setStyle({{chain:bc}},{{cartoon:{{color:c}}}});}}else if(m==='surface'){{vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#999',opacity:0.5}}}});vw.setStyle({{chain:bc}},{{cartoon:{{color:c,opacity:0.5}}}});vw.addSurface($3Dmol.SurfaceType.VDW,{{opacity:0.6,color:'#999'}},{{chain:tc2}});vw.addSurface($3Dmol.SurfaceType.VDW,{{opacity:0.6,color:c}},{{chain:bc}});}}else if(m==='sticks'){{vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#999',opacity:0.8}}}});vw.setStyle({{chain:bc}},{{cartoon:{{color:c}}}});vw.addStyle({{chain:bc,within:{{distance:5,sel:{{chain:tc2}}}}}},{{stick:{{color:c}}}});vw.addStyle({{chain:tc2,within:{{distance:5,sel:{{chain:bc}}}}}},{{stick:{{color:'#FF6347'}}}});}}else if(m==='sequence'){{var ac={{'ALA':'#E8860C','VAL':'#E8860C','LEU':'#E8860C','ILE':'#E8860C','MET':'#E8860C','PHE':'#E8860C','TRP':'#E8860C','PRO':'#E8860C','SER':'#2ECC71','THR':'#2ECC71','ASN':'#2ECC71','GLN':'#2ECC71','TYR':'#2ECC71','CYS':'#2ECC71','LYS':'#3498DB','ARG':'#3498DB','HIS':'#3498DB','ASP':'#E74C3C','GLU':'#E74C3C','GLY':'#95A5A6'}};vw.setStyle({{chain:tc2}},{{cartoon:{{color:'#DDD',opacity:0.5}}}});Object.keys(ac).forEach(function(r){{vw.setStyle({{chain:bc,resn:r}},{{cartoon:{{color:ac[r]}},stick:{{color:ac[r]}}}});}});}}vw.render();['cartoon','surface','sticks','sequence'].forEach(function(mm){{var b=document.getElementById('btn_'+mm+'_'+i);if(b)b.classList.toggle('active',mm===m);}});var l=document.getElementById('seqleg_'+i);if(l)l.classList.toggle('visible',m==='sequence');}}
function resetView(i){{if(vs[i]){{vs[i].viewer.zoomTo();vs[i].viewer.render();}}}}
function resetTargetView(){{if(tv){{tv.zoomTo();tv.render();}}}}
document.addEventListener('DOMContentLoaded',ivv);
</script>
</body>
</html>"""

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html)
    print(f"\nWrote {OUTPUT_HTML} ({OUTPUT_HTML.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    build_html()
