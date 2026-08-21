"""Windows 物理网卡枚举与局域网 TCP 探测。"""

from __future__ import annotations

import ctypes
import ipaddress
import socket
import sys
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event
from typing import Callable, Iterable, Optional


IF_OPER_STATUS_UP = 1
IF_TYPE_ETHERNET_CSMACD = 6
IF_TYPE_IEEE80211 = 71
MAX_TOTAL_PROBE_TARGETS = 4096
MAX_PROBE_WORKERS = 64

AF_INET = 2
ERROR_BUFFER_OVERFLOW = 111
GAA_FLAG_SKIP_ANYCAST = 0x0002
GAA_FLAG_SKIP_MULTICAST = 0x0004
GAA_FLAG_SKIP_DNS_SERVER = 0x0008
GAA_FLAG_INCLUDE_PREFIX = 0x0010
IP_ADAPTER_IPV4_ENABLED = 0x0080
MAX_ADAPTER_ADDRESS_LENGTH = 8
IF_MAX_STRING_SIZE = 256
IF_MAX_PHYS_ADDRESS_LENGTH = 32

_BYTE = ctypes.c_uint8
_USHORT = ctypes.c_uint16
_ULONG = ctypes.c_uint32
_LONG = ctypes.c_int32
_ULONG64 = ctypes.c_uint64


class WindowsAdapterError(RuntimeError):
    """Windows IP Helper 网卡枚举失败。"""


def is_tcp_endpoint_open(host: str, port: int = 5555, timeout: float = 0.5) -> bool:
    """轻量检测单个 TCP 端点是否可达。"""
    connection = None
    try:
        connection = socket.create_connection((host, port), timeout=timeout)
        return True
    except OSError:
        return False
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", _ULONG),
        ("Data2", _USHORT),
        ("Data3", _USHORT),
        ("Data4", _BYTE * 8),
    ]


class _NET_LUID(ctypes.Union):
    _fields_ = [("Value", _ULONG64)]


class _SOCKADDR(ctypes.Structure):
    _fields_ = [("sa_family", _USHORT), ("sa_data", _BYTE * 14)]


class _SOCKADDR_IN(ctypes.Structure):
    _fields_ = [
        ("sin_family", _USHORT),
        ("sin_port", _USHORT),
        ("sin_addr", _BYTE * 4),
        ("sin_zero", _BYTE * 8),
    ]


class _SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [
        ("lpSockaddr", ctypes.POINTER(_SOCKADDR)),
        ("iSockaddrLength", _LONG),
    ]


class _IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    pass


class _UNICAST_HEADER(ctypes.Structure):
    _fields_ = [("Length", _ULONG), ("Flags", _ULONG)]


class _UNICAST_ALIGNMENT(ctypes.Union):
    _anonymous_ = ("Header",)
    _fields_ = [("Alignment", _ULONG64), ("Header", _UNICAST_HEADER)]


_IP_ADAPTER_UNICAST_ADDRESS._anonymous_ = ("Header",)
_IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
    ("Header", _UNICAST_ALIGNMENT),
    ("Next", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
    ("Address", _SOCKET_ADDRESS),
    ("PrefixOrigin", _LONG),
    ("SuffixOrigin", _LONG),
    ("DadState", _LONG),
    ("ValidLifetime", _ULONG),
    ("PreferredLifetime", _ULONG),
    ("LeaseLifetime", _ULONG),
    ("OnLinkPrefixLength", _BYTE),
]


class _IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


class _ADAPTER_HEADER(ctypes.Structure):
    _fields_ = [("Length", _ULONG), ("IfIndex", _ULONG)]


class _ADAPTER_ALIGNMENT(ctypes.Union):
    _anonymous_ = ("Header",)
    _fields_ = [("Alignment", _ULONG64), ("Header", _ADAPTER_HEADER)]


_IP_ADAPTER_ADDRESSES._anonymous_ = ("Header",)
_IP_ADAPTER_ADDRESSES._fields_ = [
    ("Header", _ADAPTER_ALIGNMENT),
    ("Next", ctypes.POINTER(_IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", _BYTE * MAX_ADAPTER_ADDRESS_LENGTH),
    ("PhysicalAddressLength", _ULONG),
    ("Flags", _ULONG),
    ("Mtu", _ULONG),
    ("IfType", _ULONG),
    ("OperStatus", _LONG),
    ("Ipv6IfIndex", _ULONG),
    ("ZoneIndices", _ULONG * 16),
    ("FirstPrefix", ctypes.c_void_p),
    ("TransmitLinkSpeed", _ULONG64),
    ("ReceiveLinkSpeed", _ULONG64),
    ("FirstWinsServerAddress", ctypes.c_void_p),
    ("FirstGatewayAddress", ctypes.c_void_p),
    ("Ipv4Metric", _ULONG),
    ("Ipv6Metric", _ULONG),
    ("Luid", _NET_LUID),
]


class _MIB_IF_ROW2(ctypes.Structure):
    _fields_ = [
        ("InterfaceLuid", _NET_LUID),
        ("InterfaceIndex", _ULONG),
        ("InterfaceGuid", _GUID),
        ("Alias", _USHORT * (IF_MAX_STRING_SIZE + 1)),
        ("Description", _USHORT * (IF_MAX_STRING_SIZE + 1)),
        ("PhysicalAddressLength", _ULONG),
        ("PhysicalAddress", _BYTE * IF_MAX_PHYS_ADDRESS_LENGTH),
        ("PermanentPhysicalAddress", _BYTE * IF_MAX_PHYS_ADDRESS_LENGTH),
        ("Mtu", _ULONG),
        ("Type", _ULONG),
        ("TunnelType", _LONG),
        ("MediaType", _LONG),
        ("PhysicalMediumType", _LONG),
        ("AccessType", _LONG),
        ("DirectionType", _LONG),
        ("InterfaceAndOperStatusFlags", _BYTE),
        ("OperStatus", _LONG),
        ("AdminStatus", _LONG),
        ("MediaConnectState", _LONG),
        ("NetworkGuid", _GUID),
        ("ConnectionType", _LONG),
        ("TransmitLinkSpeed", _ULONG64),
        ("ReceiveLinkSpeed", _ULONG64),
        ("InOctets", _ULONG64),
        ("InUcastPkts", _ULONG64),
        ("InNUcastPkts", _ULONG64),
        ("InDiscards", _ULONG64),
        ("InErrors", _ULONG64),
        ("InUnknownProtos", _ULONG64),
        ("InUcastOctets", _ULONG64),
        ("InMulticastOctets", _ULONG64),
        ("InBroadcastOctets", _ULONG64),
        ("OutOctets", _ULONG64),
        ("OutUcastPkts", _ULONG64),
        ("OutNUcastPkts", _ULONG64),
        ("OutDiscards", _ULONG64),
        ("OutErrors", _ULONG64),
        ("OutUcastOctets", _ULONG64),
        ("OutMulticastOctets", _ULONG64),
        ("OutBroadcastOctets", _ULONG64),
        ("OutQLen", _ULONG64),
    ]

_RFC1918_NETWORKS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)


@dataclass(frozen=True)
class RawAdapterAddress:
    """Windows API 返回的一条网卡 IPv4 地址记录。"""

    interface_index: int
    interface_name: str
    local_ip: ipaddress.IPv4Address
    prefix_length: int
    metric: int
    if_type: int
    oper_status: int
    hardware_interface: bool
    filter_interface: bool = False
    media_connected: bool = True
    endpoint_interface: bool = False


@dataclass(frozen=True)
class ScanNetwork:
    """一次扫描实际使用的网段和源网卡。"""

    interface_index: int
    interface_name: str
    local_ip: ipaddress.IPv4Address
    network: ipaddress.IPv4Network
    original_network: ipaddress.IPv4Network
    metric: int


@dataclass(frozen=True)
class ProbeTarget:
    """一个带指定源网卡地址的 TCP 探测目标。"""

    ip: ipaddress.IPv4Address
    source_ip: ipaddress.IPv4Address


@dataclass(frozen=True)
class ProbeResult:
    """TCP 端口开放的探测结果。"""

    ip: ipaddress.IPv4Address
    source_ip: ipaddress.IPv4Address


def _log(log: Optional[Callable[[str], None]], message: str) -> None:
    if log:
        log(message)


def _is_rfc1918(ip: ipaddress.IPv4Address) -> bool:
    return any(ip in network for network in _RFC1918_NETWORKS)


def select_scan_networks(
    records: Iterable[RawAdapterAddress],
    log: Optional[Callable[[str], None]] = None,
) -> list[ScanNetwork]:
    """筛选可扫描的物理网卡，并对相同有效网段按 metric 去重。"""

    selected: dict[ipaddress.IPv4Network, ScanNetwork] = {}
    for record in records:
        reason = None
        if record.oper_status != IF_OPER_STATUS_UP:
            reason = "网卡未启用"
        elif not record.hardware_interface:
            reason = "非物理网卡"
        elif record.filter_interface:
            reason = "过滤器接口"
        elif not record.media_connected:
            reason = "网络未连接"
        elif record.endpoint_interface:
            reason = "终端接口"
        elif record.if_type not in (IF_TYPE_ETHERNET_CSMACD, IF_TYPE_IEEE80211):
            reason = f"接口类型不支持({record.if_type})"
        elif not _is_rfc1918(record.local_ip):
            reason = "非 RFC1918 IPv4"
        elif not 1 <= record.prefix_length <= 30:
            reason = f"前缀长度不支持(/{record.prefix_length})"

        if reason:
            _log(log, f"[扫描] 跳过网卡 {record.interface_name} {record.local_ip}: {reason}")
            continue

        original = ipaddress.IPv4Network(
            (record.local_ip, record.prefix_length), strict=False
        )
        effective = (
            original
            if record.prefix_length >= 22
            else ipaddress.IPv4Network((record.local_ip, 24), strict=False)
        )
        candidate = ScanNetwork(
            interface_index=record.interface_index,
            interface_name=record.interface_name,
            local_ip=record.local_ip,
            network=effective,
            original_network=original,
            metric=record.metric,
        )
        current = selected.get(effective)
        if current is None or (
            candidate.metric,
            candidate.interface_index,
            int(candidate.local_ip),
        ) < (
            current.metric,
            current.interface_index,
            int(current.local_ip),
        ):
            selected[effective] = candidate

    networks = sorted(
        selected.values(),
        key=lambda item: (int(item.network.network_address), item.network.prefixlen),
    )
    for item in networks:
        if item.original_network != item.network:
            _log(
                log,
                f"[扫描] 网卡 {item.interface_name} 的 {item.original_network} 过大，限制为 {item.network}",
            )
        else:
            _log(log, f"[扫描] 使用网卡 {item.interface_name}: {item.network}")
    return networks


def enumerate_windows_adapter_addresses() -> list[RawAdapterAddress]:
    """通过 Windows IP Helper API 枚举网卡的单播 IPv4 地址。"""

    if sys.platform != "win32":
        raise WindowsAdapterError("网卡枚举仅支持 Windows")
    if ctypes.sizeof(_MIB_IF_ROW2) != 1352:
        raise WindowsAdapterError(
            f"MIB_IF_ROW2 结构大小异常: {ctypes.sizeof(_MIB_IF_ROW2)}"
        )

    iphlpapi = ctypes.WinDLL("iphlpapi")
    get_adapters = iphlpapi.GetAdaptersAddresses
    get_adapters.argtypes = [
        _ULONG,
        _ULONG,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_ULONG),
    ]
    get_adapters.restype = _ULONG
    get_if_entry = iphlpapi.GetIfEntry2
    get_if_entry.argtypes = [ctypes.POINTER(_MIB_IF_ROW2)]
    get_if_entry.restype = _ULONG

    flags = (
        GAA_FLAG_SKIP_ANYCAST
        | GAA_FLAG_SKIP_MULTICAST
        | GAA_FLAG_SKIP_DNS_SERVER
        | GAA_FLAG_INCLUDE_PREFIX
    )
    size = _ULONG(15 * 1024)
    buffer = None
    result = ERROR_BUFFER_OVERFLOW
    for _attempt in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        result = int(
            get_adapters(
                AF_INET,
                flags,
                None,
                ctypes.cast(buffer, ctypes.c_void_p),
                ctypes.byref(size),
            )
        )
        if result != ERROR_BUFFER_OVERFLOW:
            break
    if result != 0 or buffer is None:
        raise WindowsAdapterError(f"GetAdaptersAddresses 失败，错误码 {result}")

    records: list[RawAdapterAddress] = []
    adapter_ptr = ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_ADDRESSES))
    while adapter_ptr:
        adapter = adapter_ptr.contents
        if adapter.Flags & IP_ADAPTER_IPV4_ENABLED:
            row = _MIB_IF_ROW2()
            row.InterfaceIndex = adapter.IfIndex
            row_result = int(get_if_entry(ctypes.byref(row)))
            status_flags = int(row.InterfaceAndOperStatusFlags) if row_result == 0 else 0
            hardware_interface = row_result == 0 and bool(status_flags & 0x01)
            filter_interface = row_result == 0 and bool(status_flags & 0x02)
            media_connected = row_result == 0 and not bool(status_flags & 0x10)
            endpoint_interface = row_result == 0 and bool(status_flags & 0x80)

            name = adapter.FriendlyName or adapter.Description
            if not name and adapter.AdapterName:
                name = adapter.AdapterName.decode("utf-8", errors="replace")
            name = name or f"接口 {adapter.IfIndex}"

            unicast_ptr = adapter.FirstUnicastAddress
            while unicast_ptr:
                unicast = unicast_ptr.contents
                sockaddr_ptr = unicast.Address.lpSockaddr
                if sockaddr_ptr and sockaddr_ptr.contents.sa_family == AF_INET:
                    sockaddr = ctypes.cast(
                        sockaddr_ptr, ctypes.POINTER(_SOCKADDR_IN)
                    ).contents
                    local_ip = ipaddress.IPv4Address(bytes(sockaddr.sin_addr))
                    records.append(
                        RawAdapterAddress(
                            interface_index=int(adapter.IfIndex),
                            interface_name=str(name),
                            local_ip=local_ip,
                            prefix_length=int(unicast.OnLinkPrefixLength),
                            metric=int(adapter.Ipv4Metric),
                            if_type=int(adapter.IfType),
                            oper_status=int(adapter.OperStatus),
                            hardware_interface=hardware_interface,
                            filter_interface=filter_interface,
                            media_connected=media_connected,
                            endpoint_interface=endpoint_interface,
                        )
                    )
                unicast_ptr = unicast.Next
        adapter_ptr = adapter.Next
    return records


def get_windows_scan_networks(
    log: Optional[Callable[[str], None]] = None,
    adapter_provider: Optional[Callable[[], list[RawAdapterAddress]]] = None,
) -> list[ScanNetwork]:
    """枚举并筛选所有适合扫描的 Windows 物理局域网。"""

    provider = adapter_provider or enumerate_windows_adapter_addresses
    return select_scan_networks(provider(), log=log)


def build_probe_targets(
    networks: Iterable[ScanNetwork],
    max_total: int = MAX_TOTAL_PROBE_TARGETS,
    log: Optional[Callable[[str], None]] = None,
) -> list[ProbeTarget]:
    """展开网段，排除本机地址并为重叠目标选择更优源网卡。"""

    network_list = list(networks)
    local_ips = {item.local_ip for item in network_list}
    selected: dict[ipaddress.IPv4Address, tuple[tuple[int, int, int, int], ProbeTarget]] = {}
    for item in network_list:
        priority = (
            item.metric,
            -item.network.prefixlen,
            item.interface_index,
            int(item.local_ip),
        )
        for target_ip in item.network.hosts():
            if target_ip in local_ips:
                continue
            target = ProbeTarget(ip=target_ip, source_ip=item.local_ip)
            current = selected.get(target_ip)
            if current is None or priority < current[0]:
                selected[target_ip] = (priority, target)

    targets = [entry[1] for entry in selected.values()]
    targets.sort(key=lambda item: int(item.ip))
    limit = max(0, min(int(max_total), MAX_TOTAL_PROBE_TARGETS))
    if len(targets) > limit:
        _log(log, f"[扫描] 目标地址共 {len(targets)} 个，按安全上限截取前 {limit} 个")
        targets = targets[:limit]
    _log(log, f"[扫描] 待探测地址 {len(targets)} 个")
    return targets


def probe_tcp_targets(
    targets: Iterable[ProbeTarget],
    cancel_event: Event,
    port: int = 5555,
    timeout: float = 0.4,
    max_workers: int = MAX_PROBE_WORKERS,
    socket_factory: Callable[..., socket.socket] = socket.socket,
    log: Optional[Callable[[str], None]] = None,
) -> list[ProbeResult]:
    """通过绑定源地址的受控线程池探测 TCP 端口。"""

    if cancel_event.is_set():
        return []
    target_list = list(targets)
    if not target_list:
        return []
    worker_count = max(1, min(int(max_workers), MAX_PROBE_WORKERS, len(target_list)))

    def probe(target: ProbeTarget) -> Optional[ProbeResult]:
        if cancel_event.is_set():
            return None
        try:
            with socket_factory(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.bind((str(target.source_ip), 0))
                if sock.connect_ex((str(target.ip), port)) != 0:
                    return None
                if cancel_event.is_set():
                    return None
                return ProbeResult(ip=target.ip, source_ip=target.source_ip)
        except OSError:
            return None

    results: list[ProbeResult] = []
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="lan-probe") as executor:
        futures = []
        for target in target_list:
            if cancel_event.is_set():
                break
            futures.append(executor.submit(probe, target))
        for future in as_completed(futures):
            if cancel_event.is_set():
                for pending in futures:
                    pending.cancel()
                break
            try:
                result = future.result()
            except Exception as exc:
                _log(log, f"[扫描] TCP 探测任务异常: {exc}")
                continue
            if result is not None:
                results.append(result)

    results.sort(key=lambda item: int(item.ip))
    _log(log, f"[扫描] 端口探测完成，{len(results)} 个 IP 的 {port} 端口开放")
    return results
