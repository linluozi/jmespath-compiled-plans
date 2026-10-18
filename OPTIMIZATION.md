# Optimization notes

This document records what was changed, why it was changed, how the gain was
measured, and which measurements turned out to be misleading. Everything here
refers to `jmespath.py` at upstream commit `2812594` (v1.1.0, `develop`).

## 1. Where the time actually went

Before changing anything, a profile of a realistic query:

```python
expression = 'records[?meta.score > `50`].{id: id, name: name, top: tags[0]}'
data = {'records': make_records(2000)}          # 2 000 rows, 5 keys each
```

`cProfile`, 30 searches (~0.30 s total):

```
ncalls  tottime  cumtime  function
762180/3060  0.380  1.033  visitor.py:87(visit)          <- 76% of all time
1099260      0.148  0.148  {method 'get' of 'dict' objects}
 60000       0.063  0.417  visitor.py:140(visit_comparator)
337080       0.062  0.135  visitor.py:134(visit_field)
123000       0.059  0.319  visitor.py:128(visit_subexpression)
```

The evaluator is not slow because any single node handler is slow. It is slow
because **every node visit pays a dispatch tax**:

```python
def visit(self, node, *args, **kwargs):        # visitor.py
    node_type = node['type']                   # subscript
    method = self._method_cache.get(node_type) # dict lookup
    if method is None: ...
    return method(node, *args, **kwargs)       # *args tuple build + call
```

Three separate costs per node, multiplied by the number of nodes touched --
which for a filter over *n* rows is proportional to *n*, not to the size of
the expression. The AST itself never changes between searches, so all of that
work is repeated needlessly.

Micro-benchmarks that informed the fix (CPython 3.13, this machine):

| operation | time |
| --- | --- |
| `getattr(self, '_token_nud_%s' % t)` | 133 ns |
| precomputed dispatch table + `.get` | 48 ns |
| `isinstance(x, Number)` (ABC) | 167 ns |
| `type(x) is int or type(x) is float` | 75 ns |
| building `TreeInterpreter()` per search | 0.54 us |

The last row matters: `ParsedResult.search` constructed a fresh interpreter on
*every* call, which is 16% of a small query's runtime before any work happens.

## 2. The change: compile the AST into closures

New module `jmespath/planner.py`. `compile_plan(ast)` walks the tree once and
returns a graph of closures, each `callable(value) -> result`. A projection
whose body is a field access becomes a loop that reads the key inline:

```python
def projection_field(value):
    base = base_plan(value)
    if not isinstance(base, list):
        return None
    collected = []
    append = collected.append
    for element in base:
        try:
            current = element.get(rhs_key)   # no per-element closure call
        except AttributeError:
            continue
        if current is not None:
            append(current)
    return collected
```

`ParsedResult` caches the plan, keyed by the `Options` shape, because
`dict_cls` and `custom_functions` change what the plan has to capture:

```python
def search(self, value, options=None):
    return self._plan_for(options)(value)
```

`TreeInterpreter` is untouched. It is still the reference implementation and
third-party code that walks ASTs keeps working.

### Semantics that had to be preserved (and were nearly broken)

These are the traps found while porting; each one is now covered by a test.

1. **`0 == False`.** Python says `True`, JMESPath says `False`. A naive
   `x == y` fast path is wrong. The fix compares types first -- two operands of
   the same type can never hit the bool-vs-number case -- and falls back to
   `visitor._equals` otherwise.
2. **Ordering with booleans.** `bool` subclasses `int`, so `isinstance(True, int)`
   is true and `true < 2` would silently become a valid comparison. The
   compliance corpus caught this (`boolean.json`, `one < boolvalue`): booleans
   must be excluded explicitly.
3. **`!0` is `false`.** Zero is truthy in JMESPath but falsy in Python. The
   interpreter has a numeric special case; the compiled `not` closure needs it
   too, including for `Decimal`/`Fraction`.
4. **Projections drop `null`.** `[*]` is not `list(base)`; elements that
   evaluate to `None` are removed. An "identity projection" optimisation that
   returned `list(base)` was wrong and is now a filtered comprehension.
5. **Falsiness.** `""`, `[]`, `{}`, `null`, `false` are falsy; `0` is not.
   Copying Python's `if not value` would change results.
6. **`&expr` references.** `functions` recognises expression references by the
   *type name* `_Expression`, so the plan supplies an object with that name
   plus `expression` and `visit()`. It cannot simply be a closure.

## 3. Cold path: lexer and parser

`jmespath.search(expr, data)` (the one-shot form) also pays parse cost, so the
two front-end stages were rewritten with the same input/output contract.

**Lexer.** The old scanner kept a `list(expression)` copy, called `_next()` for
every character and tested `set` membership through attribute lookups, building
identifiers with repeated `str +=`. The new scanner walks the string with an
integer cursor, hoists the character classes into locals and slices identifier
runs once. Token `start`/`end` values are reproduced **bit for bit**, including
upstream's quirk where delimited tokens (raw strings, literals, quoted
identifiers) store the token *length* rather than an absolute offset, minus one
when the delimiter ends the expression:

```python
def _delimited_end(self, start, after, length):
    size = after - start
    if after >= length:
        size -= 1          # upstream stopped on the delimiter
    return size
```

**Parser.** `_expression` resolved handlers with
`getattr(self, '_token_nud_%s' % type)` on every token -- 133 ns. The tables
are now built once per class and store *unbound* functions.

Two mistakes were made and reverted here, both caught by measurement:

* Building the tables in `Parser.__init__` cost 13 us per parser, taking cold
  compilation from 8.7 us to 20 us. Class-level caching fixed it (0.15 us).
* Storing *bound* methods in a class-level table shares them across parser
  instances, which corrupted parser state (`TypeError: 'NoneType' object is not
  subscriptable`). Unbound functions take `self` explicitly instead.

**Functions.** `_get_allowed_pytypes` split every `'array-number'` signature
string on *every* function call and was the hottest frame for `map`/`sort_by`
expressions. Signatures are static, so they are resolved once and cached on the
function object. Note the subtlety: entries with an empty `types` list are
skipped, so the compiled check list must keep the original argument index --
using `enumerate()` over the filtered list shifted every later argument and
broke custom functions.

## 4. Measurements

### 4.1 Design

The laptop this was measured on drifts a lot. Two concrete lessons:

* **Comparing two absolute medians taken minutes apart is not valid.** The same
  benchmark reported 4.45x on one run and 5.4x on another purely from machine
  load.
* **A single-process multi-size sweep is invalid.** Running 10/100/.../10000
  rows in one interpreter reported 2.0x at 2 000 rows, while the same size
  measured in a fresh process reported 5.1x -- allocator state and GC pressure
  from the previous sizes leaked forward.

So every number here comes from:

* one fresh process per (arm, case) sample,
* interleaved rounds across arms,
* the **median of per-round ratios**, not the ratio of medians,
* three full independent runs, with the run-to-run spread reported.

Tooling: `bench/bench_compare.py` (ten expression shapes),
`bench/bench_scale.py` (document sizes), `bench/bench_ablation.py` (layer by
layer, prints its own noise floor).

### 4.2 Noise floor

`armC` and the shipped checkout are byte-identical by construction
(`diff -r` clean). Measuring them against each other gives the noise floor of
this setup:

| metric | value |
| --- | --- |
| median ratio | 1.019 |
| observed range | 0.93 - 1.24 |

That is a ~20% spread on identical code. It is why the README quotes medians
over three runs and why any single-run ratio below ~1.3 should be treated as
inconclusive on this machine.

### 4.3 Search path (hot)

2 000 rows, ten shapes, median over three runs:

| case | upstream | fork | speedup |
| --- | --- | --- | --- |
| flatten | 2 114 us | 271 us | 7.7x |
| filter + multi-select hash | 6 637 us | 1 234 us | 5.4x |
| multi-select over a slice | 197 us | 41 us | 4.8x |
| nested projection | 3 566 us | 778 us | 4.6x |
| projection + pipe + index | 1 514 us | 328 us | 4.6x |
| field projection | 1 538 us | 353 us | 4.4x |
| small select (`foo.bar[0].baz`) | 3.6 us | 0.9 us | 4.0x |
| filter + plain field | 2 107 us | 542 us | 4.0x |
| `sort_by(&expr)` | 2 200 us | 759 us | 2.9x |
| `map(&fn)` | 3 559 us | 1 304 us | 2.7x |

Median: **4.4x**. The bottom two shapes spend most of their time inside
`sorted()`/`map()` calling back into user-visible work, so there is less
dispatch left to remove -- the honest ceiling for an evaluator rewrite.

By document size the factor is flat (4.1x-6.0x from 10 to 10 000 rows), which
is what a constant-factor change should look like.

### 4.4 Compile path (cold)

Mean over six expressions, parser cache purged each iteration:

| arm | parse + AST | speedup |
| --- | --- | --- |
| upstream | 13.2 us | -- |
| + compiled plans | 13.6 us | 0.97x |
| + single-pass lexer | 10.7 us | 1.24x |
| + table dispatch + signature cache | 9.4 us | **1.41x** |

The first layer slightly *hurts* parsing (the planner module is imported and
plans are built on first search); the front-end work more than pays it back.
`planner.compile_plan` itself costs ~3.2 us per expression, paid once.

### 4.5 Memory

`tracemalloc` peak for `compile` + one search: **181.4 KiB** vs 180.8 KiB
upstream -- unchanged within the 1 KiB resolution of the measurement. The plan
reuses the same closures for every call, so per-search allocation behaviour is
identical.

Full one-shot path (`jmespath.search` with a cold parser cache, i.e. lex +
parse + plan + search) on the same document: **2.4 ms vs 8.3 ms** upstream.

## 5. Correctness

Two independent nets, because "looks right" is not evidence:

1. **`tests/test_planner_equivalence.py`** evaluates every compliance case
   through both engines and compares results *and exception types*. It also
   covers a full scalar matrix (`0`/`1`/`True`/`False`/`""`/`[]`/`{}`/`None`
   against each other for all six comparators plus `&&`, `||`, `!`), custom
   function registries, `dict_cls`, plan reuse across option shapes, and 600
   randomly generated ASTs.
2. **`bench/fuzz_diff.py`** runs the compliance corpus plus thousands of random
   expressions (including deliberately broken ones) through an upstream
   checkout and this one in separate subprocesses, comparing token streams,
   ASTs, results and exception messages:

   ```
   compared 6000 expressions, 0 mismatches
   ```

The differential harness found real bugs during development: the `end` field of
delimited tokens, the boolean-ordering case, and the skipped-`types` argument
indexing. Each is now a regression test.

Test totals: **1342 passed, 2 skipped** on CPython 3.13.14 and 3.14.5
(991 upstream tests + 351 equivalence cases).

## 6. What was *not* done, and why

* **Code generation to Python source** (`exec` a specialised function per
  expression) would remove even the closure calls. Rejected: it multiplies the
  attack surface for an expression language that is often fed untrusted input,
  makes tracebacks opaque, and breaks `sys.settrace`-based tooling. The closure
  graph already captures nearly all of the available gain.
* **Rewriting `sort_by`/`map` to avoid the `keyfunc` wrapper.** Tempting (these
  are the two slowest shapes), but it changes error behaviour: `keyfunc`
  validates the *key type of the first element* and raises
  `JMESPathTypeError` on mismatch mid-sort. Keeping that contract means keeping
  a Python-level callback.
* **A C extension or Cython build.** Out of scope for a drop-in fork with no
  runtime dependencies, and it would change the install story completely.
* **Caching results by document identity.** Unsound: documents are mutable and
  usually unhashable.

## 7. Reproducing

```bash
pip install -r requirements.txt
git clone --depth 1 https://github.com/jmespath/jmespath.py ../upstream

python -m pytest tests -q                       # correctness
python bench/fuzz_diff.py ../upstream . 6000    # differential fuzzing
python bench/bench_compare.py --baseline ../upstream --optimized .
python bench/bench_scale.py   --baseline ../upstream --optimized .
python bench/bench_ablation.py --root .. --baseline upstream
python bench/make_figures.py                    # needs matplotlib
```
