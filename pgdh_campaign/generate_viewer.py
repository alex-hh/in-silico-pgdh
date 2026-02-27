#!/usr/bin/env python3
"""Generate a standalone HTML page with interactive 3Dmol.js structure viewers
comparing PGDH binder designs across multiple tools and strategies."""

import csv
import gzip
import json
from pathlib import Path

BASE = Path(__file__).parent
STRUCTURES_DIR = BASE / "structures"
OUTPUT_HTML = BASE / "designs_viewer.html"

# Strategy metadata
STRATEGIES = {
    "boltzgen_strategy3": {
        "label": "BoltzGen — Surface",
        "tool": "BoltzGen",
        "strategy": "Strategy 3: Model-free surface",
        "color": "#00CED1",
        "hotspots": "Auto-detected",
    },
    "rfd3_active_site": {
        "label": "RFdiffusion3 — Active Site",
        "tool": "RFdiffusion3",
        "strategy": "Strategy 1: Active site blocker",
        "color": "#FF6B6B",
        "hotspots": "Ser138, Gln148, Tyr151, Lys155, Phe185, Tyr217",
    },
}


def load_boltzgen_designs():
    """Load BoltzGen designs with metrics from CSV."""
    out_dir = BASE / "out" / "boltzgen"
    designs_dir = out_dir / "designs"
    csv_path = out_dir / "all_designs_metrics.csv"

    metrics_by_id = {}
    if csv_path.exists():
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                metrics_by_id[row["id"]] = row

    designs = []
    if designs_dir.exists():
        for cif in sorted(designs_dir.glob("*.cif")):
            matched = None
            for mid, row in metrics_by_id.items():
                if mid in cif.stem:
                    matched = row
                    break

            seq = extract_sequence(cif, "B")
            d = {
                "name": cif.stem,
                "cif": cif.read_text(),
                "sequence": seq,
                "length": len(seq),
                "group": "boltzgen_strategy3",
                "binder_chain": "B",
            }
            if matched:
                d.update({
                    "rank": matched.get("final_rank", "?"),
                    "design_to_target_iptm": matched.get("design_to_target_iptm", ""),
                    "min_design_to_target_pae": matched.get("min_design_to_target_pae", ""),
                    "design_ptm": matched.get("design_ptm", ""),
                    "filter_rmsd": matched.get("filter_rmsd", ""),
                    "plip_hbonds": matched.get("plip_hbonds_refolded", ""),
                    "delta_sasa": matched.get("delta_sasa_refolded", ""),
                    "helix": matched.get("helix", ""),
                    "sheet": matched.get("sheet", ""),
                    "loop": matched.get("loop", ""),
                    "num_design": matched.get("num_design", ""),
                    "num_filters_passed": matched.get("num_filters_passed", ""),
                    "quality_score": matched.get("quality_score", ""),
                    "liability_violations": matched.get("liability_violations_summary", ""),
                })
            designs.append(d)
    return designs


def load_rfd3_designs():
    """Load RFdiffusion3 designs with metrics from JSON."""
    rfd3_dir = BASE / "out" / "rfd3"
    if not rfd3_dir.exists():
        return []

    designs = []
    for jf in sorted(rfd3_dir.glob("*.json")):
        data = json.loads(jf.read_text())
        metrics = data.get("metrics", {})
        spec = data.get("specification", {})

        # Find matching CIF (may be gzipped)
        stem = jf.stem
        cif_path = rfd3_dir / f"{stem}.cif"
        cif_gz = rfd3_dir / f"{stem}.cif.gz"

        if not cif_path.exists() and cif_gz.exists():
            with gzip.open(cif_gz, "rb") as f_in:
                cif_path.write_bytes(f_in.read())

        if not cif_path.exists():
            continue

        # RFD3 outputs: binder is chain A (diffused), target is chain B (fixed)
        # Try both chain orderings
        seq = extract_sequence(cif_path, "A")
        binder_chain = "A"
        if not seq:
            seq = extract_sequence(cif_path, "B")
            binder_chain = "B"

        hotspots = list(spec.get("select_hotspots", {}).keys())
        num_diffused = len(data.get("diffused_index_map", {}))

        d = {
            "name": stem,
            "cif": cif_path.read_text(),
            "sequence": seq,
            "length": len(seq),
            "group": "rfd3_active_site",
            "binder_chain": binder_chain,
            "helix": str(metrics.get("helix_fraction", "")),
            "sheet": str(metrics.get("sheet_fraction", "")),
            "loop": str(metrics.get("loop_fraction", "")),
            "num_design": str(num_diffused),
            "radius_of_gyration": f"{metrics.get('radius_of_gyration', 0):.1f}",
            "max_ca_deviation": f"{metrics.get('max_ca_deviation', 0):.3f}",
            "n_chainbreaks": str(metrics.get("n_chainbreaks", "")),
            "num_ss_elements": str(metrics.get("num_ss_elements", "")),
            "hotspots": ", ".join(hotspots),
            "ala_content": f"{metrics.get('alanine_content', 0):.3f}",
            "gly_content": f"{metrics.get('glycine_content', 0):.3f}",
        }
        designs.append(d)
    return designs


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


def cls_low(val, good, warn):
    """CSS class for lower-is-better metrics."""
    try:
        v = float(val)
        return "good" if v <= good else "warn" if v <= warn else "bad"
    except (ValueError, TypeError):
        return ""


def cls_high(val, good, warn):
    """CSS class for higher-is-better metrics."""
    try:
        v = float(val)
        return "good" if v >= good else "warn" if v >= warn else "bad"
    except (ValueError, TypeError):
        return ""


def build_html():
    boltzgen = load_boltzgen_designs()
    rfd3 = load_rfd3_designs()
    all_designs = boltzgen + rfd3

    target_cif = STRUCTURES_DIR / "2GDZ.cif"
    target_cif_text = target_cif.read_text() if target_cif.exists() else ""

    active_site = [138, 148, 151, 155, 185, 217]
    dimer_interface = [146, 153, 161, 167, 168, 171, 172, 206]

    groups = {}
    for d in all_designs:
        g = d["group"]
        groups.setdefault(g, []).append(d)

    # Global index for viewer IDs
    idx = [0]

    def metric_row(label, value, css_class=""):
        c = f' class="metric-value {css_class}"' if css_class else ' class="metric-value"'
        return f'<div class="metric-row"><span class="metric-label">{label}</span><span{c}>{value}</span></div>'

    def design_card_html(d):
        i = idx[0]
        idx[0] += 1
        group_meta = STRATEGIES.get(d["group"], {})
        binder_color = group_meta.get("color", "#00CED1")

        helix_pct = float(d.get("helix", 0) or 0) * 100
        sheet_pct = float(d.get("sheet", 0) or 0) * 100
        loop_pct = float(d.get("loop", 0) or 0) * 100
        rank = d.get("rank", "")
        rank_html = f'<span class="rank-badge">Rank #{rank}</span>' if rank else ""

        card = f"""
<div class="design-card" data-group="{d['group']}" data-viewer-idx="{i}">
  <div class="card-header">
    <h2>{d['name']}</h2>
    <div>{rank_html} <span class="tool-badge" style="background:{binder_color}">{group_meta.get('tool','')}</span></div>
  </div>
  <div class="card-body">
    <div class="viewer-container" id="viewer_{i}">
      <div class="seq-legend" id="seqleg_{i}">
        <span class="seq-legend-item"><span class="seq-legend-dot" style="background:#E8860C"></span>Hydrophobic</span>
        <span class="seq-legend-item"><span class="seq-legend-dot" style="background:#2ECC71"></span>Polar</span>
        <span class="seq-legend-item"><span class="seq-legend-dot" style="background:#3498DB"></span>Positive</span>
        <span class="seq-legend-item"><span class="seq-legend-dot" style="background:#E74C3C"></span>Negative</span>
        <span class="seq-legend-item"><span class="seq-legend-dot" style="background:#95A5A6"></span>Gly</span>
      </div>
      <div class="viewer-controls">
        <button onclick="resetView({i})">Reset</button>
        <button onclick="toggleStyle({i},'cartoon')" class="active" id="btn_cartoon_{i}">Cartoon</button>
        <button onclick="toggleStyle({i},'surface')" id="btn_surface_{i}">Surface</button>
        <button onclick="toggleStyle({i},'sticks')" id="btn_sticks_{i}">Interface</button>
        <button onclick="toggleStyle({i},'sequence')" id="btn_sequence_{i}">Sequence</button>
      </div>
    </div>
    <div class="metrics-panel">
      <div class="section-label">Key Metrics</div>
"""
        # Tool-specific metrics
        if d["group"].startswith("boltzgen"):
            card += metric_row("Refolding RMSD", f"{d.get('filter_rmsd','N/A')} &Aring;",
                               cls_low(d.get("filter_rmsd"), 2.0, 2.5))
            card += metric_row("Design-to-Target ipTM", d.get("design_to_target_iptm", "N/A"),
                               cls_high(d.get("design_to_target_iptm"), 0.7, 0.5))
            card += metric_row("Min Design-to-Target PAE", d.get("min_design_to_target_pae", "N/A"),
                               cls_low(d.get("min_design_to_target_pae"), 3.0, 5.0))
            card += metric_row("Design pTM", d.get("design_ptm", "N/A"),
                               cls_high(d.get("design_ptm"), 0.8, 0.7))
            card += metric_row("H-bonds (PLIP)", d.get("plip_hbonds", "N/A"))
            card += metric_row("&Delta;SASA", f"{d.get('delta_sasa','N/A')} &Aring;&sup2;")
            card += metric_row("Designed Residues", d.get("num_design", "N/A"))
            fp = d.get("num_filters_passed", "")
            if fp:
                card += metric_row("Filters Passed", f"{fp}/9")
        elif d["group"].startswith("rfd3"):
            card += metric_row("Diffused Residues", d.get("num_design", "N/A"))
            card += metric_row("Radius of Gyration", f"{d.get('radius_of_gyration','N/A')} &Aring;")
            card += metric_row("Max C&alpha; Deviation", f"{d.get('max_ca_deviation','N/A')} &Aring;",
                               cls_low(d.get("max_ca_deviation"), 0.5, 1.0))
            card += metric_row("Chain Breaks", d.get("n_chainbreaks", "N/A"),
                               cls_low(d.get("n_chainbreaks"), 0, 0))
            card += metric_row("SS Elements", d.get("num_ss_elements", "N/A"))
            card += metric_row("Ala Content", d.get("ala_content", "N/A"))
            card += metric_row("Gly Content", d.get("gly_content", "N/A"))
            card += metric_row("Hotspots", d.get("hotspots", "N/A"))

        # Secondary structure bar (common)
        card += f"""
      <div class="section-label">Secondary Structure</div>
      <div class="ss-bar">
        <div class="helix" style="width:{helix_pct:.1f}%"></div>
        <div class="sheet" style="width:{sheet_pct:.1f}%"></div>
        <div class="loop" style="width:{loop_pct:.1f}%"></div>
      </div>
      <div class="legend">
        <span class="h">Helix {helix_pct:.0f}%</span>
        <span class="s">Sheet {sheet_pct:.0f}%</span>
        <span class="l">Loop {loop_pct:.0f}%</span>
      </div>
"""
        liab = d.get("liability_violations", "")
        if liab:
            card += f'      <div class="liabilities"><strong>Liabilities:</strong> {liab}</div>\n'

        if d["sequence"]:
            card += f"""
      <div class="section-label">Binder Sequence ({d['length']} AA)</div>
      <div class="sequence-box">{d['sequence']}</div>
"""
        else:
            card += f"""
      <div class="section-label">Backbone Only</div>
      <div class="sequence-box" style="color:#999">RFD3 outputs are backbone-only &mdash; no sequence designed yet. Use ProteinMPNN or LigandMPNN for inverse folding.</div>
"""

        card += """    </div>
  </div>
</div>
"""
        return card

    # --- Build full HTML ---
    group_labels = {gid: STRATEGIES[gid]["label"] for gid in groups}
    tab_ids = list(groups.keys())

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PGDH Binder Designs — Strategy Comparison</title>
<script src="https://3Dmol.org/build/3Dmol-min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #333; }
  .header { background: #1a1a2e; color: white; padding: 24px 32px; }
  .header h1 { font-size: 24px; font-weight: 600; }
  .header p { color: #aaa; margin-top: 4px; font-size: 14px; }
  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }

  /* Tabs */
  .tabs { display: flex; gap: 0; margin-bottom: 24px; border-bottom: 2px solid #ddd; }
  .tab { padding: 12px 24px; cursor: pointer; font-size: 14px; font-weight: 600; color: #666;
         border-bottom: 3px solid transparent; margin-bottom: -2px; transition: all 0.2s; }
  .tab:hover { color: #333; }
  .tab.active { color: #1a1a2e; border-bottom-color: #e94560; }
  .tab .count { background: #eee; color: #666; padding: 1px 8px; border-radius: 10px; font-size: 12px; margin-left: 6px; }
  .tab.active .count { background: #e94560; color: white; }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }

  /* Summary */
  .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .stat { background: white; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  .stat .value { font-size: 28px; font-weight: 700; color: #1a1a2e; }
  .stat .label { font-size: 13px; color: #888; margin-top: 4px; }

  /* Strategy info banner */
  .strategy-info { background: white; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px;
                   box-shadow: 0 1px 3px rgba(0,0,0,0.1); display: flex; gap: 32px; align-items: center; flex-wrap: wrap; }
  .strategy-info .info-item { font-size: 13px; }
  .strategy-info .info-label { color: #888; }
  .strategy-info .info-value { font-weight: 600; color: #1a1a2e; }

  /* Cards */
  .design-card { background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 24px; overflow: hidden; }
  .design-card .card-header { background: #16213e; color: white; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; }
  .design-card .card-header h2 { font-size: 16px; font-weight: 600; }
  .rank-badge { background: #e94560; color: white; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
  .tool-badge { color: white; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; margin-left: 6px; }
  .card-body { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
  .viewer-container { height: 500px; position: relative; border-right: 1px solid #eee; }
  .viewer-controls { position: absolute; bottom: 12px; left: 12px; z-index: 10; display: flex; gap: 6px; }
  .viewer-controls button { background: rgba(255,255,255,0.9); border: 1px solid #ddd; border-radius: 6px; padding: 6px 12px; font-size: 12px; cursor: pointer; }
  .viewer-controls button:hover { background: #e94560; color: white; border-color: #e94560; }
  .viewer-controls button.active { background: #e94560; color: white; border-color: #e94560; }
  .metrics-panel { padding: 20px; overflow-y: auto; max-height: 500px; }
  .metric-row { display: flex; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid #f0f0f0; }
  .metric-row:last-child { border-bottom: none; }
  .metric-label { color: #666; font-size: 13px; }
  .metric-value { font-weight: 600; font-size: 13px; }
  .metric-value.good { color: #27ae60; }
  .metric-value.warn { color: #f39c12; }
  .metric-value.bad { color: #e74c3c; }
  .sequence-box { background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; padding: 12px;
                  margin-top: 12px; font-family: 'Courier New', monospace; font-size: 12px;
                  word-break: break-all; line-height: 1.6; max-height: 120px; overflow-y: auto; }
  .section-label { font-size: 14px; font-weight: 600; color: #1a1a2e; margin: 14px 0 6px; }
  .ss-bar { display: flex; height: 18px; border-radius: 4px; overflow: hidden; margin-top: 4px; }
  .ss-bar .helix { background: #e94560; }
  .ss-bar .sheet { background: #4361ee; }
  .ss-bar .loop { background: #ddd; }
  .legend { display: flex; gap: 16px; margin-top: 6px; font-size: 12px; color: #888; }
  .legend span::before { content: ''; display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }
  .legend .h::before { background: #e94560; }
  .legend .s::before { background: #4361ee; }
  .legend .l::before { background: #ddd; }
  .liabilities { margin-top: 8px; font-size: 12px; color: #e74c3c; }
  .seq-legend { position: absolute; top: 12px; left: 12px; z-index: 10; background: rgba(255,255,255,0.92);
                border-radius: 6px; padding: 8px 12px; font-size: 11px; display: none; box-shadow: 0 1px 4px rgba(0,0,0,0.15); }
  .seq-legend.visible { display: block; }
  .seq-legend-item { display: inline-block; margin-right: 10px; }
  .seq-legend-dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 3px; vertical-align: middle; }
  @media (max-width: 900px) {
    .card-body { grid-template-columns: 1fr; }
    .viewer-container { border-right: none; border-bottom: 1px solid #eee; }
    .strategy-info { flex-direction: column; gap: 8px; }
  }
</style>
</head>
<body>
<div class="header">
  <h1>15-PGDH Binder Designs</h1>
  <p>Comparing design strategies &mdash; Target: 2GDZ (15-hydroxyprostaglandin dehydrogenase)</p>
</div>
<div class="container">
"""

    # Global summary
    html += f"""
<div class="summary">
  <div class="stat"><div class="value">{len(all_designs)}</div><div class="label">Total Designs</div></div>
  <div class="stat"><div class="value">{len(groups)}</div><div class="label">Tools / Strategies</div></div>
  <div class="stat"><div class="value">{len(boltzgen)}</div><div class="label">BoltzGen Designs</div></div>
  <div class="stat"><div class="value">{len(rfd3)}</div><div class="label">RFdiffusion3 Designs</div></div>
</div>
"""

    # Tabs
    html += '<div class="tabs">\n'
    html += f'  <div class="tab active" onclick="switchTab(\'all\')">All<span class="count">{len(all_designs)}</span></div>\n'
    for gid in tab_ids:
        meta = STRATEGIES[gid]
        html += f'  <div class="tab" onclick="switchTab(\'{gid}\')">{meta["label"]}<span class="count">{len(groups[gid])}</span></div>\n'
    html += '  <div class="tab" onclick="switchTab(\'target\')">Target (2GDZ)</div>\n'
    html += '</div>\n'

    # All designs panel
    html += '<div class="tab-panel active" id="panel_all">\n'
    for d in all_designs:
        html += design_card_html(d)
    html += '</div>\n'

    # Per-group panels
    for gid, designs_in_group in groups.items():
        meta = STRATEGIES[gid]
        html += f'<div class="tab-panel" id="panel_{gid}">\n'
        html += f"""<div class="strategy-info">
  <div class="info-item"><span class="info-label">Tool</span><br><span class="info-value">{meta['tool']}</span></div>
  <div class="info-item"><span class="info-label">Strategy</span><br><span class="info-value">{meta['strategy']}</span></div>
  <div class="info-item"><span class="info-label">Hotspots</span><br><span class="info-value">{meta['hotspots']}</span></div>
  <div class="info-item"><span class="info-label">Designs</span><br><span class="info-value">{len(designs_in_group)}</span></div>
</div>\n"""
        for d in designs_in_group:
            html += design_card_html(d)
        html += '</div>\n'

    # Target panel
    if target_cif_text:
        html += '<div class="tab-panel" id="panel_target">\n'
        html += f"""<div class="design-card">
  <div class="card-header">
    <h2>15-PGDH Target (2GDZ)</h2>
    <span class="rank-badge" style="background:#4361ee">Reference</span>
  </div>
  <div class="card-body">
    <div class="viewer-container" id="viewer_target" style="height:500px;">
      <div class="viewer-controls">
        <button onclick="resetTargetView()">Reset</button>
      </div>
    </div>
    <div class="metrics-panel">
      <div class="section-label">Binding Strategy Hotspots</div>
      <p style="font-size:13px;color:#666;margin-bottom:12px">Highlighted on the structure:</p>
      {metric_row("Active Site (Red)", "Ser138, Gln148, Tyr151, Lys155, Phe185, Tyr217")}
      {metric_row("Dimer Interface (Blue)", "Ala146, Ala153, Phe161, Leu167, Ala168, Leu171, Met172, Tyr206")}
      <div class="section-label" style="margin-top:20px">Design Strategies</div>
      {metric_row("Strategy 1", "Active site blocker (RFD3)")}
      {metric_row("Strategy 2", "Dimer disruptor (planned)")}
      {metric_row("Strategy 3", "Model-free surface (BoltzGen)")}
    </div>
  </div>
</div>
</div>\n"""

    # JavaScript — build design data for viewers
    # Each design needs: cif, binder_chain, group
    viewer_data = []
    # Reset idx counter to match card generation order
    # Cards are generated: all panel first, then per-group panels
    # But each card has unique data-viewer-idx. We need to track them.
    # Actually, design_card_html increments idx[0] each time, so we have
    # len(all_designs) cards in "all" + sum of per-group cards = duplicates.
    # Each gets its own viewer. Total viewers = len(all_designs) * 2 (all + group panel).
    # Let's just build the list matching the idx counter.

    total_viewers = idx[0]  # total cards generated
    # Rebuild the list in order: all_designs, then per-group
    ordered = list(all_designs)
    for gid in tab_ids:
        ordered.extend(groups[gid])

    for d in ordered:
        viewer_data.append({
            "cif": d["cif"],
            "name": d["name"],
            "binderChain": d.get("binder_chain", "B"),
            "group": d["group"],
        })

    viewers_json = json.dumps(viewer_data)
    target_json = json.dumps(target_cif_text)
    active_site_json = json.dumps(active_site)
    dimer_json = json.dumps(dimer_interface)
    colors_json = json.dumps({gid: meta["color"] for gid, meta in STRATEGIES.items()})

    html += f"""
</div><!-- /container -->

<script>
var viewerData = {viewers_json};
var targetCif = {target_json};
var activeSite = {active_site_json};
var dimerInterface = {dimer_json};
var groupColors = {colors_json};
var viewers = {{}};
var targetViewer = null;
var initialized = {{}};

function getBinderColor(group) {{
  return groupColors[group] || '#00CED1';
}}

function initViewer(i) {{
  if (initialized[i]) return;
  var el = document.getElementById('viewer_' + i);
  if (!el || el.offsetParent === null) return; // not visible
  var d = viewerData[i];
  var viewer = $3Dmol.createViewer(el, {{backgroundColor: 'white'}});
  viewer.addModel(d.cif, 'cif');
  var bc = d.binderChain;
  var tc = bc === 'A' ? 'B' : 'A';
  var color = getBinderColor(d.group);
  viewer.setStyle({{chain: tc}}, {{cartoon: {{color: '#999999', opacity: 0.8}}}});
  viewer.setStyle({{chain: bc}}, {{cartoon: {{color: color}}}});
  viewer.zoomTo();
  viewer.render();
  viewers[i] = {{viewer: viewer, binderChain: bc, targetChain: tc, color: color}};
  initialized[i] = true;
}}

function initTargetViewer() {{
  if (targetViewer) return;
  var el = document.getElementById('viewer_target');
  if (!el || el.offsetParent === null) return;
  targetViewer = $3Dmol.createViewer('viewer_target', {{backgroundColor: 'white'}});
  targetViewer.addModel(targetCif, 'cif');
  targetViewer.setStyle({{}}, {{cartoon: {{color: '#CCCCCC', opacity: 0.7}}}});
  targetViewer.setStyle({{chain: 'A', resi: activeSite}}, {{cartoon: {{color: '#FF4500'}}, stick: {{color: '#FF4500'}}}});
  targetViewer.setStyle({{chain: 'A', resi: dimerInterface}}, {{cartoon: {{color: '#1E90FF'}}, stick: {{color: '#1E90FF'}}}});
  targetViewer.zoomTo();
  targetViewer.render();
}}

function initVisibleViewers() {{
  // Init any viewer that is currently visible
  for (var i = 0; i < viewerData.length; i++) {{
    var el = document.getElementById('viewer_' + i);
    if (el && el.offsetParent !== null && !initialized[i]) {{
      initViewer(i);
    }}
  }}
  if (document.getElementById('viewer_target') &&
      document.getElementById('viewer_target').offsetParent !== null) {{
    initTargetViewer();
  }}
}}

function switchTab(tabId) {{
  // Update tab styles
  document.querySelectorAll('.tab').forEach(function(t, idx) {{
    var tabs = ['all'];
    var groups = {json.dumps(tab_ids)};
    tabs = tabs.concat(groups);
    tabs.push('target');
    t.classList.toggle('active', tabs[idx] === tabId);
  }});
  // Update panels
  document.querySelectorAll('.tab-panel').forEach(function(p) {{
    p.classList.remove('active');
  }});
  var panel = document.getElementById('panel_' + tabId);
  if (panel) panel.classList.add('active');
  // Lazy-init viewers that became visible
  setTimeout(initVisibleViewers, 100);
}}

function toggleStyle(idx, mode) {{
  var v = viewers[idx];
  if (!v) return;
  var viewer = v.viewer;
  viewer.removeAllSurfaces();
  var bc = v.binderChain, tc = v.targetChain, color = v.color;

  if (mode === 'cartoon') {{
    viewer.setStyle({{chain: tc}}, {{cartoon: {{color: '#999999', opacity: 0.8}}}});
    viewer.setStyle({{chain: bc}}, {{cartoon: {{color: color}}}});
  }} else if (mode === 'surface') {{
    viewer.setStyle({{chain: tc}}, {{cartoon: {{color: '#999999', opacity: 0.5}}}});
    viewer.setStyle({{chain: bc}}, {{cartoon: {{color: color, opacity: 0.5}}}});
    viewer.addSurface($3Dmol.SurfaceType.VDW, {{opacity: 0.6, color: '#999999'}}, {{chain: tc}});
    viewer.addSurface($3Dmol.SurfaceType.VDW, {{opacity: 0.6, color: color}}, {{chain: bc}});
  }} else if (mode === 'sticks') {{
    viewer.setStyle({{chain: tc}}, {{cartoon: {{color: '#999999', opacity: 0.8}}}});
    viewer.setStyle({{chain: bc}}, {{cartoon: {{color: color}}}});
    viewer.addStyle({{chain: bc, within: {{distance: 5, sel: {{chain: tc}}}}}}, {{stick: {{color: color}}}});
    viewer.addStyle({{chain: tc, within: {{distance: 5, sel: {{chain: bc}}}}}}, {{stick: {{color: '#FF6347'}}}});
  }} else if (mode === 'sequence') {{
    // Color binder residues by amino acid property
    var aaColors = {{
      // Hydrophobic (orange)
      'ALA':'#E8860C','VAL':'#E8860C','LEU':'#E8860C','ILE':'#E8860C','MET':'#E8860C','PHE':'#E8860C','TRP':'#E8860C','PRO':'#E8860C',
      // Polar (green)
      'SER':'#2ECC71','THR':'#2ECC71','ASN':'#2ECC71','GLN':'#2ECC71','TYR':'#2ECC71','CYS':'#2ECC71',
      // Positive (blue)
      'LYS':'#3498DB','ARG':'#3498DB','HIS':'#3498DB',
      // Negative (red)
      'ASP':'#E74C3C','GLU':'#E74C3C',
      // Special (gray)
      'GLY':'#95A5A6'
    }};
    viewer.setStyle({{chain: tc}}, {{cartoon: {{color: '#DDDDDD', opacity: 0.5}}}});
    Object.keys(aaColors).forEach(function(res) {{
      viewer.setStyle({{chain: bc, resn: res}}, {{cartoon: {{color: aaColors[res]}}, stick: {{color: aaColors[res]}}}});
    }});
  }}
  viewer.render();

  ['cartoon','surface','sticks','sequence'].forEach(function(m) {{
    var btn = document.getElementById('btn_' + m + '_' + idx);
    if (btn) btn.classList.toggle('active', m === mode);
  }});
  var leg = document.getElementById('seqleg_' + idx);
  if (leg) leg.classList.toggle('visible', mode === 'sequence');
}}

function resetView(idx) {{
  if (viewers[idx]) {{ viewers[idx].viewer.zoomTo(); viewers[idx].viewer.render(); }}
}}
function resetTargetView() {{
  if (targetViewer) {{ targetViewer.zoomTo(); targetViewer.render(); }}
}}

document.addEventListener('DOMContentLoaded', function() {{
  initVisibleViewers();
}});
</script>
</body>
</html>
"""

    OUTPUT_HTML.write_text(html)
    print(f"Wrote {OUTPUT_HTML} ({OUTPUT_HTML.stat().st_size / 1024:.1f} KB)")
    print(f"Open in browser: file://{OUTPUT_HTML.resolve()}")


if __name__ == "__main__":
    build_html()
