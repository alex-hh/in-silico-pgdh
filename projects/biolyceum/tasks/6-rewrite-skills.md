# Task 6: Rewrite Skills for Biolyceum

## Status: Pending

## Objective
Update the Claude Code skills to include Lyceum as a compute option alongside Modal.

## Skills to Update

### `_shared/compute-setup.md`
Add Lyceum as a compute provider option:
- Lyceum CLI installation: `pip install lyceum-cli`
- Authentication: `lyceum auth login` or `LYCEUM_API_KEY` env var
- Storage model: `/lyceum/storage/` auto-mounted S3
- Execution modes: Python (`lyceum python run`) and Docker (`POST /execution/image/start`)

### `setup/SKILL.md`
Add Lyceum setup path alongside Modal setup.

### Per-tool skills
For each tool skill (chai, boltz, rfdiffusion, etc.), add Lyceum command examples:
```
## Running on Lyceum
lyceum python run src/lyceum_chai1.py -r src/requirements/chai1.txt -m gpu.a100

## Or via client.py
python -c "
from src.utils.client import LyceumClient
client = LyceumClient()
client.run('src/lyceum_chai1.py', requirements='src/requirements/chai1.txt', ...)
"
```

### `/bioltask` skill
Already created in Task 1. Verify it works:
```
/bioltask 4
```
Should load task 4 and guide the user through it.

## Files to Modify
- `.claude/skills/_shared/compute-setup.md`
- `.claude/skills/setup/SKILL.md`
- `.claude/skills/` — each tool-specific skill file
- `.claude/skills/bioltask/SKILL.md` (created in Task 1)

## Acceptance Criteria
- [ ] `compute-setup.md` includes Lyceum provider docs
- [ ] Setup skill includes Lyceum installation path
- [ ] At least the Tier 1 tool skills have Lyceum examples
- [ ] `/bioltask` skill works correctly

## Dependencies on Other Tasks
- Task 5 (need working entrypoints to document correct commands)
