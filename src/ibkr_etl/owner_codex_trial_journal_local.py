"""Temporary, create-only LOCAL rehearsal backend, never a live adapter.

An explicitly reviewed, already-created OS temporary directory AND its registered
temporary parent are required. The trusted caller supplies both: no environment,
tempfile probing, directory discovery or implicit creation occurs here. This is
not independent proof of the caller's root registration. Keys belong to one fixed lifecycle namespace,
target scope and bounded numeric slots; callers cannot supply a filename.

The complete bounded record is written/fsynced in the SAME directory, then an
atomic no-replace hard link publishes it. A failed/lost publication response may
leave a COMPLETE consumed slot: callers must not retry effects. Readers never
read pending files. POSIX directory-fsync follows publication/temporary removal.
Windows holds no-delete directory handles and opens final files without reparse
following. Its file fsync + atomic link gives process-visible/restart semantics,
NOT a portable power-loss persistence guarantee. Filesystems without these
primitives fail closed. No fallback to copy, truncate, overwrite or unsafe rename.

This is not immutable storage, an authorization boundary, protected evidence or
a durable Azure deployment. The trusted operator owns the temporary directory;
host administrators can alter it. supports_live_execution is ALWAYS False.
No network, SQL, Azure, credentials, models, production actions or cleanup API.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import uuid

from .owner_codex_trial_journal import JOURNAL_NAMESPACE, MAX_RECORD_BYTES, MAX_SLOTS
from .owner_codex_trial_lifecycle import TrialTarget


ROOT_PREFIX = "owner-codex-trial-journal-"
ERROR_CODES = frozenset({"local_root_invalid", "local_root_changed", "local_backend_unavailable",
    "local_record_invalid", "journal_key_invalid", "journal_record_invalid"})


class LocalJournalError(ValueError):
    """Fixed codes only; never retain filesystem/provider exception text."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in ERROR_CODES else "local_backend_unavailable"
        super().__init__(self.code)


def _require(value, code):
    if not value:
        raise LocalJournalError(code)


def _regular(metadata):
    return (stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
            and not getattr(metadata, "st_file_attributes", 0) & 0x400)


def _windows_api():
    # These APIs are opened lazily only for a requested filesystem operation.
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    api.GetDriveTypeW.argtypes = (wintypes.LPCWSTR,)
    api.GetDriveTypeW.restype = wintypes.UINT
    return api, ctypes.c_void_p(-1).value


class LocalTrialJournalBackend:
    @property
    def supports_live_execution(self):
        return False

    @property
    def durability_scope(self):
        return "local_process_restart_only"

    def __init__(self, reviewed_root, *, reviewed_parent):
        _require(type(reviewed_root) is str or isinstance(reviewed_root, Path), "local_root_invalid")
        _require(type(reviewed_parent) is str or isinstance(reviewed_parent, Path), "local_root_invalid")
        root = Path(reviewed_root)
        parent = Path(reviewed_parent)
        _require(root.is_absolute() and parent.is_absolute() and ".." not in root.parts
            and ".." not in parent.parts and root.parent == parent
            and re.fullmatch(re.escape(ROOT_PREFIX) + r"[a-z0-9_]{8,64}", root.name) is not None,
            "local_root_invalid")
        self._root = root
        self._identity = None
        try:
            metadata = self._validate_root()
        except OSError:
            raise LocalJournalError("local_root_invalid") from None
        self._identity = (metadata.st_dev, metadata.st_ino)
        _require(type(metadata.st_ino) is int and metadata.st_ino > 0, "local_root_invalid")

    def _validate_root(self):
        if os.name == "nt":
            # Refuse UNC/mapped-network paths BEFORE lstat or any path access.
            _require(re.fullmatch(r"[A-Za-z]:", self._root.drive) is not None,
                     "local_root_invalid")
            api, _ = _windows_api()
            _require(api.GetDriveTypeW(self._root.anchor) == 3, "local_backend_unavailable")
        for path in (*reversed(self._root.parents), self._root):
            metadata = path.lstat()
            _require(stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
                and not getattr(metadata, "st_file_attributes", 0) & 0x400, "local_root_invalid")
        metadata = self._root.lstat()
        if self._identity is not None:
            _require((metadata.st_dev, metadata.st_ino) == self._identity, "local_root_changed")
        if os.name == "posix":
            _require(metadata.st_uid == os.geteuid() and metadata.st_mode & 0o077 == 0,
                     "local_root_invalid")
        return metadata

    @staticmethod
    def _slot(key):
        target = TrialTarget()
        target.__post_init__()
        prefix = JOURNAL_NAMESPACE + "/" + target.scope_sha256 + "/"
        _require(type(key) is str and key.startswith(prefix), "journal_key_invalid")
        slot = key[len(prefix):]
        _require(re.fullmatch(r"[0-9]{2}", slot) is not None
                 and 1 <= int(slot) <= MAX_SLOTS, "journal_key_invalid")
        return slot

    @contextmanager
    def _directory(self):
        """Bind all path operations to the reviewed directory identity."""
        self._validate_root()
        fd, handles, api = None, [], None
        try:
            if os.name == "posix":
                _require(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"), "local_backend_unavailable")
                fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                metadata = os.fstat(fd)
                _require((metadata.st_dev, metadata.st_ino) == self._identity, "local_root_changed")
            elif os.name == "nt":
                api, invalid = _windows_api()
                # FILE_SHARE_READ|WRITE, deliberately NOT FILE_SHARE_DELETE.
                # Hold the registered temporary parent and child. Higher system
                # ancestors are checked, not opened with write-sharing locks:
                # ordinary Windows users may not open their profile ancestor.
                # Host-admin path/ACL mutation is outside this local rehearsal.
                for path in (self._root.parent, self._root):
                    handle = api.CreateFileW(str(path), 0, 3, None, 3, 0x02000000 | 0x00200000, None)
                    _require(handle not in (None, invalid), "local_backend_unavailable")
                    handles.append(handle)
            else:
                raise LocalJournalError("local_backend_unavailable")
            self._validate_root()
            yield fd
            self._validate_root()
        finally:
            if fd is not None:
                os.close(fd)
            if api is not None:
                for handle in reversed(handles):
                    api.CloseHandle(handle)

    def _open_read(self, slot, directory):
        if os.name == "posix":
            # Reject a FIFO/device after fstat without blocking while opening it.
            _require(hasattr(os, "O_NONBLOCK"), "local_backend_unavailable")
            return os.open(slot, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        import msvcrt
        import ctypes
        api, invalid = _windows_api()
        # Share deletion of the pending hard-link name during publication, but
        # NOT writes to this inode. The open handle remains bound if a trusted
        # operator removes a directory entry; this is not immutable storage.
        handle = api.CreateFileW(str(self._root / slot), 0x80000000, 1 | 4, None, 3, 0x00200000, None)
        if handle in (None, invalid):
            if ctypes.get_last_error() in (2, 3):
                raise FileNotFoundError()
            raise LocalJournalError("local_record_invalid")
        try:
            return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except Exception:
            api.CloseHandle(handle)
            raise

    def _read_slot(self, slot, directory):
        try:
            fd = self._open_read(slot, directory)
        except FileNotFoundError:
            return None
        try:
            metadata = os.fstat(fd)
            _require(_regular(metadata) and metadata.st_dev == self._identity[0]
                and 0 < metadata.st_size <= MAX_RECORD_BYTES, "local_record_invalid")
            chunks, remaining = [], metadata.st_size + 1
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            after = os.fstat(fd)
            # Access-time and temporary hard-link count changes are harmless.
            stable = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns)
            _require(len(payload) == metadata.st_size and stable(after) == stable(metadata), "local_record_invalid")
            return payload
        finally:
            os.close(fd)

    def read(self, key):
        slot = self._slot(key)
        try:
            with self._directory() as directory:
                return self._read_slot(slot, directory)
        except LocalJournalError:
            raise
        except OSError:
            raise LocalJournalError("local_backend_unavailable") from None

    def create_if_absent(self, key, value):
        slot = self._slot(key)
        _require(type(value) is bytes and 0 < len(value) <= MAX_RECORD_BYTES, "journal_record_invalid")
        pending = ".pending-" + uuid.uuid4().hex
        try:
            with self._directory() as directory:
                return self._create(slot, pending, value, directory)
        except LocalJournalError:
            raise
        except OSError:
            raise LocalJournalError("local_backend_unavailable") from None

    def _create(self, slot, pending, value, directory):
        fd, created = None, False
        try:
            if directory is None:
                fd = os.open(self._root / pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
            else:
                fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
            created = True
            remaining = memoryview(value)
            while remaining:
                count = os.write(fd, remaining)
                _require(type(count) is int and count > 0, "local_backend_unavailable")
                remaining = remaining[count:]
            os.fsync(fd)
            metadata = os.fstat(fd)
            _require(_regular(metadata) and metadata.st_size == len(value), "local_record_invalid")
            # Windows' final no-write reader must not conflict with
            # our own CRT write handle to this same inode under its pending name.
            if directory is None:
                os.close(fd)
                fd = None
            try:
                if directory is None:
                    os.link(self._root / pending, self._root / slot, follow_symlinks=False)
                else:
                    os.link(pending, slot, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            except FileExistsError:
                _require(self._read_slot(slot, directory) is not None, "local_record_invalid")
                return False
            # Validate the exact published inode/content, not only link success.
            _require(self._read_slot(slot, directory) == value, "local_record_invalid")
            return True
        finally:
            if fd is not None:
                os.close(fd)
            if created:
                if directory is None:
                    os.unlink(self._root / pending)
                else:
                    os.unlink(pending, dir_fd=directory)
            if directory is not None:
                os.fsync(directory)
