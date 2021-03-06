#!/usr/bin/python3
# -*- coding: utf-8; tab-width: 4; indent-tabs-mode: t -*-

import os
import time
import fcntl
import socket
import struct
import logging
import netifaces
import threading
import subprocess
from gi.repository import GLib


def get_plugin_list():
    return [
        "generic-dhcp",
    ]


def get_plugin(name):
    if name == "generic-dhcp":
        return _PluginObject()
    else:
        assert False


class _PluginObject:

    def init2(self, cfg, tmpDir, ownResolvConf, upCallback, downCallback):
        self.cfg = cfg
        self.tmpDir = tmpDir
        self.ownResolvConf = ownResolvConf
        self.upCallback = upCallback
        self.downCallback = downCallback
        self.logger = logging.getLogger(self.__module__ + "." + self.__class__.__name__)

        self.proc = None
        self.waitIpThread = None
        self.bAlive = False

    def get_interface(self):
        return self.cfg["interface"]

    def start(self):
        pass

    def stop(self):
        if self.waitIpThread is not None:
            self.waitIpThread.stop()
            self.waitIpThread.join()
            self.waitIpThread = None
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait()
            self.proc = None
            _Util.setInterfaceUpDown(self.cfg["interface"], False)
        if self.bAlive:
            self.downCallback()
            self.bAlive = False
            self.logger.info("Interface \"%s\" unmanaged." % (self.cfg["interface"]))

    def is_connected(self):
        return self.bAlive

    def get_ip(self):
        assert self.bAlive
        return netifaces.ifaddresses(self.cfg["interface"])[netifaces.AF_INET][0]["addr"]

    def get_netmask(self):
        assert self.bAlive
        return netifaces.ifaddresses(self.cfg["interface"])[netifaces.AF_INET][0]["netmask"]

    def get_extra_prefix_list(self):
        assert self.bAlive
        return []

    def get_business_attributes(self):
        assert self.bAlive
        return dict()

    def interface_appear(self, ifname):
        if ifname != self.cfg["interface"]:
            return False

        assert self.proc is None
        _Util.setInterfaceUpDown(self.cfg["interface"], True)

        # create dhclient.conf, copied from nm-dhcp-dhclient-utils.c in networkmanager-1.4.4
        cfgf = os.path.join(self.tmpDir, "dhclient.conf")
        with open(cfgf, "w") as f:
            buf = ""
            buf += "send host-name \"%s\";\n" % (socket.gethostname())
            buf += "\n"
            buf += "option rfc3442-classless-static-routes code 121 = array of unsigned integer 8;\n"
            buf += "option wpad code 252 = string;\n"
            buf += "\n"
            buf += "also request rfc3442-classless-static-routes;\n"
            buf += "also request static-routes;\n"
            buf += "also request wpad;\n"
            buf += "also request ntp-servers;\n"
            f.write(buf)

        # create dhclient process
        self.proc = subprocess.Popen([
            "/usr/bin/python3",
            os.path.join(os.path.dirname(os.path.realpath(__file__)), "subproc_dhclient.py"),
            self.tmpDir,
            cfgf,
            self.cfg["interface"],
            self.ownResolvConf,
        ])

        # start wait ip thread
        self.waitIpThread = _WaitIpThread(self)
        self.waitIpThread.start()

        return True

    def interface_disappear(self, ifname):
        assert ifname == self.cfg["interface"]
        if self.waitIpThread is not None:
            self.waitIpThread.stop()
            self.waitIpThread.join()
            self.waitIpThread = None
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait()
            self.proc = None
        if self.bAlive:
            self.downCallback()
            self.bAlive = False
            self.logger.info("Interface \"%s\" unmanaged." % (self.cfg["interface"]))

    def _upCallback(self):
        self.bAlive = True
        self.logger.info("Interface \"%s\" managed." % (self.cfg["interface"]))
        try:
            self.upCallback()
        except:
            assert False               # fixme, what to do?


class _WaitIpThread(threading.Thread):

    def __init__(self, pObj):
        threading.Thread.__init__(self)
        self.pObj = pObj
        self.bStop = False

    def run(self):
        count = 0
        while not self.bStop:
            if netifaces.AF_INET in netifaces.ifaddresses(self.pObj.cfg["interface"]):
                count += 1
            else:
                count = 0
            if count >= 3:
                _Util.idleInvoke(self.pObj._upCallback)      # ip address must be stablized for 3 seconds
                break
            time.sleep(1.0)
        self.waitIpThread = None

    def stop(self):
        self.bStop = True


class _Util:

    @staticmethod
    def idleInvoke(func, *args):
        def _idleCallback(func, *args):
            func(*args)
            return False
        GLib.idle_add(_idleCallback, func, *args)

    @staticmethod
    def setInterfaceUpDown(ifname, upOrDown):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            ifreq = struct.pack("16sh", ifname.encode("ascii"), 0)
            ret = fcntl.ioctl(s.fileno(), 0x8913, ifreq)
            flags = struct.unpack("16sh", ret)[1]                   # SIOCGIFFLAGS

            if upOrDown:
                flags |= 0x1
            else:
                flags &= ~0x1

            ifreq = struct.pack("16sh", ifname.encode("ascii"), flags)
            fcntl.ioctl(s.fileno(), 0x8914, ifreq)                  # SIOCSIFFLAGS
        finally:
            s.close()
