import os


class SendBuffer:

  def __init__(self, *buffers, maxsize=None):
    length = sum(len(x) for x in buffers)
    assert all(len(x) for x in buffers)
    assert 1 <= length, length
    assert not maxsize or length <= length, (length, maxsize)
    lenbuf = length.to_bytes(4, 'little', signed=False)
    self.buffers = [lenbuf, *buffers]
    self.pos = 0

  def send(self, sock):
    first, *others = self.buffers
    size = os.writev(sock.fileno(), [memoryview(first)[self.pos:], *others])
    assert 0 <= size, size
    self.pos += max(0, size)
    while self.buffers and self.pos >= len(self.buffers[0]):
      self.pos -= len(self.buffers.pop(0))
    return size

  def done(self):
    return not self.buffers


class RecvBuffer:

  def __init__(self, maxsize):
    self.maxsize = maxsize
    self.lenbuf = bytearray(4)
    self.buffer = None
    self.pos = 0

  def recv(self, sock):
    if self.buffer is None:
      size = sock.recv_into(memoryview(self.lenbuf)[self.pos:])
      self.pos += max(0, size)
      if self.pos == 4:
        length = int.from_bytes(self.lenbuf, 'little', signed=False)
        assert 0 < length <= self.maxsize, (length, self.maxsize)
        self.buffer = bytearray(length)
        self.pos = 0
    else:
      size = sock.recv_into(memoryview(self.buffer)[self.pos:])
      self.pos += max(0, size)
    return size

  def done(self):
    return self.buffer and self.pos == len(self.buffer)

  def result(self):
    return self.buffer