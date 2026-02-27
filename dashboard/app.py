"""PGDH Binder Design Tracker — Streamlit app backed by Lyceum S3."""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from client import LyceumClient
from loaders import classify_metric
from tracker import CampaignTracker

st.set_page_config(page_title="PGDH Design Tracker", page_icon="🧬", layout="wide")


# ── Auth ─────────────────────────────────────────────────────────────────

def _get_authenticator():
    """Create Google OAuth authenticator from secrets.toml values."""
    import json as _json
    import tempfile

    from streamlit_google_auth import Authenticate

    creds = {
        "web": {
            "client_id": st.secrets["google"]["client_id"],
            "client_secret": st.secrets["google"]["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [st.secrets["auth"]["redirect_uri"]],
        }
    }
    creds_path = tempfile.mktemp(suffix=".json")
    with open(creds_path, "w") as f:
        _json.dump(creds, f)

    return Authenticate(
        secret_credentials_path=creds_path,
        cookie_name="pgdh_tracker",
        cookie_key=st.secrets["auth"]["cookie_key"],
        redirect_uri=st.secrets["auth"]["redirect_uri"],
    )


def init_auth():
    """Check for existing auth cookie on page load (non-blocking)."""
    if st.session_state.get("authenticated"):
        return
    try:
        auth = _get_authenticator()
        auth.check_authentification()
        if st.session_state.get("connected"):
            email = st.session_state.get("user_info", {}).get("email", "")
            allowed = [e.strip() for e in st.secrets["auth"]["allowed_emails"].split(",")]
            if email in allowed:
                st.session_state.authenticated = True
                st.session_state.user_email = email
    except Exception:
        pass


def is_authenticated() -> bool:
    """True if the current user is signed in and on the allowlist."""
    return st.session_state.get("authenticated", False)


def require_auth():
    """Gate for write actions. Shows sign-in prompt if not authenticated."""
    if is_authenticated():
        return True
    st.warning("Sign in to perform this action (use the sidebar).")
    return False


# Check for existing session on load (doesn't block page rendering)
init_auth()


# ── Client & Tracker (cached) ───────────────────────────────────────────

@st.cache_resource
def get_client() -> LyceumClient:
    try:
        return LyceumClient(api_key=st.secrets["lyceum"]["api_key"])
    except (KeyError, Exception):
        return None


def get_tracker() -> CampaignTracker | None:
    """Get tracker, reloading from S3 if stale."""
    client = get_client()
    if client is None:
        return None
    if "tracker" not in st.session_state:
        try:
            st.session_state.tracker = CampaignTracker(client)
        except Exception:
            return None
    return st.session_state.tracker


def require_tracker() -> CampaignTracker:
    """Get tracker or show error and stop."""
    tracker = get_tracker()
    if tracker is None:
        st.error(
            "Could not connect to Lyceum. Check that `[lyceum] api_key` is set "
            "in Streamlit secrets (Settings → Secrets)."
        )
        st.stop()
    return tracker


# ── Sidebar ──────────────────────────────────────────────────────────────

st.sidebar.title("PGDH Design Tracker")
page = st.sidebar.radio(
    "Navigate",
    ["Dashboard", "Designs", "Jobs", "New Run", "Design Detail"],
    index=0,
)

if st.sidebar.button("Refresh from S3"):
    st.session_state.pop("tracker", None)
    st.rerun()

st.sidebar.markdown("---")

# Auth controls in sidebar
if is_authenticated():
    st.sidebar.success(f"Signed in as {st.session_state.get('user_email', 'team member')}")
    if st.sidebar.button("Sign out"):
        try:
            auth = _get_authenticator()
            auth.logout()
        except Exception:
            pass
        st.session_state.pop("authenticated", None)
        st.session_state.pop("user_email", None)
        st.session_state.pop("connected", None)
        st.rerun()
else:
    st.sidebar.info("Read-only mode. Sign in to submit jobs.")
    if st.sidebar.button("Sign in with Google"):
        auth = _get_authenticator()
        auth.check_authentification()
        if st.session_state.get("connected"):
            email = st.session_state.get("user_info", {}).get("email", "")
            allowed = [e.strip() for e in st.secrets["auth"]["allowed_emails"].split(",")]
            if email in allowed:
                st.session_state.authenticated = True
                st.session_state.user_email = email
                st.rerun()
            else:
                st.sidebar.error("Access denied — not on team allowlist.")
                auth.logout()
        else:
            auth.login()

st.sidebar.markdown("---")
st.sidebar.caption("15-PGDH binder design campaign")
st.sidebar.caption("Target: 2GDZ")


# ── Helpers ──────────────────────────────────────────────────────────────

STATUS_COLORS = {
    "designed": "🔵",
    "validated": "🟡",
    "scored": "🟠",
    "selected": "🟢",
    "failed": "🔴",
}

TOOL_LABELS = {
    "boltzgen": "BoltzGen",
    "rfdiffusion3": "RFdiffusion3",
    "boltz2": "Boltz-2",
    "ipsae": "ipSAE",
}


def _fmt_metric(val):
    if val is None or val == "":
        return ""
    try:
        return f"{float(val):.3f}"
    except (ValueError, TypeError):
        return str(val)


# ══════════════════════════════════════════════════════════════════════════
# DASHBOARD PAGE
# ══════════════════════════════════════════════════════════════════════════

if page == "Dashboard":
    tracker = require_tracker()
    designs = tracker.list_designs()
    jobs = tracker.list_jobs()

    st.header("Campaign Dashboard")

    # Summary cards
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Designs", len(designs))

    by_tool = {}
    for d in designs:
        by_tool[d.get("tool", "?")] = by_tool.get(d.get("tool", "?"), 0) + 1
    c2.metric("BoltzGen", by_tool.get("boltzgen", 0))
    c3.metric("RFdiffusion3", by_tool.get("rfdiffusion3", 0))

    by_status = {}
    for d in designs:
        s = d.get("status", "designed")
        by_status[s] = by_status.get(s, 0) + 1
    c4.metric("Selected", by_status.get("selected", 0))

    # Pipeline funnel
    st.subheader("Pipeline Funnel")
    funnel_stages = ["designed", "validated", "scored", "selected"]
    funnel_counts = [by_status.get(s, 0) for s in funnel_stages]
    if any(funnel_counts):
        fig = px.funnel(
            x=funnel_counts,
            y=[s.capitalize() for s in funnel_stages],
            title="Design Pipeline",
        )
        fig.update_layout(height=300)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No designs tracked yet. Use 'Sync from S3' below or submit new runs.")

    # By strategy breakdown
    if designs:
        st.subheader("By Strategy")
        by_strategy = {}
        for d in designs:
            s = d.get("strategy", "unknown")
            by_strategy[s] = by_strategy.get(s, 0) + 1
        fig2 = px.bar(
            x=list(by_strategy.keys()),
            y=list(by_strategy.values()),
            labels={"x": "Strategy", "y": "Count"},
            title="Designs by Strategy",
        )
        st.plotly_chart(fig2, use_container_width=True)

    # Recent jobs
    st.subheader("Recent Jobs")
    if jobs:
        job_df = pd.DataFrame(jobs[-10:][::-1])
        display_cols = [c for c in ["id", "tool", "status", "submitted_at", "execution_id"] if c in job_df.columns]
        st.dataframe(job_df[display_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No jobs tracked yet.")

    # Evaluation pipeline
    st.subheader("Evaluation Pipeline")
    st.caption(
        "Scans all tool outputs on S3, standardises metrics, ranks designs, "
        "and updates the tracker. This is the ONLY way to populate the "
        "`designs/` source of truth on S3."
    )
    if st.button("Run Evaluation Pipeline", type="primary") and require_auth():
        with st.spinner("Running evaluation pipeline (collect → rank → sync)..."):
            try:
                # Import evaluate_designs from the pgdh_campaign directory
                eval_path = Path(__file__).resolve().parent.parent / "pgdh_campaign"
                sys.path.insert(0, str(eval_path))
                from evaluate_designs import run_evaluation
                results = run_evaluation(client=get_client())
                # Force tracker reload
                st.session_state.pop("tracker", None)
                st.success(f"Evaluated {len(results)} designs. Tracker synced.")
            except Exception as e:
                st.error(f"Evaluation failed: {e}")
                import traceback
                st.code(traceback.format_exc())

    # Suggest next steps
    st.subheader("Suggested Next Steps")
    if designs:
        # Coverage matrix
        coverage = {}
        for d in designs:
            tool = d.get("tool", "unknown")
            strat = d.get("strategy", "unknown")
            coverage.setdefault(tool, {})
            coverage[tool][strat] = coverage[tool].get(strat, 0) + 1

        all_strategies = sorted({d.get("strategy", "unknown") for d in designs})
        all_tools = sorted(coverage.keys())
        cov_rows = []
        for tool in all_tools:
            row = {"tool": TOOL_LABELS.get(tool, tool)}
            for strat in all_strategies:
                row[strat] = coverage.get(tool, {}).get(strat, 0)
            cov_rows.append(row)
        st.markdown("**Coverage (tool x strategy):**")
        st.dataframe(pd.DataFrame(cov_rows), use_container_width=True, hide_index=True)

        # Identify gaps and suggestions
        suggestions = []
        for strat in ["active_site", "dimer_interface", "surface"]:
            for tool in ["boltzgen", "rfdiffusion3"]:
                if tool == "rfdiffusion3" and strat == "surface":
                    continue  # RFD3 doesn't support surface strategy
                count = coverage.get(tool, {}).get(strat, 0)
                if count == 0:
                    suggestions.append(
                        f"**[GAP]** No {TOOL_LABELS.get(tool, tool)} designs for `{strat}`. "
                        f"Submit via New Run page."
                    )
                elif count < 5:
                    suggestions.append(
                        f"**[LOW]** Only {count} {TOOL_LABELS.get(tool, tool)} designs for `{strat}`. "
                        f"Consider generating more."
                    )

        # Check pipeline bottlenecks
        n_designed = by_status.get("designed", 0)
        n_validated = by_status.get("validated", 0)
        n_scored = by_status.get("scored", 0)
        if n_designed > 5 and n_validated == 0:
            suggestions.append(
                f"**[BOTTLENECK]** {n_designed} designs at 'designed' but none validated. "
                f"Run Boltz-2 cross-validation."
            )
        if n_validated > 3 and n_scored == 0:
            suggestions.append(
                f"**[BOTTLENECK]** {n_validated} validated designs but none scored. "
                f"Run ipSAE scoring."
            )

        if suggestions:
            for s in suggestions:
                st.markdown(f"- {s}")
        else:
            st.success("Campaign looks healthy — no obvious gaps or bottlenecks.")
    else:
        st.info(
            "No designs yet. Run the Evaluation Pipeline above to scan S3, "
            "or submit new design runs from the New Run page."
        )


# ══════════════════════════════════════════════════════════════════════════
# DESIGNS PAGE
# ══════════════════════════════════════════════════════════════════════════

elif page == "Designs":
    tracker = require_tracker()
    designs = tracker.list_designs()

    st.header("All Designs")

    if not designs:
        st.info("No designs tracked. Go to Dashboard and sync from S3, or submit new runs.")
        st.stop()

    # Filters
    fc1, fc2, fc3 = st.columns(3)
    tools = sorted({d.get("tool", "") for d in designs})
    strategies = sorted({d.get("strategy", "") for d in designs})
    statuses = sorted({d.get("status", "") for d in designs})

    sel_tool = fc1.multiselect("Tool", tools, default=tools)
    sel_strategy = fc2.multiselect("Strategy", strategies, default=strategies)
    sel_status = fc3.multiselect("Status", statuses, default=statuses)

    filtered = [
        d for d in designs
        if d.get("tool", "") in sel_tool
        and d.get("strategy", "") in sel_strategy
        and d.get("status", "") in sel_status
    ]

    # Build flat table
    rows = []
    for d in filtered:
        m = d.get("metrics", {})
        rows.append({
            "id": d["id"],
            "tool": TOOL_LABELS.get(d.get("tool", ""), d.get("tool", "")),
            "strategy": d.get("strategy", ""),
            "status": f"{STATUS_COLORS.get(d.get('status', ''), '')} {d.get('status', '')}",
            "rank": d.get("rank", ""),
            "score": _fmt_metric(d.get("composite_score", "")),
            "residues": d.get("num_residues", ""),
            "iptm": _fmt_metric(m.get("iptm", m.get("design_to_target_iptm", ""))),
            "ptm": _fmt_metric(m.get("ptm", m.get("design_ptm", ""))),
            "rmsd": _fmt_metric(m.get("filter_rmsd", "")),
            "pae": _fmt_metric(m.get("min_design_to_target_pae", "")),
            "helix": _fmt_metric(m.get("helix", "")),
            "sheet": _fmt_metric(m.get("sheet", "")),
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "id": st.column_config.TextColumn("Design ID", width="medium"),
            "tool": st.column_config.TextColumn("Tool", width="small"),
            "strategy": st.column_config.TextColumn("Strategy", width="small"),
            "status": st.column_config.TextColumn("Status", width="small"),
            "rank": st.column_config.TextColumn("Rank", width="small"),
            "score": st.column_config.TextColumn("Score", width="small"),
            "iptm": st.column_config.TextColumn("ipTM"),
            "ptm": st.column_config.TextColumn("pTM"),
            "rmsd": st.column_config.TextColumn("RMSD"),
            "pae": st.column_config.TextColumn("PAE"),
        },
    )

    # Bulk actions (auth required)
    if is_authenticated():
        st.subheader("Bulk Actions")
        selected_ids = st.multiselect(
            "Select designs",
            [d["id"] for d in filtered],
        )
        ba1, ba2, ba3 = st.columns(3)
        with ba1:
            if st.button("Mark as Validated") and selected_ids:
                tracker.bulk_update_status(selected_ids, "validated")
                st.success(f"Updated {len(selected_ids)} designs to 'validated'")
                st.rerun()
        with ba2:
            if st.button("Mark as Scored") and selected_ids:
                tracker.bulk_update_status(selected_ids, "scored")
                st.success(f"Updated {len(selected_ids)} designs to 'scored'")
                st.rerun()
        with ba3:
            if st.button("Mark as Selected") and selected_ids:
                tracker.bulk_update_status(selected_ids, "selected")
                st.success(f"Updated {len(selected_ids)} designs to 'selected'")
                st.rerun()

    # Click to detail
    st.markdown("---")
    detail_id = st.selectbox("View design detail", [""] + [d["id"] for d in filtered])
    if detail_id:
        st.session_state.detail_design_id = detail_id
        st.info(f"Switch to 'Design Detail' page in the sidebar to view {detail_id}")


# ══════════════════════════════════════════════════════════════════════════
# JOBS PAGE
# ══════════════════════════════════════════════════════════════════════════

elif page == "Jobs":
    tracker = require_tracker()
    jobs = tracker.list_jobs()

    st.header("Job Tracker")

    if not jobs:
        st.info("No jobs tracked yet. Submit a run from the 'New Run' page.")
        st.stop()

    # Refresh running jobs
    rc1, rc2 = st.columns(2)
    with rc1:
        refresh_clicked = st.button("Refresh Running Jobs")
    with rc2:
        auto_eval = st.checkbox("Auto-evaluate on completion", value=True)

    if refresh_clicked:
        client = get_client()
        updated = 0
        newly_completed = False
        for job in jobs:
            if job.get("status") in ("pending", "queued", "running"):
                try:
                    result = client.get_status(job["execution_id"])
                    new_status = result.get("status", job["status"])
                    if new_status != job.get("status"):
                        tracker.update_job(job["id"], status=new_status)
                        if new_status in ("completed", "failed", "failed_user", "failed_system", "timeout", "cancelled"):
                            tracker.update_job(job["id"], completed_at=datetime.now(timezone.utc).isoformat())
                        if new_status == "completed":
                            newly_completed = True
                        updated += 1
                except Exception as e:
                    st.warning(f"Could not check {job['id']}: {e}")
        st.success(f"Refreshed {updated} job(s)")

        # Auto-evaluate when jobs complete
        if newly_completed and auto_eval:
            with st.spinner("Job completed — running evaluation pipeline..."):
                try:
                    eval_path = Path(__file__).resolve().parent.parent / "pgdh_campaign"
                    sys.path.insert(0, str(eval_path))
                    from evaluate_designs import run_evaluation
                    results = run_evaluation(client=get_client())
                    st.session_state.pop("tracker", None)
                    st.success(f"Auto-evaluated {len(results)} designs after job completion.")
                except Exception as e:
                    st.warning(f"Auto-evaluation failed: {e}")
        st.rerun()

    # Job table
    job_rows = []
    for j in reversed(jobs):
        status_icon = {"completed": "✅", "failed": "❌", "running": "🔄",
                       "pending": "⏳", "queued": "⏳"}.get(j.get("status", ""), "❓")
        job_rows.append({
            "id": j["id"],
            "tool": TOOL_LABELS.get(j.get("tool", ""), j.get("tool", "")),
            "status": f"{status_icon} {j.get('status', '')}",
            "execution_id": j.get("execution_id", "")[:12] + "..." if j.get("execution_id") else "",
            "submitted": j.get("submitted_at", "")[:19],
            "completed": j.get("completed_at", "")[:19] if j.get("completed_at") else "",
            "config": str(j.get("config", {}))[:60],
        })

    st.dataframe(pd.DataFrame(job_rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════
# NEW RUN PAGE
# ══════════════════════════════════════════════════════════════════════════

elif page == "New Run":
    st.header("Submit New Run")

    if not is_authenticated():
        st.warning("Sign in with Google to submit new runs (use the sidebar).")
        st.stop()

    tool = st.selectbox("Tool", ["BoltzGen", "RFdiffusion3", "Boltz-2 Validation", "ipSAE Scoring", "Custom FASTA Upload"])

    if tool == "BoltzGen":
        st.subheader("BoltzGen Configuration")
        strategy = st.selectbox("Strategy", [
            "active_site (S1)", "dimer_interface (S2)", "surface (S3)"
        ])
        num_designs = st.number_input("Number of designs", min_value=1, max_value=50, value=10)
        protocol = st.selectbox("Protocol", ["protein-anything", "protein-protein"])
        machine = st.selectbox("Machine", ["gpu.a100", "gpu.h100"], index=0)
        timeout = st.number_input("Timeout (seconds)", min_value=60, max_value=3600, value=600)

        strategy_key = strategy.split(" ")[0]
        output_subdir = f"output/boltzgen/{strategy_key}/"
        st.caption(f"Output will be saved to: `{output_subdir}`")

        if st.button("Submit BoltzGen Job", type="primary"):
            config = {
                "strategy": strategy_key,
                "num_designs": num_designs,
                "protocol": protocol,
                "output_subdir": output_subdir,
            }

            with st.spinner("Submitting BoltzGen job..."):
                try:
                    client = get_client()
                    yaml_name = f"strategy{'1' if 'active' in strategy else '2' if 'dimer' in strategy else '3'}"
                    cmd = (
                        f"bash /mnt/s3/scripts/boltzgen/run_boltzgen.sh"
                        f" --input-yaml /root/boltzgen_work/{yaml_name}.yaml"
                        f" --output-dir /mnt/s3/{output_subdir}"
                        f" --protocol {protocol}"
                        f" --num-designs {num_designs}"
                        f" --cache /mnt/s3/models/boltzgen"
                    )
                    exec_id, _ = client.submit_docker_job(
                        docker_image="pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime",
                        command=cmd,
                        execution_type=machine,
                        timeout=timeout,
                    )
                    job_id = f"job_{uuid.uuid4().hex[:8]}"
                    tracker = require_tracker()
                    tracker.add_job({
                        "id": job_id,
                        "tool": "boltzgen",
                        "execution_id": exec_id,
                        "status": "pending",
                        "config": config,
                        "submitted_at": datetime.now(timezone.utc).isoformat(),
                    })
                    st.success(f"Submitted! Job ID: {job_id}, Execution: {exec_id}")
                except Exception as e:
                    st.error(f"Submission failed: {e}")

    elif tool == "RFdiffusion3":
        st.subheader("RFdiffusion3 Configuration")
        strategy = st.selectbox("Strategy", [
            "active_site", "dimer_interface"
        ])
        num_designs = st.number_input("Number of designs", min_value=1, max_value=20, value=4)
        contig = st.text_input(
            "Contig string",
            value="60-120,/0,A0-265" if strategy == "active_site" else "60-140,/0,A0-265",
        )
        machine = st.selectbox("Machine", ["gpu.a100", "gpu.h100"], index=0)
        timeout = st.number_input("Timeout (seconds)", min_value=60, max_value=3600, value=600)

        output_subdir = f"output/rfdiffusion3/{strategy}/"
        st.caption(f"Output will be saved to: `{output_subdir}`")

        if st.button("Submit RFD3 Job", type="primary"):
            config = {
                "strategy": strategy,
                "num_designs": num_designs,
                "contig": contig,
                "output_subdir": output_subdir,
            }

            with st.spinner("Submitting RFdiffusion3 job..."):
                try:
                    client = get_client()
                    exec_id, _ = client.submit_docker_job(
                        docker_image="rosettacommons/foundry:latest",
                        command=f"rfdiffusion3 --config /mnt/s3/input/rfdiffusion3/rfd3_pgdh_binder.json --num-designs {num_designs} --output-dir /mnt/s3/{output_subdir}",
                        execution_type=machine,
                        timeout=timeout,
                    )
                    job_id = f"job_{uuid.uuid4().hex[:8]}"
                    tracker = require_tracker()
                    tracker.add_job({
                        "id": job_id,
                        "tool": "rfdiffusion3",
                        "execution_id": exec_id,
                        "status": "pending",
                        "config": config,
                        "submitted_at": datetime.now(timezone.utc).isoformat(),
                    })
                    st.success(f"Submitted! Job ID: {job_id}, Execution: {exec_id}")
                except Exception as e:
                    st.error(f"Submission failed: {e}")

    elif tool == "Boltz-2 Validation":
        st.subheader("Boltz-2 Validation")
        st.info("Select designs from the Designs page, then validate their predicted complexes with Boltz-2.")

        tracker = require_tracker()
        designs = tracker.list_designs()
        design_ids = [d["id"] for d in designs if d.get("sequence")]

        if not design_ids:
            st.warning("No designs with sequences available. Run BoltzGen first.")
        else:
            selected = st.multiselect("Designs to validate", design_ids)
            machine = st.selectbox("Machine", ["gpu.a100", "gpu.h100"], index=0)
            timeout = st.number_input("Timeout (seconds)", min_value=60, max_value=3600, value=600)

            if st.button("Submit Boltz-2 Validation", type="primary") and selected:
                with st.spinner("Submitting Boltz-2 validation..."):
                    try:
                        client = get_client()
                        exec_id, _ = client.submit_docker_job(
                            docker_image="boltz/boltz2:latest",
                            command=f"boltz predict --input /mnt/s3/input/boltz2/ --output /mnt/s3/output/boltz2/",
                            execution_type=machine,
                            timeout=timeout,
                        )
                        job_id = f"job_{uuid.uuid4().hex[:8]}"
                        tracker.add_job({
                            "id": job_id,
                            "tool": "boltz2",
                            "execution_id": exec_id,
                            "status": "pending",
                            "config": {"designs": selected},
                            "submitted_at": datetime.now(timezone.utc).isoformat(),
                        })
                        st.success(f"Submitted! Job ID: {job_id}, Execution: {exec_id}")
                    except Exception as e:
                        st.error(f"Submission failed: {e}")

    elif tool == "ipSAE Scoring":
        st.subheader("ipSAE Scoring")
        st.info("Score predicted complexes with ipSAE to rank binding affinity.")

        tracker = require_tracker()
        designs = tracker.list_designs()
        design_ids = [d["id"] for d in designs]

        if not design_ids:
            st.warning("No designs available to score.")
        else:
            selected = st.multiselect("Designs to score", design_ids)
            if st.button("Submit ipSAE Scoring", type="primary") and selected:
                with st.spinner("Submitting ipSAE scoring..."):
                    try:
                        client = get_client()
                        exec_id, _ = client.submit_docker_job(
                            docker_image="ghcr.io/wells-wood-research/ipsae:latest",
                            command="python score.py --input /mnt/s3/output/boltz2/ --output /mnt/s3/output/ipsae/",
                            execution_type="cpu",
                            timeout=300,
                        )
                        job_id = f"job_{uuid.uuid4().hex[:8]}"
                        tracker.add_job({
                            "id": job_id,
                            "tool": "ipsae",
                            "execution_id": exec_id,
                            "status": "pending",
                            "config": {"designs": selected},
                            "submitted_at": datetime.now(timezone.utc).isoformat(),
                        })
                        st.success(f"Submitted! Job ID: {job_id}, Execution: {exec_id}")
                    except Exception as e:
                        st.error(f"Submission failed: {e}")

    elif tool == "Custom FASTA Upload":
        st.subheader("Upload Custom Designs")
        st.info(
            "Upload a FASTA file with binder sequences. Each sequence will be "
            "registered as a 'custom' design and run through the evaluation pipeline."
        )

        designer_name = st.text_input("Designer name", value="custom", help="Label for these designs (e.g. your name, 'manual', 'external')")
        strategy = st.selectbox("Strategy (optional)", ["unknown", "active_site", "dimer_interface", "surface"])
        fasta_file = st.file_uploader("FASTA file", type=["fasta", "fa", "fst"])

        if fasta_file and st.button("Upload & Evaluate", type="primary"):
            # Parse FASTA
            fasta_text = fasta_file.read().decode()
            sequences = []
            current_name = ""
            current_seq = []
            for line in fasta_text.strip().split("\n"):
                if line.startswith(">"):
                    if current_name and current_seq:
                        sequences.append((current_name, "".join(current_seq)))
                    current_name = line[1:].strip().split()[0]
                    current_seq = []
                else:
                    current_seq.append(line.strip())
            if current_name and current_seq:
                sequences.append((current_name, "".join(current_seq)))

            if not sequences:
                st.error("No sequences found in FASTA file.")
            else:
                st.write(f"Found {len(sequences)} sequences. Running evaluation pipeline...")

                # Build design dicts for the evaluation pipeline
                extra_designs = []
                for seq_name, seq in sequences:
                    design_id = f"{designer_name}_{seq_name}"
                    extra_designs.append({
                        "design_id": design_id,
                        "tool": designer_name,
                        "strategy": strategy,
                        "status": "designed",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "sequence": seq,
                        "num_residues": len(seq),
                        "source_files": {},
                        "design_metrics": {"source": designer_name},
                        "refolding": None,
                        "validation": None,
                        "scoring": None,
                        "composite_score": None,
                    })

                with st.spinner("Running evaluation pipeline with custom designs..."):
                    try:
                        eval_path = Path(__file__).resolve().parent.parent / "pgdh_campaign"
                        sys.path.insert(0, str(eval_path))
                        from evaluate_designs import run_evaluation
                        results = run_evaluation(
                            client=get_client(),
                            extra_designs=extra_designs,
                        )
                        st.session_state.pop("tracker", None)
                        st.success(f"Uploaded {len(sequences)} designs. {len(results)} total designs evaluated.")
                    except Exception as e:
                        st.error(f"Evaluation failed: {e}")
                        import traceback
                        st.code(traceback.format_exc())


# ══════════════════════════════════════════════════════════════════════════
# DESIGN DETAIL PAGE
# ══════════════════════════════════════════════════════════════════════════

elif page == "Design Detail":
    tracker = require_tracker()
    designs = tracker.list_designs()

    st.header("Design Detail")

    if not designs:
        st.info("No designs tracked yet.")
        st.stop()

    # Use stored selection or let user pick
    default_id = st.session_state.get("detail_design_id", "")
    design_ids = [d["id"] for d in designs]
    default_idx = design_ids.index(default_id) if default_id in design_ids else 0

    selected_id = st.selectbox("Select design", design_ids, index=default_idx)
    design = tracker.get_design(selected_id)

    if not design:
        st.warning("Design not found.")
        st.stop()

    # Header info
    hc1, hc2, hc3, hc4 = st.columns(4)
    hc1.metric("Tool", TOOL_LABELS.get(design.get("tool", ""), design.get("tool", "")))
    hc2.metric("Strategy", design.get("strategy", ""))
    hc3.metric("Status", f"{STATUS_COLORS.get(design.get('status', ''), '')} {design.get('status', '')}")
    hc4.metric("Residues", design.get("num_residues", "?"))

    # Metrics
    st.subheader("Metrics")
    metrics = design.get("metrics", {})
    if metrics:
        mc = st.columns(min(4, len(metrics)))
        for i, (key, val) in enumerate(metrics.items()):
            cls = classify_metric(key, val)
            color = {"good": "green", "warn": "orange", "bad": "red"}.get(cls, "gray")
            mc[i % len(mc)].markdown(
                f"**{key}**: :{color}[{_fmt_metric(val)}]"
            )
    else:
        st.info("No metrics available for this design.")

    # Sequence
    st.subheader("Sequence")
    seq = design.get("sequence", "")
    if seq:
        st.code(seq, language=None)
        st.caption(f"{len(seq)} amino acids")
    else:
        st.info("No sequence (backbone-only design from RFdiffusion3)")

    # Structure viewer placeholder
    st.subheader("3D Structure")
    st.info(
        "Structure viewer requires `stmol` or `streamlit-molstar`. "
        "Install with `pip install stmol py3Dmol` and uncomment the viewer code below."
    )
    # Uncomment when stmol is installed:
    # import py3Dmol
    # from stmol import showmol
    # cif_key = design.get("source_key", "").replace(".csv", ".cif").replace(".json", ".cif")
    # if cif_key:
    #     try:
    #         cif_data = get_client().download_bytes(cif_key).decode()
    #         view = py3Dmol.view(width=700, height=500)
    #         view.addModel(cif_data, "cif")
    #         view.setStyle({"cartoon": {"color": "spectrum"}})
    #         view.zoomTo()
    #         showmol(view, height=500, width=700)
    #     except Exception as e:
    #         st.warning(f"Could not load structure: {e}")

    # Notes & status (auth required)
    if is_authenticated():
        st.subheader("Notes")
        notes = st.text_area("Design notes", value=design.get("notes", ""), key="design_notes")
        if st.button("Save Notes"):
            tracker.update_design(selected_id, notes=notes)
            st.success("Notes saved!")

        st.subheader("Update Status")
        new_status = st.selectbox(
            "New status",
            ["designed", "validated", "scored", "selected", "failed"],
            index=["designed", "validated", "scored", "selected", "failed"].index(
                design.get("status", "designed")
            ),
        )
        if st.button("Update Status"):
            tracker.update_design(selected_id, status=new_status)
            st.success(f"Status updated to '{new_status}'")
            st.rerun()
