"""Interleaved benchmark of the current checkout.

Reports, per expression shape, the median search time over several rounds plus
the spread, and writes ``bench_results.json`` for ``make_figures.py``.

Usage::

    python bench/bench_arms.py            # benchmark this checkout
    python bench/bench_arms.py --rounds 7

The measurement design matters more than the numbers:

* every round runs in a fresh process, because allocator state and GC pressure
  from a previous round leak into the next one;
* rounds are interleaved with the *same* code (``--control`` runs the whole
  benchmark twice) so machine drift can be separated from real differences;
* the reported value is the best of ``repeat`` batches inside a round
  (``timeit.repeat`` minimum), which suppresses scheduler noise;
* results are stored per round, so ratios can be compared round by round
  instead of comparing two absolute medians.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

WORKER = r'''
import json, sys, timeit
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
import jmespath
from workdata import make_records

data = {'records': make_records(2000)}

CASES = [
    ('small_select', 'foo.bar[0].baz', 3000),
    ('field_projection', 'records[*].meta.score', 60),
    ('nested_projection', 'records[*].items[*].v', 40),
    ('filter_hash', 'records[?meta.score > `50`].{id: id, name: name, top: tags[0]}', 30),
    ('filter_plain', 'records[?meta.active].name', 40),
    ('flatten', 'records[*].tags[]', 60),
    ('sort_by', 'sort_by(records, &meta.score)[-1].name', 20),
    ('pipe_index', 'records[*].meta.score | [0]', 60),
    ('multi_select', 'records[:100].[id, meta.score, tags[0]]', 40),
    ('functions', 'map(&length(name), records) | [0]', 30),
]

out = {}
for label, expression, number in CASES:
    compiled = jmespath.compile(expression)
    for _ in range(3):
        compiled.search(data)                 # warm up plan + parser cache
    best = min(timeit.repeat(lambda: compiled.search(data),
                             number=number, repeat=7))
    out[label] = best / number * 1e6
print(json.dumps(out))
'''


def measure(package_root, rounds):
    worker = os.path.join(HERE, '_worker_%s.py' % os.getpid())
    with open(worker, 'w', encoding='utf-8', newline='\n') as f:
        f.write(WORKER)
    samples = []
    try:
        for _ in range(rounds):
            proc = subprocess.run(
                [sys.executable, worker, package_root, HERE],
                capture_output=True, text=True, check=True)
            samples.append(json.loads(proc.stdout))
    finally:
        os.remove(worker)
    return samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--package-root', default=ROOT,
                        help='directory containing the jmespath package')
    parser.add_argument('--control', action='store_true',
                        help='run the same code twice to expose the noise floor')
    args = parser.parse_args()

    first = measure(args.package_root, args.rounds)
    labels = list(first[0])
    summary = {}
    for label in labels:
        values = [s[label] for s in first]
        summary[label] = {
            'us_median': round(statistics.median(values), 1),
            'us_min': round(min(values), 1),
            'us_max': round(max(values), 1),
            'cv_pct': round(statistics.stdev(values) / statistics.mean(values)
                            * 100, 1),
        }
    payload = {'package_root': args.package_root,
               'rounds': args.rounds,
               'summary': summary}

    if args.control:
        second = measure(args.package_root, args.rounds)
        ratios = []
        for a, b in zip(first, second):
            ratios.extend(a[label] / b[label] for label in labels)
        payload['noise_floor'] = {
            'ratio_median': round(statistics.median(ratios), 3),
            'ratio_min': round(min(ratios), 3),
            'ratio_max': round(max(ratios), 3),
        }
        print('noise floor (same code, two runs): median %.3f, range %.2f-%.2f'
              % (payload['noise_floor']['ratio_median'],
                 payload['noise_floor']['ratio_min'],
                 payload['noise_floor']['ratio_max']))

    for label in labels:
        row = summary[label]
        print('%-18s %9.1f us  (min %9.1f, max %9.1f, CV %4.1f%%)' % (
            label, row['us_median'], row['us_min'], row['us_max'],
            row['cv_pct']))

    with open(os.path.join(ROOT, 'bench_results.json'), 'w',
              encoding='utf-8', newline='\n') as f:
        json.dump(payload, f, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
