#! /usr/bin/python3

import asyncio
import atexit
from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.service import (ServiceInterface, method)
import logging
import logging.handlers
import re
import time


class NetworkManager(object):
    # https://networkmanager.dev/docs/api/latest/spec.html
    def __init__(self, nm):
        self.nm = nm
        self.cb = None
        self.nm.on_state_changed(self.state_changed)

    @classmethod
    async def create(cls):
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        introspection = await bus.introspect('org.freedesktop.NetworkManager',
                                             '/org/freedesktop/NetworkManager')
        proxy = bus.get_proxy_object('org.freedesktop.NetworkManager',
                                     '/org/freedesktop/NetworkManager',
                                     introspection)
        iface = proxy.get_interface('org.freedesktop.NetworkManager')

        return NetworkManager(iface)

    def register_callback(self, cb):
        self.cb = cb

    async def check_network(self):
        try:
            conns = await self.nm.get_active_connections()
            for conn in conns:
                introspection = await self.nm.bus.introspect('org.freedesktop.NetworkManager', conn)
                proxy = self.nm.bus.get_proxy_object('org.freedesktop.NetworkManager', conn,
                                                     introspection)
                iface = proxy.get_interface('org.freedesktop.NetworkManager.Connection.Active')
                default = await iface.get_default()
                if not default:
                    default = await iface.get_default6()
                if not default: continue
                return await iface.get_id()
        except Exception as e:
            logging.warning(f"failed to get active conenctions: {str(e)}")
            return

    def state_changed(self, state):
        # https://networkmanager.dev/docs/api/latest/nm-dbus-types.html#NMState
        if not self.cb:
            return
        if state in (60, 70):
            task = asyncio.get_event_loop().create_task(self.check_network())
            task.add_done_callback(lambda x: self.cb(x.result()))
        else:
            self.cb(None)


class ScreenSaver(object):
    def __init__(self, ss):
        self.ss = ss
        self.cb = None
        self.ss.on_active_changed(self.active_changed)

    @classmethod
    async def create(cls):
        bus = await MessageBus().connect()
        introspection = await bus.introspect('org.kde.screensaver', '/org/freedesktop/ScreenSaver')
        proxy = bus.get_proxy_object('org.kde.screensaver', '/ScreenSaver', introspection)
        iface = proxy.get_interface('org.freedesktop.ScreenSaver')

        return ScreenSaver(iface)

    def register_callback(self, cb):
        self.cb = cb

    async def check_idletime(self):
        try:
            return await self.ss.call_get_session_idle_time()
        except Exception as e:
            logging.warning(f"failed to get idle time: {str(e)}")
            return

    def active_changed(self, on):
        if self.cb:
            self.cb(on)


class FocusTracker(object):
    def __init__(self, screensaver, networkmanager):
        self.ss = screensaver
        self.nm = networkmanager
        self.window = None
        self.wstart = 0
        self.start = time.time()
        self.total = 0
        self.last = time.time()
        asyncio.get_event_loop().call_soon(self.nap_check)
        self.ss.register_callback(self.screensaver)
        self.nm.register_callback(self.network)
        task = asyncio.get_event_loop().create_task(self.nm.check_network())
        task.add_done_callback(lambda x: self.network(x.result()))

    def focus(self, window):
        if window == self.window:
            return
        duration = time.time() - self.wstart
        if self.window:
            if duration >= 2:
                logging.info(f"focus: {duration:.0f} {self.window}")
            else:
                logging.debug(f"focus: {duration:.0f} {self.window}")
        self.window = window
        self.wstart = time.time()

    def idle(self, idle):
        if idle:
            self.total -= int(idle / 1000)
            logging.info(f"idle for {idle/1000:.0f}s, total={self.total:.0f}")

    def screensaver(self, on):
        if on:
            self.focus(None)
            self.total += time.time() - self.start
            task = asyncio.get_event_loop().create_task(self.ss.check_idletime())
            task.add_done_callback(lambda x: self.idle(x.result()))
        else:
            self.start = time.time()
        logging.info(f"screensaver={on} total={self.total:.0f}")

    def network(self, network):
        if self.network != network:
            self.network = network
            logging.info(f"network: {network}")

    def nap_check(self):
        elapsed = time.time() - self.last
        if elapsed > 1:
            logging.info(f"took a {elapsed:.0f}s nap")
        if time.strftime('%Y%m%d', time.localtime(self.last)) != time.strftime('%Y%m%d', time.localtime(time.time())):
            logging.info(f"new day, resetting total time")
            self.total = 0
        self.last = time.time()
        asyncio.get_event_loop().call_later(0.5, self.nap_check)


class KwinBridge(ServiceInterface):
    def __init__(self, tracker):
        super().__init__('com.github.syrkuit.ktt')
        self.tracker = tracker

    @method()
    def Log(self, msg: 's'):
        logging.info(msg)

    @method()
    def ScreenConfiguration(self, num: 'i', width: 'i', height: 'i'):
        logging.info(f"screen configuration: {num} {width}x{height}");

    @method()
    def Focus(self, desktop: 'i', window: 's'):
        shortname = re.split(r' [^\w\s] ', window)[-1]
        if shortname.endswith('>‎'):  # Konsole <2>
            window = shortname.rsplit(' ', 1)[0]
        elif shortname != 'Google Chrome':
            window = shortname
        else:
            m = re.match(r'.+ - (\S+@\S+ - (?:\S+ Mail|Gmail) - Google Chrome)$', window)
            if m:
                window = m.group(1)
        self.tracker.focus(f"{desktop} {window}")

    @method()
    def FocusLost(self):
        if False:  # there's noise that isn't easy to filter out here
            self.tracker.focus(None)


async def main():
    # initialize tracker
    nm = await NetworkManager.create()
    ss = await ScreenSaver.create()
    tracker = FocusTracker(ss, nm)

    # set up KTT service
    interface = KwinBridge(tracker)
    bus = await MessageBus().connect()
    bus.export('/KTT', interface)
    await bus.request_name('com.github.syrkuit.ktt')
    await bus.wait_for_disconnect()


class DatedFileHandler(logging.FileHandler):
    def __init__(self, filename, **kwargs):
        kwargs['delay'] = True
        logging.FileHandler.__init__(self, filename, **kwargs)
        self.prefixFilename = self.baseFilename

    def _open(self):
        self.baseFilename = self.prefixFilename + '_' + time.strftime('%Y-%m-%d', time.localtime())
        return logging.FileHandler._open(self)

    def emit(self, record):
        if self.stream and self.baseFilename != self.prefixFilename + '_' + time.strftime('%Y-%m-%d', time.localtime()):
            self.stream.close()
            self.stream = self._open()
        logging.FileHandler.emit(self, record)


if __name__ == '__main__':
    from optparse import OptionParser
    import os

    op = OptionParser(usage='%prog [ <options> ]')
    op.add_option('-l', dest='log_path', action='store',
                  default=os.path.join(os.getenv('HOME'), '.ktt'),
                  help='Log path prefix')
    op.add_option('-v', dest='verbose', action='store_true', default=False,
                  help='More verbose logging')
    options, args = op.parse_args()

    logfmt = logging.Formatter(fmt='%(levelname).1s %(asctime).19s %(message)s',
                               datefmt='%Y-%m-%d %H:%M:%S')
    loghdlr = DatedFileHandler(options.log_path)
    loghdlr.setFormatter(logfmt)
    logger = logging.getLogger()
    logger.addHandler(loghdlr)
    logger.setLevel(logging.DEBUG if options.verbose else logging.INFO)
    atexit.register(logging.shutdown)
    logging.info('starting')
    print(f"writing to {options.log_path}_{time.strftime('%Y-%m-%d', time.localtime())}")
    asyncio.run(main())
