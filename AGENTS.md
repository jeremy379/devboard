# FAO AI Agents — devboard

## What this project is

A single-page local dashboard that aggregates open GitHub PRs and Jira tickets for a developer.  
No framework, no build step. A Python HTTP server (`server.py`) serves `index.html` and exposes a `/refresh` endpoint.

## How data flows

1. `POST /refresh` is called (by the browser button or via `curl`)
2. `server.py` runs `gh search prs` to get all open PRs authored by the user in `your-github-org`
3. For each PR, Jira ticket keys are extracted from the PR title (regex `[A-Z]+-\d+`)
4. Ticket details are fetched from Jira REST API (`/rest/api/3/issue/{key}`)
5. Jira tickets assigned to the user with no matching PR are fetched via `/rest/api/3/search/jql`
6. The result is serialised as JSON and injected into `index.html` as `const DATA = {...};` (single line, regex-replaced)
7. The browser reloads and re-renders from the inlined data

## Key files

| File | Role |
|------|------|
| `index.html` | Frontend — all JS inline, data inlined as `const DATA` |
| `server.py` | Python 3 stdlib HTTP server — serves files + `/refresh` endpoint |
| `.env` | `JIRA_EMAIL` + `JIRA_TOKEN` (gitignored) |
| `.env.example` | Template |

## Architecture decisions

- **Data inlined in HTML** — avoids CORS issues when opening as `file://`. The `const DATA = ...` line is a single long line so the regex `re.sub(r"  const DATA = \{.*?\};\n", ...)` can reliably replace it. Use a **lambda** in `re.sub` to avoid backslash interpretation in the replacement string.
- **No external dependencies** — pure Python 3 stdlib + `gh` CLI. No `pip install` needed.
- **Notes in localStorage** — keyed by Jira ticket key (e.g. `WIP-2619`). Not touched by refresh.

## When modifying server.py

- `fetch_prs()` — calls `gh search prs`. Returns list with keys: `number`, `title`, `isDraft`, `repository.name`, `url`
- `fetch_jira_ticket(key, email, token)` — single ticket fetch via `/rest/api/3/issue/{key}`
- `fetch_jira_assigned(email, token)` — JQL search via `/rest/api/3/search/jql` (NOT `/rest/api/3/search` — that returns 410)
- `build_data(prs_raw, email, token)` — assembles the final data dict
- `update_html(data)` — injects data into `index.html`. **Use lambda in re.sub** to avoid escaping issues with backslashes in JSON strings

## When modifying index.html

- The `const DATA` line **must remain a single line** — the server's regex depends on it
- Notes auto-save via debounced `input` event listener (600ms), stored in `localStorage` under key `devboard-task-notes`
- Jira status → CSS class mapping is in `statusClass()` — add new statuses there if needed
- All rendering happens in `Promise.resolve(DATA).then(data => { ... })` — keep it after the helper functions and event listeners

## Running locally

```bash
python3 server.py        # starts on http://localhost:8765
curl -X POST http://localhost:8765/refresh   # trigger refresh from CLI
```

## "Refresh my task follow-up" command

When a user says this, re-fetch all data and update `index.html`:

1. Call `github-mcp-server-search_pull_requests` with `query: "is:open author:@me org:your-github-org"`
2. Call `Atlassian-getJiraIssue` for each unique ticket key found in PR titles
3. Call `Atlassian-searchJiraIssuesUsingJql` with `assignee = currentUser() AND statusCategory != Done AND status != Cancelled`
4. Build the data structure matching the schema in `index.html`'s `const DATA`
5. Edit `index.html` replacing the `const DATA = ...` line
