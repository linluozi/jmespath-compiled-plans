"""Emit lexer/parser/evaluation observations for an expression corpus (JSON).

Run as a subprocess by ``bench/fuzz_diff.py`` with one arm on ``sys.path`` at a
time; both arms ship a package literally named ``jmespath``, so they cannot be
imported into the same interpreter.

Usage::

    python bench/probe_dump.py <arm_dir> <cases.json> <out.json>
"""
import json
import re
import sys

# Object reprs embed a memory address, which differs between runs even for the
# same semantics; collapse them so the comparison stays about behaviour.
_ADDRESS = re.compile(r'<jmespath\.\w+\._Expression object[^>]*>')


def normalise_tokens(tokens):
    return [[t.get('type'), repr(t.get('value')), t.get('start'), t.get('end')]
            for t in tokens]


def main():
    arm_dir, cases_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, arm_dir)
    from jmespath import lexer, parser

    with open(cases_path, encoding='utf-8') as f:
        cases = json.load(f)
    data = {'a': {'b': 1, 'c': [1, 2, 3]}, 'foo': {'bar': [{'baz': 'x'}]},
            'b_1': [1, 2], '_x': 'str', 'q': 1, 'r': 's'}
    observations = []
    for expression in cases:
        try:
            tokens = normalise_tokens(list(lexer.Lexer().tokenize(expression)))
        except Exception as e:  # noqa: BLE001
            observations.append(['lex_error', type(e).__name__, str(e)])
            continue
        try:
            parsed = parser.Parser().parse(expression)
        except Exception as e:  # noqa: BLE001
            observations.append(['parse_error', type(e).__name__, str(e), tokens])
            continue
        ast_repr = _ADDRESS.sub('', repr(parsed.parsed))
        try:
            result = _ADDRESS.sub('', repr(parsed.search(data)))
        except Exception as e:  # noqa: BLE001
            observations.append(['eval_error', type(e).__name__, str(e), tokens,
                                 ast_repr])
            continue
        observations.append(['ok', tokens, ast_repr, result])
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(observations, f)
    return 0


if __name__ == '__main__':
    sys.exit(main())
