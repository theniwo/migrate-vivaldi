import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import migrate_vivaldi as migration


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old = self.profile('old', 'old')
        self.new = self.profile('new', 'new')
        self.output = self.root / 'prepared'
        self.processes = patch.object(migration, 'browser_pids', return_value=[])
        self.processes.start()
        self.addCleanup(self.processes.stop)
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def profile(self, name, value):
        profile = self.root / name
        profile.mkdir()
        migration.write_json(profile / 'Preferences', {
            'unrelated': value, 'vivaldi': {'workspaces': {'list': [{'id': 42, 'name': value}]}}
        })
        migration.write_json(profile / 'Bookmarks', {'roots': {'bookmark_bar': {
            'guid': 'root', 'children': [{'guid': 'bookmark', 'name': value,
                'url': 'https://example.com', 'meta_info': {
                    'Thumbnail': f'chrome://vivaldi-data/thumbnail/{value}.png',
                    'Description': value}}]}}})
        for folder in ('Sessions', 'VivaldiThumbnails', 'SyncedFiles'):
            (profile / folder).mkdir()
        (profile / 'Sessions' / f'Session_{value}').write_bytes(b'SNSS\x03\0\0\0')
        (profile / 'VivaldiThumbnails' / f'{value}.png').write_bytes(value.encode())
        migration.write_json(profile / 'SyncedFiles/SyncedFilesData', {'files_info': {}})
        return profile

    def test_prepare_preserves_other_preferences_and_bookmark_data(self):
        old_before, new_before = migration.validate(self.old), migration.validate(self.new)
        report = migration.prepare(self.old, self.new, self.output)
        preferences = migration.read_json(self.output / 'Preferences')
        self.assertEqual(preferences['unrelated'], 'new')
        self.assertEqual(preferences['vivaldi']['workspaces']['list'][0]['name'], 'old')
        bookmarks = migration.read_json(self.output / 'Bookmarks')
        node = next(n for n in migration.bookmark_nodes(bookmarks) if n['guid'] == 'bookmark')
        self.assertEqual(node['name'], 'new')
        self.assertEqual(node['meta_info']['Description'], 'new')
        self.assertTrue(node['meta_info']['Thumbnail'].endswith('/old.png'))
        self.assertEqual(report['workspaces'], 1)
        self.assertEqual(report['available_local_image_references'], 1)
        self.assertEqual(migration.validate(self.old), old_before)
        self.assertEqual(migration.validate(self.new), new_before)

    def test_mismatched_bookmarks_abort_before_output(self):
        data = migration.read_json(self.new / 'Bookmarks')
        data['roots']['bookmark_bar']['guid'] = 'different'
        migration.write_json(self.new / 'Bookmarks', data)
        with self.assertRaisesRegex(ValueError, 'GUID sets differ'):
            migration.prepare(self.old, self.new, self.output)
        self.assertFalse(self.output.exists())

    def test_synced_file_metadata_and_missing_image_report(self):
        data = migration.read_json(self.old / 'Bookmarks')
        child = data['roots']['bookmark_bar']['children'][0]
        child['meta_info']['Thumbnail'] = 'chrome://vivaldi-data/synced-store/image.3'
        migration.write_json(self.old / 'Bookmarks', data)
        migration.write_json(self.old / 'SyncedFiles/SyncedFilesData', {'files_info': {
            'image.3': {'has_content_locally': True, 'mimetype': 'image/png'}}})
        new_store = {'files_info': {'image.3': {'has_content_locally': False,
                                              'local_references': {'bookmark': ['test']}}}}
        migration.write_json(self.new / 'SyncedFiles/SyncedFilesData', new_store)
        report = migration.prepare(self.old, self.new, self.output)
        self.assertEqual(len(report['missing_images']), 1)
        store = migration.read_json(self.output / 'SyncedFiles/SyncedFilesData')
        self.assertFalse(store['files_info']['image.3']['has_content_locally'])
        self.assertEqual(store['files_info']['image.3']['local_references'], {'bookmark': ['test']})
        self.assertEqual(store['files_info']['image.3']['mimetype'], 'image/png')

    def test_synced_file_contents_are_copied(self):
        (self.old / 'SyncedFiles/image.3').write_bytes(b'abc')
        migration.write_json(self.old / 'SyncedFiles/SyncedFilesData', {'files_info': {
            'image.3': {'has_content_locally': False, 'mimetype': 'image/png'}}})
        migration.prepare(self.old, self.new, self.output)
        self.assertEqual((self.output / 'SyncedFiles/image.3').read_bytes(), b'abc')
        store = migration.read_json(self.output / 'SyncedFiles/SyncedFilesData')
        self.assertTrue(store['files_info']['image.3']['has_content_locally'])

    def test_install_replaces_sessions_and_restore_retains_current_state(self):
        before = migration.validate(self.new)
        saved = migration.install(self.old, self.new)
        self.assertEqual(migration.validate(self.new), migration.validate(self.old))
        self.assertFalse((self.new / 'Sessions/Session_new').exists())
        self.assertEqual(migration.validate(saved), before)
        second_saved = migration.install(saved, self.new)
        self.assertEqual(migration.validate(self.new), before)
        self.assertEqual(migration.validate(second_saved), migration.validate(self.old))

    def test_running_browser_aborts_without_writing(self):
        before = migration.validate(self.new)
        with patch.object(migration, 'browser_pids', return_value=['123']):
            with self.assertRaisesRegex(ValueError, 'Close every Vivaldi'):
                migration.install(self.old, self.new)
        self.assertEqual(migration.validate(self.new), before)
        self.assertFalse((self.root / 'vivaldi-transfer-backups').exists())

    def test_dry_run_writes_nothing_even_with_browser_running(self):
        before = migration.fingerprint(self.root)
        with patch.object(migration, 'browser_pids', return_value=['123']):
            migration.install(self.old, self.new, dry_run=True)
        self.assertEqual(migration.fingerprint(self.root), before)

    def test_failed_install_rolls_back(self):
        before = migration.validate(self.new)
        rename = Path.rename

        def fail_bookmark_install(path, destination):
            if path.parent.name == 'prepared' and path.name == 'Bookmarks':
                raise OSError('simulated disk error')
            return rename(path, destination)

        with patch.object(Path, 'rename', fail_bookmark_install):
            with self.assertRaisesRegex(OSError, 'simulated disk error'):
                migration.install(self.old, self.new)
        self.assertEqual(migration.validate(self.new), before)

    def test_corrupt_staged_copy_aborts_before_replacement(self):
        before = migration.validate(self.new)
        real_copy = migration.copy_item

        def corrupt_copy(source, target):
            real_copy(source, target)
            if target.name == 'Preferences':
                migration.write_json(target, {'corrupt': True})

        with patch.object(migration, 'copy_item', corrupt_copy):
            with self.assertRaisesRegex(ValueError, 'Copy verification failed'):
                migration.install(self.old, self.new)
        self.assertEqual(migration.validate(self.new), before)

    def test_overlap_and_symlinks_rejected(self):
        with self.assertRaisesRegex(ValueError, 'must not overlap'):
            migration.install(self.old, self.old)
        (self.old / 'Sessions/link').symlink_to(self.new / 'Preferences')
        with self.assertRaisesRegex(ValueError, 'Symbolic links'):
            migration.install(self.old, self.new)

    def test_existing_output_not_overwritten(self):
        self.output.mkdir()
        marker = self.output / 'keep'
        marker.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'must not already exist'):
            migration.prepare(self.old, self.new, self.output)
        self.assertEqual(marker.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
