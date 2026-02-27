# /bioltask — Biolyceum Task Runner

## Description
Loads a biolyceum task file and guides the user through it.

## Usage
```
/bioltask <task_number>
```

## Instructions

1. Read the task file at `projects/biolyceum/tasks/<task_number>-*.md` (glob for the matching file)
2. Display the task title, status, and objective
3. Walk through each step, asking the user before proceeding to the next
4. For steps that involve running commands, show the command and ask if the user wants to execute it
5. Track which acceptance criteria are met as you go
6. When all acceptance criteria are met, suggest marking the task as done by updating its status

## Arguments
- `task_number` (required): The task number to load (1-6)

## Example
```
/bioltask 4
```
Loads Task 4 (Port ESM2) and guides through: reading the source, writing the script, testing on Lyceum, verifying outputs.
