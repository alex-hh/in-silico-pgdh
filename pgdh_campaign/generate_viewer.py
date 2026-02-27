#!/usr/bin/env python3
"""Generate a standalone HTML page with interactive 3Dmol.js structure viewers
for PGDH binder designs from BoltzGen."""

import csv
import json
from pathlib import Path

OUT_DIR = Path(__file__).parent / "out" / "boltzgen"
DESIGNS_DIR = OUT_DIR / "designs"
STRUCTURES_DIR = Path(__file__).parent / "structures"
OUTPUT_HTML = Path(__file__).parent / "designs_viewer.html"


def load_metrics():
    """Load design metrics from CSV."""
    csv_path = OUT_DIR / "all_designs_metrics.csv"
    if not csv_path.exists():
        return []
    rows = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def read_cif(path):
    """Read CIF file content, escape for JS embedding."""
    return path.read_text()


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


def build_html():
    metrics = load_metrics()
    metrics_by_id = {r["id"]: r for r in metrics}

    cif_files = sorted(DESIGNS_DIR.glob("*.cif")) if DESIGNS_DIR.exists() else []
    target_cif = STRUCTURES_DIR / "2GDZ.cif"

    # Build design data for JS
    designs = []
    for cif in cif_files:
        name = cif.stem
        # Match to metrics by finding the design id in the filename
        matched = None
        for mid, row in metrics_by_id.items():
            if mid in name:
                matched = row
                break

        seq = extract_sequence(cif, "B")
        cif_text = read_cif(cif)

        d = {
            "name": name,
            "cif": cif_text,
            "sequence": seq,
            "length": len(seq),
        }
        if matched:
            d["rank"] = matched.get("final_rank", "?")
            d["design_to_target_iptm"] = matched.get("design_to_target_iptm", "")
            d["min_design_to_target_pae"] = matched.get("min_design_to_target_pae", "")
            d["design_ptm"] = matched.get("design_ptm", "")
            d["filter_rmsd"] = matched.get("filter_rmsd", "")
            d["plip_hbonds"] = matched.get("plip_hbonds_refolded", "")
            d["delta_sasa"] = matched.get("delta_sasa_refolded", "")
            d["helix"] = matched.get("helix", "")
            d["sheet"] = matched.get("sheet", "")
            d["loop"] = matched.get("loop", "")
            d["num_design"] = matched.get("num_design", "")
            d["num_filters_passed"] = matched.get("num_filters_passed", "")
            d["quality_score"] = matched.get("quality_score", "")
            d["liability_violations"] = matched.get("liability_violations_summary", "")
        designs.append(d)

    target_cif_text = read_cif(target_cif) if target_cif.exists() else ""

    # Active site and dimer interface residues for target visualization
    active_site = [138, 148, 151, 155, 185, 217]
    dimer_interface = [146, 153, 161, 167, 168, 171, 172, 206]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PGDH Binder Designs — Interactive Viewer</title>
<script src="https://3Dmol.org/build/3Dmol-min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #333; }}
  .header {{ background: #1a1a2e; color: white; padding: 24px 32px; }}
  .header h1 {{ font-size: 24px; font-weight: 600; }}
  .header p {{ color: #aaa; margin-top: 4px; font-size: 14px; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
  .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 32px; }}
  .stat {{ background: white; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .stat .value {{ font-size: 28px; font-weight: 700; color: #1a1a2e; }}
  .stat .label {{ font-size: 13px; color: #888; margin-top: 4px; }}
  .design-card {{ background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 24px; overflow: hidden; }}
  .design-card .card-header {{ background: #16213e; color: white; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; }}
  .design-card .card-header h2 {{ font-size: 18px; font-weight: 600; }}
  .rank-badge {{ background: #e94560; color: white; padding: 4px 12px; border-radius: 20px; font-size: 13px; font-weight: 600; }}
  .card-body {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0; }}
  .viewer-container {{ height: 500px; position: relative; border-right: 1px solid #eee; }}
  .viewer-controls {{ position: absolute; bottom: 12px; left: 12px; z-index: 10; display: flex; gap: 6px; }}
  .viewer-controls button {{ background: rgba(255,255,255,0.9); border: 1px solid #ddd; border-radius: 6px; padding: 6px 12px; font-size: 12px; cursor: pointer; }}
  .viewer-controls button:hover {{ background: #e94560; color: white; border-color: #e94560; }}
  .viewer-controls button.active {{ background: #e94560; color: white; border-color: #e94560; }}
  .metrics-panel {{ padding: 24px; overflow-y: auto; max-height: 500px; }}
  .metric-row {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #f0f0f0; }}
  .metric-row:last-child {{ border-bottom: none; }}
  .metric-label {{ color: #666; font-size: 13px; }}
  .metric-value {{ font-weight: 600; font-size: 13px; }}
  .metric-value.good {{ color: #27ae60; }}
  .metric-value.warn {{ color: #f39c12; }}
  .metric-value.bad {{ color: #e74c3c; }}
  .sequence-box {{ background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; padding: 12px; margin-top: 12px; font-family: 'Courier New', monospace; font-size: 12px; word-break: break-all; line-height: 1.6; max-height: 120px; overflow-y: auto; }}
  .section-label {{ font-size: 14px; font-weight: 600; color: #1a1a2e; margin: 16px 0 8px; }}
  .ss-bar {{ display: flex; height: 20px; border-radius: 4px; overflow: hidden; margin-top: 4px; }}
  .ss-bar .helix {{ background: #e94560; }}
  .ss-bar .sheet {{ background: #4361ee; }}
  .ss-bar .loop {{ background: #ddd; }}
  .legend {{ display: flex; gap: 16px; margin-top: 6px; font-size: 12px; color: #888; }}
  .legend span::before {{ content: ''; display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
  .legend .h::before {{ background: #e94560; }}
  .legend .s::before {{ background: #4361ee; }}
  .legend .l::before {{ background: #ddd; }}
  .liabilities {{ margin-top: 8px; font-size: 12px; color: #e74c3c; }}
  .target-section {{ margin-top: 32px; }}
  @media (max-width: 900px) {{
    .card-body {{ grid-template-columns: 1fr; }}
    .viewer-container {{ border-right: none; border-bottom: 1px solid #eee; }}
  }}
</style>
</head>
<body>
<div class="header">
  <h1>15-PGDH Binder Designs</h1>
  <p>BoltzGen surface strategy (Strategy 3) &mdash; Target: 2GDZ</p>
</div>
<div class="container">

<div class="summary">
  <div class="stat"><div class="value">{len(designs)}</div><div class="label">Designs Generated</div></div>
  <div class="stat"><div class="value">{sum(1 for d in designs if d.get('quality_score','0') not in ('','0','0.0'))}</div><div class="label">Passing Quality</div></div>
  <div class="stat"><div class="value">{min((float(d.get('filter_rmsd','99')) for d in designs if d.get('filter_rmsd')), default=0):.2f} A</div><div class="label">Best RMSD</div></div>
  <div class="stat"><div class="value">{max((float(d.get('design_to_target_iptm','0')) for d in designs if d.get('design_to_target_iptm')), default=0):.3f}</div><div class="label">Best ipTM</div></div>
</div>

"""

    # Design cards
    for i, d in enumerate(designs):
        rank = d.get("rank", i + 1)
        viewer_id = f"viewer_{i}"

        # Metric quality classes
        def cls(val, good, warn):
            try:
                v = float(val)
                if v <= good:
                    return "good"
                elif v <= warn:
                    return "warn"
                return "bad"
            except (ValueError, TypeError):
                return ""

        def cls_high(val, good, warn):
            try:
                v = float(val)
                if v >= good:
                    return "good"
                elif v >= warn:
                    return "warn"
                return "bad"
            except (ValueError, TypeError):
                return ""

        rmsd_cls = cls(d.get("filter_rmsd", ""), 2.0, 2.5)
        iptm_cls = cls_high(d.get("design_to_target_iptm", ""), 0.7, 0.5)
        ptm_cls = cls_high(d.get("design_ptm", ""), 0.8, 0.7)
        pae_cls = cls(d.get("min_design_to_target_pae", ""), 3.0, 5.0)

        helix_pct = float(d.get("helix", 0)) * 100
        sheet_pct = float(d.get("sheet", 0)) * 100
        loop_pct = float(d.get("loop", 0)) * 100

        liab = d.get("liability_violations", "")

        html += f"""
<div class="design-card">
  <div class="card-header">
    <h2>{d['name']}</h2>
    <span class="rank-badge">Rank #{rank}</span>
  </div>
  <div class="card-body">
    <div class="viewer-container" id="{viewer_id}" data-idx="{i}">
      <div class="viewer-controls">
        <button onclick="resetView({i})">Reset</button>
        <button onclick="toggleStyle({i}, 'cartoon')" class="active" id="btn_cartoon_{i}">Cartoon</button>
        <button onclick="toggleStyle({i}, 'surface')" id="btn_surface_{i}">Surface</button>
        <button onclick="toggleStyle({i}, 'sticks')" id="btn_sticks_{i}">Interface</button>
      </div>
    </div>
    <div class="metrics-panel">
      <div class="section-label">Key Metrics</div>
      <div class="metric-row"><span class="metric-label">Refolding RMSD</span><span class="metric-value {rmsd_cls}">{d.get('filter_rmsd', 'N/A')} A</span></div>
      <div class="metric-row"><span class="metric-label">Design-to-Target ipTM</span><span class="metric-value {iptm_cls}">{d.get('design_to_target_iptm', 'N/A')}</span></div>
      <div class="metric-row"><span class="metric-label">Min Design-to-Target PAE</span><span class="metric-value {pae_cls}">{d.get('min_design_to_target_pae', 'N/A')}</span></div>
      <div class="metric-row"><span class="metric-label">Design pTM</span><span class="metric-value {ptm_cls}">{d.get('design_ptm', 'N/A')}</span></div>
      <div class="metric-row"><span class="metric-label">H-bonds (PLIP)</span><span class="metric-value">{d.get('plip_hbonds', 'N/A')}</span></div>
      <div class="metric-row"><span class="metric-label">Delta SASA</span><span class="metric-value">{d.get('delta_sasa', 'N/A')}</span></div>
      <div class="metric-row"><span class="metric-label">Designed Residues</span><span class="metric-value">{d.get('num_design', 'N/A')}</span></div>
      <div class="metric-row"><span class="metric-label">Filters Passed</span><span class="metric-value">{d.get('num_filters_passed', 'N/A')}/9</span></div>

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
        if liab:
            html += f'      <div class="liabilities"><strong>Liabilities:</strong> {liab}</div>\n'

        html += f"""
      <div class="section-label">Binder Sequence ({d['length']} AA)</div>
      <div class="sequence-box">{d['sequence']}</div>
    </div>
  </div>
</div>
"""

    # Target structure section
    if target_cif_text:
        html += f"""
<div class="target-section">
  <div class="design-card">
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
        <p style="font-size:13px;color:#666;margin-bottom:12px;">
          Colored on the 3D view:
        </p>
        <div class="metric-row"><span class="metric-label" style="color:#FF4500">Active Site (Red)</span><span class="metric-value">Ser138, Gln148, Tyr151, Lys155, Phe185, Tyr217</span></div>
        <div class="metric-row"><span class="metric-label" style="color:#1E90FF">Dimer Interface (Blue)</span><span class="metric-value">Ala146, Ala153, Phe161, Leu167, Ala168, Leu171, Met172, Tyr206</span></div>

        <div class="section-label" style="margin-top:24px">Design Strategies</div>
        <div class="metric-row"><span class="metric-label">Strategy 1</span><span class="metric-value">Active site blocker</span></div>
        <div class="metric-row"><span class="metric-label">Strategy 2</span><span class="metric-value">Dimer disruptor</span></div>
        <div class="metric-row"><span class="metric-label">Strategy 3</span><span class="metric-value">Model-free surface (used here)</span></div>
      </div>
    </div>
  </div>
</div>
"""

    # JavaScript for 3Dmol viewers
    designs_json = json.dumps([{"cif": d["cif"], "name": d["name"]} for d in designs])
    target_json = json.dumps(target_cif_text)
    active_site_json = json.dumps(active_site)
    dimer_json = json.dumps(dimer_interface)

    html += f"""
</div><!-- /container -->

<script>
var designs = {designs_json};
var targetCif = {target_json};
var activeSite = {active_site_json};
var dimerInterface = {dimer_json};
var viewers = [];
var viewerModes = [];
var targetViewer = null;

function initViewers() {{
  designs.forEach(function(d, i) {{
    var el = document.getElementById('viewer_' + i);
    var viewer = $3Dmol.createViewer(el, {{backgroundColor: 'white'}});
    viewer.addModel(d.cif, 'cif');
    applyCartoon(viewer);
    viewer.zoomTo();
    viewer.render();
    viewers.push(viewer);
    viewerModes.push('cartoon');
  }});

  if (targetCif && document.getElementById('viewer_target')) {{
    targetViewer = $3Dmol.createViewer('viewer_target', {{backgroundColor: 'white'}});
    targetViewer.addModel(targetCif, 'cif');
    targetViewer.setStyle({{}}, {{cartoon: {{color: '#CCCCCC', opacity: 0.7}}}});
    targetViewer.setStyle({{chain: 'A', resi: activeSite}}, {{cartoon: {{color: '#FF4500'}}, stick: {{color: '#FF4500'}}}});
    targetViewer.setStyle({{chain: 'A', resi: dimerInterface}}, {{cartoon: {{color: '#1E90FF'}}, stick: {{color: '#1E90FF'}}}});
    targetViewer.zoomTo();
    targetViewer.render();
  }}
}}

function applyCartoon(viewer) {{
  viewer.setStyle({{chain: 'A'}}, {{cartoon: {{color: '#999999', opacity: 0.8}}}});
  viewer.setStyle({{chain: 'B'}}, {{cartoon: {{color: '#00CED1'}}}});
  viewer.render();
}}

function applySurface(viewer) {{
  viewer.removeAllSurfaces();
  viewer.setStyle({{chain: 'A'}}, {{cartoon: {{color: '#999999', opacity: 0.5}}}});
  viewer.setStyle({{chain: 'B'}}, {{cartoon: {{color: '#00CED1', opacity: 0.5}}}});
  viewer.addSurface($3Dmol.SurfaceType.VDW, {{opacity: 0.6, color: '#999999'}}, {{chain: 'A'}});
  viewer.addSurface($3Dmol.SurfaceType.VDW, {{opacity: 0.6, color: '#00CED1'}}, {{chain: 'B'}});
  viewer.render();
}}

function applySticks(viewer) {{
  viewer.setStyle({{chain: 'A'}}, {{cartoon: {{color: '#999999', opacity: 0.8}}}});
  viewer.setStyle({{chain: 'B'}}, {{cartoon: {{color: '#00CED1'}}}});
  viewer.addStyle(
    {{chain: 'B', within: {{distance: 5, sel: {{chain: 'A'}}}}}},
    {{stick: {{color: '#00CED1'}}}}
  );
  viewer.addStyle(
    {{chain: 'A', within: {{distance: 5, sel: {{chain: 'B'}}}}}},
    {{stick: {{color: '#FF6347'}}}}
  );
  viewer.render();
}}

function toggleStyle(idx, mode) {{
  var viewer = viewers[idx];
  viewer.removeAllSurfaces();
  if (mode === 'cartoon') applyCartoon(viewer);
  else if (mode === 'surface') applySurface(viewer);
  else if (mode === 'sticks') applySticks(viewer);
  viewerModes[idx] = mode;

  ['cartoon', 'surface', 'sticks'].forEach(function(m) {{
    var btn = document.getElementById('btn_' + m + '_' + idx);
    if (btn) btn.classList.toggle('active', m === mode);
  }});
}}

function resetView(idx) {{
  viewers[idx].zoomTo();
  viewers[idx].render();
}}

function resetTargetView() {{
  if (targetViewer) {{
    targetViewer.zoomTo();
    targetViewer.render();
  }}
}}

document.addEventListener('DOMContentLoaded', initViewers);
</script>
</body>
</html>
"""

    OUTPUT_HTML.write_text(html)
    print(f"Wrote {OUTPUT_HTML} ({OUTPUT_HTML.stat().st_size / 1024:.1f} KB)")
    print(f"Open in browser: file://{OUTPUT_HTML.resolve()}")


if __name__ == "__main__":
    build_html()
