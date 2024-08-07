import multiprocessing as mp
import sys
import time
import traceback

import pytest
import zerofun


class TestProcess:

  def test_kill(self):
    def fn():
      while True:
        time.sleep(0.01)
    worker = zerofun.Process(fn, start=True)
    assert worker.running
    worker.kill()
    assert not worker.running

  def test_stop(self):
    def fn(context, q):
      q.put('start')
      while context.running:
        time.sleep(0.01)
      q.put('stop')
    q = mp.get_context().SimpleQueue()
    worker = zerofun.StoppableProcess(fn, q)
    worker.start()
    worker.stop()
    assert q.get() == 'start'
    assert q.get() == 'stop'

  def test_exitcode(self):
    worker = zerofun.Process(lambda: None)
    assert worker.exitcode is None
    worker.start()
    worker.join()
    assert worker.exitcode == 0

  def test_exception(self):
    def fn1234(q):
      q.put(42)
      raise KeyError('foo')
    q = mp.get_context().SimpleQueue()
    worker = zerofun.Process(fn1234, q, start=True)
    q.get()
    time.sleep(0.1)
    assert not worker.running
    assert worker.exitcode == 1
    with pytest.raises(KeyError) as info:
      worker.check()
    worker.kill()  # Shoud not hang or reraise.
    with pytest.raises(KeyError) as info:
      worker.check()  # Can reraise multiple times.
    assert repr(info.value) == "KeyError('foo')"
    e = info.value
    typ, tb = type(e), e.__traceback__
    tb = ''.join(traceback.format_exception(typ, e, tb))
    assert "KeyError: 'foo'" in tb
    if sys.version_info.minor >= 11:
      assert 'Traceback' in tb
      assert ' File ' in tb
      assert 'fn1234' in tb

  def test_nested_kill(self):
    q = mp.get_context().SimpleQueue()
    def inner():
      while True:
        time.sleep(0.01)
    def outer(q):
      child = zerofun.Process(inner, start=True)
      q.put(child.pid)
      while True:
        time.sleep(0.01)
    parent = zerofun.Process(outer, q, start=True)
    child_pid = q.get()
    assert zerofun.proc_alive(parent.pid)
    assert zerofun.proc_alive(child_pid)
    parent.kill()
    time.sleep(0.01)
    assert not zerofun.proc_alive(parent.pid)
    assert not zerofun.proc_alive(child_pid)

  def test_nested_exception(self):
    q = mp.get_context().SimpleQueue()
    def inner():
      time.sleep(0.1)
      raise KeyError('foo')
    def outer(q):
      child = zerofun.Process(inner, start=True)
      q.put(child.pid)
      while True:
        child.check()
        time.sleep(0.01)
    parent = zerofun.Process(outer, q, start=True)
    child_pid = q.get()
    assert zerofun.proc_alive(parent.pid)
    assert zerofun.proc_alive(child_pid)
    with pytest.raises(KeyError) as info:
      while True:
        parent.check()
        time.sleep(0.1)
    assert repr(info.value) == "KeyError('foo')"
    time.sleep(0.01)
    assert not zerofun.proc_alive(parent.pid)
    assert not zerofun.proc_alive(child_pid)
