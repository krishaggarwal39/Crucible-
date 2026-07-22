import asyncio
import httpx
import httpcore

class SSRFSafeBackend(httpcore.AnyIOBackend):
    async def connect_tcp(self, host: str, port: int, timeout: float = None, local_address: str = None, **kwargs):
        import socket
        addr_info = socket.getaddrinfo(host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        ip = addr_info[0][4][0]
        print(f"Connecting to resolved IP: {ip} for host {host}")
        return await super().connect_tcp(ip, port, timeout=timeout, local_address=local_address, **kwargs)

async def main():
    transport = httpx.AsyncHTTPTransport()
    # Replace the network backend
    transport._pool._network_backend = SSRFSafeBackend()
    
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://httpbin.org/get")
        print(resp.status_code)

asyncio.run(main())
