import enum
import itertools
import msgpack
import threading
import time

import elements
import numpy as np
import zmq


DEBUG = False
# DEBUG = True

class Type(enum.Enum):
  PING   = int(1).to_bytes(1, 'big')  # rid
  PONG   = int(2).to_bytes(1, 'big')  # rid
  CALL   = int(3).to_bytes(1, 'big')  # rid, text, payload
  RESULT = int(4).to_bytes(1, 'big')  # rid, payload
  ERROR  = int(5).to_bytes(1, 'big')  # rid, text

class ConnectError(RuntimeError): pass
class NotAliveError(RuntimeError): pass
class RemoteError(RuntimeError): pass
class ProtocolError(RuntimeError): pass


class ClientSocket:

  def __init__(self, identity, ipv6=False, pings=10, maxage=60):
    self.socket = zmq.Context.instance().socket(zmq.DEALER)
    self.socket.setsockopt(zmq.IDENTITY, identity.to_bytes(16, 'big'))
    self.socket.setsockopt(zmq.IPV6, int(ipv6))
    self.socket.setsockopt(zmq.LINGER, 0)
    self.socket.set_hwm(0)
    self.pings = pings
    self.maxage = maxage
    self.connected = False
    self.last_call = float('-inf')
    self.last_ping = float('-inf')
    self.last_response = float('-inf')
    self.addr = None
    self.rid = iter(itertools.count(0))
    self.running = True
    self.lock = threading.RLock()

  def connect(self, addr, timeout=10.0):
    self.disconnect()
    with self.lock:
      self.socket.connect(addr)
      self.addr = addr
      rid = next(self.rid).to_bytes(8, 'big')
      self.socket.send_multipart([Type.PING.value, rid])
      start = time.time()
      while True:
        try:
          with self.lock:
            parts = self.socket.recv_multipart(zmq.NOBLOCK, copy=False)
          self.last_response = time.time()
          typ, rid2, *args = [x.buffer for x in parts]
          if typ == Type.PONG.value and rid == rid2:
            self.connected = True
            return
          else:
            raise ProtocolError(Type(typ).name)
        except zmq.Again:
          pass
        if timeout and time.time() - start >= timeout:
          raise ConnectError()
        time.sleep(0.01)

  def disconnect(self):
    if self.addr:
      with self.lock:
        self.socket.disconnect(self.addr)
        self.connected = False

  def receive(self):
    assert self.connected
    now = time.time()
    try:
      with self.lock:
        parts = self.socket.recv_multipart(zmq.NOBLOCK, copy=False)
      self.last_response = now
    except zmq.Again:
      parts = None
    if parts is None:

      # This is the time since the last response or if the server is not
      # responding, since the last ping so that we can try again.
      last_ping_or_resp = max(self.last_response, self.last_ping)
      if self.pings and now - last_ping_or_resp >= self.pings:
        self.last_ping = now
        self.send_ping()

      # This is the time since the last call, unless the server sent back
      # something in the meantime to keep the connection alive.
      last_call_or_resp = max(self.last_call, self.last_response)
      if self.maxage and now - last_call_or_resp >= self.maxage:
        raise NotAliveError(
            f'\nlast call:     {now - self.last_call:.3f}s ago'
            f'\nlast response: {now - self.last_response:.3f}s ago'
            f'\nlast pinged:   {now - self.last_ping:.3f}s ago'
        )
      return None

    typ, rid, *args = [x.buffer for x in parts]
    rid = bytes(rid)
    DEBUG and print(
        f'Client received {Type(bytes(typ)).name} ' +
        f'with rid {int.from_bytes(rid, "big")}')
    if typ == Type.PING.value:
      assert not args
      with self.lock:
        self.socket.send_multipart([Type.PONG.value, rid])
      return None
    elif typ == Type.PONG.value:
      assert not args
      return None
    elif typ == Type.RESULT.value:
      payload = args
      return rid, payload
    elif typ == Type.ERROR.value:
      msgs = [str(x, 'utf-8') for x in args]
      raise RemoteError(rid, *msgs)
    else:
      raise ProtocolError(Type(bytes(typ)).name)

  def send_call(self, name, payload):
    assert self.connected
    rid = next(self.rid)
    DEBUG and print(f"Client calling '{name}' with rid {rid}")
    rid = rid.to_bytes(8, 'big')
    name = name.encode('utf-8')
    with self.lock:
      self.socket.send_multipart([Type.CALL.value, rid, name, *payload])
    self.last_call = time.time()
    return rid

  def send_ping(self):
    assert self.connected
    rid = next(self.rid)
    DEBUG and print(f'Client ping with rid {rid}')
    rid = rid.to_bytes(8, 'big')
    with self.lock:
      self.socket.send_multipart([Type.PING.value, rid])
    return rid

  def close(self):
    with self.lock:
      self.socket.close()


class ServerSocket:

  def __init__(self, addr, ipv6=False):
    assert any(addr.startswith(x) for x in ('tcp://', 'ipc://')), addr
    if addr.startswith('tcp://'):
      port = addr.split(':')[-1]
      addr = f'tcp://*:{port}'
    self.socket = zmq.Context.instance().socket(zmq.ROUTER)
    self.socket.setsockopt(zmq.IPV6, ipv6)
    self.socket.setsockopt(zmq.LINGER, 0)
    self.socket.set_hwm(0)
    self.socket.bind(addr)
    self.alive = {}
    self.rid = iter(itertools.count(0))
    self.lock = threading.RLock()

  def clients(self, maxage=float('inf')):
    now = time.time()
    with self.lock:
      return tuple(k for k, v in self.alive.items() if now - v <= maxage)

  def receive(self):
    now = time.time()
    try:
      with self.lock:
        parts = self.socket.recv_multipart(zmq.NOBLOCK, copy=False)
    except zmq.Again:
      return None
    addr, typ, rid, *args = [x.buffer for x in parts]
    addr = bytes(addr)
    self.alive[addr] = now
    if typ == Type.PING.value:
      assert not args
      with self.lock:
        self.socket.send_multipart([addr, Type.PONG.value, bytes(rid)])
      return None
    elif typ == Type.PONG.value:
      assert not args
      return None
    elif typ == Type.CALL.value:
      method, *payload = args
      method = str(method, 'utf-8')
      return addr, rid, method, payload
    else:
      msg = f'Server received unexpected message of type {typ}'
      self.send_error(addr, rid, msg)
      return None

  def send_ping(self, addr):
    rid = next(self.rid).to_bytes(8, 'big')
    with self.lock:
      self.socket.send_multipart([addr, Type.PING.value, rid])
    return rid

  def send_result(self, addr, rid, payload):
    with self.lock:
      self.socket.send_multipart(
          [addr, Type.RESULT.value, rid, *payload], copy=False, track=True)

  def send_error(self, addr, rid, text):
    text = text.encode('utf-8')
    with self.lock:
      self.socket.send_multipart([addr, Type.ERROR.value, rid, text])

  def close(self):
    with self.lock:
      self.socket.close()


def pack(data):
  leaves, structure = elements.tree.flatten(data)
  dtypes, shapes, buffers = [], [], []
  for value in leaves:
    value = np.asarray(value)
    if value.dtype == object:
      raise TypeError(data)
    assert value.data.c_contiguous, (
        "Array is not contiguous in memory. Use np.asarray(arr, order='C') " +
        "before passing the data into pack().")
    dtypes.append(value.dtype.str)
    shapes.append(value.shape)
    buffers.append(value.data)
  meta = (structure, dtypes, shapes)
  payload = [msgpack.packb(meta), *buffers]
  return payload


def unpack(payload):
  meta, *buffers = payload
  structure, dtypes, shapes = msgpack.unpackb(meta)
  leaves = [
      np.frombuffer(b, d).reshape(s)
      for i, (d, s, b) in enumerate(zip(dtypes, shapes, buffers))]
  data = elements.tree.unflatten(leaves, structure)
  return data
