import collections
import time

import elements
import numpy as np

from . import process
from . import server
from . import sockets


class ProcServer:

  def __init__(
      self, address, name='Server', ipv6=False, workers=1, errors=True):
    self.address = address
    self.inner = f'ipc:///tmp/inner{np.random.randint(2 ** 32)}'
    self.name = name
    self.ipv6 = ipv6
    self.server = server.Server(self.inner, name, ipv6, workers, errors)
    self.batchsizes = {}
    self.batcher = None

  def bind(self, name, workfn, logfn=None, workers=0, batch=0):
    self.batchsizes[name] = batch
    self.server.bind(name, workfn, logfn, workers, batch=0)

  def start(self):
    self.batcher = process.StoppableProcess(
        self._batcher, self.address, self.inner,
        self.batchsizes, self.name, self.ipv6, name='batcher', start=True)
    self.server.start()

  def check(self):
    self.batcher.check()
    self.server.check()

  def close(self):
    self.server.close()
    self.batcher.stop()

  def run(self):
    try:
      self.start()
      while True:
        self.check()
        time.sleep(1)
    finally:
      self.close()

  def stats(self):
    return self.server.stats()

  def __enter__(self):
    self.start()
    return self

  def __exit__(self, type, value, traceback):
    self.close()

  @staticmethod
  def _batcher(context, address, inner, batchsizes, name, ipv6):

    socket = sockets.ServerSocket(address, ipv6)
    inbound = sockets.ClientSocket(identity=0, pings=0, maxage=0)
    inbound.connect(inner, timeout=120)
    queues = collections.defaultdict(list)
    buffers = {}
    pending = {}
    elements.print(f'[{name}] Listening at {address}')

    while context.running:

      result = socket.receive()
      if result:
        addr, rid, name, payload = result
        batch = batchsizes.get(name, None)
        if batch is not None:
          if batch:
            queue = queues[name]
            queue.append((addr, rid, payload))
            if len(queue) == batch:
              addrs, rids, payloads = zip(*queue)
              queue.clear()
              datas = [sockets.unpack(x) for x in payloads]
              if name not in buffers:
                buffers[name] = buffer = elements.tree.map(
                    lambda *xs: np.stack(xs), *datas)
              else:
                buffers[name] = buffer = elements.tree.map(
                    lambda buf, *xs: np.stack(xs, out=buf),
                    buffers[name], *datas)
              payload = sockets.pack(buffer)
              rid = inbound.send_call(name, payload)
              pending[rid] = (name, addrs, rids)
          else:
            inner_rid = inbound.send_call(name, payload)
            pending[inner_rid] = (name, addr, rid)
        else:
          socket.send_error(addr, rid, f'Unknown method {name}.')

      try:
        result = inbound.receive()
        if result:
          inner_rid, payload = result
          name, addr, rid = pending.pop(inner_rid)
          if batchsizes[name]:
            addrs, rids = addr, rid
            result = sockets.unpack(payload)
            results = [
                elements.tree.map(lambda x: x[i], result)
                for i in range(batchsizes[name])]
            payloads = [sockets.pack(x) for x in results]
            for addr, rid, payload in zip(addrs, rids, payloads):
              socket.send_result(addr, rid, payload)
          else:
            socket.send_result(addr, rid, payload)
      except sockets.RemoteError as e:
        inner_rid, msg = e.args[:2]
        name, addr, rid = pending.pop(inner_rid)
        if batchsizes[name]:
          addrs, rids = addr, rid
          for addr, rid in zip(addrs, rids):
            socket.send_error(addr, rid, msg)
        else:
          socket.send_error(addr, rid, msg)

    socket.close()
    inbound.close()
