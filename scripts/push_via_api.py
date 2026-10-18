"""Push a local commit to GitHub through the Git Data API.

Used when ``git push`` over HTTPS is unusable (proxy down / connection reset)
but ``api.github.com`` is reachable.  Every blob is uploaded with its content
base64-encoded, trees are created bottom-up, and the resulting root tree SHA is
asserted to equal the local ``git rev-parse HEAD^{tree}`` -- that equality is
the proof that the remote tree is byte-for-byte identical to the local one.

Usage::

    python scripts/push_via_api.py <owner>/<repo> <branch>
"""
import base64
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args, **kwargs):
    kwargs.setdefault('text', True)
    return subprocess.run(['git'] + list(args), cwd=REPO_ROOT,
                          capture_output=True, check=True, **kwargs)


def api(method, path, payload=None):
    cmd = ['gh', 'api', '--method', method, path]
    data = None
    if payload is not None:
        cmd += ['--input', '-']
        data = json.dumps(payload)
    proc = subprocess.run(cmd, cwd=REPO_ROOT, input=data,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError('%s %s failed: %s' % (method, path, proc.stderr))
    return json.loads(proc.stdout)


def upload_tree(tree_sha):
    """Create blobs for every entry of a local tree; return the new tree."""
    listing = git('ls-tree', '-r', '-z', tree_sha).stdout
    entries = []
    for record in listing.split('\0'):
        if not record:
            continue
        meta, path = record.split('\t', 1)
        mode, kind, sha = meta.split()
        entries.append((mode, kind, sha, path))

    cache_path = os.path.join(REPO_ROOT, '.git', 'blob_map.json')
    blob_map = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding='utf-8') as f:
            blob_map = json.load(f)

    uploaded = 0
    for mode, kind, sha, path in entries:
        if sha in blob_map:
            continue
        if kind == 'commit':          # submodule: keep the gitlink as-is
            blob_map[sha] = sha
            continue
        # Binary-safe: read the object through a pipe, not text mode.
        content = git('cat-file', 'blob', sha, text=False).stdout
        encoded = base64.b64encode(content).decode('ascii')
        result = api('POST', 'repos/%s/git/blobs' % REPO, {
            'content': encoded, 'encoding': 'base64'})
        blob_map[sha] = result['sha']
        uploaded += 1
        if uploaded % 10 == 0:
            print('  uploaded %d blobs' % uploaded)
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(blob_map, f)

    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(blob_map, f)

    # Rebuild the tree bottom-up from the flat listing.
    root = {}
    for mode, kind, sha, path in entries:
        parts = path.split('/')
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = (mode, kind, blob_map[sha])

    def build(node, prefix):
        items = []
        for name, value in sorted(node.items()):
            if isinstance(value, dict):
                items.append({'path': name, 'mode': '040000', 'type': 'tree',
                              'sha': build(value, prefix + name + '/')})
            else:
                mode, kind, sha = value
                items.append({
                    'path': name,
                    'mode': '160000' if kind == 'commit' else mode,
                    'type': 'commit' if kind == 'commit' else 'blob',
                    'sha': sha})
        result = api('POST', 'repos/%s/git/trees' % REPO, {'tree': items})
        print('  tree %s%s -> %s' % (prefix, '', result['sha'][:12]))
        return result['sha']

    return build(root, '')


def get_parents():
    """Parents of the local HEAD, by SHA as they exist on the remote.

    The first push has no parent; later pushes reuse the remote commit that
    was created from the previous local HEAD (the SHAs differ from the local
    ones because the API sets the committer itself).
    """
    return [sha for sha in os.environ.get('PUSH_PARENTS', '').split(',') if sha]


def main():
    global REPO
    REPO = sys.argv[1]
    branch = sys.argv[2] if len(sys.argv) > 2 else 'main'
    local_tree = git('rev-parse', 'HEAD^{tree}').stdout.strip()
    message = git('log', '-1', '--format=%B').stdout.rstrip()

    author = git('log', '-1', '--format=%an|%ae').stdout.strip().split('|')

    print('local root tree: %s' % local_tree)
    remote_tree = upload_tree(local_tree)
    print('remote root tree: %s' % remote_tree)
    if remote_tree != local_tree:
        raise SystemExit('TREE MISMATCH -- remote content differs, aborting')

    commit = api('POST', 'repos/%s/git/commits' % REPO, {
        'message': message,
        'tree': remote_tree,
        'parents': get_parents(),
        'author': {'name': author[0], 'email': author[1]},
    })
    print('commit: %s' % commit['sha'])
    try:
        api('POST', 'repos/%s/git/refs' % REPO,
            {'ref': 'refs/heads/%s' % branch, 'sha': commit['sha']})
        print('created refs/heads/%s' % branch)
    except RuntimeError as e:
        if 'already exists' not in str(e) and 'Reference already exists' not in str(e):
            raise
        api('PATCH', 'repos/%s/git/refs/heads/%s' % (REPO, branch),
            {'sha': commit['sha'], 'force': True})
        print('updated refs/heads/%s' % branch)


if __name__ == '__main__':
    main()
