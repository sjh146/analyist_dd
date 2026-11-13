#!/usr/bin/env bash
# Move this repo's git metadata back inside the project folder.
#
# Why this exists: /mnt/c is mounted without the drvfs `metadata` option, so
# chmod fails there and `git init` cannot create .git in place -- the repo was
# created with `--separate-git-dir` and its metadata lives outside the folder.
#
# Run this AFTER enabling metadata for /mnt/c (see /etc/wsl.conf):
#
#     [automount]
#     options = "metadata,umask=22,fmask=11"
#
# ...and after `wsl --shutdown` + reopening WSL.  Then:
#
#     bash migrate_git_to_folder.sh
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POINTER="$REPO_DIR/.git"
EXTERNAL_DEFAULT="/home/$(whoami)/git-repos/trader-agent.git"

cd "$REPO_DIR"

if [ ! -f "$POINTER" ]; then
    echo "no .git pointer file here -- nothing to migrate (already a normal repo?)"
    exit 0
fi

GIT_DIR_PATH="$(sed -n 's/^gitdir: //p' "$POINTER")"
if [ -z "$GIT_DIR_PATH" ]; then
    echo "ERROR: .git is not a pointer file; aborting" >&2
    exit 1
fi
echo "external git dir: $GIT_DIR_PATH"

# 1) The metadata mount must actually support chmod, or the move leaves a
#    half-broken repo.  Fail early instead.
probe="$REPO_DIR/.chmod_probe"
: > "$probe" 2>/dev/null || { echo "ERROR: cannot write probe file" >&2; exit 1; }
if ! chmod 600 "$probe" 2>/dev/null; then
    rm -f "$probe"
    echo "ERROR: chmod still not permitted on this filesystem." >&2
    echo "        Enable metadata for /mnt/c in /etc/wsl.conf, run 'wsl --shutdown'," >&2
    echo "        reopen WSL, then run this script again." >&2
    exit 1
fi
rm -f "$probe"
echo "chmod works here -- proceeding"

# 2) Refuse to run if the working tree is dirty (avoid surprising a move).
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: working tree has uncommitted changes; commit or stash first" >&2
    exit 1
fi

# 3) Move the metadata in: remove the pointer, move the directory.
rm -f "$POINTER"
mv "$GIT_DIR_PATH" "$POINTER"
echo "moved git dir into $POINTER"

# 4) Verify.
git status --short | head -5 || true
git rev-parse --git-dir
echo "HEAD: $(git log --oneline -1)"
echo "remote: $(git remote get-url origin)"
echo "OK: this folder is now a normal git repository."
