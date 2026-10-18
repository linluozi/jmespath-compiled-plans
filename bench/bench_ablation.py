"""Ablation: what each optimisation layer contributes.

Arms are cumulative and live in sibling directories of the checkout::

    upstream/   pristine upstream copy
    armA/       + compiled plans (planner.py, ParsedResult.search)
    armB/       + single-pass index-based lexer
    armC/       + table-driven parser dispatch, cached function signatures
    .           + inlined field projections/filters  (this checkout)

Two metrics, because the layers act on different code paths:

``parse_us``  tokenise + build the AST, no evaluation.  Isolates the lexer and
              parser work, i.e. the cold path of ``jmespath.search``.
``search_us`` repeated search on a pre-compiled expression (hot path).

Each arm runs in its own process and rounds are interleaved.  ``armC`` and the
checkout are byte-identical by construction, so their difference is the noise
floor and is printed alongside the real numbers -- without it, a 20% spread
looks like a real 20% effect.

Usage::

    python bench/bench_ablation.py --root .. --baseline upstream
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
from jmespath.parser import Parser
from workdata import make_records

EXPRESSIONS = [
    'foo.bar.baz',
    'records[?meta.score > `50`].{id: id, name: name, top: tags[0]}',
    'a[*].b[*].c | [0]',
    'sort_by(items, &v)[-1].v',
    "length(@) == `3` && contains(name, 'x')",
    'foo[*].bar[].[a, b]',
]
data = {'records': make_records(2000)}
HEAVY = EXPRESSIONS[1]

def parse_all():
    for expression in EXPRESSIONS:
        Parser.purge()
        Parser().parse(expression)

parse_us = min(timeit.repeat(parse_all, number=120, repeat=7)) / (120 * len(EXPRESSIONS))

compiled = jmespath.compile(HEAVY)
for _ in range(3):
    compiled.search(data)
search_us = min(timeit.repeat(lambda: compiled.search(data), number=40, repeat=7)) / 40

plan_us = None
try:
    from jmespath import planner
    parsed = jmespath.compile(HEAVY).parsed
    plan_us = min(timeit.repeat(lambda: planner.compile_plan(parsed),
                                number=300, repeat=7)) / 300
except ImportError:
    pass

print(json.dumps({'parse_us': parse_us * 1e6,
                  'search_us': search_us * 1e6,
                  'plan_us': None if plan_us is None else plan_us * 1e6}))
'''


def measure(package_root, rounds):
    worker = os.path.join(HERE, '_abl_%s.py' % os.getpid())
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
    parser.add_argument('--root', default=os.path.dirname(ROOT),
                        help='directory holding the arm checkouts')
    parser.add_argument('--baseline', default='upstream')
    parser.add_argument('--rounds', type=int, default=5)
    args = parser.parse_args()

    arms = [(args.baseline, os.path.join(args.root, args.baseline)),
            ('armA', os.path.join(args.root, 'armA')),
            ('armB', os.path.join(args.root, 'armB')),
            ('armC', os.path.join(args.root, 'armC')),
            ('fork', ROOT)]
    samples = {name: measure(path, args.rounds) for name, path in arms}

    result = {}
    for name, _ in arms:
        result[name] = {
            'parse_us': round(statistics.median(
                [s['parse_us'] for s in samples[name]]), 3),
            'search_us': round(statistics.median(
                [s['search_us'] for s in samples[name]]), 1),
            'plan_us': (round(statistics.median(
                [s['plan_us'] for s in samples[name]
                 if s['plan_us'] is not None]), 3)
                if any(s['plan_us'] is not None for s in samples[name])
                else None),
        }
        print('%-9s parse=%7.2fus  plan=%6.2fus  search=%8.1fus' % (
            name, result[name]['parse_us'], result[name]['plan_us'] or 0.0,
            result[name]['search_us']))

    control = [a['search_us'] / b['search_us']
               for a, b in zip(samples['fork'], samples['armC'])]
    floor = {'ratio_median': round(statistics.median(control), 3),
             'ratio_min': round(min(control), 3),
             'ratio_max': round(max(control), 3)}
    result['noise_floor'] = floor
    print('noise floor (armC vs fork, identical sources): '
          'median %.3f, range %.2f-%.2f'
          % (floor['ratio_median'], floor['ratio_min'], floor['ratio_max']))

    base = samples[args.baseline]
    print('')
    print('per-round speedup vs %s (median [min-max]):' % args.baseline)
    for name, _ in arms[1:]:
        for metric in ('parse_us', 'search_us'):
            ratios = [b[metric] / o[metric]
                      for b, o in zip(base, samples[name])]
            print('  %-6s %-9s %6.2fx [%5.2f-%5.2f]' % (
                name, metric[:-3], statistics.median(ratios),
                min(ratios), max(ratios)))

    with open(os.path.join(ROOT, 'bench_ablation.json'), 'w',
              encoding='utf-8', newline='\n') as f:
        json.dump(result, f, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
