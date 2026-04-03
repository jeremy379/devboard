#!/usr/bin/env python3
"""
devboard — local refresh server
Run: python3 server.py
Open: http://localhost:8765
"""

import http.server
import subprocess
import json
import os
import re
import urllib.parse
import urllib.request
import base64
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).parent
HTML_FILE = DIR / "index.html"
ENV_FILE = DIR / ".env"
USER_DATA_FILE = DIR / "user_data.json"


def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("\"'")
    return env


PORT = int(load_env().get("PORT", 8765))


def fetch_prs(github_org):
    result = subprocess.run(
        [
            "gh", "search", "prs",
            "--author", "@me",
            "--state", "open",
            "--json", "number,title,isDraft,repository,url",
            "--limit", "50",
            f"org:{github_org}",
        ],
        capture_output=True, text=True, cwd=str(DIR),
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh CLI error: {result.stderr.strip()}")
    return json.loads(result.stdout)


def fetch_jira_ticket(key, email, token, jira_base_url):
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    url = f"{jira_base_url}/rest/api/3/issue/{key}?fields=summary,status"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {creds}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def fetch_jira_assigned(email, token, jira_base_url):
    """Fetch all open Jira tickets assigned to the user."""
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    # Resolve account ID from email first
    user_url = f"{jira_base_url}/rest/api/3/user/search?query={urllib.parse.quote(email)}"
    account_id = None
    try:
        req = urllib.request.Request(
            user_url,
            headers={"Authorization": f"Basic {creds}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            users = json.loads(r.read())
            if users:
                account_id = users[0]["accountId"]
    except Exception as e:
        print(f"  [warn] Could not resolve Jira account ID: {e}")
        return []

    jql = urllib.parse.quote(f'assignee = "{account_id}" AND statusCategory != Done AND status != Cancelled ORDER BY updated DESC')
    url = f"{jira_base_url}/rest/api/3/search/jql?jql={jql}&fields=summary,status&maxResults=50"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {creds}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return data.get("issues", [])
    except Exception as e:
        print(f"  [warn] Could not fetch Jira assigned tickets: {e}")
        return []



def fetch_pr_review_decision(github_org, repo, number):
    result = subprocess.run(
        ["gh", "pr", "view", str(number), "--repo", f"{github_org}/{repo}", "--json", "reviewDecision"],
        capture_output=True, text=True, cwd=str(DIR),
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout).get("reviewDecision")
    except Exception:
        return None


def extract_jira_keys(text):
    return list(set(re.findall(r"[A-Z]+-\d+", text)))



    return list(set(re.findall(r"[A-Z]+-\d+", text)))


def build_data(prs_raw, email, token, github_org, jira_base_url):
    tickets: dict = {}
    no_ticket = []

    for pr in prs_raw:
        repo = pr["repository"]["name"]
        keys = extract_jira_keys(pr["title"])
        pr_obj = {
            "number": pr["number"],
            "title": pr["title"],
            "repo": repo,
            "url": pr["url"],
            "draft": pr["isDraft"],
            "approved": fetch_pr_review_decision(github_org, repo, pr["number"]) == "APPROVED",
        }
        if keys:
            for key in keys:
                if key not in tickets:
                    tickets[key] = {"prs": []}
                tickets[key]["prs"].append(pr_obj)
        else:
            no_ticket.append(pr_obj)

    result_tickets = []
    for key, info in sorted(tickets.items()):
        jira = fetch_jira_ticket(key, email, token, jira_base_url)
        if jira:
            status = jira["fields"]["status"]["name"]
            summary = jira["fields"]["summary"]
        else:
            status = "Unknown"
            summary = key
        result_tickets.append(
            {
                "jira_key": key,
                "jira_title": summary,
                "jira_status": status,
                "jira_url": f"{jira_base_url}/browse/{key}",
                "prs": info["prs"],
            }
        )

    # Jira tickets assigned to me but with no open PR
    assigned = fetch_jira_assigned(email, token, jira_base_url)
    pr_keys = set(tickets.keys())
    no_pr_tickets = []
    for issue in assigned:
        key = issue["key"]
        if key not in pr_keys:
            no_pr_tickets.append({
                "jira_key": key,
                "jira_title": issue["fields"]["summary"],
                "jira_status": issue["fields"]["status"]["name"],
                "jira_url": f"{jira_base_url}/browse/{key}",
            })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tickets": result_tickets,
        "no_ticket": no_ticket,
        "no_pr_tickets": no_pr_tickets,
    }


DATA_FILE = DIR / "data.json"


def write_data_json(data):
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def refresh():
    env = load_env()
    email = env.get("JIRA_EMAIL", "")
    token = env.get("JIRA_TOKEN", "")
    github_org = env.get("GITHUB_ORG", "")
    jira_base_url = env.get("JIRA_BASE_URL", "").rstrip("/")
    if not email or not token:
        raise RuntimeError("Missing JIRA_EMAIL or JIRA_TOKEN in .env — see .env.example")
    if not github_org:
        raise RuntimeError("Missing GITHUB_ORG in .env — see .env.example")
    if not jira_base_url:
        raise RuntimeError("Missing JIRA_BASE_URL in .env — see .env.example")
    prs_raw = fetch_prs(github_org)
    data = build_data(prs_raw, email, token, github_org, jira_base_url)
    write_data_json(data)
    return data


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", ""):
            self.path = "/index.html"
        if self.path == "/user-data":
            body = json.dumps(load_user_data()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in ("/index.html", "/"):
            env = load_env()
            app_name = env.get("APP_NAME", "Task Follow-up")
            app_emoji = env.get("APP_EMOJI", "📋")
            html = HTML_FILE.read_text()
            html = html.replace("{{APP_NAME}}", app_name).replace("{{APP_EMOJI}}", app_emoji)
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/user-data":
            length = int(self.headers.get("Content-Length", 0))
            body_raw = self.rfile.read(length)
            try:
                data = json.loads(body_raw)
                save_user_data(data)
                body = b'{"ok":true}'
                self.send_response(200)
            except Exception as e:
                body = json.dumps({"ok": False, "error": str(e)}).encode()
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/refresh":
            try:
                data = refresh()
                body = json.dumps({"ok": True, "generated_at": data["generated_at"]}).encode()
                self.send_response(200)
            except Exception as e:
                body = json.dumps({"ok": False, "error": str(e)}).encode()
                self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")


if __name__ == "__main__":
    os.chdir(DIR)
    print(f"\n📋  devboard")
    print(f"    http://localhost:{PORT}\n")
    env = load_env()
    if not env.get("JIRA_EMAIL") or not env.get("JIRA_TOKEN"):
        print("  ⚠️  .env not configured — copy .env.example and fill in credentials\n")
    server = http.server.HTTPServer(("localhost", PORT), Handler)
    server.serve_forever()
