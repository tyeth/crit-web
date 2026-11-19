#!/usr/bin/env bash
# Share a PR (or uncommitted work) to crit-web as a round-diff review.
#
# crit-web has no git-diff shares, so the review is built from two rounds:
#   round 1 = the changed files at the base, round 2 = the same files at head.
# tyeth/crit-web (branch code-round-diff) renders "Toggle diff" for code files
# too (Pierre FileDiff, split/unified), so both rounds together show the change
# with the same diff UI as local crit. A PR-<n>.md / WIP.md cover carries the
# title, link and description (its name becomes the review title).
#
#   share-pr-rounds.sh <repo-dir> <pr-number> [extra-exclude-regex]
#   share-pr-rounds.sh <repo-dir> wip         [extra-exclude-regex]   # HEAD vs working tree
#
# Re-run after new pushes / edits to add a round (same link); Toggle diff then
# shows what changed since the previous share. Comments come back with
# `crit fetch` run in the work dir printed below.
#
# Re-runs keep the review consistent:
#   - the exclude regex is remembered per work dir (.share-exclude), so a
#     re-run without it shares the same files;
#   - the file list only grows (.share-files): a file the PR stops touching
#     stays in the review, a new one is added, and the review file's
#     share_scope is pinned to the list so crit updates the same link instead
#     of silently creating a new review;
#   - before a new round, the previous round is re-shared unchanged. That
#     pulls web comments into the local review and sends them back with an id,
#     so crit can match them later;
#   - comments are then carried to their new lines (crit-carry-comments.py,
#     same rules as crit-web), or marked drifted when their code is gone.
# Needs: git, gh (PR mode), python, crit logged in to the share target.
# Helper tests: python -m unittest scripts/test_crit_carry_comments.py
set -euo pipefail

REPO=${1:?repo dir}; WHAT=${2:?pr number or wip}; EXTRA=${3:-}
WORKROOT=${CRIT_PR_WORK:-$HOME/.crit-pr-shares}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$(git -C "$REPO" rev-parse --show-toplevel)"
# Name work dirs after the origin repo, so a worktree shares the same review.
NAME=$(basename -s .git "$(git remote get-url origin 2>/dev/null || pwd)")
lf() { tr -d '\r'; }   # GitHub bodies are CRLF; lone CRs break crit-web's markdown

if [ "$WHAT" = wip ]; then
  BRANCH=$(git rev-parse --abbrev-ref HEAD)
  WORK="$WORKROOT/$NAME-wip-${BRANCH//\//_}"
else
  PR=$WHAT
  WORK="$WORKROOT/$NAME-PR$PR"
fi

# Exclude regex: given now, or the one this review was first shared with.
EXCLUDE='\.test\.only$'
if [ -z "$EXTRA" ] && [ -f "$WORK/.share-exclude" ]; then
  EXTRA=$(cat "$WORK/.share-exclude")
  [ -n "$EXTRA" ] && echo "using the saved exclude regex: $EXTRA"
fi
[ -n "$EXTRA" ] && EXCLUDE="$EXCLUDE|$EXTRA"

if [ "$WHAT" = wip ]; then
  BASE=HEAD; HEADREV=WORKTREE
  COVER="WIP.md"
  mapfile -t NEWFILES < <( { git diff --name-only HEAD; git ls-files --others --exclude-standard; } | sort -u | grep -vE "$EXCLUDE" || true)
  cover() {
    printf '# %s: uncommitted changes on `%s`\n\nRound 1 = `HEAD` (%s), later rounds = working tree. Use **Toggle diff**.\n\n```\n%s\n```\n' \
      "$NAME" "$BRANCH" "$(git rev-parse --short HEAD)" "$(git diff --stat=100 HEAD -- "${FILES[@]}" 2>/dev/null)"
  }
  at_base() { git cat-file -e "HEAD:$1" 2>/dev/null && git show "HEAD:$1"; }
  at_head() { [ -f "$1" ] && cat "$1"; }
else
  meta=$(gh pr view "$PR" --json title,url,body,headRefOid,baseRefName,headRefName)
  field() { printf '%s' "$meta" | python -I -c "import json,sys; sys.stdout.write(json.load(sys.stdin)['$1'])" | lf; }
  HEADREV=$(field headRefOid); BASEREF=$(field baseRefName)
  git cat-file -e "$HEADREV^{commit}" 2>/dev/null || git fetch -q origin "pull/$PR/head"
  git fetch -q origin "$BASEREF"
  BASE=$(git merge-base "origin/$BASEREF" "$HEADREV")
  COVER="PR-$PR.md"
  mapfile -t NEWFILES < <(git diff --name-only "$BASE" "$HEADREV" | grep -vE "$EXCLUDE" || true)
  cover() {
    printf '# PR #%s: %s\n\n%s\n\nBranch `%s` vs `%s`: round 1 = merge base %s, later rounds = PR head (now %s). Use **Toggle diff**.\n\n```\n%s\n```\n\n---\n\n%s\n' \
      "$PR" "$(field title)" "$(field url)" "$(field headRefName)" "$BASEREF" \
      "$(git rev-parse --short "$BASE")" "$(git rev-parse --short "$HEADREV")" \
      "$(git diff --stat=100 "$BASE" "$HEADREV" -- "${FILES[@]}")" "$(field body)"
  }
  at_base() { git cat-file -e "$BASE:$1" 2>/dev/null && git show "$BASE:$1"; }
  at_head() { git cat-file -e "$HEADREV:$1" 2>/dev/null && git show "$HEADREV:$1"; }
fi

# The script's own files in the work dir (not shared)
STATE_FILTER="! -path ./.prev-round/* ! -path ./.share-files ! -path ./.share-exclude"
set -f   # STATE_FILTER is word-split into find arguments, never globbed

# The list shared last time (bootstrap for work dirs older than .share-files:
# whatever the work dir holds).
PREV=()
if [ -f "$WORK/.share-files" ]; then
  mapfile -t PREV < "$WORK/.share-files"
elif [ -d "$WORK" ]; then
  mapfile -t PREV < <(cd "$WORK" && find . -type f $STATE_FILTER ! -path "./$COVER" | sed 's|^\./||' | sort)
fi
# This round's list: everything shared before plus everything changed now.
mapfile -t FILES < <(printf '%s\n' "${PREV[@]}" "${NEWFILES[@]}" | sed '/^$/d' | sort -u)

[ ${#FILES[@]} -gt 0 ] || { echo "nothing to share"; exit 1; }
echo "$NAME $WHAT: ${#FILES[@]} files -> $WORK"
added=$(comm -13 <(printf '%s\n' "${PREV[@]}" | sed '/^$/d' | sort) <(printf '%s\n' "${FILES[@]}"))
[ ${#PREV[@]} -gt 0 ] && [ -n "$added" ] && printf 'new in this round:\n%s\n' "$added"

# A file missing at a revision is written as the marker that tyeth/crit-web
# diffs as an empty file (ABSENT_MARKER in code-file-view.js). Empty files get
# one line, as crit-web rejects empty content.
ABSENT='(file not present at this revision)'
write_round() {  # $1 = at_base|at_head
  local f out
  for f in "${FILES[@]}"; do
    out="$WORK/$f"; mkdir -p "$(dirname "$out")"
    if "$1" "$f" > "$out" 2>/dev/null; then
      [ -s "$out" ] || echo "(empty file)" > "$out"
    else
      echo "$ABSENT" > "$out"
    fi
  done
}
review_file() { (cd "$WORK" && crit status 2>/dev/null) | sed -n 's/^Review file: *//p' | head -n1 | lf; }
# Point the review file's share_scope at this exact list (crit computes it as
# sha256 of the sorted paths, first 16 hex). Without it crit treats a changed
# list as a different review and creates a new link.
pin_scope() {  # paths...
  local rf; rf=$(review_file)
  [ -n "$rf" ] && [ -f "$rf" ] || return 0
  python -I - "$rf" "$@" <<'EOF'
import hashlib, json, sys
rf, paths = sys.argv[1], sys.argv[2:]
d = json.load(open(rf, encoding='utf-8'))
if d.get('share_url'):
    scope = hashlib.sha256('\n'.join(sorted(paths)).encode()).hexdigest()[:16]
    if d.get('share_scope') != scope:
        d['share_scope'] = scope
        json.dump(d, open(rf, 'w', encoding='utf-8'), indent=2)
EOF
}
share() { pin_scope "$COVER" "$@"; (cd "$WORK" && crit share "$COVER" "$@"); }
save_state() {
  printf '%s\n' "${FILES[@]}" > "$WORK/.share-files"
  printf '%s' "$EXTRA" > "$WORK/.share-exclude"
}

if [ ! -d "$WORK" ]; then
  mkdir -p "$WORK"
  cover | lf > "$WORK/$COVER"; write_round at_base
  echo "round 1 (base):"; share "${FILES[@]}"
  save_state
elif [ -n "$(review_file)" ] && [ ${#PREV[@]} -gt 0 ]; then
  # Re-share the previous round unchanged: no new round, but web comments are
  # pulled in and sent back with an id before any line numbers move.
  echo "syncing comments on the previous round:"; share "${PREV[@]}"
fi

OLD="$WORK/.prev-round"
rm -rf "$OLD"; mkdir -p "$OLD"
(cd "$WORK" && find . -type f $STATE_FILTER -exec cp --parents {} "$OLD/" \;)
cover | lf > "$WORK/$COVER"; write_round at_head
RF=$(review_file)
[ -n "$RF" ] && [ -f "$RF" ] && python -I "$HERE/crit-carry-comments.py" "$RF" "$OLD" "$WORK"
rm -rf "$OLD"
echo "next round (head):"; share "${FILES[@]}"
save_state
