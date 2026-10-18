"""How the speedup scales with document size.

Each (arm, row-count) pair runs in its own process: running several row counts
inside one interpreter makes the larger cases look slower, because allocator
state and GC pressure from the previous counts leak into the next
measurement (this produced a 2x underestimate before it was split up).

Usage::

    python bench/bench_scale.py --baseline ../upstream --optimized .

Writes ``bench_scale.json`` for ``bench/make_figures.py``.
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

EXPRESSION = 'records[?meta.score > `50`].{id: id, name: name, top: tags[0]}'
n = int(sys.argv[3])
data = {'records': make_records(n)}
compiled = jmespath.compile(EXPRESSION)
for _ in range(3):
    compiled.search(data)          # warm up
number = max(2, 6000 // n)
best = min(timeit.repeat(lambda: compiled.search(data),
                         number=number, repeat=7)) / number
print(json.dumps({'us': best * 1e6}))
'''

ROWS = (10, 100, 500, 1000, 2000, 5000, 10000)


def measure(package_root, rows, rounds):
    worker = os.path.join(HERE, '_scale_%s.py' % os.getpid())
    with open(worker, 'w', encoding='utf-8', newline='\n') as f:
        f.write(WORKER)
    values = []
    try:
        for _ in range(rounds):
            proc = subprocess.run(
                [sys.executable, worker, package_root, HERE, str(rows)],
                capture_output=True, text=True, check=True)
            values.append(json.loads(proc.stdout)['us'])
    finally:
        os.remove(worker)
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', default=os.path.join(ROOT, '..', 'upstream'))
    parser.add_argument('--optimized', default=ROOT)
    parser.add_argument('--rounds', type=int, default=5)
    args = parser.parse_args()

    table = []
    for rows in ROWS:
        base = measure(args.baseline, rows, args.rounds)
        opt = measure(args.optimized, rows, args.rounds)
        b = statistics.median(base)
        o = statistics.median(opt)
        table.append({'rows': rows,
                      'baseline_us': round(b, 1),
                      'optimized_us': round(o, 1),
                      'speedup': round(b / o, 2)})
        print('rows=%-6d baseline=%9.1fus optimized=%9.1fus speedup=%.2fx'
              % (rows, b, o, b / o))

    payload = {'expression': 'records[?meta.score > `50`].{id,name,top}',
               'rows': table}
    with open(os.path.join(ROOT, 'bench_scale.json'), 'w',
              encoding='utf-8', newline='\n') as f:
        json.dump(payload, f, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
