#!/usr/bin/env python3
"""Check an agent workflow diagram against the code, and render it as one HTML page.

    bauplan.py check  docs/agent-workflow/workflow.json [--fix]
    bauplan.py render docs/agent-workflow/workflow.json [--out PATH] [--fragment] [--no-check]
                       [--stats PATH] [--examples PATH]

`check` finds every row's `at` line in the code: where a function row's function is defined (in most common
languages), or the `match` text of any other row. It reports rows that moved or are gone, prompt files that are
missing, example runs that name steps the workflow no longer has, and the blocks whose files changed since the
diagram was drawn (`drawn_at.commit`). `--fix` rewrites the line numbers of rows that moved. It exits 1 when a row
or prompt is not found, so it can run in CI.

`render` runs the same check and writes the viewer with the workflow, the check results, and whatever sits beside
the workflow: `stats.json` (usage numbers), `examples.json` (real runs), plus the text of every prompt file the
model calls use. Standard library only.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / 'assets' / 'viewer.html'
BRANCHES = (('calls', 'c'), ('yes', 'y'), ('no', 'n'))
# A definition found this close to its `at` line counts as in place.
TOLERANCE = 2
PROMPT_LIMIT = 60_000
# Words that show a condition or loop row sits on its line, across common languages.
KEYWORDS = {
    'if': ('if ', 'if(', 'elif ', 'else', 'except', 'catch', 'unless', 'while', 'return', 'raise', 'throw', '?', 'when'),
    'switch': ('switch', 'match', 'case', 'when', 'if ', 'if(', 'elif', 'select'),
    'each': ('for ', 'for(', 'for await', 'while', 'loop', 'each', 'map(', 'gather', 'all(', 'submit', 'until'),
}
# How a function or class is defined, by file extension. NAME is replaced by the escaped name.
DEFINITIONS = {
    ('.py',): [r'^\s*(?:async\s+def|def|class)\s+NAME\b'],
    ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts'): [
        r'\bfunction\s*\*?\s*NAME\s*[<(]', r'\b(?:const|let|var)\s+NAME\s*[:=]', r'\bclass\s+NAME\b',
        r'^\s*(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|protected\s+|static\s+|async\s+|override\s+|readonly\s+|get\s+|set\s+)*\*?NAME\s*[<(]',
        r'^\s*NAME\s*:\s*(?:async\s+)?(?:function\b|\()',
    ],
    ('.go',): [r'^\s*func\s+(?:\([^)]*\)\s*)?NAME\s*[\[(]', r'^\s*type\s+NAME\b'],
    ('.rb',): [r'^\s*def\s+(?:self\.)?NAME\b', r'^\s*(?:class|module)\s+NAME\b'],
    ('.rs',): [r'^\s*(?:pub(?:\([\w:]+\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+NAME\b', r'^\s*(?:pub\s+)?(?:struct|enum|trait)\s+NAME\b'],
    ('.php',): [r'\bfunction\s+NAME\s*\(', r'\bclass\s+NAME\b'],
    ('.java', '.kt', '.kts', '.cs', '.scala', '.swift', '.dart'): [
        r'\b(?:fun|func|def)\s+(?:<[^>]*>\s*)?NAME\s*[<(]', r'\b(?:class|interface|object|struct|record|enum)\s+NAME\b',
        r'^\s*(?:[\w<>\[\],?@]+\s+)+NAME\s*\(',
    ],
}


def git(root, *args):
    try:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    except FileNotFoundError:
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


def repo_root(path):
    """The git repository holding the workflow, or the workflow's own folder when there is none."""
    top = git(path.resolve().parent, 'rev-parse', '--show-toplevel')
    return Path(top) if top else path.resolve().parent


def walk(rows, prefix):
    """Every row with its key; keys match the viewer: '<node>:<i>', then '.c<i>', '.y<i>', '.n<i>', '.k<case>-<i>'."""
    for i, row in enumerate(rows or []):
        key = f'{prefix}{i}'
        yield key, row
        for field, mark in BRANCHES:
            if row.get(field):
                yield from walk(row[field], f'{key}.{mark}')
        for case_index, case in enumerate(row.get('cases') or []):
            yield from walk(case.get('rows'), f'{key}.k{case_index}-')


def detail_rows(spec):
    for node_id, node in spec['nodes'].items():
        for key, row in walk(node.get('detail'), f'{node_id}:'):
            yield node_id, key, row


def effects(spec):
    for node_id, key, row in detail_rows(spec):
        for fx in row.get('fx') or []:
            yield node_id, key, row, fx


def target(row):
    """What the `at` line must hold: the definition of a function row, else the row's `match` text."""
    if row.get('match'):
        return 'text', row['match']
    if row.get('t') == 'fn':
        name = re.match(r'\s*(?:await\s+|new\s+)?([A-Za-z_$][\w.$:]*)', row.get('fn', ''))
        if name:
            return 'def', re.split(r'[.:]+', name.group(1))[-1]
    return None


def definition_patterns(suffix, name):
    for suffixes, patterns in DEFINITIONS.items():
        if suffix in suffixes:
            return [re.compile(p.replace('NAME', re.escape(name))) for p in patterns]
    # An unknown language: the name followed by an opening parenthesis or a definition keyword before it.
    return [re.compile(rf'\b(?:def|fn|func|function|fun|sub|proc)\s+{re.escape(name)}\b'), re.compile(rf'\b{re.escape(name)}\s*\(')]


def find_lines(lines, kind, value, suffix):
    if kind == 'def':
        patterns = definition_patterns(suffix, value)
        return [i + 1 for i, line in enumerate(lines) if any(p.search(line) for p in patterns)]
    return [i + 1 for i, line in enumerate(lines) if value in line]


def check_row(row, lines, line, suffix):
    want = target(row)
    if want:
        found = find_lines(lines, *want, suffix)
        if not found:
            return {'status': 'missing', 'line': line}
        nearest = min(found, key=lambda n: abs(n - line))
        return {'status': 'ok' if abs(nearest - line) <= TOLERANCE else 'moved', 'line': nearest}
    if line > len(lines):
        return {'status': 'missing', 'line': line}
    words = KEYWORDS.get(row.get('t'), ())
    return {'status': 'ok' if any(w in lines[line - 1] for w in words) else 'unchecked', 'line': line}


def repo_path(spec, path):
    """A path from code_root, as git names it from the repository root."""
    return '/'.join(part for part in (spec['repo'].get('code_root', '').strip('/'), path) if part)


def no_check(spec):
    """Check results for a workflow rendered without its code, such as an example."""
    return {'rows': {}, 'blocks': {}, 'prompts': {}, 'summary': None}


def stale_example_keys(spec, examples):
    """Per example, the row keys and effect ids it names that the workflow no longer has."""
    rows = {key: row for _, key, row in detail_rows(spec)}
    effect_ids = {f'{key}#{j}' for key, row in rows.items() for j in range(len(row.get('fx') or []))}
    stale = {}
    for item in examples:
        gone = [k for k in list(item.get('rows') or {}) + list(item.get('path') or {}) if k not in rows]
        gone += [k for k in item.get('fx') or {} if k not in effect_ids]
        if gone:
            stale[item.get('id', '?')] = gone
    return stale


def load_examples(spec_path, path=None):
    path = path or spec_path.with_name('examples.json')
    return json.loads(path.read_text()) if path.is_file() else []


def run_check(spec, root, examples=()):
    code_root = root / spec['repo']['code_root']
    prompts_root = root / spec['repo'].get('prompts_root', 'prompts')
    files = {}
    rows = {}
    node_files = {node_id: set() for node_id in spec['nodes']}
    for node_id, node in spec['nodes'].items():
        if node.get('file'):
            node_files[node_id].add(repo_path(spec, node['file'].rstrip('/')))
    for node_id, key, row in detail_rows(spec):
        at = row.get('at')
        if not at:
            continue
        path, _, line = at.rpartition(':')
        if not path or not line.isdigit():
            rows[key] = {'status': 'missing', 'line': 0, 'reason': 'at must be path:line'}
            continue
        node_files[node_id].add(repo_path(spec, path))
        file = code_root / path
        if file not in files:
            files[file] = file.read_text(errors='replace').splitlines() if file.is_file() else None
        lines = files[file]
        rows[key] = {'status': 'missing-file', 'line': int(line)} if lines is None else check_row(row, lines, int(line), file.suffix)

    prompts = {}
    for _, _, _, fx in effects(spec):
        prompt = fx.get('prompt')
        if fx.get('tag') == 'LLM' and prompt and prompt != 'inline':
            prompts[prompt] = 'ok' if (prompts_root / prompt).is_file() else 'missing'
    files_missing = sorted(
        node['file'] for node in spec['nodes'].values()
        if node.get('file') and not (code_root / node['file']).exists()
    )

    drawn = (spec.get('drawn_at') or {}).get('commit', '')
    changed_paths = set(git(root, 'diff', '--name-only', drawn, '--').splitlines()) if drawn else set()
    blocks = {}
    for node_id, paths in node_files.items():
        hits = sorted(p for p in changed_paths if any(p == f or p.startswith(f + '/') for f in paths))
        blocks[node_id] = {'changed': hits}

    head = git(root, 'rev-parse', '--short', 'HEAD')
    pushed = bool(git(root, 'branch', '-r', '--contains', 'HEAD'))
    upstream = git(root, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}').split('/', 1)[-1] or 'main'
    counts = {'ok': 0, 'moved': 0, 'missing': 0, 'unchecked': 0}
    for result in rows.values():
        counts['missing' if result['status'] == 'missing-file' else result['status']] += 1
    order = [i for band in spec.get('bands', []) for i in band.get('main', []) + [s['id'] for s in band.get('side', [])]]
    summary = {
        'head': (head + (' with uncommitted changes' if git(root, 'status', '--porcelain', '--untracked-files=no') else '')) if head else 'a folder without git',
        'link_commit': git(root, 'rev-parse', 'HEAD') if pushed else upstream,
        'checked_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'drawn_commit': drawn,
        'counts': counts,
        'prompts_missing': sorted(p for p, s in prompts.items() if s == 'missing'),
        'files_missing': files_missing,
        'changed_blocks': [i for i in order if blocks.get(i, {}).get('changed')],
        'examples_stale': stale_example_keys(spec, examples),
    }
    return {'rows': rows, 'blocks': blocks, 'prompts': prompts, 'summary': summary}


def label(spec, node_id):
    node = spec['nodes'][node_id]
    return f"{node['num']:02d} {node['title']}" if node.get('num') else node['title']


def report(spec, spec_path, result):
    s = result['summary']
    c = s['counts']
    out = [f"Checked {spec_path} against the code at {s['head']}.",
           f"  {c['ok']} rows found, {c['moved']} moved, {c['missing']} not found, {c['unchecked']} without a line to check."]
    keyed = {key: (node_id, row) for node_id, key, row in detail_rows(spec)}
    for status, title in (('moved', 'Moved'), ('missing', 'Not found'), ('missing-file', 'File not found')):
        hits = [(k, r) for k, r in result['rows'].items() if r['status'] == status]
        if hits:
            out.append(f'  {title}:')
            for key, r in hits:
                node_id, row = keyed[key]
                name = row.get('fn') or row.get('title') or row.get('cond') or row.get('over')
                where = f"now line {r['line']}" if status == 'moved' else 'not there'
                out.append(f"    {label(spec, node_id)}: {name} at {row['at']}: {where}")
    if s['prompts_missing']:
        out.append(f"  Prompt files not found: {', '.join(s['prompts_missing'])}")
    if s['files_missing']:
        out.append(f"  Overview file paths not found: {', '.join(s['files_missing'])}")
    for example_id, keys in s.get('examples_stale', {}).items():
        out.append(f"  Example {example_id} names {len(keys)} step(s) no longer in the workflow: {', '.join(keys[:6])}")
    if s['changed_blocks']:
        out.append(f"  Changed since {s['drawn_commit']}:")
        for node_id in s['changed_blocks']:
            out.append(f"    {label(spec, node_id)}: {', '.join(result['blocks'][node_id]['changed'])}")
    elif s['drawn_commit']:
        out.append(f"  Nothing it covers changed since {s['drawn_commit']}.")
    return '\n'.join(out)


def fix_lines(spec, spec_path, result):
    """Rewrite `at` line numbers of rows found elsewhere, keeping the file's own formatting."""
    moves = {}
    for _, key, row in detail_rows(spec):
        r = result['rows'].get(key)
        if r and r['status'] in ('ok', 'moved') and row.get('at') and not row['at'].endswith(f":{r['line']}"):
            moves.setdefault(row['at'], set()).add(f"{row['at'].rpartition(':')[0]}:{r['line']}")
    text = spec_path.read_text()
    fixed = 0
    for old, new in moves.items():
        if len(new) == 1:
            text, n = re.subn(rf'("at"\s*:\s*"){re.escape(old)}(")', rf'\g<1>{next(iter(new))}\g<2>', text)
            fixed += n
    spec_path.write_text(text)
    return fixed


def page_title(spec):
    if spec.get('page_title'):
        return spec['page_title']
    return f"{spec['title'].split(':')[0].strip().title()} Workflow"


def render(spec, spec_path, root, result, out, fragment, stats_path=None, examples_path=None):
    stats_path = stats_path or spec_path.with_name('stats.json')
    stats = json.loads(stats_path.read_text()) if stats_path.is_file() else {}
    prompts_root = root / spec['repo'].get('prompts_root', 'prompts')
    prompts = {
        name: (prompts_root / name).read_text(errors='replace')[:PROMPT_LIMIT]
        for name, status in result['prompts'].items() if status == 'ok'
    }
    template = spec['repo'].get('link')
    summary = result['summary'] or {}
    # The local path goes into the page only for editor links that need it ({root}).
    link = {'template': template, 'commit': summary.get('link_commit') or 'HEAD',
            'root': str(root) if '{root}' in template else ''} if template else None
    data = {'spec': spec, 'check': result, 'stats': stats, 'prompts': prompts, 'link': link, 'examples': load_examples(spec_path, examples_path)}
    payload = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    html = TEMPLATE.read_text().replace('/*__DATA__*/null', payload).replace('__PAGE_TITLE__', page_title(spec))
    if not fragment:
        head, _, body = html.partition('</style>')
        html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                '<style>[hidden]{display:none!important}</style>\n'
                f'{head}</style>\n</head>\n<body>{body}\n</body>\n</html>\n')
    out.write_text(html)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    check_cmd = sub.add_parser('check', help='check every row against the code')
    check_cmd.add_argument('workflow', type=Path)
    check_cmd.add_argument('--fix', action='store_true', help='rewrite the line numbers of rows that moved')
    render_cmd = sub.add_parser('render', help='write the viewer page')
    render_cmd.add_argument('workflow', type=Path)
    render_cmd.add_argument('--out', type=Path, help='default: index.html beside the workflow')
    render_cmd.add_argument('--fragment', action='store_true', help='leave out <html>, <head> and <body>, for hosts that add them')
    render_cmd.add_argument('--no-check', action='store_true', help='render without the code, as for an example')
    render_cmd.add_argument('--stats', type=Path, help='usage numbers; default: stats.json beside the workflow')
    render_cmd.add_argument('--examples', type=Path, help='example runs; default: examples.json beside the workflow')
    args = parser.parse_args()

    spec_path = args.workflow
    spec = json.loads(spec_path.read_text())
    root = repo_root(spec_path)
    if getattr(args, 'no_check', False):
        out = args.out or spec_path.with_name('index.html')
        render(spec, spec_path, root, no_check(spec), out, args.fragment, args.stats, args.examples)
        print(f'Wrote {out} without checking it against the code.')
        return
    result = run_check(spec, root, load_examples(spec_path, getattr(args, 'examples', None)))
    if args.command == 'check':
        if args.fix:
            fixed = fix_lines(spec, spec_path, result)
            spec = json.loads(spec_path.read_text())
            result = run_check(spec, root, load_examples(spec_path))
            print(f'Rewrote {fixed} line number(s).')
        print(report(spec, spec_path, result))
        s = result['summary']
        sys.exit(1 if s['counts']['missing'] or s['prompts_missing'] else 0)
    out = args.out or spec_path.with_name('index.html')
    render(spec, spec_path, root, result, out, args.fragment, args.stats, args.examples)
    print(report(spec, spec_path, result))
    print(f'Wrote {out}')


if __name__ == '__main__':
    main()
