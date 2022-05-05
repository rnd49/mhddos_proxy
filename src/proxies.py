from collections import defaultdict
from random import choice, random
from typing import List, Optional, Tuple

from aiohttp_socks import ProxyConnector
from yarl import URL

from .core import (
    logger, cl,
    ONLY_MY_IP, PROXIES_URLS, PROXY_ALIVE_PRIOR, PROXY_ALIVE_THRESHOLD
)
from .dns_utils import resolve_all
from .system import read_or_fetch, fetch


# @formatter:off
_globals_before = set(globals().keys()).union({'_globals_before'})
# noinspection PyUnresolvedReferences
from .load_proxies import *
decrypt_proxies = globals()[set(globals().keys()).difference(_globals_before).pop()]
# @formatter:on


INVALID_SCHEME_ERROR = "Invalid scheme component"
INVALID_PORT_ERROR = "Invalid port component"


def normalize_url(url: str) -> str:
    try:
        ProxyConnector.from_url(url)
        return url
    except ValueError as e:
        if INVALID_SCHEME_ERROR in str(e):
            return normalize_url(f"http://{url}")
        elif INVALID_PORT_ERROR in str(e) and url.count(":") == 4:
            url, username, password = url.rsplit(":", 2)
            return str(URL(url).with_user(username).with_password(password))
        else:
            raise ValueError("Proxy config parsing failed") from e


class ProxySet:

    def __init__(self, proxies_file: Optional[str] = None, skip_ratio: int = 0):
        self._proxies_file = proxies_file
        self._skip_ratio = skip_ratio
        self._loaded_proxies = []
        self._connections = defaultdict(int)

    @property
    def has_proxies(self) -> bool:
        return self._skip_ratio != ONLY_MY_IP

    async def reload(self) -> int:
        if not self.has_proxies: return 0
        if self._proxies_file:
            proxies = await load_provided_proxies(self._proxies_file)
        else:
            proxies = await load_system_proxies()

        if not proxies:
            return 0

        # resolve DNS entires in case proxy is given using hostname rather than IP
        urls = [URL(proxy_url) for proxy_url in proxies]
        ips = await resolve_all([url.host for url in urls])
        proxies = [str(url.with_host(ips.get(url.host, url.host))) for url in urls]
        self._loaded_proxies = proxies
        self._connections = defaultdict(int)
        return len(self._loaded_proxies)

    def pick_random(self) -> Optional[str]:
        if not self.has_proxies: return None
        if self._skip_ratio > 0 and random() * 100 <= self._skip_ratio: return None
        alive_proxies = self.alive
        if len(alive_proxies) > PROXY_ALIVE_THRESHOLD and random() < PROXY_ALIVE_PRIOR:
            _, proxy_url = choice(alive_proxies)
            return proxy_url
        else:
            return choice(self._loaded_proxies)

    def pick_random_connector(self) -> Optional[ProxyConnector]:
        proxy_url = self.pick_random()
        return ProxyConnector.from_url(proxy_url) if proxy_url is not None else None
    
    def __len__(self) -> int:
        if not self.has_proxies: return 0
        return len(self._loaded_proxies)

    def track_alive(self, proxy_url: str) -> None:
        self._connections[proxy_url] += 1

    @property
    def alive(self) -> List[Tuple[int, str]]:
        if PROXY_ALIVE_THRESHOLD == 0: return []
        return sorted([(v,k) for (k,v) in self._connections.items()], reverse=True)


class NoProxySet:

    alive = []

    @staticmethod
    def pick_random(self) -> Optional[str]:
        return None

    @staticmethod
    def pick_random_connector(self) -> Optional[ProxyConnector]:
        return None

    @staticmethod
    def has_proxies(self) -> bool:
        return False

    @staticmethod
    def track_open_connection(self, proxy_url: str) -> None:
        pass


# XXX: move logging to the runner?
async def load_provided_proxies(proxies_file: str) -> Optional[List[str]]:
    content = await read_or_fetch(proxies_file)
    if content is None:
        logger.warning(f'{cl.RED}Не вдалося зчитати проксі з {proxies_file}{cl.RESET}')
        return None

    proxies = list(map(normalize_url, content.split()))
    if not proxies:
        logger.warning(
            f"{cl.RED}У {proxies_file} не знайдено проксі - перевірте формат{cl.RESET}")
    else:
        logger.info(f'{cl.YELLOW}Зчитано {cl.BLUE}{len(proxies)}{cl.YELLOW} проксі{cl.RESET}')
    return proxies


async def load_system_proxies():
    raw = await fetch(choice(PROXIES_URLS))
    try:
        proxies = decrypt_proxies(raw)
    except Exception:
        proxies = []
    proxies = list(map(normalize_url, proxies))
    if proxies:
        logger.info(
            f'{cl.YELLOW}Отримано вибірку {cl.BLUE}{len(proxies):,}{cl.YELLOW} проксі '
            f'зі списку {cl.BLUE}25.000+{cl.YELLOW} робочих{cl.RESET}'
        )
    else:
        logger.warning(f'{cl.RED}Не вдалося отримати персональну вибірку проксі{cl.RESET}')
    return proxies
