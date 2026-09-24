"""Recover Vivaldi stack titles from matching historical session metadata."""

import json
from pathlib import Path
import struct

SESSION_HEADER = struct.Struct('<4sI')
RECORD_SIZE = struct.Struct('<H')
PICKLE_HEADER = struct.Struct('<IiI')
SUPPORTED_VERSIONS = (1, 3)
TAB_METADATA_COMMAND = 21
PICKLE_ALIGNMENT = 4
MAX_RECORD_SIZE = 65535


def read_session(path):
    """Parse unencrypted SNSS records without interpreting unrelated commands."""
    raw = path.read_bytes()
    if len(raw) < SESSION_HEADER.size:
        raise ValueError(f'Truncated session header: {path}')
    signature, version = SESSION_HEADER.unpack_from(raw)
    if signature != b'SNSS' or version not in SUPPORTED_VERSIONS:
        raise ValueError(f'Unsupported session format: {path}')
    records = []
    offset = SESSION_HEADER.size
    while offset < len(raw):
        if offset + RECORD_SIZE.size > len(raw):
            raise ValueError(f'Truncated session record: {path}')
        size = RECORD_SIZE.unpack_from(raw, offset)[0]
        offset += RECORD_SIZE.size
        if not size or offset + size > len(raw):
            raise ValueError(f'Invalid session record size: {path}')
        records.append(raw[offset:offset + size])
        offset += size
    return raw[:SESSION_HEADER.size], records


def tab_metadata(records):
    """Return the last metadata command per tab, as used during replay."""
    result = {}
    for index, record in enumerate(records):
        if record[0] != TAB_METADATA_COMMAND:
            continue
        if len(record) < 1 + PICKLE_HEADER.size:
            raise ValueError('Truncated Vivaldi tab metadata.')
        payload_size, tab_id, text_size = PICKLE_HEADER.unpack_from(record, 1)
        text_start = 1 + PICKLE_HEADER.size
        padded_size = (text_size + PICKLE_ALIGNMENT - 1) // PICKLE_ALIGNMENT * PICKLE_ALIGNMENT
        if payload_size != 8 + padded_size or len(record) != 5 + payload_size:
            raise ValueError('Unsupported Vivaldi tab metadata layout.')
        try:
            metadata = json.loads(record[text_start:text_start + text_size])
        except (ValueError, UnicodeError) as error:
            raise ValueError('Invalid Vivaldi tab metadata JSON.') from error
        if not isinstance(metadata, dict):
            raise ValueError('Vivaldi tab metadata must be an object.')
        result[tab_id] = (index, metadata)
    return result


def group_key(metadata):
    group = metadata.get('group')
    workspace = metadata.get('workspaceId')
    if not isinstance(group, str) or not group:
        return None
    if workspace is not None and (isinstance(workspace, bool) or not isinstance(workspace, (int, float))):
        raise ValueError('Unsupported workspace identifier in tab metadata.')
    return group, workspace


def encode_metadata(tab_id, metadata):
    text = json.dumps(metadata, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    padding = b'\0' * (-len(text) % PICKLE_ALIGNMENT)
    record = bytes([TAB_METADATA_COMMAND]) + PICKLE_HEADER.pack(8 + len(text) + len(padding), tab_id, len(text)) + text + padding
    if len(record) > MAX_RECORD_SIZE:
        raise ValueError('Recovered tab metadata exceeds the session record limit.')
    return record


def history_files(directory):
    # Tabs_* uses the separate closed-tab command protocol and must not be parsed here.
    return sorted(path for path in Path(directory).iterdir()
                  if path.is_file() and (path.name.startswith('Session_') or path.suffix == '.bin'))


def recover_names(history_directory, target_directory):
    """Fill missing titles in Session_* files; skip ambiguous historical names."""
    candidates = {}
    for path in history_files(history_directory):
        _, records = read_session(path)
        for _, metadata in tab_metadata(records).values():
            key = group_key(metadata)
            title = metadata.get('fixedGroupTitle')
            if key is not None and isinstance(title, str) and title:
                candidates.setdefault(key, set()).add(title)
    recovered_groups = set()
    changed_records = 0
    changed_files = 0
    for path in sorted(Path(target_directory).glob('Session_*')):
        header, records = read_session(path)
        latest = tab_metadata(records)
        named_groups = {group_key(metadata) for _, metadata in latest.values()
                        if metadata.get('fixedGroupTitle')}
        changed = False
        for tab_id, (index, metadata) in latest.items():
            key = group_key(metadata)
            titles = candidates.get(key, set())
            if key is None or key in named_groups or len(titles) != 1:
                continue
            metadata['fixedGroupTitle'] = next(iter(titles))
            records[index] = encode_metadata(tab_id, metadata)
            recovered_groups.add(key)
            changed_records += 1
            changed = True
        if changed:
            path.write_bytes(header + b''.join(RECORD_SIZE.pack(len(record)) + record for record in records))
            changed_files += 1
    return {'recovered_tab_groups': len(recovered_groups),
            'updated_tab_metadata_records': changed_records,
            'updated_session_files': changed_files,
            'ambiguous_historical_tab_groups': sum(len(titles) > 1 for titles in candidates.values())}
