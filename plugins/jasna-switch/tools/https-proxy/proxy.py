#!/usr/bin/env python3
"""Minimal TLS-terminating TCP proxy: HTTPS in, plain HTTP to Jasna out.

Exists only to satisfy browser mixed-content rules when Stash is served over
HTTPS but Jasna's --stream server only speaks plain HTTP. Byte-for-byte pass
through after the TLS handshake - no HTTP parsing/rewriting, so every Jasna
endpoint (/, /open, /stop, /stream.m3u8, /seg_*.ts) works unmodified.
"""
import asyncio
import ssl

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8766
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 8765
CERT_FILE = "jasna-proxy.crt"
KEY_FILE = "jasna-proxy.key"


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()


async def handle_client(client_reader, client_writer):
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(UPSTREAM_HOST, UPSTREAM_PORT)
    except OSError as err:
        print(f"[proxy] upstream connect failed: {err}")
        client_writer.close()
        return

    await asyncio.gather(
        pipe(client_reader, upstream_writer),
        pipe(upstream_reader, client_writer),
    )


async def main():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    server = await asyncio.start_server(handle_client, LISTEN_HOST, LISTEN_PORT, ssl=ctx)
    addr = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    print(f"[proxy] listening on {addr} -> https://... proxied to http://{UPSTREAM_HOST}:{UPSTREAM_PORT}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
