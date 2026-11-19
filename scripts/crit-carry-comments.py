#!/usr/bin/env python3
"""Carry crit line comments from one round's files to the next.

  crit-carry-comments.py <review.json> <old-dir> <new-dir>

crit only moves comments between rounds inside a running review session.
share-pr-rounds.sh rewrites the files on disk and shares headless, so without
this every comment keeps its old line numbers and lands on unrelated new code
(crit-web re-inserts comments at the lines the CLI sends).

Mirrors crit-web's own carry-forward (lib/crit/comment_carry_forward.ex and
LineDiff.map_old_to_new):
  - unchanged lines map straight across; a removed line maps to the next new
    line after it (the last new line at the end of the file);
  - the comment's anchor (the text it was written on) is checked at the mapped
    line: still recognisable -> placed there; found elsewhere -> followed
    (nearest exact match); gone -> stays on the mapped line, drifted = true.
The anchor itself is kept as the original text, so later rounds still compare
against what the reviewer saw. Replies, file and review comments are untouched.
"""
import difflib
import json
import os
import sys


def read_lines(path):
    try:
        with open(path, encoding='utf-8', newline='') as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    text = text.replace('\r\n', '\n')
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines


def line_map(old, new):
    """1-based old line -> new line, as crit-web's map_old_to_new."""
    mapped = {}
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    removed = []  # (old_line, next_new_line_after_the_removal)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                mapped[i1 + k + 1] = j1 + k + 1
        elif tag in ('delete', 'replace'):
            # next new line: first new line of this block if it inserts any
            # (replace), else the first new line after it
            nxt = j1 + 1 if j1 < len(new) else 0
            for k in range(i1, i2):
                removed.append((k + 1, nxt))
    last_new = len(new)
    for old_line, nxt in removed:
        if old_line not in mapped:
            mapped[old_line] = nxt if nxt else last_new
    return mapped


def size(s):
    return len(s.encode('utf-8'))  # crit-web compares byte sizes


def one_middle_cut(short, long):
    """short is long with one stretch cut out of the middle (or a prefix)."""
    if size(short) >= size(long):
        return False
    if long.startswith(short):
        return True
    return any(long.startswith(short[:k]) and long.endswith(short[k:])
               for k in range(len(short) + 1))


def levenshtein_ratio(a, b):
    if not a and not b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1 - prev[-1] / max(len(a), len(b))


def similar(candidate, anchor):
    """crit-web's anchor_similar?: equal when trimmed, contained or cut in the
    middle (shorter side >= 8 bytes), or a Levenshtein ratio >= 0.7."""
    a, b = candidate.strip(), anchor.strip()
    if a == b:
        return True
    if not a or not b:
        return False
    shorter, longer = (a, b) if size(a) <= size(b) else (b, a)
    if size(shorter) >= 8 and (shorter in longer or one_middle_cut(shorter, longer)):
        return True
    return levenshtein_ratio(a, b) >= 0.7


def anchored_at(lines, start, anchor):
    n = len(anchor.split('\n'))
    if start < 1 or start + n - 1 > len(lines):
        return False
    cand = '\n'.join(lines[start - 1:start - 1 + n])
    return cand == anchor or similar(cand, anchor)


def find_anchor(lines, anchor, preferred):
    al = anchor.split('\n')
    n = len(al)
    hits = [i + 1 for i in range(0, len(lines) - n + 1) if lines[i:i + n] == al]
    if not hits:
        return 0
    return min(hits, key=lambda h: abs(h - preferred))


def place(old, new, start, end, anchor):
    lm = line_map(old, new)
    max_line = max(len(new), 1)
    s = lm.get(start) or start
    e = lm.get(end) or end
    s = max(1, min(s, max_line))
    e = max(s, min(e, max_line))
    if not anchor:
        return s, e, False
    n = len(anchor.split('\n'))
    if anchored_at(new, s, anchor):
        return s, s + n - 1, False
    found = find_anchor(new, anchor, s)
    if found:
        return found, found + n - 1, False
    return s, e, True


def main():
    review_path, old_dir, new_dir = sys.argv[1:4]
    with open(review_path, encoding='utf-8') as f:
        review = json.load(f)
    moved = drifted = 0
    for path, entry in (review.get('files') or {}).items():
        comments = entry.get('comments') or []
        if not comments:
            continue
        old = read_lines(os.path.join(old_dir, path))
        new = read_lines(os.path.join(new_dir, path))
        if old is None or new is None or old == new:
            continue
        for c in comments:
            start, end = c.get('start_line') or 0, c.get('end_line') or 0
            if c.get('scope', 'line') != 'line' or start < 1 or end < start:
                continue
            anchor = c.get('anchor') or '\n'.join(old[start - 1:end])
            s, e, d = place(old, new, start, end, anchor)
            if (s, e) != (start, end) or d != bool(c.get('drifted')):
                print(f'  {path}:{start}-{end} -> {s}-{e}' + (' (drifted)' if d else ''))
                moved += 1
                drifted += d
            c['start_line'], c['end_line'], c['anchor'] = s, e, anchor
            c['drifted'] = d
    with open(review_path, 'w', encoding='utf-8') as f:
        json.dump(review, f, indent=2)
    print(f'carried comments: {moved} moved, {drifted} drifted')


if __name__ == '__main__':
    main()
