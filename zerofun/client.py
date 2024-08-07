import time
import weakref
from functools import partial as bind
from collections import deque

import elements
import numpy as np

from . import sockets


class Client:

  RESOLVERS = []

  def __init__(
      self, address, name='Client', ipv6=False, identity=None,
      pings=10, maxage=120, maxinflight=16, errors=True,
      connect=False):
    assert isinstance(name, str)
    if identity is None:
      identity = int(np.random.randint(2 ** 32))
    self.address = address
    self.identity = identity
    self.name = name
    self.maxinflight = maxinflight
    self.errors = errors
    self.resolved = None
    self.socket = sockets.ClientSocket(identity, ipv6, pings, maxage)
    self.futures = weakref.WeakValueDictionary()
    self.queue = deque()
    self.conn_per_sec = elements.FPS()
    self.send_per_sec = elements.FPS()
    self.recv_per_sec = elements.FPS()
    connect and self.connect()

  def __getattr__(self, name):
    if name.startswith('__'):
      raise AttributeError(name)
    try:
      return bind(self.call, name)
    except AttributeError:
      raise ValueError(name)

  def stats(self):
    return {
        'futures': len(self.futures),
        'inflight': len(self.queue),
        'conn_per_sec': self.conn_per_sec.result(),
        'send_per_sec': self.send_per_sec.result(),
        'recv_per_sec': self.recv_per_sec.result(),
    }

  @elements.timer.section('client_connect')
  def connect(self, retry=True, timeout=10):
    msg1 = False
    msg2 = False
    while True:
      self.resolved = self._resolve(self.address)
      if not msg1:
        msg1 = True
        self._print(f'Connecting to {self.resolved}')
      try:
        self.socket.connect(self.resolved, timeout)
        self._print('Connection established')
        self.conn_per_sec.step(1)
        return
      except sockets.ProtocolError as e:
        self._print(f'Ignoring unexpected message: {e}')
      except sockets.ConnectError:
        if not msg2:
          msg2 = True
          self._print('--- Could not connect yet and will retry forever ---')
      if retry:
        continue
      else:
        raise sockets.ConnectError

  @elements.timer.section('client_call')
  def call(self, method, data):
    assert len(self.futures) < 1000, (
        f'Too many unresolved requests in client {self.name}.\n' +
        f'Futures: {len(self.futures)}\n' +
        f'Resolved: {sum([x.done() for x in self.futures.values()])}')
    if self.maxinflight:
      with elements.timer.section('inflight_wait'):
        while sum(not x.done() for x in self.queue) >= self.maxinflight:
          self.queue[0].check()
          time.sleep(0.001)
    if self.errors:
      try:
        while self.queue[0].done():
          self.queue.popleft().result()
      except IndexError:
        pass
    data = sockets.pack(data)
    rid = self.socket.send_call(method, data)
    self.send_per_sec.step(1)
    future = Future(self._receive, rid)
    self.futures[rid] = future
    if self.errors or self.maxinflight:
      self.queue.append(future)
    return future

  def close(self):
    return self.socket.close()

  def _receive(self, rid, retry):
    with elements.timer.section(f'{self.name}_client_receive'):
      while rid in self.futures and not self.futures[rid].done():
        result = self._listen()
        if result is None and not retry:
          return
        time.sleep(0.0001)

  @elements.timer.section('client_listen')
  def _listen(self):
    try:
      result = self.socket.receive()
      if result is not None:
        other, payload = result
        if other in self.futures:
          self.futures[other].set_result(sockets.unpack(payload))
        self.recv_per_sec.step(1)
      return result
    except sockets.NotAliveError:
      self._print('Server is not responding')
      raise
    except sockets.RemoteError as e:
      self._print(f'Received error response: {e.args[1]}')
      other = e.args[0]
      if other in self.futures:
        self.futures[other].set_error(sockets.RemoteError(e.args[1]))
    except sockets.ProtocolError as e:
      self._print(f'Ignoring unexpected message: {e}')

  @elements.timer.section('client_resolve')
  def _resolve(self, address):
    for check, resolve in self.RESOLVERS:
      if check(address):
        return resolve(address)
    return address

  def _print(self, text):
    elements.print(f'[{self.name}] {text}')


class Future:

  def __init__(self, waitfn, *args):
    self._waitfn = waitfn
    self._args = args
    self._status = 0
    self._result = None
    self._error = None

  def check(self):
    if self._status == 0:
      self._waitfn(*self._args, retry=False)

  def done(self):
    return self._status > 0

  def result(self):
    if self._status == 0:
      self._waitfn(*self._args, retry=True)
    if self._status == 1:
      return self._result
    elif self._status == 2:
      raise self._error
    else:
      assert False

  def set_result(self, result):
    self._status = 1
    self._result = result

  def set_error(self, error):
    self._status = 2
    self._error = error
