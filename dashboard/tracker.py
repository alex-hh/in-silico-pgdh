"""S3-backed campaign state manager.

Reads/writes tracker/state.json on Lyceum S3 to track designs,
jobs, and campaign metadata. Used by both the Streamlit app and
Claude Code for agentic orchestration.
"""

import json
from datetime import datetime, timezone

from client import LyceumClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state() -> dict:
    return {
        "campaign": "pgdh_2gdz",
        "updated_at": _now(),
        "designs": [],
        "jobs": [],
    }


class CampaignTracker:
    """Reads/writes tracker/state.json on Lyceum S3."""

    STATE_KEY = "tracker/state.json"

    def __init__(self, client: LyceumClient):
        self.client = client
        self.state = self._load()

    def _load(self) -> dict:
        """Download tracker/state.json from S3, return parsed dict."""
        try:
            data = self.client.download_bytes(self.STATE_KEY)
            return json.loads(data)
        except Exception:
            return _default_state()

    def _save(self):
        """Upload state.json back to S3."""
        self.state["updated_at"] = _now()
        data = json.dumps(self.state, indent=2).encode()
        self.client.upload_bytes(data, self.STATE_KEY)

    def reload(self):
        """Re-download state from S3."""
        self.state = self._load()

    # ── Design management ────────────────────────────────────────────────

    def list_designs(self) -> list[dict]:
        return self.state.get("designs", [])

    def get_design(self, design_id: str) -> dict | None:
        for d in self.state.get("designs", []):
            if d["id"] == design_id:
                return d
        return None

    def add_designs(self, designs: list[dict]):
        """Add designs, skipping duplicates by id."""
        existing_ids = {d["id"] for d in self.state.get("designs", [])}
        for d in designs:
            if d["id"] not in existing_ids:
                self.state["designs"].append(d)
                existing_ids.add(d["id"])
        self._save()

    def update_design(self, design_id: str, **fields):
        """Update fields on a design by id."""
        for d in self.state.get("designs", []):
            if d["id"] == design_id:
                d.update(fields)
                break
        self._save()

    def bulk_update_status(self, design_ids: list[str], status: str):
        """Set status on multiple designs at once."""
        id_set = set(design_ids)
        for d in self.state.get("designs", []):
            if d["id"] in id_set:
                d["status"] = status
        self._save()

    # ── Job tracking ─────────────────────────────────────────────────────

    def list_jobs(self) -> list[dict]:
        return self.state.get("jobs", [])

    def add_job(self, job: dict):
        """Add a job record."""
        self.state.setdefault("jobs", []).append(job)
        self._save()

    def update_job(self, job_id: str, **fields):
        """Update fields on a job by id."""
        for j in self.state.get("jobs", []):
            if j["id"] == job_id:
                j.update(fields)
                break
        self._save()

    # ── Sync from S3 outputs ─────────────────────────────────────────────

    def sync_boltzgen(self, loader_fn, prefix="output/boltzgen/"):
        """Sync BoltzGen designs from S3 CSVs into state."""
        files = self.client.list_files(prefix)
        csv_files = [f for f in files if f.endswith(".csv")]
        if not csv_files:
            return 0
        designs = []
        for csv_key in csv_files:
            data = self.client.download_bytes(csv_key)
            designs.extend(loader_fn(data.decode(), csv_key))
        self.add_designs(designs)
        return len(designs)

    def sync_rfd3(self, loader_fn, prefix="output/rfdiffusion3/"):
        """Sync RFdiffusion3 designs from S3 JSONs into state."""
        files = self.client.list_files(prefix)
        json_files = [f for f in files if f.endswith(".json")]
        if not json_files:
            return 0
        designs = []
        for json_key in json_files:
            data = self.client.download_bytes(json_key)
            designs.extend(loader_fn(data.decode(), json_key))
        self.add_designs(designs)
        return len(designs)
