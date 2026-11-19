"""Tests for crit-carry-comments.py: the cases of
test/crit/comment_carry_forward_test.exs, so the script places comments the
way crit-web's own carry-forward does, plus the review file round trip.

  python -m unittest scripts/test_crit_carry_comments.py
"""
import importlib.util
import json
import os
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location(
    'crit_carry_comments', os.path.join(os.path.dirname(__file__), 'crit-carry-comments.py'))
carry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(carry)


def lines(text):
    out = text.split('\n')
    if out and out[-1] == '':
        out.pop()
    return out


def place(old, new, start, end, anchor):
    return carry.place(lines(old), lines(new), start, end, anchor)


class PlaceLikeCritWeb(unittest.TestCase):
    def test_follows_an_anchor_when_lines_are_inserted_above_it(self):
        old = "# Plan\n\nStep 1\n\nStep 2\n"
        new = "# Plan\n\nNew line A\nNew line B\nStep 1\n\nStep 2\n"
        self.assertEqual(place(old, new, 3, 3, "Step 1"), (5, 5, False))

    def test_marks_a_comment_drifted_when_its_anchor_text_is_gone(self):
        self.assertEqual(place("# Plan\n\nStep 1\n", "# Plan\n\nSomething else\n", 3, 3, "Step 1"),
                         (3, 3, True))

    def test_keeps_an_in_place_append_anchored(self):
        anchor = "- Don't create a group for just 1 commit — merge it into the closest group"
        old = f"# Plan\n\n{anchor}\n"
        new = f"# Plan\n\n{anchor} (unless it's a single significant feature that warrants its own callout)\n"
        self.assertEqual(place(old, new, 3, 3, anchor), (3, 3, False))

    def test_keeps_a_line_anchored_when_a_middle_clause_is_cut_out(self):
        anchor = '      continue. If this organization has no locations yet,{" "}'
        old = ('<div>\n  Please select a location above to\n' + anchor +
               '\n  <Link to="/admin">create one here</Link>\n</div>\n')
        new = ('<div>\n  Please select a location above to\n      continue.{" "}\n'
               '  <Link to="/admin">Go to admin to create one</Link>\n</div>\n')
        self.assertEqual(place(old, new, 3, 3, anchor), (3, 3, False))

    def test_remaps_without_a_drift_flag_when_there_is_no_anchor(self):
        old = "# Plan\n\nStep 1\n\nStep 2\n"
        new = "# Plan\n\nNew line A\nNew line B\nStep 1\n\nStep 2\n"
        self.assertEqual(place(old, new, 3, 3, None), (5, 5, False))

    def test_a_removed_line_maps_to_the_next_surviving_line(self):
        self.assertEqual(place("a\nb\nc\nd\n", "a\nd\n", 2, 3, None), (2, 2, False))

    def test_a_removed_tail_maps_to_the_last_line(self):
        self.assertEqual(place("a\nb\nc\n", "a\n", 3, 3, None), (1, 1, False))

    def test_similarity_rules(self):
        self.assertTrue(carry.similar("intro, keep this sentence, and more", "keep this sentence"))
        self.assertFalse(carry.similar("gone", "keep this sentence intact"))
        self.assertTrue(carry.similar("  same  ", "same"))
        self.assertFalse(carry.similar("", "x"))


class ReviewFileRoundTrip(unittest.TestCase):
    def test_moves_line_comments_and_leaves_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir, new_dir = os.path.join(tmp, 'old'), os.path.join(tmp, 'new')
            for d, body in ((old_dir, 'one\ntwo\nthree\n'), (new_dir, 'zero\none\ntwo\nTHREE gone\n')):
                os.makedirs(d)
                with open(os.path.join(d, 'f.txt'), 'w', encoding='utf-8') as f:
                    f.write(body)
            review = {
                'files': {'f.txt': {'comments': [
                    {'id': 'c1', 'start_line': 2, 'end_line': 2, 'body': 'on two'},
                    {'id': 'c2', 'start_line': 3, 'end_line': 3, 'body': 'on three'},
                    {'id': 'c3', 'scope': 'file', 'start_line': 0, 'end_line': 0, 'body': 'file'},
                ]}},
                'review_comments': [{'id': 'r1', 'body': 'overall'}],
            }
            path = os.path.join(tmp, 'review.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(review, f)
            import contextlib, io, sys
            argv = sys.argv
            sys.argv = ['x', path, old_dir, new_dir]
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    carry.main()
            finally:
                sys.argv = argv
            with open(path, encoding='utf-8') as f:
                out = json.load(f)
            c1, c2, c3 = out['files']['f.txt']['comments']
            self.assertEqual((c1['start_line'], c1['end_line'], c1['drifted'], c1['anchor']), (3, 3, False, 'two'))
            self.assertEqual((c2['start_line'], c2['drifted'], c2['anchor']), (4, True, 'three'))
            self.assertEqual((c3['start_line'], 'drifted' in c3), (0, False))
            self.assertEqual(out['review_comments'], review['review_comments'])


if __name__ == '__main__':
    unittest.main()
