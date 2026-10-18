"""Differential equivalence tests: compiled plan vs. AST interpreter.

The compiled plan in :mod:`jmespath.planner` replaces the per-node dispatch of
``visitor.TreeInterpreter``.  A performance change is only acceptable if it is
*observably* identical, so every expression in the compliance corpus is
evaluated through both engines and the results -- and the raised exceptions --
are compared.

Two engines are available in this test:

* ``_reference_search`` always walks the AST with ``TreeInterpreter``.
* ``jmespath.search`` goes through the compiled plan.

The corpus is data driven (``tests/compliance/*.json``), which means new
conformance cases automatically extend this test.
"""
import json
import os
import random

import pytest

import jmespath
from jmespath import functions
from jmespath import parser
from jmespath import visitor

COMPLIANCE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'compliance')


def _reference_search(expression, data, options=None):
    """Evaluate ``expression`` with the original interpreter, no plan."""
    parsed = parser.Parser().parse(expression)
    return visitor.TreeInterpreter(options).visit(parsed.parsed, data)


def _load_cases():
    cases = []
    for name in sorted(os.listdir(COMPLIANCE_DIR)):
        if not name.endswith('.json'):
            continue
        with open(os.path.join(COMPLIANCE_DIR, name), encoding='utf-8') as f:
            payload = json.load(f)
        for case in payload:
            if 'expression' not in case:
                continue
            cases.append(pytest.param(
                case['expression'], case.get('given'), id='%s-%s' % (
                    name, case.get('expression', '')[:40])))
    return cases


def _outcome(func, *args):
    """Run ``func`` and normalise into a comparable ``(status, value)`` pair."""
    try:
        return ('ok', func(*args))
    except Exception as e:  # noqa: BLE001 - we compare exception classes
        return ('raise', type(e).__name__)


@pytest.mark.parametrize('expression, given', _load_cases())
def test_plan_matches_interpreter_on_compliance_corpus(expression, given):
    expected = _outcome(_reference_search, expression, given)
    actual = _outcome(jmespath.search, expression, given)
    assert actual == expected


class _CustomFunctions(functions.Functions):
    @functions.signature({'types': ['string', 'array', 'object', 'null']})
    def _func_len0(self, s):
        return 0 if s is None else len(s)


def test_plan_honours_custom_functions():
    options = jmespath.Options(custom_functions=_CustomFunctions())
    data = {'a': {'b': [1, 2, 3]}, 'c': None}
    for expression in ('len0(a.b)', 'len0(c)', 'len0(a.b) == `3`',
                       '[len0(a.b), len0(c)]'):
        assert (jmespath.search(expression, data, options) ==
                _reference_search(expression, data, options))


def test_plan_honours_dict_cls():
    from collections import OrderedDict
    options = jmespath.Options(dict_cls=OrderedDict)
    data = {'a': 1, 'b': 2}
    result = jmespath.search('{x: a, y: b}', data, options)
    expected = _reference_search('{x: a, y: b}', data, options)
    assert type(result) is OrderedDict
    assert result == expected


def test_plan_reused_across_option_shapes():
    """Plans are cached per ``Options`` shape; mixing must not leak state."""
    compiled = jmespath.compile('{x: a}')
    data = {'a': 1}
    plain = compiled.search(data)
    typed = compiled.search(data, jmespath.Options(dict_cls=dict))
    from collections import OrderedDict
    ordered = compiled.search(data, jmespath.Options(dict_cls=OrderedDict))
    assert type(plain) is dict
    assert type(typed) is dict
    assert type(ordered) is OrderedDict
    assert plain == typed == ordered == {'x': 1}


SCALARS = [None, True, False, 0, 1, -1, 2.5, 0.0, '', 'a', 'abc', [], [1],
           [None], {}, {'a': 1}, [[]], [{}]]


@pytest.mark.parametrize('left', SCALARS)
@pytest.mark.parametrize('right', SCALARS)
def test_comparator_semantics_on_awkward_operands(left, right):
    """Equality/ordering must keep JMESPath's booleans-are-not-numbers rule."""
    data = {'l': left, 'r': right}
    for expression in ('l == r', 'l != r', 'l < r', 'l <= r',
                       'l > r', 'l >= r', 'l && r', 'l || r', '!l'):
        expected = _outcome(_reference_search, expression, data)
        actual = _outcome(jmespath.search, expression, data)
        assert actual == expected, expression


def test_not_expression_keeps_zero_truthy():
    # ``!0`` is false: 0 is truthy in JMESPath but falsy in Python.
    assert jmespath.search('!`0`', None) is False
    assert jmespath.search('!`0.0`', None) is False
    assert jmespath.search('!`1`', None) is False
    assert jmespath.search('!`true`', None) is False
    assert jmespath.search('!``', None) is True
    assert jmespath.search('!`null`', None) is True


@pytest.mark.parametrize('expression', [
    'foo.bar.baz',
    'records[?meta.score > `50`].{id: id, top: tags[0]}',
    'a[*].b[*].c | [0]',
    'sort_by(items, &v)[-1].v',
    'length(@) == `3` && contains(name, \'x\')',
    'foo[*].bar[].[a, b]',
    '*.values()',
    'items[?@ > `2`] | [0]',
    'a[?b == `1`] | length(@)',
    'reverse(@)[:2]',
    'max_by(items, &v).v',
    'not_null(a, b, c)',
    'merge(a, b)',
    '{first: a[0], last: a[-1]}',
    'a[::2]',
    'to_string(a)',
    'to_number(a)',
    'join(\',\', a)',
    'map(&v, items)[?@ > `3`]',
    'items[].{k: v, n: @}',
])
def test_plan_matches_interpreter_on_typical_expressions(expression):
    data = {
        'foo': {'bar': {'baz': 'x'}},
        'records': [{'meta': {'score': 90}, 'id': 1, 'tags': ['a']},
                    {'meta': {'score': 10}, 'id': 2, 'tags': []}],
        'a': [1, 2, 3],
        'b': {'c': 4},
        'c': 5,
        'items': [{'v': 1}, {'v': 5}, {'v': 3}],
        'name': 'xy',
        'v': 7,
    }
    expected = _outcome(_reference_search, expression, data)
    actual = _outcome(jmespath.search, expression, data)
    assert actual == expected


def _random_ast(rnd, depth=0):
    """Build a random but syntactically valid AST (not from source text)."""
    if depth >= 3:
        kind = rnd.choice(['field', 'literal', 'current', 'identity'])
    else:
        kind = rnd.choice(['field', 'literal', 'current', 'identity',
                           'subexpression', 'comparator', 'projection',
                           'filter_projection', 'multi_select_list',
                           'multi_select_dict', 'pipe', 'and', 'or', 'not',
                           'flatten', 'index_expression', 'slice',
                           'function_expression', 'value_projection'])

    def sub():
        return _random_ast(rnd, depth + 1)

    if kind == 'field':
        return {'type': 'field', 'children': [],
                'value': rnd.choice(['a', 'b', 'c', 'items', 'v'])}
    if kind == 'literal':
        return {'type': 'literal', 'children': [],
                'value': rnd.choice([None, 0, 1, 2, 'x', [], {}])}
    if kind == 'current':
        return {'type': 'current', 'children': []}
    if kind == 'identity':
        return {'type': 'identity', 'children': []}
    if kind == 'subexpression':
        return {'type': 'subexpression',
                'children': [sub(), sub()]}
    if kind == 'comparator':
        return {'type': 'comparator',
                'children': [sub(), sub()],
                'value': rnd.choice(['eq', 'ne', 'lt', 'lte', 'gt', 'gte'])}
    if kind == 'projection':
        return {'type': 'projection', 'children': [sub(), sub()]}
    if kind == 'value_projection':
        return {'type': 'value_projection', 'children': [sub(), sub()]}
    if kind == 'filter_projection':
        return {'type': 'filter_projection',
                'children': [sub(), sub(), sub()]}
    if kind == 'multi_select_list':
        return {'type': 'multi_select_list', 'children': [sub(), sub()]}
    if kind == 'multi_select_dict':
        return {'type': 'multi_select_dict',
                'children': [{'type': 'key_val_pair', 'children': [sub()],
                              'value': rnd.choice(['k1', 'k2'])}]}
    if kind == 'pipe':
        return {'type': 'pipe', 'children': [sub(), sub()]}
    if kind == 'and':
        return {'type': 'and_expression', 'children': [sub(), sub()]}
    if kind == 'or':
        return {'type': 'or_expression', 'children': [sub(), sub()]}
    if kind == 'not':
        return {'type': 'not_expression', 'children': [sub()]}
    if kind == 'flatten':
        return {'type': 'flatten', 'children': [sub()]}
    if kind == 'index_expression':
        return {'type': 'index_expression',
                'children': [sub(), {'type': 'index', 'children': [],
                                     'value': rnd.choice([0, 1, -1])}]}
    if kind == 'slice':
        start, end, step = rnd.choice([(None, None, None), (0, 2, None),
                                       (None, None, -1), (1, None, 2)])
        return {'type': 'slice', 'children': [start, end, step]}
    return {'type': 'function_expression',
            'children': [sub(), sub()],
            'value': rnd.choice(['not_null', 'to_array', 'contains',
                                 'max', 'min', 'length', 'type'])}


def test_plan_matches_interpreter_on_random_asts():
    from jmespath import planner

    rnd = random.Random(20261018)
    data = {'a': {'b': 1, 'c': [1, 2, 3]}, 'b': [3, 1, 2], 'c': None,
            'items': [{'v': 2}, {'v': 0}, None], 'v': 1}
    checked = 0
    for _ in range(600):
        node = _random_ast(rnd)
        plan = planner.compile_plan(node)
        expected = _outcome(visitor.TreeInterpreter().visit, node, data)
        actual = _outcome(plan, data)
        assert actual == expected, node
        checked += 1
    assert checked == 600


def test_unknown_node_type_raises_like_interpreter():
    from jmespath import planner
    node = {'type': 'definitely_not_a_node', 'children': []}
    with pytest.raises(NotImplementedError):
        planner.compile_plan(node)
    with pytest.raises(NotImplementedError):
        visitor.TreeInterpreter().visit(node, {})


def test_compile_cache_still_returns_same_plan_object():
    first = jmespath.compile('a.b')
    second = jmespath.compile('a.b')
    assert first is second
    assert first.search({'a': {'b': 2}}) == 2
    assert first.search({'a': {'b': 2}}) == 2
