# Task 2: Check Lyceum Access

## Status: Pending

## Objective
Verify we have working Lyceum access — CLI, GPU execution, and storage.

## Steps

### 1. Install CLI
```bash
pip install lyceum-cli
```

### 2. Authenticate
```bash
lyceum auth login
```
Follow the prompts to authenticate. Store the API key in `LYCEUM_API_KEY` env var for later use by `client.py`.

### 3. Test basic execution
```bash
lyceum python run "print('hello from lyceum')"
```

### 4. Test GPU access
```bash
lyceum python run "import torch; print(f'CUDA available: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0)}')" -m gpu --import torch
```

### 5. Test storage
```bash
# Upload a test file
echo "test content" > /tmp/lyceum_test.txt
# Use the API or CLI to upload to storage
lyceum storage upload /tmp/lyceum_test.txt --key test/hello.txt

# Verify it's accessible in an execution
lyceum python run "print(open('/lyceum/storage/test/hello.txt').read())"
```

### 6. Check available credits
```bash
# Via API
curl -H "Authorization: Bearer $LYCEUM_API_KEY" \
  https://api.lyceum.technology/api/v2/external/user/credits
```

## Acceptance Criteria
- [ ] `lyceum-cli` installed and authenticated
- [ ] Basic Python execution works
- [ ] GPU execution works (torch.cuda.is_available() returns True)
- [ ] Storage upload/download works
- [ ] Know available credits and GPU quotas

## Notes
- If CLI is not available, fall back to REST API directly
- Document any differences from the API reference in task 1
- Save any auth tokens/keys securely (env var, not committed)
