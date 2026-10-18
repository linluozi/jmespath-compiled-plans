"""Render the figures referenced by README.rst.

Inputs are the JSON files produced by ``bench/bench_arms.py`` and
``bench/bench_scale.py`` so the plots cannot drift from the measurements.

Run with the visualisation environment:
    python bench/make_figures.py
"""
import json
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, 'assets')

INK = '#2b2119'
PAPER = '#faf6f0'
AMBER = '#c8892b'
TERRACOTTA = '#b4552f'
OLIVE = '#6b6b3a'
MUTED = '#8a7a68'
RULE = '#ddd0be'

plt.rcParams.update({
    'figure.facecolor': PAPER,
    'axes.facecolor': PAPER,
    'savefig.facecolor': PAPER,
    'font.family': 'DejaVu Sans',
    'font.size': 10,
    'axes.edgecolor': RULE,
    'axes.labelcolor': INK,
    'text.color': INK,
    'xtick.color': MUTED,
    'ytick.color': MUTED,
    'axes.titlecolor': INK,
})


def _load(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return json.load(f)


def _style(ax):
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    ax.spines['left'].set_color(RULE)
    ax.spines['bottom'].set_color(RULE)
    ax.grid(axis='x', color=RULE, linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)


LABELS = {
    'small_select': 'foo.bar[0].baz',
    'field_projection': 'records[*].meta.score',
    'nested_projection': 'records[*].items[*].v',
    'filter_hash': 'records[?meta.score > `50`].{...}',
    'filter_plain': 'records[?meta.active].name',
    'flatten': 'records[*].tags[]',
    'sort_by': 'sort_by(records, &meta.score)[-1]',
    'pipe_index': 'records[*].meta.score | [0]',
    'multi_select': 'records[:100].[id, score, tag]',
    'functions': 'map(&length(name), records)',
}


def figure_speedup_by_case():
    data = _load('bench_results.json')['summary']
    order = sorted(data, key=lambda k: data[k]['ratio_median'])
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    positions = range(len(order))
    values = [data[k]['ratio_median'] for k in order]
    lows = [data[k]['ratio_median'] - data[k]['ratio_min'] for k in order]
    highs = [data[k]['ratio_max'] - data[k]['ratio_median'] for k in order]
    colors = [TERRACOTTA if v >= 4 else AMBER for v in values]
    ax.barh(positions, values, color=colors, height=0.62, zorder=3)
    ax.errorbar(values, positions, xerr=[lows, highs], fmt='none',
                ecolor=MUTED, elinewidth=1.1, capsize=3, zorder=4)
    ax.set_yticks(list(positions))
    ax.set_yticklabels([LABELS[k] for k in order], fontsize=9)
    for pos, value, high in zip(positions, values, highs):
        # Labels sit past the whisker tip, never on top of it.
        ax.text(value + high + 0.16, pos, '%.1fx' % value, va='center',
                ha='left', fontsize=9, color=INK, fontweight='bold')
    ax.axvline(1.0, color=MUTED, linewidth=1.0, linestyle=(0, (4, 3)))
    ax.set_xlim(0, max(v + h for v, h in zip(values, highs)) * 1.10)
    ax.set_xlabel('search speedup vs. upstream (times)')
    ax.set_title('Search speedup by expression shape', loc='left',
                 fontsize=12, fontweight='bold', pad=14)
    ax.text(0, -0.17, 'median of 5 interleaved rounds; whiskers span min-max',
            transform=ax.transAxes, fontsize=8.5, color=MUTED)
    _style(ax)
    fig.tight_layout(rect=(0.012, 0, 1, 0.97))
    fig.savefig(os.path.join(ASSETS, 'speedup_by_case.png'), dpi=190)
    plt.close(fig)


def figure_scale():
    rows = _load('bench_scale.json')['rows']
    xs = [r['rows'] for r in rows]
    base = [r['baseline_us'] for r in rows]
    opt = [r['optimized_us'] for r in rows]
    fig, (ax, ax2) = plt.subplots(
        1, 2, figsize=(9.6, 4.0), gridspec_kw={'width_ratios': [1.15, 1]})

    ax.plot(xs, base, color=TERRACOTTA, linewidth=2.0, marker='o',
            markersize=5, label='upstream (AST interpreter)', zorder=3)
    ax.plot(xs, opt, color=OLIVE, linewidth=2.0, marker='s',
            markersize=5, label='this fork (compiled plan)', zorder=3)
    ax.set_xscale('log')
    ax.set_yscale('log')
    ax.set_xlabel('records in document')
    ax.set_ylabel('search time (µs, log scale)')
    ax.set_title('Wall time grows with rows, offset does not shrink',
                 loc='left', fontsize=11, fontweight='bold', pad=10)
    ax.legend(frameon=False, fontsize=8.5, loc='upper left',
              bbox_to_anchor=(0, -0.16), ncol=2)
    _style(ax)
    ax.grid(axis='y', color=RULE, linewidth=0.8, alpha=0.9)

    ratios = [r['speedup'] for r in rows]
    ax2.plot(xs, ratios, color=AMBER, linewidth=2.0, marker='D',
             markersize=5, zorder=3)
    ax2.fill_between(xs, 1, ratios, color=AMBER, alpha=0.16, zorder=2)
    for x, y in zip(xs, ratios):
        ax2.annotate('%.1fx' % y, (x, y), textcoords='offset points',
                     xytext=(0, 7), ha='center', fontsize=8.5, color=INK)
    ax2.axhline(1.0, color=MUTED, linewidth=1.0, linestyle=(0, (4, 3)))
    ax2.set_xscale('log')
    ax2.set_ylim(0, max(ratios) * 1.28)
    ax2.set_xlabel('records in document')
    ax2.set_ylabel('speedup (times)')
    ax2.set_title('Speedup by document size', loc='left', fontsize=11,
                  fontweight='bold', pad=10)
    _style(ax2)
    fig.tight_layout(rect=(0.012, 0.02, 1, 0.97))
    fig.savefig(os.path.join(ASSETS, 'speedup_by_size.png'), dpi=190)
    plt.close(fig)


NL = chr(10)


def figure_pipeline():
    """Side-by-side call graphs: per-node dispatch vs. compiled closures."""
    fig, ax = plt.subplots(figsize=(10.0, 5.0))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis('off')

    def box(x, y, w, h, text, fill, edge, size=8.6):
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h,
            boxstyle='round,pad=0.5,rounding_size=1.6',
            linewidth=1.1, facecolor=fill, edgecolor=edge, zorder=3))
        ax.text(x + w / 2, y + h / 2, text, ha='center', va='center',
                fontsize=size, color=INK, zorder=4, linespacing=1.5)

    def arrow(x, y1, y2, color=MUTED):
        ax.add_patch(FancyArrowPatch(
            (x, y1), (x, y2), arrowstyle='-|>', mutation_scale=11,
            linewidth=1.2, color=color, zorder=2,
            shrinkA=0, shrinkB=0))

    ax.text(24, 95, 'upstream: dispatch on every node visit', ha='center',
            fontsize=10.5, fontweight='bold', color=TERRACOTTA)
    ax.text(76, 95, 'this fork: compile once, then call', ha='center',
            fontsize=10.5, fontweight='bold', color=OLIVE)

    steps = [
        (82, 'expression string'),
        (69, 'Lexer -> token list'),
        (56, 'Pratt parser -> AST dict'),
    ]
    for y, label in steps:
        box(6, y, 36, 9, label, PAPER, RULE)
        box(58, y, 36, 9, label, PAPER, RULE)
    arrow(24, 82, 78)
    arrow(24, 69, 65)
    arrow(24, 56, 52)

    box(6, 30, 36, 18,
        NL.join(["for each node of the tree:",
                 "visit(node, value)",
                 "  node['type'] -> dict lookup",
                 "  bound method + *args",
                 "  node['children'] re-read"]),
        '#f6e7dc', TERRACOTTA, size=8.4)
    arrow(24, 56, 48.5)
    box(6, 10, 36, 11, 'result', PAPER, RULE)
    arrow(24, 30, 21.5)

    box(58, 38, 36, 11,
        NL.join(['planner.compile_plan(AST)',
                 'closures, cached per expression']),
        '#e7eddc', OLIVE, size=8.4)
    arrow(76, 56, 49.5, OLIVE)
    box(58, 9, 36, 20,
        NL.join(['for each node of the tree:',
                 'plan(value)',
                 '  one positional call',
                 '  no string dispatch',
                 '  invariants hoisted']),
        '#e7eddc', OLIVE, size=8.4)
    arrow(76, 38, 29.5, OLIVE)

    ax.annotate('compiled once', xy=(94.6, 43.5), xytext=(95.0, 43.5),
                fontsize=7.8, color=OLIVE, style='italic', va='center',
                ha='left')
    ax.annotate('runs every search', xy=(94.6, 19), xytext=(95.0, 19),
                fontsize=7.8, color=OLIVE, style='italic', va='center',
                ha='left')

    fig.subplots_adjust(left=0.01, right=0.93, top=0.97, bottom=0.02)
    fig.savefig(os.path.join(ASSETS, 'pipeline.png'), dpi=190)
    plt.close(fig)


def figure_ablation():
    """Cumulative effect of each layer, on the parse path and the search path."""
    data = _load('bench_ablation.json')
    # ``work`` is the shipped code; ``armC`` is byte-identical to it, so using
    # armC here would just pick the luckier of two noise draws.
    arms = ['upstream', 'armA', 'armB', 'fork']
    names = ['upstream', NL.join(['+ compiled', 'plans']),
             NL.join(['+ single-pass', 'lexer']),
             NL.join(['+ table dispatch', '& signature cache'])]
    parse = [data[a]['parse_us'] for a in arms]
    search = [data[a]['search_us'] for a in arms]

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(9.8, 4.0))
    xs = range(len(arms))

    ax.plot(xs, parse, color=AMBER, linewidth=2.0, marker='o',
            markersize=6, zorder=3)
    for x, y in zip(xs, parse):
        ax.annotate('%.1f' % y, (x, y), textcoords='offset points',
                    xytext=(0, 9), ha='center', fontsize=8.5, color=INK)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(names, fontsize=8.5)
    ax.set_ylabel('parse + AST build (µs)')
    ax.set_ylim(0, max(parse) * 1.22)
    ax.set_title('Compile path: %.1fx faster' % (parse[0] / parse[-1]),
                 loc='left', fontsize=11, fontweight='bold', pad=10)
    ax.text(0, -0.30, 'mean over 6 expressions, cache purged each iteration',
            transform=ax.transAxes, fontsize=8, color=MUTED)
    _style(ax)
    ax.grid(axis='y', color=RULE, linewidth=0.8, alpha=0.9)

    ax2.plot(xs, search, color=TERRACOTTA, linewidth=2.0, marker='s',
             markersize=6, zorder=3)
    for x, y in zip(xs, search):
        ax2.annotate('%.0f' % y, (x, y), textcoords='offset points',
                     xytext=(0, 9), ha='center', fontsize=8.5, color=INK)
    ax2.set_xticks(list(xs))
    ax2.set_xticklabels(names, fontsize=8.5)
    ax2.set_ylabel('search on 2 000 rows (µs)')
    ax2.set_ylim(0, max(search) * 1.22)
    ax2.set_title('Search path: %.1fx faster' % (search[0] / search[-1]),
                  loc='left', fontsize=11,
                  fontweight='bold', pad=10)
    ax2.text(0, -0.30, 'filter + multi-select hash; only the 1st layer moves it',
             transform=ax2.transAxes, fontsize=8, color=MUTED)
    _style(ax2)
    ax2.grid(axis='y', color=RULE, linewidth=0.8, alpha=0.9)

    fig.tight_layout(rect=(0.012, 0.03, 1, 0.97))
    fig.savefig(os.path.join(ASSETS, 'ablation.png'), dpi=190)
    plt.close(fig)


def main():
    os.makedirs(ASSETS, exist_ok=True)
    figure_speedup_by_case()
    figure_scale()
    figure_pipeline()
    figure_ablation()
    print('figures written to %s' % ASSETS)


if __name__ == '__main__':
    main()
