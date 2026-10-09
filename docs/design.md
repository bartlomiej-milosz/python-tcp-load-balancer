# Design notes

## Data flow and ownership

`TCPServer` owns the listener and starts one non-daemon worker per accepted client.
The worker closes its client socket in `finally`. Active sockets and workers are
registered under a lifecycle lock so `stop()` can interrupt them and join threads.
Listener startup also has a cleanup boundary covering bind/listen failures.
Instances are single-use; calling `stop()` repeatedly is safe.

`ProxyServer` opens and registers one backend socket for each client. A `select`
loop watches the two readable directions. Bytes are copied unchanged with `sendall`.
`LoadBalancerServer` overrides destination selection: its lock covers reading the
round-robin cursor and advancing it, while connect and forwarding run outside the
lock. The backend list is copied and validated before any listener socket is created.
The selected backend stays fixed for the whole TCP connection.

## Stream boundaries and half-close

TCP supplies an ordered byte stream, not message boundaries. A receive may return
part of a header, several body fragments, or bytes beyond a complete request.
HTTP reads until CRLF-CRLF, validates framing before reading the body, then reads
exactly `Content-Length` bytes. Body decoding happens only after all declared
bytes arrive, so a split UTF-8 code point works. Extra bytes are discarded because
the connection supports only one request. No length means no body.

An empty receive in the proxy removes that socket from the read set and calls
`shutdown(SHUT_WR)` on the destination. It leaves the opposite direction open,
allowing a backend to reply after the client finishes sending. Completion of both
read directions, a network error, idle expiry, or server stop ends forwarding.
Removing the EOF direction prevents a busy loop.

## Time and shutdown

The HTTP receive deadline uses a monotonic clock and is shared by the head and
body. Each receive gets only the remaining time, so sending a byte periodically
cannot extend the request indefinitely. HTTP writes have a separate timeout.

The proxy bounds TCP connect, idle waiting, and socket writes separately. `select`
only reports readiness; it does not make `sendall` non-blocking. A write can delay
reading the opposite direction until it finishes or times out. Hostname resolution
uses the system resolver and is not bounded by the TCP socket timeout.

`stop()` marks shutdown, closes the listener, and shuts down both directions of
registered streams (including backend sockets). Workers keep their descriptors
until their own cleanup closes them; this avoids a close racing with entry into
a timed receive/select operation. Threads are joined within one deadline, after
which any remaining streams are closed as a fallback. Shutdown wakes readers
and writers. Entry-point signal handlers only request shutdown,
avoiding a lock acquisition inside interrupted code; the accept loop notices the
request within its 0.2-second polling interval and cleans up. An arbitrary custom
handler cannot be killed safely by Python; a worker exceeding the join budget is
reported. This educational implementation has no limit on active client threads.

## Deliberate HTTP subset

Request heads are ASCII, header names are normalized to lowercase, and bodies are
UTF-8 text. Invalid syntax, incomplete framing, duplicate lengths, or any transfer
encoding is rejected before dispatch. Supported request versions are HTTP/1.0 and
HTTP/1.1, with exact origin-target routing. One response ends the connection.
The server does not implement persistent connections or full method semantics.

Serialization encodes the body first, derives its byte length, and replaces framing
headers without changing the handler's response object. Handlers are the deliberate
broad exception boundary (500); network code catches expected socket failures.
TCP proxying never parses HTTP and never fabricates HTTP errors.

## Design questions

- Why can one `recv` contain both headers and body, or only half a UTF-8 character?
- How does a byte length differ from a Python string length?
- Why must EOF in one direction leave the reverse direction available?
- What changes if the round-robin lock also covers network operations?
- Why does `select` readiness not guarantee that `sendall` completes immediately?
- How do an idle timeout and a total request deadline treat a slow sender differently?
- Why would retrying a partially forwarded stream risk duplicating an operation?
