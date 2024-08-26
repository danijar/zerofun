import time

import pytest
import zerofun


class TestSocket:

  def test_basic(self):
    port = zerofun.free_port()
    server = zerofun.ServerSocket(port)
    client = zerofun.ClientSocket('localhost', port, connect=True)
    assert client.connected
    client.send(b'foo')
    addr, data = server.recv()
    assert addr[0] == '127.0.0.1'
    assert data == b'foo'
    server.send(addr, b'bar')
    assert client.recv() == b'bar'
    server.close()
    client.close()

  def test_multi_buffer(self):
    port = zerofun.free_port()
    server = zerofun.ServerSocket(port)
    client = zerofun.ClientSocket('localhost', port, connect=True)
    client.send(b'foo', b'bar', b'baz')
    addr, data = server.recv()
    assert data == b'foobarbaz'
    server.send(addr, b'ab', b'c')
    assert client.recv() == b'abc'
    server.close()
    client.close()

  def test_multiple_send(self):
    port = zerofun.free_port()
    server = zerofun.ServerSocket(port)
    client = zerofun.ClientSocket('localhost', port, connect=True)
    client.send(b'foo')
    client.send(b'ba', b'r')
    client.send(b'baz')
    assert server.recv()[1] == b'foo'
    assert server.recv()[1] == b'bar'
    assert server.recv()[1] == b'baz'
    assert len(server.connections) == 1
    addr = server.connections[0]
    server.send(addr, b'baz')
    server.send(addr, b'ba', b'r')
    server.send(addr, b'foo')
    assert client.recv() == b'baz'
    assert client.recv() == b'bar'
    assert client.recv() == b'foo'
    server.close()
    client.close()

  @pytest.mark.parametrize('repeat', range(3))
  def test_disconnect_server(self, repeat):
    port = zerofun.free_port()
    server = zerofun.ServerSocket(port)
    client = zerofun.ClientSocket(
        'localhost', port, connect=True, reconnect=False)
    server.close()
    with pytest.raises(zerofun.Disconnected):
      client.recv()
    server = zerofun.ServerSocket(port)
    with pytest.raises(zerofun.Disconnected):
      client.recv()
    client.connect()
    time.sleep(0.2)
    server.send(server.connections[0], b'foo')
    assert client.recv() == b'foo'
    server.close()
    time.sleep(0.2)
    with pytest.raises(zerofun.Disconnected):
      client.send(b'bar')

  @pytest.mark.parametrize('repeat', range(3))
  def test_disconnect_client(self, repeat):
    port = zerofun.free_port()
    server = zerofun.ServerSocket(port)
    client = zerofun.ClientSocket('localhost', port, connect=True)
    client.send(b'foo')
    assert server.recv()[1] == b'foo'
    assert len(server.connections) == 1
    client.close()
    time.sleep(0.2)
    assert len(server.connections) == 0
    client = zerofun.ClientSocket('localhost', port, connect=True)
    time.sleep(0.2)
    assert len(server.connections) == 1
    server.close()
    client.close()

  def test_server_dies(self):
    port = zerofun.free_port()
    ready = zerofun.context().mp.Semaphore(0)
    q = zerofun.context().mp.Queue()

    def server_fn(ready, port, q):
      server = zerofun.ServerSocket(port)
      ready.release()
      q.put(server.recv()[1])
      server.close()

    def client_fn(ready, port, q):
      client = zerofun.ClientSocket(
          'localhost', port,
          connect=True, reconnect=False,
          keepalive_after=1,
          keepalive_every=1,
          keepalive_fails=1)
      ready.release()
      try:
        while True:
          client.send(b'foo')
          time.sleep(0.1)
      except zerofun.Disconnected:
        q.put(b'disconnected')
        client.connect(timeout=None)
        client.send(b'reconnected')
      client.close()

    server = zerofun.Process(server_fn, ready, port, q, start=True)
    client = zerofun.Process(client_fn, ready, port, q, start=True)
    ready.acquire()
    ready.acquire()
    # NOTE: We kill the server process to close the server without closing its
    # connection propertly. However, because connections are handled by the
    # operationg system, the connection is actually still closed explicitly.
    # It's not clear how to unit test the keepalive timeouts.
    server.kill()
    assert q.get() == b'disconnected'
    server = zerofun.Process(server_fn, ready, port, q, start=True)
    assert q.get() == b'reconnected'
    server.join()
    client.join()

  # TODO:
  # - test keep-alive
  # - queue limits
  # - test resolve
  # - test timeouts