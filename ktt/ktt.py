#! /usr/bin/python3

import asyncio
import atexit
from dbus_next.aio import MessageBus
from dbus_next.service import (ServiceInterface, method)
import logging
import logging.handlers
import re
import time


class FocusTracker(object):
    def __init__(self, screensaver):
        self.ssbus = screensaver
        self.window = None
        self.wstart = 0
        self.start = time.time()
        self.total = 0
        self.last = time.time()
        asyncio.get_event_loop().call_soon(self.nap_check)

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

    async def check_idletime(self):
        try:
            idle = await self.ssbus.call_get_session_idle_time()
        except Exception as e:
            logging.warning(f"failed to get idle time: {str(e)}")
            return
        self.total -= int(idle / 1000)
        logging.info(f"idle for {idle/1000:.0f}s, total={self.total:.0f}")

    def screensaver(self, on):
        if on:
            self.focus(None)
            self.total += time.time() - self.start
            asyncio.get_event_loop().create_task(self.check_idletime())
        else:
            self.start = time.time()
        logging.info(f"screensaver={on} total={self.total:.0f}")

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
    bus = await MessageBus().connect()

    # listen to screensaver events
    introspection = await bus.introspect('org.kde.screensaver', '/org/freedesktop/ScreenSaver')
    #print(introspection.tostring())
    ssobj = bus.get_proxy_object('org.kde.screensaver', '/ScreenSaver', introspection)
    ssiface = ssobj.get_interface('org.freedesktop.ScreenSaver')
    tracker = FocusTracker(ssiface)
    ssiface.on_active_changed(tracker.screensaver)

    interface = KwinBridge(tracker)
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
