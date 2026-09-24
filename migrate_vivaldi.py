#!/usr/bin/env python3
"""Restore Vivaldi workspaces, sessions and Speed Dial thumbnails on Linux."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

from session_names import recover_names

ITEMS = ('Preferences', 'Bookmarks', 'Sessions', 'VivaldiThumbnails', 'SyncedFiles')
BACKUP_DIRECTORY = 'vivaldi-transfer-backups'
BACKUP_PREFIX = 'profile-'
PREPARATION_PREFIX = '.vivaldi-prepare-'
REPORT_FILENAME = 'migration-report.json'
SYNCED_STORE_PATH = Path('SyncedFiles/SyncedFilesData')
READ_CHUNK_SIZE = 1024 * 1024


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fingerprint(path):
    """Reject links and special files; hash files without loading them in memory."""
    if path.is_symlink():
        raise ValueError(f'Symbolic links are not supported: {path}')
    if path.is_dir():
        return {p.name: fingerprint(p) for p in sorted(path.iterdir())}
    if path.is_file():
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(READ_CHUNK_SIZE), b''):
                digest.update(chunk)
        return digest.hexdigest()
    raise ValueError(f'Missing or unsupported file: {path}')


def validate(profile):
    result = {name: fingerprint(profile / name) for name in ITEMS}
    for name in ('Preferences', 'Bookmarks'):
        read_json(profile / name)
    return result


def copy_item(source, target):
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def distinct_paths(*paths):
    resolved = [p.resolve() for p in paths]
    for index, left in enumerate(resolved):
        for right in resolved[index + 1:]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError('Source, target and output paths must not overlap.')


def bookmark_nodes(bookmarks):
    def walk(node):
        yield node
        for child in node.get('children', []):
            yield from walk(child)
    for node in bookmarks['roots'].values():
        yield from walk(node)


def bookmark_index(bookmarks):
    result = {}
    for node in bookmark_nodes(bookmarks):
        guid = node.get('guid')
        if not guid or guid in result:
            raise ValueError('Bookmarks must have unique GUIDs.')
        result[guid] = node
    return result


def browser_pids():
    """Conservatively detect Vivaldi processes owned by the current Linux user."""
    if not Path('/proc/self').is_dir():
        raise ValueError('Process checks require Linux /proc.')
    found = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            comm = (proc / 'comm').read_text().strip().lower()
            if 'vivaldi' in comm:
                found.append(proc.name)
                continue
            executable = (proc / 'exe').resolve().name.lower()
            if 'vivaldi' in executable:
                found.append(proc.name)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as error:
            raise ValueError(f'Cannot inspect process {proc.name}; refusing to write.') from error
    return found


def require_closed():
    pids = browser_pids()
    if pids:
        raise ValueError('Close every Vivaldi window first. Running PIDs: ' + ', '.join(pids))


def prepare(old, new, output):
    """Build five migration entries from two offline profile copies."""
    distinct_paths(old, new, output)
    if output.exists():
        raise ValueError('Output must not already exist.')
    old_hashes, new_hashes = validate(old), validate(new)
    old_preferences = read_json(old / 'Preferences')
    preferences = read_json(new / 'Preferences')
    workspaces = old_preferences.get('vivaldi', {}).get('workspaces')
    if not isinstance(workspaces, dict) or not isinstance(workspaces.get('list'), list):
        raise ValueError('Old profile has no supported workspace list.')
    bookmarks = read_json(new / 'Bookmarks')
    old_index = bookmark_index(read_json(old / 'Bookmarks'))
    new_index = bookmark_index(bookmarks)
    if set(old_index) != set(new_index):
        raise ValueError('Bookmark GUID sets differ. Sync bookmarks first; no automatic bookmark merge is supported.')
    preferences.setdefault('vivaldi', {})['workspaces'] = copy.deepcopy(workspaces)
    updated = 0
    for guid, node in new_index.items():
        thumbnail = old_index[guid].get('meta_info', {}).get('Thumbnail', '')
        if thumbnail and thumbnail != node.get('meta_info', {}).get('Thumbnail'):
            node.setdefault('meta_info', {})['Thumbnail'] = thumbnail
            updated += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=PREPARATION_PREFIX, dir=output.parent))
    try:
        for name in ITEMS:
            copy_item(new / name, staging / name)
        write_json(staging / 'Preferences', preferences)
        write_json(staging / 'Bookmarks', bookmarks)
        shutil.rmtree(staging / 'Sessions')
        shutil.copytree(old / 'Sessions', staging / 'Sessions')
        shutil.copytree(old / 'VivaldiThumbnails', staging / 'VivaldiThumbnails', dirs_exist_ok=True)
        store_path = SYNCED_STORE_PATH
        store = read_json(new / store_path) if (new / store_path).exists() else {'files_info': {}}
        old_store = read_json(old / store_path) if (old / store_path).exists() else {'files_info': {}}
        for item in (old / 'SyncedFiles').iterdir():
            if item.is_file() and not item.name.startswith('SyncedFilesData'):
                shutil.copy2(item, staging / 'SyncedFiles' / item.name)
        for key, info in old_store['files_info'].items():
            store['files_info'].setdefault(key, copy.deepcopy(info))
        for key, info in store['files_info'].items():
            if Path(key).name != key or key in ('.', '..'):
                raise ValueError('Invalid synced file identifier.')
            info['has_content_locally'] = (staging / 'SyncedFiles' / key).is_file()
            if not info.get('mimetype'):
                info['mimetype'] = old_store['files_info'].get(key, {}).get('mimetype', '')
        write_json(staging / store_path, store)
        write_json(staging / 'SyncedFiles/SyncedFilesData.bak', store)
        missing, available = [], 0
        for node in new_index.values():
            reference = node.get('meta_info', {}).get('Thumbnail', '')
            for kind, folder in [('thumbnail', 'VivaldiThumbnails'), ('synced-store', 'SyncedFiles')]:
                if reference.startswith(f'chrome://vivaldi-data/{kind}/'):
                    if (staging / folder / reference.rsplit('/', 1)[-1]).is_file():
                        available += 1
                    else:
                        missing.append({'name': node.get('name'), 'reference': reference})
        report = {'workspaces': len(workspaces['list']), 'updated_thumbnail_references': updated,
                  'available_local_image_references': available, 'missing_images': missing}
        if fingerprint(staging / 'Sessions') != old_hashes['Sessions']:
            raise ValueError('Session copy verification failed.')
        report.update(recover_names(old / 'Sessions', staging / 'Sessions'))
        write_json(staging / REPORT_FILENAME, report)
        if old_hashes != validate(old) or new_hashes != validate(new):
            raise ValueError('Input profiles changed during preparation. Use offline copies.')
        validate(staging)
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def prepare_names(history, current, output):
    """Prepare a current profile with only missing session group names repaired."""
    distinct_paths(history, current, output)
    if output.exists():
        raise ValueError('Output must not already exist.')
    history_before = fingerprint(history / 'Sessions')
    current_before = validate(current)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=PREPARATION_PREFIX, dir=output.parent))
    try:
        for name in ITEMS:
            copy_item(current / name, staging / name)
        if validate(staging) != current_before:
            raise ValueError('Profile copy verification failed.')
        report = recover_names(history / 'Sessions', staging / 'Sessions')
        write_json(staging / REPORT_FILENAME, report)
        if validate(current) != current_before or fingerprint(history / 'Sessions') != history_before:
            raise ValueError('Input profiles changed during preparation. Use offline copies.')
        validate(staging)
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(json.dumps(report, indent=2))
    return report


def install(source, target, dry_run=False):
    """Replace exactly five entries, retaining originals and rolling back on errors."""
    distinct_paths(source, target)
    expected, before = validate(source), validate(target)
    print(f'Source: {source}\nTarget: {target}', flush=True)
    pids = browser_pids()
    if dry_run:
        print('Validation passed. No files changed.')
        if pids:
            print('Close Vivaldi before installing. PIDs: ' + ', '.join(pids))
        return None
    require_closed()
    backup_root = target.parent / BACKUP_DIRECTORY
    # A source stored inside the backup root is valid for restore, but never a parent of it.
    if backup_root.resolve() == source.resolve() or source.resolve() in backup_root.resolve().parents:
        raise ValueError('Backup directory would overlap the source.')
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=BACKUP_PREFIX, dir=backup_root))
    stage, saved = backup / 'prepared', backup / 'original'
    stage.mkdir(mode=0o700)
    saved.mkdir(mode=0o700)
    print(f'Backup: {saved}', flush=True)
    write_json(backup / 'info.json', {'source': str(source), 'target': str(target), 'items': ITEMS})
    for name in ITEMS:
        copy_item(source / name, stage / name)
    if validate(stage) != expected or validate(target) != before:
        raise ValueError('Copy verification failed or target changed. Nothing replaced.')
    require_closed()
    moved, installed = [], []
    try:
        for name in ITEMS:
            (target / name).rename(saved / name)
            moved.append(name)
            (stage / name).rename(target / name)
            installed.append(name)
        if validate(target) != expected:
            raise ValueError('Final verification failed.')
    except BaseException:
        for name in reversed(installed):
            (target / name).rename(stage / name)
        for name in reversed(moved):
            (saved / name).rename(target / name)
        raise
    print('Installed and verified. You may start Vivaldi now.')
    print('To restore (close Vivaldi first):\n' + shlex.join([
        'python3', str(Path(__file__).resolve()), 'install', '--source', str(saved), '--target', str(target)
    ]))
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    preparation = commands.add_parser('prepare', help='Prepare data from two offline profile copies')
    preparation.add_argument('--old', required=True, type=Path)
    preparation.add_argument('--new', required=True, type=Path)
    preparation.add_argument('--output', required=True, type=Path)
    names = commands.add_parser('prepare-names', help='Recover missing group names while retaining the current profile')
    names.add_argument('--history', required=True, type=Path)
    names.add_argument('--current', required=True, type=Path)
    names.add_argument('--output', required=True, type=Path)
    installation = commands.add_parser('install', help='Install prepared data or restore a backup')
    installation.add_argument('--source', required=True, type=Path)
    installation.add_argument('--target', required=True, type=Path)
    installation.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Only Linux is supported.')
    if os.geteuid() == 0:
        parser.error('Run as the profile owner, without sudo.')
    if args.command == 'prepare':
        prepare(args.old.expanduser().resolve(), args.new.expanduser().resolve(), args.output.expanduser().resolve())
    elif args.command == 'prepare-names':
        prepare_names(args.history.expanduser().resolve(), args.current.expanduser().resolve(), args.output.expanduser().resolve())
    else:
        install(args.source.expanduser().resolve(), args.target.expanduser().resolve(), args.dry_run)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Aborted: {error}', file=sys.stderr)
        sys.exit(1)
