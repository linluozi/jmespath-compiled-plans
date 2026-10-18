"""Deterministic workload data shared by every benchmark arm."""
import random


def make_records(n=2000, seed=7):
    rnd = random.Random(seed)
    words = ['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta']
    out = []
    for i in range(n):
        out.append({
            'id': i,
            'name': '%s-%d' % (words[i % len(words)], i),
            'tags': [words[(i + k) % len(words)] for k in range(3)],
            'meta': {'score': rnd.randint(0, 100),
                     'active': i % 3 != 0,
                     'ratio': rnd.random()},
            'items': [{'v': rnd.randint(0, 20)} for _ in range(4)],
        })
    return out


def make_doc():
    return {'foo': {'bar': [{'baz': 'x'}, {'baz': 'y'}]}, 'a': 1, 'b': 2}
