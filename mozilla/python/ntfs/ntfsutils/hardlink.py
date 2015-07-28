# Copyright (c) 2012, the Mozilla Foundation. All rights reserved.
# Use of this source code is governed by the Simplified BSD License which can
# be found in the LICENSE file.

# Library to deal with hardlinks

__all__ = ["create", "samefile"]

import fs
import ctypes
import os.path
from ctypes import POINTER, WinError, sizeof, byref
from ctypes.wintypes import DWORD, HANDLE, BOOL

CreateHardLink = ctypes.windll.kernel32.CreateHardLinkW
CreateHardLink.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p]
CreateHardLink.restype = BOOL

def create(source, link_name):
    """
    Creates a hardlink at link_name referring to the same file as source.
    """
    res = CreateHardLink(link_name, source, None)
    if res == 0:
        raise fs.win_error(ctypes.GetLastError(), source)

def samefile(path1, path2):
    """
    Returns True if path1 and path2 refer to the same file.
    """
    # Check if both are on the same volume and have the same file ID
    info1 = fs.getfileinfo(path1)
    info2 = fs.getfileinfo(path2)
    return (info1.dwVolumeSerialNumber == info2.dwVolumeSerialNumber and
            info1.nFileIndexHigh == info2.nFileIndexHigh and
            info1.nFileIndexLow == info2.nFileIndexLow)

def is_hardlink(path):
    info = fs.getfileinfo(path)
    return info.nNumberOfLinks > 0

def readlink(path):
    links = fs.get_all_hardlinkds(path)
    if (len(links) > 0):
        link = links.pop()
        return link
    return None
