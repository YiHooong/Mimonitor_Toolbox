import ipaddress
import threading
import unittest
from unittest import mock

from mimonitor_toolbox import network_scan
from mimonitor_toolbox.network_scan import (
    RawAdapterAddress,
    ScanNetwork,
    ProbeTarget,
    WindowsAdapterError,
    build_probe_targets,
    enumerate_windows_adapter_addresses,
    get_windows_scan_networks,
    probe_tcp_targets,
    select_scan_networks,
)


def raw(
    ip,
    prefix=24,
    *,
    index=1,
    name="以太网",
    metric=25,
    if_type=6,
    oper_status=1,
    hardware=True,
    filter_interface=False,
    media_connected=True,
    endpoint_interface=False,
):
    return RawAdapterAddress(
        interface_index=index,
        interface_name=name,
        local_ip=ipaddress.IPv4Address(ip),
        prefix_length=prefix,
        metric=metric,
        if_type=if_type,
        oper_status=oper_status,
        hardware_interface=hardware,
        filter_interface=filter_interface,
        media_connected=media_connected,
        endpoint_interface=endpoint_interface,
    )


class ScanNetworkSelectionTests(unittest.TestCase):
    def test_selects_all_physical_private_networks_and_rejects_tunnel(self):
        records = [
            raw("192.168.5.10", index=1, metric=25),
            raw("192.168.5.20", index=2, metric=5),
            raw("192.168.8.10", index=3, name="Wi-Fi", metric=20, if_type=71),
            raw("10.0.0.2", index=4, name="Tunnel", metric=1, if_type=131, hardware=False),
            raw("10.1.0.2", index=5, name="虚拟以太网", hardware=False),
            raw("10.2.0.2", index=6, name="已断开", oper_status=2, media_connected=False),
            raw("169.254.10.2", index=7, name="APIPA"),
            raw("198.18.0.1", index=8, name="测试网络"),
        ]

        networks = select_scan_networks(records)

        self.assertEqual(
            [(str(item.network), item.interface_index, str(item.local_ip)) for item in networks],
            [
                ("192.168.5.0/24", 2, "192.168.5.20"),
                ("192.168.8.0/24", 3, "192.168.8.10"),
            ],
        )

    def test_uses_real_small_prefix_and_limits_broad_network_to_local_24(self):
        networks = select_scan_networks([
            raw("192.168.4.10", prefix=23, index=1),
            raw("10.20.30.40", prefix=16, index=2),
        ])

        self.assertEqual(
            [(str(item.original_network), str(item.network)) for item in networks],
            [
                ("10.20.0.0/16", "10.20.30.0/24"),
                ("192.168.4.0/23", "192.168.4.0/23"),
            ],
        )

        targets = build_probe_targets(networks)
        target_ips = [str(item.ip) for item in targets]
        self.assertEqual(len(targets), 762)
        self.assertNotIn("10.20.30.40", target_ips)
        self.assertNotIn("192.168.4.10", target_ips)
        self.assertEqual(target_ips[0], "10.20.30.1")
        self.assertEqual(target_ips[-1], "192.168.5.254")

    def test_deduplicates_overlapping_targets_and_applies_total_limit(self):
        broad = ScanNetwork(
            interface_index=1,
            interface_name="以太网",
            local_ip=ipaddress.IPv4Address("192.168.4.10"),
            network=ipaddress.IPv4Network("192.168.4.0/23"),
            original_network=ipaddress.IPv4Network("192.168.4.0/23"),
            metric=25,
        )
        narrow = ScanNetwork(
            interface_index=2,
            interface_name="Wi-Fi",
            local_ip=ipaddress.IPv4Address("192.168.5.20"),
            network=ipaddress.IPv4Network("192.168.5.0/24"),
            original_network=ipaddress.IPv4Network("192.168.5.0/24"),
            metric=5,
        )

        targets = build_probe_targets([broad, narrow], max_total=300)

        self.assertEqual(len(targets), 300)
        self.assertEqual(len({item.ip for item in targets}), 300)
        overlapping = next(item for item in targets if str(item.ip) == "192.168.5.1")
        self.assertEqual(str(overlapping.source_ip), "192.168.5.20")


class TcpProbeTests(unittest.TestCase):
    def test_single_endpoint_probe_reports_reachability_and_closes_socket(self):
        probe = getattr(network_scan, "is_tcp_endpoint_open", None)
        self.assertTrue(callable(probe), "缺少单目标 TCP 可用性探测")
        connection = mock.MagicMock()

        with mock.patch.object(network_scan.socket, "create_connection", return_value=connection) as connect:
            self.assertTrue(probe("192.168.5.205", 5555, timeout=0.4))

        connect.assert_called_once_with(("192.168.5.205", 5555), timeout=0.4)
        connection.close.assert_called_once_with()

        with mock.patch.object(network_scan.socket, "create_connection", side_effect=OSError("offline")):
            self.assertFalse(probe("192.168.5.205", 5555, timeout=0.4))

    def test_closed_target_is_retried_once_and_can_open_on_second_pass(self):
        attempts = {}

        class ImmediateRetryEvent(threading.Event):
            def wait(self, timeout=None):
                return self.is_set()

        class FakeSocket:
            def __init__(self, family, sock_type):
                pass

            def settimeout(self, value):
                pass

            def bind(self, address):
                pass

            def connect_ex(self, address):
                attempts[address] = attempts.get(address, 0) + 1
                return 0 if attempts[address] == 2 else 10061

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self.close()

        target = ProbeTarget(
            ipaddress.IPv4Address("192.168.5.5"),
            ipaddress.IPv4Address("192.168.5.10"),
        )

        results = probe_tcp_targets(
            [target],
            ImmediateRetryEvent(),
            socket_factory=FakeSocket,
        )

        self.assertEqual([str(item.ip) for item in results], ["192.168.5.5"])
        self.assertEqual(attempts[("192.168.5.5", 5555)], 2)

    def test_binds_source_closes_sockets_and_sorts_open_results(self):
        created = []
        open_ips = {"192.168.5.2", "192.168.5.30"}

        class ImmediateRetryEvent(threading.Event):
            def wait(self, timeout=None):
                return self.is_set()

        class FakeSocket:
            def __init__(self, family, sock_type):
                self.family = family
                self.sock_type = sock_type
                self.timeout = None
                self.bound = None
                self.target = None
                self.closed = False
                created.append(self)

            def settimeout(self, value):
                self.timeout = value

            def bind(self, address):
                self.bound = address

            def connect_ex(self, address):
                self.target = address
                return 0 if address[0] in open_ips else 10061

            def close(self):
                self.closed = True

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self.close()

        targets = [
            ProbeTarget(ipaddress.IPv4Address("192.168.5.30"), ipaddress.IPv4Address("192.168.5.10")),
            ProbeTarget(ipaddress.IPv4Address("192.168.5.3"), ipaddress.IPv4Address("192.168.5.10")),
            ProbeTarget(ipaddress.IPv4Address("192.168.5.2"), ipaddress.IPv4Address("192.168.5.20")),
        ]

        results = probe_tcp_targets(
            targets,
            ImmediateRetryEvent(),
            timeout=0.4,
            max_workers=2,
            socket_factory=FakeSocket,
        )

        self.assertEqual(
            [(str(item.ip), str(item.source_ip)) for item in results],
            [
                ("192.168.5.2", "192.168.5.20"),
                ("192.168.5.30", "192.168.5.10"),
            ],
        )
        self.assertEqual(len(created), 4)
        self.assertTrue(all(item.timeout == 0.4 for item in created))
        self.assertTrue(all(item.bound[1] == 0 for item in created))
        self.assertTrue(all(item.closed for item in created))

    def test_cancelled_probe_creates_no_sockets(self):
        cancel_event = threading.Event()
        cancel_event.set()
        created = []

        def socket_factory():
            created.append(object())
            raise AssertionError("取消后不应创建 socket")

        results = probe_tcp_targets(
            [ProbeTarget(ipaddress.IPv4Address("192.168.5.2"), ipaddress.IPv4Address("192.168.5.10"))],
            cancel_event,
            socket_factory=socket_factory,
        )

        self.assertEqual(results, [])
        self.assertEqual(created, [])


class WindowsAdapterProviderTests(unittest.TestCase):
    def test_native_enumeration_rejects_non_windows_platform(self):
        with mock.patch("mimonitor_toolbox.network_scan.sys.platform", "linux"):
            with self.assertRaisesRegex(WindowsAdapterError, "仅支持 Windows"):
                enumerate_windows_adapter_addresses()

    def test_injected_provider_still_uses_physical_adapter_filter(self):
        records = [
            raw("192.168.5.10", index=1, name="以太网"),
            raw("10.0.0.2", index=2, name="Wintun", if_type=6, hardware=False, metric=1),
        ]

        networks = get_windows_scan_networks(adapter_provider=lambda: records)

        self.assertEqual(
            [(str(item.network), item.interface_name) for item in networks],
            [("192.168.5.0/24", "以太网")],
        )


if __name__ == "__main__":
    unittest.main()
