#!/usr/bin/env python3
"""Parse .omo/plans/*.md plan files to structured JSON.

Usage:
    python3 scripts/plan-parser.py --plan .omo/plans/enable-metrics.md
    cat .omo/plans/enable-metrics.md | python3 scripts/plan-parser.py
"""

import argparse
import json
import re
import sys


def extract_file_paths(text: str) -> list:
    """Extract file paths from reference lines."""
    paths = []
    for line in text.split("\n"):
        line = line.strip()
        # Match lines with known prefixes: services/, config/, scripts/, .omo/, init-scripts/
        # or known top-level files: docker-compose.yml, Jenkinsfile
        # Also match paths like `services/...` (with backticks or without)
        m = re.findall(
            r"`?((?:services|config|scripts|\.omo|init-scripts)/[a-zA-Z0-9_./-]+\.[a-z]+(?:\.groovy|\.py|\.txt|\.yml|\.yaml|\.json|\.md|\.sh|\.env|\.cypher)?|docker-compose\.yml|Jenkinsfile)`?",
            line,
        )
        paths.extend(m)
    return list(dict.fromkeys(paths))  # deduplicate preserving order


def parse_plan(filepath: str, content: str) -> dict:
    """Parse a plan Markdown file into a structured dict."""

    # Extract slug from filename (preferred) or from the first `# slug - Title`
    if filepath:
        slug_match = re.search(r"/([^/]+)\.md$", filepath)
        slug = slug_match.group(1) if slug_match else "unknown"
    else:
        first_header = re.search(r"^#\s+(.+?)\s*-\s*(.+)$", content, re.MULTILINE)
        slug = first_header.group(1).strip() if first_header else "unknown"
    # Title from first heading
    first_header = re.search(r"^#\s+(.+?)\s*-\s*(.+)$", content, re.MULTILINE)
    title = first_header.group(2).strip() if first_header else "unknown"

    # Find the ## Todos section
    todos_match = re.search(r"^##\s+Todos\s*$", content, re.MULTILINE)
    if not todos_match:
        return {"slug": slug, "title": title, "todos": []}

    todos_section = content[todos_match.end() :]

    # Find all checkbox lines: `- [ ] N. **title**`
    todo_pattern = re.compile(
        r"^- \[\s*[ xX]\s*\]\s+(\d+)\.\s+\*\*(.+?)\*\*",
        re.MULTILINE,
    )

    todos = []
    prev_end = 0
    for match in todo_pattern.finditer(todos_section):
        todo_num = int(match.group(1))
        todo_title = match.group(2).strip()
        todo_start = match.start()
        todo_end = match.end()

        # Extract the entire todo block (from this todo to the next todo or end of section)
        next_match = todo_pattern.search(todos_section, todo_end)
        if next_match:
            block = todos_section[todo_start : next_match.start()]
        else:
            block = todos_section[todo_start:]

        # Extract What to do / Must NOT do block
        what_to_do_match = re.search(
            r"What to do / Must NOT do:\s*(.*?)(?=\n\s*(?:Parallelization|References|Acceptance criteria|Commit|QA scenarios))",
            block,
            re.DOTALL,
        )
        what_to_do = what_to_do_match.group(1).strip() if what_to_do_match else ""

        # Extract Acceptance criteria
        acceptance_match = re.search(
            r"Acceptance criteria:\s*(.*?)(?=\n\s*(?:QA scenarios|Parallelization|References|Commit))",
            block,
            re.DOTALL,
        )
        acceptance_raw = acceptance_match.group(1).strip() if acceptance_match else ""
        acceptance = [a.strip() for a in acceptance_raw.split("\n") if a.strip()]

        # Extract QA scenarios
        qa_match = re.search(
            r"QA scenarios:\s*(.*?)(?=\n\s*(?:Commit|Evidence|Parallelization|References))",
            block,
            re.DOTALL,
        )
        qa_raw = qa_match.group(1).strip() if qa_match else ""
        qa_commands = []
        for line in qa_raw.split("\n"):
            line = line.strip()
            if line.startswith("-") or re.match(r"^\d+\.", line):
                line = re.sub(r"^[-*]\s*(?:Happy|Failure|Edge):\s*", "", line)
                line = re.sub(r"^\d+\.\s*", "", line)
                if line:
                    qa_commands.append(line.strip())

        # Extract Commit message
        commit_match = re.search(r"Commit:\s*[YN]\s*\|\s*(.+?)(?:\n|$)", block, re.MULTILINE)
        commit_msg = commit_match.group(1).strip().strip("`") if commit_match else ""

        # Extract file paths from references
        files = extract_file_paths(block)

        todos.append(
            {
                "id": todo_num,
                "title": todo_title,
                "what_to_do": what_to_do,
                "files": files,
                "acceptance": acceptance,
                "qa_commands": qa_commands,
                "commit_msg": commit_msg,
            }
        )

    return {"slug": slug, "title": title, "todos": todos}


def main():
    parser = argparse.ArgumentParser(
        description="Parse .omo/plans/*.md plan files to JSON"
    )
    parser.add_argument("--plan", type=str, help="Path to plan file (.omo/plans/<slug>.md)")
    args = parser.parse_args()

    content = None
    filepath = None

    if args.plan:
        filepath = args.plan
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            print(f"Error: file not found: {filepath}", file=sys.stderr)
            sys.exit(1)
    else:
        # Read from stdin
        content = sys.stdin.read()
        if not content.strip():
            print("Error: no input provided (use --plan <path> or pipe stdin)", file=sys.stderr)
            sys.exit(1)

    result = parse_plan(filepath or "", content)

    if not result["todos"] and not content.startswith("#"):
        print("Error: invalid plan format", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
