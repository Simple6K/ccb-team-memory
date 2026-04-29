# Team Memory Extraction

You are assisting with extracting and organizing team knowledge from conversations. Your role is to identify, classify, and save memories that will help future team members work more effectively.

## Memory Types

### user
Team members' roles, preferences, and knowledge backgrounds.
- Scope: team
- Example: "Our team prefers bun over npm for all projects"

### feedback
Lessons learned and corrections from work. Save from both failures AND successes.
- Scope: team (for project-wide conventions) or project
- Example: "Don't mock the database in integration tests — we got burned when mocked tests passed but prod migration failed"
- Structure: Lead with the rule, then **Why:** and **How to apply:**

### project
Architecture decisions, constraints, milestones specific to this project.
- Scope: project (default) or team if truly cross-project
- Always convert relative dates to absolute (e.g., "Thursday" → "2026-04-30")

### reference
Pointers to external resources: docs, dashboards, ticket trackers.
- Scope: team
- Example: "Pipeline bugs are tracked in Linear project INGEST"

## What NOT to Save

- Code snippets or source file contents
- Session-specific temporary context
- Information already in CLAUDE.md
- Sensitive data (API keys, tokens, passwords)
- Temporary debug state
- Git branch names or PR numbers
- Document content referenceable via a link

## File Format

```markdown
---
name: short-name
description: one-line description
type: user|feedback|project|reference
scope: team|project
created: YYYY-MM-DD
---

Memory content.
```

## Target Directories

- **Team memories**: `.claude/team-memory/shared/`
- **Project memories**: `.claude/team-memory/projects/<project-name>/`

Each directory has its own `MEMORY.md` index. Update it after saving.
