import json
import struct
import unittest

import migrate_vivaldi as migration
import session_names as names
import test_migration


def metadata_record(tab_id, metadata):
    text = json.dumps(metadata, ensure_ascii=False).encode('utf-8')
    padded = text + b'\0' * (-len(text) % 4)
    return b'\x15' + struct.pack('<IiI', 8 + len(padded), tab_id, len(text)) + padded


def write_session(path, records, version=3):
    path.write_bytes(b'SNSS' + struct.pack('<I', version) + b''.join(
        struct.pack('<H', len(record)) + record for record in records))


class SessionNamesTests(unittest.TestCase):
    profile = test_migration.MigrationTests.profile

    def setUp(self):
        test_migration.MigrationTests.setUp(self)
        self.history = self.old / 'Sessions/history.bin'
        self.current = self.new / 'Sessions/Session_current'
        self.metadata = {'group': 'synthetic-group', 'workspaceId': 42, 'ext_id': 'synthetic-tab'}

    def test_names_only_preserves_current_profile_and_opaque_records(self):
        title = 'Recherche – 日本語'
        write_session(self.history, [metadata_record(1, {**self.metadata, 'fixedGroupTitle': title})], 1)
        opaque = b'\x06opaque navigation bytes'
        earlier = metadata_record(9, {**self.metadata, 'unrelated': 'earlier'})
        write_session(self.current, [earlier, opaque, metadata_record(9, self.metadata), b'\xff'])
        before = migration.validate(self.new)
        report = migration.prepare_names(self.old, self.new, self.output)
        self.assertEqual(report['recovered_tab_groups'], 1)
        self.assertEqual(report['updated_tab_metadata_records'], 1)
        _, records = names.read_session(self.output / 'Sessions/Session_current')
        self.assertEqual(records[:2], [earlier, opaque])
        self.assertEqual(records[-1], b'\xff')
        recovered = names.tab_metadata(records)[9][1]
        self.assertEqual(recovered, {**self.metadata, 'fixedGroupTitle': title})
        self.assertEqual(migration.validate(self.new), before)
        for entry in migration.ITEMS:
            if entry != 'Sessions':
                self.assertEqual(migration.fingerprint(self.output / entry), before[entry])
        self.assertEqual(names.recover_names(self.old / 'Sessions', self.output / 'Sessions')['updated_session_files'], 0)

    def test_prepare_recovers_names_automatically(self):
        write_session(self.history, [metadata_record(1, {**self.metadata, 'fixedGroupTitle': 'Research'})], 1)
        write_session(self.old / 'Sessions/Session_old', [metadata_record(1, self.metadata)])
        before = migration.validate(self.old)
        report = migration.prepare(self.old, self.new, self.output)
        self.assertEqual(report['recovered_tab_groups'], 1)
        self.assertEqual(migration.validate(self.old), before)

    def test_conflicts_existing_names_and_other_workspaces_are_untouched(self):
        cases = [
            ([{**self.metadata, 'fixedGroupTitle': 'First'}, {**self.metadata, 'fixedGroupTitle': 'Second'}], self.metadata),
            ([{**self.metadata, 'fixedGroupTitle': 'Historical'}], {**self.metadata, 'fixedGroupTitle': 'Current'}),
            ([{**self.metadata, 'fixedGroupTitle': 'Historical'}], {**self.metadata, 'workspaceId': 99}),
            ([{**self.metadata, 'fixedGroupTitle': 'Historical'}], {**self.metadata, 'group': 'another-group'}),
        ]
        for historical, current in cases:
            with self.subTest(current=current, historical=historical):
                write_session(self.history, [metadata_record(index, value) for index, value in enumerate(historical)], 1)
                write_session(self.current, [metadata_record(1, current)])
                before = self.current.read_bytes()
                report = names.recover_names(self.old / 'Sessions', self.new / 'Sessions')
                self.assertEqual(report['recovered_tab_groups'], 0)
                self.assertEqual(self.current.read_bytes(), before)

    def test_existing_name_on_another_tab_protects_whole_group(self):
        write_session(self.history, [metadata_record(1, {**self.metadata, 'fixedGroupTitle': 'Historical'})])
        write_session(self.current, [metadata_record(1, self.metadata), metadata_record(2, {
            **self.metadata, 'fixedGroupTitle': 'Current'})])
        before = self.current.read_bytes()
        names.recover_names(self.old / 'Sessions', self.new / 'Sessions')
        self.assertEqual(self.current.read_bytes(), before)

    def test_historical_metadata_uses_last_update_per_tab(self):
        write_session(self.history, [
            metadata_record(1, {**self.metadata, 'fixedGroupTitle': 'Outdated'}),
            metadata_record(1, {**self.metadata, 'fixedGroupTitle': 'Final'}),
        ])
        write_session(self.current, [metadata_record(1, self.metadata)])
        names.recover_names(self.old / 'Sessions', self.new / 'Sessions')
        _, records = names.read_session(self.current)
        self.assertEqual(names.tab_metadata(records)[1][1]['fixedGroupTitle'], 'Final')

    def test_repaired_profile_install_and_restore(self):
        write_session(self.history, [metadata_record(1, {**self.metadata, 'fixedGroupTitle': 'Research'})])
        write_session(self.current, [metadata_record(1, self.metadata)])
        before = migration.validate(self.new)
        migration.prepare_names(self.old, self.new, self.output)
        backup = migration.install(self.output, self.new)
        self.assertEqual(migration.validate(self.new), migration.validate(self.output))
        migration.install(backup, self.new)
        self.assertEqual(migration.validate(self.new), before)

    def test_invalid_sessions_leave_no_output(self):
        invalid_sessions = [b'SNSS', b'SNSS\x05\0\0\0', b'SNSS\x03\0\0\0\x10\0x',
                            b'SNSS\x03\0\0\0\x01\0\x15']
        for raw in invalid_sessions:
            with self.subTest(raw=raw):
                self.history.write_bytes(raw)
                before = migration.validate(self.new)
                with self.assertRaises(ValueError):
                    migration.prepare_names(self.old, self.new, self.output)
                self.assertFalse(self.output.exists())
                self.assertEqual(migration.validate(self.new), before)

    def test_closed_tab_protocol_is_not_interpreted(self):
        (self.old / 'Sessions/Tabs_synthetic').write_bytes(b'different command protocol')
        migration.prepare_names(self.old, self.new, self.output)

    def test_oversize_recovery_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'record limit'):
            names.encode_metadata(1, {**self.metadata, 'fixedGroupTitle': 'x' * names.MAX_RECORD_SIZE})
