"""Descriptor-relative filesystem and marker primitives for the store."""
from __future__ import annotations

import errno
import json
import os
import re
import stat
from pathlib import Path

from thyca.core.conversation import ConversationRef
from .contracts import ConversationStoreError, _ATTEMPT_RE, _local_error, _reject_duplicate_pairs, _validate_uuid

DB_FILE_NAME = "conversations.db"

LOCK_FILE_NAME = "conversations.lock"

ROOT_MARKER_NAME = "conversations.root.json"

ROOT_MARKER_TEMP = "conversations.root.json.tmp"

DM_MARKERS_NAME = "dm-markers"

ROOT_MARKER_MAX_BYTES = 89

DM_MARKER_MAX_BYTES = 210

def _stat_at(dir_fd: int, name: str):
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConversationStoreError() from exc

def _same_inode(left, right) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)

def _check_ancestor_stat(info, path: str) -> None:
    # 65534 is the kernel overflow UID for unmapped root-owned ancestors in
    # the approved user-namespace test environment; such ancestors must remain
    # non-writable except for root-owned sticky temporary directories.
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid(), 65534):
        raise ConversationStoreError()
    permissions = stat.S_IMODE(info.st_mode)
    if permissions & 0o022:
        sticky_tmp = (info.st_uid in (0, 65534) and bool(info.st_mode & stat.S_ISVTX)
                      and permissions & 0o002 and path in ('/tmp', '/var/tmp', '/dev/shm'))
        if not sticky_tmp:
            raise ConversationStoreError()

def _open_parent(root: Path) -> tuple[int, str, Path]:
    raw = os.fspath(root)
    if not isinstance(raw, str) or not raw:
        raise ConversationStoreError()
    initial = Path(raw)
    if any(part == '..' for part in initial.parts):
        raise ConversationStoreError()
    absolute = Path(os.path.abspath(raw))
    if absolute == Path('/') or absolute.name in ('', '.', '..'):
        raise ConversationStoreError()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    current = ''
    try:
        root_info = os.fstat(fd)
        _check_ancestor_stat(root_info, '/')
        for part in absolute.parts[1:-1]:
            current += '/' + part
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                  dir_fd=fd)
            except OSError as exc:
                raise ConversationStoreError() from exc
            try:
                path_info = os.stat(part, dir_fd=fd, follow_symlinks=False)
                fd_info = os.fstat(next_fd)
                if not stat.S_ISDIR(path_info.st_mode) or not _same_inode(path_info, fd_info):
                    raise ConversationStoreError()
                _check_ancestor_stat(fd_info, current)
            except BaseException:
                os.close(next_fd)
                raise
            os.close(fd)
            fd = next_fd
        return fd, absolute.name, absolute
    except BaseException:
        os.close(fd)
        raise

def _fsync_dir(fd: int) -> None:
    os.fsync(fd)

def _open_root(root: Path, *, create: bool) -> tuple[int, int, str, Path]:
    parent_fd, name, absolute = _open_parent(root)
    created_here = False
    try:
        try:
            root_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=parent_fd)
        except FileNotFoundError:
            if not create:
                raise ConversationStoreError()
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                raise ConversationStoreError() from exc
            else:
                created_here = True
                _fsync_dir(parent_fd)
            try:
                root_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                  dir_fd=parent_fd)
            except OSError as exc:
                raise ConversationStoreError() from exc
        except OSError as exc:
            raise ConversationStoreError() from exc
        try:
            path_info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            fd_info = os.fstat(root_fd)
            if (not stat.S_ISDIR(path_info.st_mode) or not _same_inode(path_info, fd_info)
                    or fd_info.st_uid != os.geteuid()):
                raise ConversationStoreError()
            if created_here:
                os.fchmod(root_fd, 0o700)
                fd_info = os.fstat(root_fd)
            if stat.S_IMODE(fd_info.st_mode) != 0o700:
                raise ConversationStoreError()
            return parent_fd, root_fd, name, absolute
        except BaseException:
            os.close(root_fd)
            raise
    except BaseException:
        os.close(parent_fd)
        raise

def _verify_root_name(parent_fd: int, root_name: str, root_fd: int) -> None:
    try:
        path_info = os.stat(root_name, dir_fd=parent_fd, follow_symlinks=False)
        fd_info = os.fstat(root_fd)
    except OSError as exc:
        raise ConversationStoreError() from exc
    if not stat.S_ISDIR(path_info.st_mode) or not _same_inode(path_info, fd_info):
        raise ConversationStoreError()

def _checked_regular_at(dir_fd: int, name: str, *, mode: int,
                        max_bytes: int | None = None, read_data: bool = False):
    try:
        path_info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if (not stat.S_ISREG(path_info.st_mode) or path_info.st_uid != os.geteuid()
                or stat.S_IMODE(path_info.st_mode) != mode or path_info.st_nlink != 1):
            raise ConversationStoreError()
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    except FileNotFoundError as exc:
        raise ConversationStoreError() from exc
    except OSError as exc:
        raise ConversationStoreError() from exc
    try:
        fd_info = os.fstat(fd)
        if (not stat.S_ISREG(fd_info.st_mode) or fd_info.st_uid != os.geteuid()
                or stat.S_IMODE(fd_info.st_mode) != mode or fd_info.st_nlink != 1
                or not _same_inode(path_info, fd_info)):
            raise ConversationStoreError()
        if max_bytes is not None and fd_info.st_size > max_bytes:
            raise ConversationStoreError()
        if not read_data:
            return fd, fd_info
        chunks = []
        remaining = None if max_bytes is None else max_bytes + 1
        while remaining is None or remaining > 0:
            size = 65536 if remaining is None else min(65536, remaining)
            chunk = os.read(fd, size)
            if not chunk:
                break
            chunks.append(chunk)
            if remaining is not None:
                remaining -= len(chunk)
        data = b''.join(chunks)
        if max_bytes is not None and len(data) > max_bytes:
            raise ConversationStoreError()
        return fd, fd_info, data
    except BaseException:
        os.close(fd)
        raise

def _open_checked_dir_at(parent_fd: int, name: str, *, mode: int = 0o700) -> tuple[int, os.stat_result]:
    try:
        path_info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (not stat.S_ISDIR(path_info.st_mode) or path_info.st_uid != os.geteuid()
                or stat.S_IMODE(path_info.st_mode) != mode):
            raise ConversationStoreError()
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=parent_fd)
    except OSError as exc:
        raise ConversationStoreError() from exc
    try:
        fd_info = os.fstat(fd)
        if (not stat.S_ISDIR(fd_info.st_mode) or fd_info.st_uid != os.geteuid()
                or stat.S_IMODE(fd_info.st_mode) != mode or not _same_inode(path_info, fd_info)):
            raise ConversationStoreError()
        return fd, fd_info
    except BaseException:
        os.close(fd)
        raise

def _validate_top_namespace(root_fd: int) -> None:
    allowed = {
        DB_FILE_NAME, DB_FILE_NAME + '-wal', DB_FILE_NAME + '-shm', LOCK_FILE_NAME,
        ROOT_MARKER_NAME, ROOT_MARKER_TEMP, DM_MARKERS_NAME,
    }
    for name in os.scandir(root_fd):
        if name.name.startswith('conversations.') and name.name not in allowed:
            raise ConversationStoreError()
    wal = _stat_at(root_fd, DB_FILE_NAME + '-wal')
    shm = _stat_at(root_fd, DB_FILE_NAME + '-shm')
    if (wal is None) != (shm is None):
        raise ConversationStoreError()
    for name, info in ((DB_FILE_NAME + '-wal', wal), (DB_FILE_NAME + '-shm', shm)):
        if info is not None:
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise ConversationStoreError()
            probe, opened = _checked_regular_at(root_fd, name, mode=0o600)
            os.close(probe)
            if not _same_inode(info, opened):
                raise ConversationStoreError()

def _open_store_lock(root_fd: int, *, create: bool = False) -> tuple[int, bool]:
    created = False
    if create:
        try:
            fd = os.open(LOCK_FILE_NAME,
                         os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=root_fd)
            created = True
            os.fchmod(fd, 0o600)
        except FileExistsError:
            fd, _ = _checked_regular_at(root_fd, LOCK_FILE_NAME, mode=0o600)
            os.close(fd)
            fd = os.open(LOCK_FILE_NAME, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
        except OSError as exc:
            raise ConversationStoreError() from exc
    else:
        try:
            fd = os.open(LOCK_FILE_NAME, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
        except OSError as exc:
            raise ConversationStoreError() from exc
    try:
        path_info = os.stat(LOCK_FILE_NAME, dir_fd=root_fd, follow_symlinks=False)
        fd_info = os.fstat(fd)
        if (not stat.S_ISREG(path_info.st_mode) or path_info.st_uid != os.geteuid()
                or stat.S_IMODE(path_info.st_mode) != 0o600 or path_info.st_nlink != 1
                or not stat.S_ISREG(fd_info.st_mode) or fd_info.st_uid != os.geteuid()
                or stat.S_IMODE(fd_info.st_mode) != 0o600 or fd_info.st_nlink != 1
                or not _same_inode(path_info, fd_info)):
            raise ConversationStoreError()
        return fd, created
    except BaseException:
        os.close(fd)
        raise

def _marker_json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')

def _parse_marker_bytes(raw: bytes, *, root_instance_id: str | None = None,
                        expected_bot_id: str | None = None) -> dict:
    try:
        text = raw.decode('utf-8', errors='strict')
        data = json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, ValueError, RecursionError, TypeError) as exc:
        raise ConversationStoreError() from exc
    if not isinstance(data, dict) or _marker_json_bytes(data) != raw:
        raise ConversationStoreError()
    if expected_bot_id is None:
        if (set(data) != {'root_instance_id', 'state', 'version'}
                or type(data.get('version')) is not int or data.get('version') != 1):
            raise ConversationStoreError()
        _validate_uuid_marker(data.get('root_instance_id'))
        if data.get('state') not in ('pending', 'ready'):
            raise ConversationStoreError()
        if root_instance_id is not None and data['root_instance_id'] != root_instance_id:
            raise ConversationStoreError()
    else:
        if set(data) != {'attempt_id', 'bot_id', 'dm', 'root_instance_id', 'version'}:
            raise ConversationStoreError()
        if (type(data.get('version')) is not int or data.get('version') != 1
                or data.get('bot_id') != expected_bot_id):
            raise ConversationStoreError()
        if not isinstance(data.get('attempt_id'), str) or not _ATTEMPT_RE.fullmatch(data['attempt_id']):
            raise ConversationStoreError()
        if data.get('dm') not in ('pending', 'ready'):
            raise ConversationStoreError()
        _validate_uuid_marker(data.get('root_instance_id'))
        if root_instance_id is not None and data['root_instance_id'] != root_instance_id:
            raise ConversationStoreError()
    return data

def _validate_uuid_marker(value: object) -> None:
    try:
        _validate_uuid(value)
    except ValueError as exc:
        raise ConversationStoreError() from exc

def _read_marker_at(dir_fd: int, name: str, *, max_bytes: int,
                    root_instance_id: str | None = None,
                    expected_bot_id: str | None = None) -> dict:
    try:
        fd, _info, raw = _checked_regular_at(dir_fd, name, mode=0o600,
                                              max_bytes=max_bytes, read_data=True)
    except ConversationStoreError:
        raise
    try:
        return _parse_marker_bytes(raw, root_instance_id=root_instance_id,
                                   expected_bot_id=expected_bot_id)
    finally:
        os.close(fd)

def _publish_marker(dir_fd: int, name: str, temp_name: str, data: dict,
                    *, max_bytes: int) -> None:
    raw = _marker_json_bytes(data)
    if len(raw) > max_bytes:
        raise ConversationStoreError()
    try:
        fd = os.open(temp_name,
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=dir_fd)
        os.fchmod(fd, 0o600)
    except OSError as exc:
        raise ConversationStoreError() from exc
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(errno.EIO, 'short marker write')
            view = view[written:]
        os.fsync(fd)
    except OSError as exc:
        raise ConversationStoreError() from exc
    finally:
        os.close(fd)
    try:
        os.replace(temp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        _fsync_dir(dir_fd)
    except OSError as exc:
        raise ConversationStoreError() from exc

def _marker_filename(bot_id: str) -> str:
    ConversationRef.dm(bot_id)
    return bot_id + '.json'

def _read_dm_marker(marker_fd: int, bot_id: str, root_instance_id: str) -> dict:
    ref = ConversationRef.dm(bot_id)
    temp = f'.{bot_id}.json.tmp'
    if _stat_at(marker_fd, temp) is not None:
        raise _local_error(ref)
    try:
        return _read_marker_at(marker_fd, _marker_filename(bot_id), max_bytes=DM_MARKER_MAX_BYTES,
                               root_instance_id=root_instance_id, expected_bot_id=bot_id)
    except ConversationStoreError as exc:
        raise _local_error(ref) from exc

def _validate_root_artifacts(root_fd: int) -> tuple[str, int]:
    _validate_top_namespace(root_fd)
    if _stat_at(root_fd, ROOT_MARKER_TEMP) is not None:
        raise ConversationStoreError()
    marker = _read_marker_at(root_fd, ROOT_MARKER_NAME, max_bytes=ROOT_MARKER_MAX_BYTES)
    if marker['state'] != 'ready':
        raise ConversationStoreError()
    marker_fd, _ = _open_checked_dir_at(root_fd, DM_MARKERS_NAME)
    return marker['root_instance_id'], marker_fd

def _reserved_root_entries(root_fd: int) -> set[str]:
    return {entry.name for entry in os.scandir(root_fd)
            if entry.name.startswith('conversations.') or entry.name in (LOCK_FILE_NAME, DM_MARKERS_NAME)}

def _create_marker_directory(root_fd: int) -> int:
    try:
        os.mkdir(DM_MARKERS_NAME, 0o700, dir_fd=root_fd)
        marker_fd, _ = _open_checked_dir_at(root_fd, DM_MARKERS_NAME)
        os.fchmod(marker_fd, 0o700)
        _fsync_dir(root_fd)
        return marker_fd
    except OSError as exc:
        raise ConversationStoreError() from exc

