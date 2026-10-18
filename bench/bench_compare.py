"""Compare two checkouts of jmespath on the same workload.

Both arms run in separate processes and their rounds are interleaved, so the
machine drift affects each arm equally.  The reported figure is the median of
per-round ratios ``baseline_time / optimized_time`` -- not the ratio of two
medians, which is noticeably noisier on a laptop.

Usage::

    python bench/bench_compare.py --baseline ../upstream --optimized .

Writes ``bench_results.json`` for ``bench/make_figures.py``.
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
    worker = os.path.join(HERE, '_cmp_%s.py' % os.getpid())
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
    parser.add_argument('--baseline', default=os.path.join(ROOT, '..', 'upstream'))
    parser.add_argument('--optimized', default=ROOT)
    parser.add_argument('--rounds', type=int, default=5)
    args = parser.parse_args()

    base = measure(args.baseline, args.rounds)
    opt = measure(args.optimized, args.rounds)

    summary = {}
    for label in base[0]:
        ratios = [b[label] / o[label] for b, o in zip(base, opt)]
        summary[label] = {
            'baseline_us': round(statistics.median([b[label] for b in base]), 1),
            'optimized_us': round(statistics.median([o[label] for o in opt]), 1),
            'ratio_median': round(statistics.median(ratios), 2),
            'ratio_min': round(min(ratios), 2),
            'ratio_max': round(max(ratios), 2),
        }
        print('%-18s %9.1f -> %9.1f us   %.2fx  [%.2f-%.2f]' % (
            label, summary[label]['baseline_us'],
            summary[label]['optimized_us'], summary[label]['ratio_median'],
            summary[label]['ratio_min'], summary[label]['ratio_max']))

    payload = {'baseline': os.path.abspath(args.baseline),
               'optimized': os.path.abspath(args.optimized),
               'rounds': args.rounds,
               'summary': summary}
    with open(os.path.join(ROOT, 'bench_results.json'), 'w',
              encoding='utf-8', newline='\n') as f:
        json.dump(payload, f, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
