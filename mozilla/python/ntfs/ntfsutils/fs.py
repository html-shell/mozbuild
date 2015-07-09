# Copyright (c) 2012, the Mozilla Foundation. All rights reserved.
# Use of this source code is governed by the Simplified BSD License which can
# be found in the LICENSE file.

import os
import ctypes
import stat
from ctypes import POINTER, WinError, sizeof, byref
from ctypes import wintypes
from ctypes.wintypes import DWORD, HANDLE, BOOL
kernel32 = ctypes.windll.kernel32

LPDWORD = POINTER(DWORD)

GENERIC_READ  = 0x80000000
GENERIC_WRITE = 0x40000000

FILE_SHARE_READ   = 0x00000001
FILE_SHARE_WRITE  = 0x00000002
FILE_SHARE_DELETE = 0x00000004

FILE_SUPPORTS_HARD_LINKS     = 0x00400000
FILE_SUPPORTS_REPARSE_POINTS = 0x00000080

FILE_ATTRIBUTE_READONLY      = 0x00000001
FILE_ATTRIBUTE_DIRECTORY     = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400

FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS   = 0x02000000

# Various constants from windows.h
ERROR_FILE_NOT_FOUND = 2
ERROR_NO_MORE_FILES = 18


# Numer of seconds between 1601-01-01 and 1970-01-01
SECONDS_BETWEEN_EPOCHS = 11644473600

OPEN_EXISTING = 3

MAX_PATH = 260

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", DWORD),
                ("dwHighDateTime", DWORD)]

class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [("dwFileAttributes", DWORD),
                ("ftCreationTime", FILETIME),
                ("ftLastAccessTime", FILETIME),
                ("ftLastWriteTime", FILETIME),
                ("dwVolumeSerialNumber", DWORD),
                ("nFileSizeHigh", DWORD),
                ("nFileSizeLow", DWORD),
                ("nNumberOfLinks", DWORD),
                ("nFileIndexHigh", DWORD),
                ("nFileIndexLow", DWORD)]

# http://msdn.microsoft.com/en-us/library/windows/desktop/aa363858
CreateFile = ctypes.windll.kernel32.CreateFileW
CreateFile.argtypes = [ctypes.c_wchar_p, DWORD, DWORD, ctypes.c_void_p,
                       DWORD, DWORD, HANDLE]
CreateFile.restype = HANDLE

# http://msdn.microsoft.com/en-us/library/windows/desktop/aa364944
GetFileAttributes = ctypes.windll.kernel32.GetFileAttributesW
GetFileAttributes.argtypes = [ctypes.c_wchar_p]
GetFileAttributes.restype = DWORD

# http://msdn.microsoft.com/en-us/library/windows/desktop/aa364952
GetFileInformationByHandle = ctypes.windll.kernel32.GetFileInformationByHandle
GetFileInformationByHandle.argtypes = [HANDLE, POINTER(BY_HANDLE_FILE_INFORMATION)]
GetFileInformationByHandle.restype = BOOL

# http://msdn.microsoft.com/en-us/library/windows/desktop/aa364996
GetVolumePathName = ctypes.windll.kernel32.GetVolumePathNameW
GetVolumePathName.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD]
GetVolumePathName.restype = BOOL

# http://msdn.microsoft.com/en-us/library/windows/desktop/aa364993
GetVolumeInformation = ctypes.windll.kernel32.GetVolumeInformationW
GetVolumeInformation.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD,
                                 LPDWORD, LPDWORD, LPDWORD, ctypes.c_wchar_p,
                                 DWORD]
GetVolumeInformation.restype = BOOL

# http://msdn.microsoft.com/en-us/library/windows/desktop/aa363216
DeviceIoControl = ctypes.windll.kernel32.DeviceIoControl
DeviceIoControl.argtypes = [HANDLE, DWORD, ctypes.c_void_p, DWORD,
                            ctypes.c_void_p, DWORD, LPDWORD, ctypes.c_void_p]
DeviceIoControl.restype = BOOL

# http://msdn.microsoft.com/en-us/library/windows/desktop/ms724211
CloseHandle = ctypes.windll.kernel32.CloseHandle
CloseHandle.argtypes = [HANDLE]
CloseHandle.restype = BOOL

FindFirstFileNameW = ctypes.windll.kernel32.FindFirstFileNameW
FindFirstFileNameW.argtypes = [
    ctypes.c_wchar_p, DWORD, LPDWORD, ctypes.c_wchar_p
]
FindFirstFileNameW.restype = HANDLE

FindNextFileNameW = ctypes.windll.kernel32.FindNextFileNameW
FindNextFileNameW.argtypes = [HANDLE, LPDWORD, ctypes.c_wchar_p]
FindNextFileNameW.restype = BOOL

FindClose = ctypes.windll.kernel32.FindClose
FindClose.argtypes = [HANDLE]
FindClose.restype = BOOL

def getfileinfo(path):
    """
    Return information for the file at the given path. This is going to be a
    struct of type BY_HANDLE_FILE_INFORMATION.
    """
    flags = FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS
    hfile = CreateFile(path, GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, flags, None)
    if hfile is None:
        raise win_error(ctypes.GetLastError(), path)
    info = BY_HANDLE_FILE_INFORMATION()
    rv = GetFileInformationByHandle(hfile, info)
    CloseHandle(hfile)
    if rv == 0:
        raise win_error(ctypes.GetLastError(), path)
    return info

def getvolumepath(path):
    # Add 1 for a trailing backslash if necessary, and 1 for the terminating
    # null character.
    volpath = ctypes.create_unicode_buffer(len(path) + 2)
    rv = GetVolumePathName(path, volpath, len(volpath))
    if rv == 0:
        raise win_error(ctypes.GetLastError(), path)
    return volpath.value

def getvolumeinfo(path):
    """
    Return information for the volume containing the given path. This is going
    to be a pair containing (file system, file system flags).
    """

    volpath = getvolumepath(path)

    fsnamebuf = ctypes.create_unicode_buffer(MAX_PATH + 1)
    fsflags = DWORD(0)
    rv = GetVolumeInformation(volpath, None, 0, None, None, byref(fsflags),
                              fsnamebuf, len(fsnamebuf))
    if rv == 0:
        raise win_error(ctypes.GetLastError(), path)

    return (fsnamebuf.value, fsflags.value)

def get_all_hardlinkds(path):
    all_links = set()
    volume_root = getvolumepath(path)
    link_name = ctypes.create_unicode_buffer(65536)
    link_name_len = DWORD(len(link_name))

    hFind = FindFirstFileNameW(path, 0, byref(link_name_len), link_name)
    if hFind == INVALID_HANDLE_VALUE:
        return all_links
    while True:
        newLink = os.path.join(volume_root, str(link_name.value))
        all_links.add(newLink)
        if FindNextFileNameW(hFind, byref(link_name_len), link_name) != True:
            break
    FindClose(hFind)
    all_links.discard(path)
    return all_links

def hardlinks_supported(path):
    (fsname, fsflags) = getvolumeinfo(path)
    # FILE_SUPPORTS_HARD_LINKS isn't supported until Windows 7, so also check
    # whether the file system is NTFS
    return bool((fsflags & FILE_SUPPORTS_HARD_LINKS) or (fsname == "NTFS"))

def junctions_supported(path):
    (fsname, fsflags) = getvolumeinfo(path)
    return bool(fsflags & FILE_SUPPORTS_REPARSE_POINTS)

def win_error(error, filename):
    exc = WindowsError(error, ctypes.FormatError(error))
    exc.filename = filename
    return exc


def attributes_to_mode(data, attributes):
    """Convert Win32 dwFileAttributes to st_mode."""
    mode = 0
    if attributes & FILE_ATTRIBUTE_DIRECTORY:
        mode |= stat.S_IFDIR | 0o111
    else:
        mode |= stat.S_IFREG
    if attributes & FILE_ATTRIBUTE_READONLY:
        mode |= 0o444
    else:
        mode |= 0o666
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT or \
        data.nNumberOfLinks > 0:
        mode |= stat.S_IFLNK
    return mode

def filetime_to_time(filetime):
    """Convert Win32 FILETIME to time since Unix epoch in seconds."""
    total = filetime.dwHighDateTime << 32 | filetime.dwLowDateTime
    return total / 10000000.0 - SECONDS_BETWEEN_EPOCHS

def find_data_to_stat(data):
    """Convert Win32 FIND_DATA struct to stat_result."""
    st_mode = attributes_to_mode(data, data.dwFileAttributes)
    st_size = data.nFileSizeHigh << 32 | data.nFileSizeLow
    st_atime = filetime_to_time(data.ftLastAccessTime)
    st_mtime = filetime_to_time(data.ftLastWriteTime)
    st_ctime = filetime_to_time(data.ftCreationTime)
    # These are set to zero rather than None, per CPython's posixmodule.c
    st_ino = 0
    st_dev = 0
    st_nlink = 0
    st_uid = 0
    st_gid = 0
    return os.stat_result((st_mode, st_ino, st_dev, st_nlink, st_uid,
                           st_gid, st_size, st_atime, st_mtime, st_ctime))

def lstat(path):
    data = getfileinfo(path)
    return find_data_to_stat(data)