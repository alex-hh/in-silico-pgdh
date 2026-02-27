# Task 1: Lyceum Docs & Port Plan

## Status: Done

## Objective
Understand Lyceum's execution model and write a detailed plan for porting all biomodals entrypoints.

## What was done
- Read Lyceum documentation (API reference, CLI usage, storage model, execution modes)
- Mapped Modal concepts to Lyceum equivalents
- Identified two execution modes: Python execution (simple) and Docker execution (complex)
- Created per-entrypoint porting plan with priority ordering
- Wrote task files for all remaining work

## Key Findings

### Modal → Lyceum Mapping

| Modal | Lyceum |
|-------|--------|
| `modal.Image.debian_slim().pip_install(...)` | `requirements.txt` or Docker base image |
| `@app.function(gpu="A100")` | `-m gpu.a100` CLI flag |
| `@app.local_entrypoint()` | Local Python CLI calling Lyceum API |
| `modal.Volume` | `/lyceum/storage/` auto-mounted S3 |
| `modal.Secret` | `docker_run_env` environment variables |
| `.remote()` call | `lyceum python run script.py -m gpu.a100 -r requirements.txt` |

### Execution Modes
1. **Python execution** (simple tools): `lyceum python run script.py -r requirements.txt -m gpu.a100`
2. **Docker execution** (complex tools): `POST /execution/image/start` with public base image + setup commands

### Lyceum API Reference
- Base URL: `https://api.lyceum.technology/api/v2/external`
- Storage auto-mounted at `/lyceum/storage/` in all executions
- GPU types: `gpu` (T4), `gpu.a100`, `gpu.h100`, `gpu.b200`

## Acceptance Criteria
- [x] Lyceum docs read and understood
- [x] Modal-to-Lyceum mapping documented
- [x] Task files written for all remaining work
- [x] `/bioltask` skill created
