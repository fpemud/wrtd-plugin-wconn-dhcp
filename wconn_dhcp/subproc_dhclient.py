#!/usr/bin/python3
# -*- coding: utf-8; tab-width: 4; indent-tabs-mode: t -*-

import os
import sys
import shutil
import ctypes
import errno
import subprocess


class _UtilNewMountNamespace:

    _CLONE_NEWNS = 0x00020000               # <linux/sched.h>
    _MS_REC = 16384                         # <sys/mount.h>
    _MS_PRIVATE = 1 << 18                   # <sys/mount.h>
    _libc = None
    _mount = None
    _setns = None
    _unshare = None

    def __init__(self):
        if self._libc is None:
            self._libc = ctypes.CDLL('libc.so.6', use_errno=True)
            self._mount = self._libc.mount
            self._mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
            self._mount.restype = ctypes.c_int
            self._setns = self._libc.setns
            self._unshare = self._libc.unshare

        self.parentfd = None

    def __enter__(self):
        self.parentfd = open("/proc/%d/ns/mnt" % (os.getpid()), 'r')

        # copied from unshare.c of util-linux
        try:
            if self._unshare(self._CLONE_NEWNS) != 0:
                e = ctypes.get_errno()
                raise OSError(e, errno.errorcode[e])

            srcdir = ctypes.c_char_p("none".encode("utf_8"))
            target = ctypes.c_char_p("/".encode("utf_8"))
            if self._mount(srcdir, target, None, (self._MS_REC | self._MS_PRIVATE), None) != 0:
                e = ctypes.get_errno()
                raise OSError(e, errno.errorcode[e])
        except BaseException:
            self.parentfd.close()
            self.parentfd = None
            raise

    def __exit__(self, *_):
        self._setns(self.parentfd.fileno(), 0)
        self.parentfd.close()
        self.parentfd = None


assert len(sys.argv) == 5
tmpDir = sys.argv[1]
cfgf = sys.argv[2]
intfName = sys.argv[3]
ownResolvConf = sys.argv[4]

selfDir = os.path.dirname(os.path.realpath(__file__))
tmpEtcDhcpDir = os.path.join(tmpDir, "etc-dhcp")
tmpEnterHook = os.path.join(tmpEtcDhcpDir, "dhclient-enter-hooks")
tmpExitHook = os.path.join(tmpEtcDhcpDir, "dhclient-exit-hooks")
proc = None

try:
    os.mkdir(tmpEtcDhcpDir)

    buf = ""
    with open(os.path.join(selfDir, "dhclient-enter-hooks"), "r") as f:
        buf = f.read()
    with open(os.path.join(tmpEnterHook), "w") as f:
        buf = buf.replace("%%RESOLV_CONF%%", ownResolvConf)
        f.write(buf)
    os.chmod(tmpEnterHook, 0o755)

    shutil.copy(os.path.join(selfDir, "dhclient-exit-hooks"), tmpExitHook)
    os.chmod(tmpExitHook, 0o755)

    with _UtilNewMountNamespace():
        # dhclient read custom scripts from the fixed location /etc/dhcp
        # this behavior sucks so we use mount namespace to workaround it
        subprocess.check_call(["/bin/mount", "--bind", tmpEtcDhcpDir, "/etc/dhcp"])

        cmd = "/sbin/dhclient "
        cmd += "-d --no-pid "
        cmd += "-cf %s " % (cfgf)
        cmd += "-lf %s " % (os.path.join(tmpDir, "dhclient.leases"))
        cmd += "%s >/dev/null 2>&1" % (intfName)

        proc = subprocess.Popen(cmd, shell=True, universal_newlines=True)
        proc.wait()
except KeyboardInterrupt:
    if proc is not None and not proc.poll():
        proc.terminate()
        proc.wait()
finally:
    shutil.rmtree(tmpEtcDhcpDir)
