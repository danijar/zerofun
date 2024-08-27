import collections
import multiprocessing as mp
import os
import pathlib
import threading
import time
import traceback

import cloudpickle
import elements
import psutil

from . import utils


CONTEXT = None

def setup(errfile=None, check_interval=20, initfns=[]):
  global CONTEXT
  if CONTEXT:
    CONTEXT.close()
  CONTEXT = Context(errfile, check_interval)
  [CONTEXT.initfn(x) for x in initfns]

def context():
  global CONTEXT
  if not CONTEXT:
    CONTEXT = Context(None)
  return CONTEXT


CHILDREN = collections.defaultdict(list)

def child(worker):
  global CHILDREN
  if hasattr(worker, 'thread'):
    assert worker.thread.ident != threading.get_ident()
  CHILDREN[threading.get_ident()].append(worker)

def children(ident):
  global CHILDREN
  return CHILDREN[ident]


class Context:

  def __init__(self, errfile=None, check_interval=20, resolver=None):
    if errfile and isinstance(errfile, str):
      errfile = pathlib.Path(errfile)
    self.mp = mp.get_context()
    self.errfile = errfile
    self.check_interval = check_interval
    self.resolver_bin = cloudpickle.dumps(resolver or (lambda x: x))
    self.resolver = None
    self.printlock = self.mp.Lock()
    self.initfns = []
    self.watcher = None
    self.started = False
    self.start()

  def __getstate__(self):
    return {
        'errfile': self.errfile,
        'check_interval': self.check_interval,
        'printlock': self.printlock,
        'initfns': self.initfns,
        'resolver_bin': self.resolver_bin,
    }

  def __setstate__(self, d):
    self.errfile = d['errfile']
    self.check_interval = d['check_interval']
    self.printlock = d['printlock']
    self.initfns = d['initfns']
    self.resolver_bin = d['resolver_bin']
    self.mp = mp.get_context()
    self.started = False

  def initfn(self, initfn):
    self.initfns.append(cloudpickle.dumps(initfn))
    initfn()

  def start(self):
    if self.started:
      return
    self.started = True
    initfns = [cloudpickle.loads(x) for x in self.initfns]
    [x() for x in initfns]
    self.resolver = cloudpickle.loads(self.resolver_bin)
    if self.errfile:
      self.watcher = threading.Thread(target=self._watcher, daemon=True)
      self.watcher.start()

  def error(self, e, name=None):
    typ, tb = type(e), e.__traceback__
    summary = list(traceback.format_exception_only(typ, e))[0].strip('\n')
    long = ''.join(traceback.format_exception(typ, e, tb)).strip('\n')
    message = f"Error in '{name}' ({summary}):\n{long}"
    with self.printlock:
      elements.print(message, color='red')
    if self.errfile:
      with self.errfile.open('wb') as f:
        f.write(message.encode('utf-8'))

  def shutdown(self, exitcode):
    utils.kill_proc(psutil.Process().children(recursive=True))
    os._exit(exitcode)

  def close(self):
    if self.watcher:
      utils.kill_thread(self.watcher)

  def _watcher(self):
    try:
      while True:
        time.sleep(self.check_interval)
        if self.errfile and self.errfile.exists():
          print('Detected error file thus shutting down:')
          print(self.errfile.read_text())
          self.shutdown(2)
    except (SystemExit, KeyboardInterrupt):
      pass