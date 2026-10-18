"""Compiled evaluation plans for JMESPath expressions.

Why this module exists
----------------------
The classic implementation walks the AST with string-keyed dispatch:
``Visitor.visit`` reads ``node['type']`` and resolves the handler through a
dict *on every single node visit*.  Evaluating

    records[?meta.score > `50`].{id: id, name: name}

over 2 000 records therefore performs ~30 000 dict lookups, ~30 000 bound
method calls with ``*args/**kwargs`` and ~30 000 subscripts on the node
itself -- even though the compiled AST never changes between searches.

``compile_plan`` walks the AST **once** and returns a graph of closures.  A
search then becomes a chain of plain one-argument calls: no string dispatch,
no ``*args`` tuple building and no repeated node subscripts.  Structural work
that only depends on the expression (identity short-circuits, dict type
selection, function arity, slice objects) is hoisted into plan build time.

Semantics
---------
Results, evaluation order, short-circuiting, the ``null``/``false``
distinction and error behaviour match ``visitor.TreeInterpreter`` exactly;
``tests/test_planner_equivalence.py`` asserts that over the full compliance
corpus plus randomised ASTs.  ``TreeInterpreter`` is kept intact: it remains
the reference implementation, the engine behind ``GraphvizVisitor`` tooling,
and what third-party code that walks ASTs directly relies on.
"""
from numbers import Number

from jmespath import functions as _functions
from jmespath import visitor as _visitor
from jmespath.compat import string_type

_ORDER_TYPES = (int, float, string_type)


def _equals(x, y):
    """``visitor._equals`` with a fast path for equal-type operands.

    ``_equals`` only deviates from ``==`` when a *bool* meets a number
    (``0 == False`` must be ``False`` in JMESPath).  Two operands of the same
    type can never reach that branch, so ``==`` is exact for them.  Mixed
    types still go through the original helper.
    """
    if type(x) is type(y):
        return x == y
    return _visitor._equals(x, y)


def _ordered(x, y, op):
    """Ordering comparison; ``None`` when either operand is not comparable."""
    # ``bool`` must be rejected even though it subclasses ``int``: JMESPath
    # treats booleans as non-numbers (see ``visitor._is_actual_number``).
    if isinstance(x, _ORDER_TYPES) and isinstance(y, _ORDER_TYPES) and \
            not isinstance(x, bool) and not isinstance(y, bool):
        return op(x, y)
    # Slow path: unusual numeric types (Decimal, Fraction, ...) and operands
    # that upstream rejects with ``None`` instead of raising.
    if not (_visitor._is_comparable(x) and _visitor._is_comparable(y)):
        return None
    return op(x, y)


def _is_false(value):
    """JMESPath falsiness: ``null``, ``false``, ``""``, ``[]`` and ``{}``.

    Mirrors ``TreeInterpreter._is_false``; note that ``0`` stays truthy.
    """
    if value is None or value is False:
        return True
    if value == '' or value == [] or value == {}:
        return True
    return False


def _identity(value):
    """``@`` and implicit identity steps."""
    return value


class _Expression(object):
    """Expression reference (``&expr``) bound to a compiled plan.

    ``functions`` recognises expression references by the *type name*
    ``_Expression`` (see ``functions.REVERSE_TYPES_MAP``) and only ever uses
    the ``expression`` attribute plus ``visit(expression, value)``, so binding
    the compiled closure directly is enough.
    """

    __slots__ = ('expression', '_plan')

    def __init__(self, expression, plan):
        self.expression = expression
        self._plan = plan

    def visit(self, node, *args, **kwargs):
        return self._plan(args[0])


class _Context(object):
    """Build-time state shared by every builder of one plan."""

    __slots__ = ('dict_cls', 'functions')

    def __init__(self, dict_cls, functions):
        self.dict_cls = dict_cls
        self.functions = functions


# ---------------------------------------------------------------------------
# Builders: one per AST node type, each returning ``callable(value) -> result``
# ---------------------------------------------------------------------------

def _build_field(node, ctx):
    key = node['value']

    def field(value):
        if type(value) is dict:
            return value.get(key)
        try:
            return value.get(key)
        except AttributeError:
            # Not a mapping (list, str, int, None, ...): JMESPath null.
            return None
    return field


def _build_identity(node, ctx):
    return _identity


def _build_current(node, ctx):
    return _identity


def _build_literal(node, ctx):
    constant = node['value']

    def literal(value):
        return constant
    return literal


def _build_subexpression(node, ctx):
    steps = [compile_node(child, ctx) for child in node['children']]
    if len(steps) == 1:
        return steps[0]
    if len(steps) == 2:
        first, second = steps

        def subexpression2(value):
            return second(first(value))
        return subexpression2

    def subexpression(value):
        for step in steps:
            value = step(value)
        return value
    return subexpression


def _build_comparator(node, ctx):
    left = compile_node(node['children'][0], ctx)
    right = compile_node(node['children'][1], ctx)
    name = node['value']
    if name == 'eq':
        def eq(value):
            return _equals(left(value), right(value))
        return eq
    if name == 'ne':
        def ne(value):
            return not _equals(left(value), right(value))
        return ne
    op = _visitor.TreeInterpreter.COMPARATOR_FUNC[name]

    def ordering(value):
        return _ordered(left(value), right(value), op)
    return ordering


def _build_expref(node, ctx):
    child = node['children'][0]
    plan = compile_node(child, ctx)

    def expref(value):
        return _Expression(child, plan)
    return expref


def _build_function_expression(node, ctx):
    args = [compile_node(child, ctx) for child in node['children']]
    name = node['value']
    call = ctx.functions.call_function
    if not args:
        def call0(value):
            return call(name, [])
        return call0
    if len(args) == 1:
        only = args[0]

        def call1(value):
            return call(name, [only(value)])
        return call1
    if len(args) == 2:
        first, second = args

        def call2(value):
            return call(name, [first(value), second(value)])
        return call2

    def calln(value):
        return call(name, [arg(value) for arg in args])
    return calln


def _field_key(node):
    """Return the key name if ``node`` is a plain field access, else ``None``.

    Used to inline the most common projection/filter body (``records[*].name``)
    into the loop, which removes one Python call per element.
    """
    if type(node) is dict and node.get('type') == 'field':
        return node['value']
    return None


def _build_filter_projection(node, ctx):
    base_plan = compile_node(node['children'][0], ctx)
    rhs_node = node['children'][1]
    rhs_plan = compile_node(rhs_node, ctx)
    condition_plan = compile_node(node['children'][2], ctx)
    rhs_key = _field_key(rhs_node)

    if rhs_key is not None:
        def filter_projection_field(value):
            base = base_plan(value)
            if not isinstance(base, list):
                return None
            collected = []
            append = collected.append
            for element in base:
                if not _is_false(condition_plan(element)):
                    try:
                        current = element.get(rhs_key)
                    except AttributeError:
                        current = None
                    if current is not None:
                        append(current)
            return collected
        return filter_projection_field

    def filter_projection(value):
        base = base_plan(value)
        if not isinstance(base, list):
            return None
        collected = []
        append = collected.append
        for element in base:
            if not _is_false(condition_plan(element)):
                current = rhs_plan(element)
                if current is not None:
                    append(current)
        return collected
    return filter_projection


def _build_flatten(node, ctx):
    base_plan = compile_node(node['children'][0], ctx)

    def flatten(value):
        base = base_plan(value)
        if not isinstance(base, list):
            return None
        merged = []
        extend = merged.extend
        append = merged.append
        for element in base:
            if isinstance(element, list):
                extend(element)
            else:
                append(element)
        return merged
    return flatten


def _build_index(node, ctx):
    index = node['value']

    def index_node(value):
        if not isinstance(value, list):
            return None
        try:
            return value[index]
        except IndexError:
            return None
    return index_node


def _build_index_expression(node, ctx):
    steps = [compile_node(child, ctx) for child in node['children']]
    if len(steps) == 2:
        first, second = steps

        def index_expression2(value):
            return second(first(value))
        return index_expression2

    def index_expression(value):
        for step in steps:
            value = step(value)
        return value
    return index_expression


def _build_slice(node, ctx):
    window = slice(*node['children'])

    def slice_node(value):
        if not isinstance(value, list):
            return None
        return value[window]
    return slice_node


def _build_key_val_pair(node, ctx):
    return compile_node(node['children'][0], ctx)


def _build_multi_select_dict(node, ctx):
    items = [(child['value'], compile_node(child, ctx))
             for child in node['children']]
    dict_cls = ctx.dict_cls or dict

    def multi_select_dict(value):
        if value is None:
            return None
        collected = dict_cls()
        for key, plan in items:
            collected[key] = plan(value)
        return collected
    return multi_select_dict


def _build_multi_select_list(node, ctx):
    plans = [compile_node(child, ctx) for child in node['children']]

    def multi_select_list(value):
        if value is None:
            return None
        return [plan(value) for plan in plans]
    return multi_select_list


def _build_or_expression(node, ctx):
    left = compile_node(node['children'][0], ctx)
    right = compile_node(node['children'][1], ctx)

    def or_expression(value):
        matched = left(value)
        if _is_false(matched):
            return right(value)
        return matched
    return or_expression


def _build_and_expression(node, ctx):
    left = compile_node(node['children'][0], ctx)
    right = compile_node(node['children'][1], ctx)

    def and_expression(value):
        matched = left(value)
        if _is_false(matched):
            return matched
        return right(value)
    return and_expression


def _build_not_expression(node, ctx):
    child = compile_node(node['children'][0], ctx)

    def not_expression(value):
        result = child(value)
        # ``!0`` is ``false``: 0 is truthy in JMESPath even though Python
        # treats it as falsy, so it needs the numeric special case.
        if isinstance(result, Number) and not isinstance(result, bool) \
                and result == 0:
            return False
        return not result
    return not_expression


def _build_pipe(node, ctx):
    steps = [compile_node(child, ctx) for child in node['children']]
    if len(steps) == 2:
        first, second = steps

        def pipe2(value):
            return second(first(value))
        return pipe2

    def pipe(value):
        for step in steps:
            value = step(value)
        return value
    return pipe


def _build_projection(node, ctx):
    base_plan = compile_node(node['children'][0], ctx)
    rhs_node = node['children'][1]
    rhs_plan = compile_node(rhs_node, ctx)
    if rhs_plan is _identity:
        # ``[*]`` / ``a.*`` with nothing after it: the generic loop below would
        # only drop ``null`` elements, so specialise exactly that case.
        def projection_identity(value):
            base = base_plan(value)
            if not isinstance(base, list):
                return None
            return [element for element in base if element is not None]
        return projection_identity

    rhs_key = _field_key(rhs_node)
    if rhs_key is not None:
        # ``[*].name`` is by far the most common projection body; reading the
        # key inline avoids one Python call per element.
        def projection_field(value):
            base = base_plan(value)
            if not isinstance(base, list):
                return None
            collected = []
            append = collected.append
            for element in base:
                try:
                    current = element.get(rhs_key)
                except AttributeError:
                    continue
                if current is not None:
                    append(current)
            return collected
        return projection_field

    def projection(value):
        base = base_plan(value)
        if not isinstance(base, list):
            return None
        collected = []
        append = collected.append
        for element in base:
            current = rhs_plan(element)
            if current is not None:
                append(current)
        return collected
    return projection


def _build_value_projection(node, ctx):
    base_plan = compile_node(node['children'][0], ctx)
    rhs_plan = compile_node(node['children'][1], ctx)
    if rhs_plan is _identity:
        def value_projection_identity(value):
            base = base_plan(value)
            try:
                values = base.values()
            except AttributeError:
                return None
            return [element for element in values if element is not None]
        return value_projection_identity

    def value_projection(value):
        base = base_plan(value)
        try:
            base = base.values()
        except AttributeError:
            return None
        collected = []
        append = collected.append
        for element in base:
            current = rhs_plan(element)
            if current is not None:
                append(current)
        return collected
    return value_projection


_BUILDERS = {
    'field': _build_field,
    'identity': _build_identity,
    'current': _build_current,
    'literal': _build_literal,
    'subexpression': _build_subexpression,
    'comparator': _build_comparator,
    'expref': _build_expref,
    'function_expression': _build_function_expression,
    'filter_projection': _build_filter_projection,
    'flatten': _build_flatten,
    'index': _build_index,
    'index_expression': _build_index_expression,
    'slice': _build_slice,
    'key_val_pair': _build_key_val_pair,
    'multi_select_dict': _build_multi_select_dict,
    'multi_select_list': _build_multi_select_list,
    'or_expression': _build_or_expression,
    'and_expression': _build_and_expression,
    'not_expression': _build_not_expression,
    'pipe': _build_pipe,
    'projection': _build_projection,
    'value_projection': _build_value_projection,
}


def compile_node(node, ctx):
    """Return a ``callable(value) -> result`` for ``node``.

    Raises ``NotImplementedError`` for unknown node types, matching
    ``TreeInterpreter.default_visit``.
    """
    try:
        builder = _BUILDERS[node['type']]
    except (KeyError, TypeError):
        raise NotImplementedError(node['type'])
    return builder(node, ctx)


def compile_plan(node, dict_cls=None, functions=None):
    """Compile a whole AST into a single ``callable(value) -> result``.

    :param node: root AST node as produced by :class:`jmespath.parser.Parser`.
    :param dict_cls: dict type used for multi-select hashes (``None`` -> dict).
    :param functions: function registry instance (``None`` -> ``Functions()``).
    """
    if functions is None:
        functions = _functions.Functions()
    return compile_node(node, _Context(dict_cls, functions))
