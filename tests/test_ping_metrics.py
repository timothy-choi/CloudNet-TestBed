from app.services.ping_metrics import extract_icmp_latencies_ms, p95_latency_ms


def test_extract_icmp_latencies_from_linux_style_ping() -> None:
    out = """
PING 10.0.0.2 (10.0.0.2) 56(84) bytes of data.
64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=12.3 ms
64 bytes from 10.0.0.2: icmp_seq=2 ttl=64 time=15.1 ms
64 bytes from 10.0.0.2: icmp_seq=3 ttl=64 time=14.8 ms
"""
    lat = extract_icmp_latencies_ms(out)
    assert lat == [12.3, 15.1, 14.8]


def test_extract_icmp_latencies_rtt_summary_fallback() -> None:
    out = "rtt min/avg/max/mdev = 0.100/0.200/0.300/0.050 ms"
    lat = extract_icmp_latencies_ms(out)
    assert lat == [0.2]


def test_p95_simple() -> None:
    assert p95_latency_ms([10.0, 20.0, 30.0, 40.0, 100.0]) == 100.0
