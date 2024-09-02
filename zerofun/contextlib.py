import collections
import multiprocessing as mp
import os
import pathlib
import threading
import traceback

import cloudpickle
import psutil

from . import utils


class Context:

  def __init__(self):
    self.hostname = ''
    self.initfns = []
    self.resolver = None
    self.errfile = None
    self.interval = 20
    self.done = threading.Event()
    self.watcher = None
    self.children = collections.defaultdict(list)
    self.mp = mp.get_context()
    self.printlock = self.mp.Lock()

  def options(self):
    return {
        'hostname': self.hostname,
        'resolver': self.resolver and cloudpickle.dumps(self.resolver),
        'errfile': self.errfile,
        'interval': self.interval,
        'initfns': self.initfns,
    }

  def setup(
      self,
      hostname=None,
      resolver=None,
      errfile=None,
      interval=None,
      initfns=None,
  ):

    if hostname is not None:
      assert isinstance(hostname, str)
      self.hostname = hostname

    if resolver:
      if isinstance(resolver, bytes):
        resolver = cloudpickle.loads(resolver)
      assert callable(resolver)
      self.resolver = resolver

    if errfile:
      if isinstance(errfile, str):
        errfile = pathlib.Path(errfile)
      assert hasattr(errfile, 'exists') and hasattr(errfile, 'write_text')
      self.errfile = errfile

    if interval:
      assert isinstance(interval, (int, float))
      self.interval = interval

    if initfns:
      for fn in initfns:
        self.initfn(fn, call_now=True)

    if self.errfile and not self.watcher:
      self.watcher = threading.Thread(target=self._watcher, daemon=True)
      self.watcher.start()

  def initfn(self, fn, call_now=True):
    if isinstance(fn, bytes):
      pkl, fn = fn, cloudpickle.loads(fn)
    else:
      pkl, fn = cloudpickle.dumps(fn), fn
    self.initfns.append(pkl)
    call_now and fn()

  def error(self, e, name=None):
    typ, tb = type(e), e.__traceback__
    summary = list(traceback.format_exception_only(typ, e))[0].strip('\n')
    long = ''.join(traceback.format_exception(typ, e, tb)).strip('\n')
    message = f"Error in '{name}' ({summary}):\n{long}"
    with self.printlock:
      style = utils.style(color='red')
      reset = utils.style(reset=True)
      print(style + '\n---\n' + message + reset)
    if self.errfile:
      self.errfile.write_text(message)
      print(f'Wrote errorfile: {self.errfile}')

  def shutdown(self, exitcode):
    utils.kill_procs(psutil.Process().children(recursive=True))
    os._exit(exitcode)

  def close(self):
    self.done.set()
    if self.watcher:
      self.watcher.join()

  def add_child(self, worker):
    parent = threading.get_ident()
    if hasattr(worker, 'thread'):
      assert worker.thread.ident != parent
    self.children[parent].append(worker)

  def get_children(self, ident):
    return self.children[ident]

  def _watcher(self):
    while True:
      if self.done.wait(self.interval):
        break
      if self.errfile and self.errfile.exists():
        print(f'Shutting down due to error file: {self.errfile}')
        self.shutdown(2)


context = Context()


def initfn(fn):
  context.initfn(fn)


def setup(**kwargs):
  context.setup(**kwargs)


def reset():
  global context
  context.close()
  context = Context()
