"""Differential fuzzing driver: compare two library arms on the same corpus.

For every expression it compares, between arm A (e.g. upstream) and arm B
(e.g. this fork):

* the full token stream (``type``, ``value``, ``start``, ``end``),
* the parsed AST,
* the search result on a fixed document,
* the exception type and message when lexing/parsing/evaluating fails.

Each arm runs in its own subprocess (``probe_dump.py``), because both ship a
package literally named ``jmespath``.

Usage::

    python bench/fuzz_diff.py <arm_a_dir> <arm_b_dir> [random_cases]
"""
import json
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ATOMS = ['a', 'foo', 'b_1', '_x', '0', '1', '24', '-3', '"q"', "'r'",
         '`1`', '`"s"`', '`null`', '`true`', '`[]`', '`{}`', '@', '*', '[',
         ']', '(', ')', '{', '}', '.', ',', ':', '|', '||', '&', '&&', '!',
         '!=', '==', '<', '<=', '>', '>=', '[]', '[?', '-', ' ', '\t',
         'foo.bar', '[*]', '[0]', '[:2]', '[::2]', 'length(@)', '"a b"',
         '`"x y"`', "'it''s'", '\\', '^', '$', '~', '#', '中文', '\u2713']


def random_expression(rnd):
    return ''.join(rnd.choice(ATOMS) for _ in range(rnd.randint(1, 9)))


def seed_cases(compliance_dir):
    """Every expression from a compliance corpus (arm-independent)."""
    cases = []
    for name in sorted(os.listdir(compliance_dir)):
        if not name.endswith('.json'):
            continue
        with open(os.path.join(compliance_dir, name),
                  encoding='utf-8') as f:
            for case in json.load(f):
                if 'expression' in case:
                    cases.append(case['expression'])
    return cases


def run_arm(arm_dir, cases, python=sys.executable):
    with tempfile.TemporaryDirectory() as tmp:
        cases_path = os.path.join(tmp, 'cases.json')
        out_path = os.path.join(tmp, 'out.json')
        with open(cases_path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(cases, f)
        subprocess.run([python, os.path.join(HERE, 'probe_dump.py'),
                        arm_dir, cases_path, out_path],
                       check=True, capture_output=True)
        with open(out_path, encoding='utf-8') as f:
            return json.load(f)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    arm_a, arm_b = sys.argv[1], sys.argv[2]
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 3000
    # The corpus is identical in both arms; take it from the baseline arm so
    # the script also works when the checkout is not named ``upstream``.
    compliance_dir = os.path.join(os.path.abspath(arm_a), 'tests', 'compliance')
    rnd = random.Random(20261018)
    cases = seed_cases(compliance_dir) + [
        random_expression(rnd) for _ in range(count)]
    left = run_arm(arm_a, cases)
    right = run_arm(arm_b, cases)
    mismatches = 0
    for expression, a, b in zip(cases, left, right):
        if a != b:
            mismatches += 1
            print('MISMATCH %r' % expression)
            print('  arm_a:', str(a)[:600])
            print('  arm_b:', str(b)[:600])
            if mismatches >= 10:
                print('stopping after 10 mismatches')
                break
    print('compared %d expressions, %d mismatches' % (len(cases), mismatches))
    return 1 if mismatches else 0


if __name__ == '__main__':
    sys.exit(main())
