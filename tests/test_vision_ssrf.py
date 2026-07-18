"""讀圖員 SSRF 守門測試（對抗健檢 2026-07-18）：_is_safe_public_url 該擋的都擋、公開位址放行。

用「字面 IP」而非網域，避免測試依賴實際 DNS／連外；驗的是網段判斷與 scheme 過濾這一層。
（已知限制：對 redirect／DNS rebinding 的 TOCTOU 不在此防線內，見 vision.py 註解。）
"""
from nodes.vision import _is_safe_public_url


def test_blocks_non_http_schemes():
    assert _is_safe_public_url("file:///etc/passwd") is False
    assert _is_safe_public_url("ftp://example.com/x.png") is False
    assert _is_safe_public_url("gopher://x") is False
    assert _is_safe_public_url("not-a-url") is False
    assert _is_safe_public_url("") is False


def test_blocks_loopback_and_localhost():
    assert _is_safe_public_url("http://127.0.0.1/x.png") is False
    assert _is_safe_public_url("http://[::1]/x.png") is False


def test_blocks_private_networks():
    assert _is_safe_public_url("http://10.0.0.5/x.png") is False
    assert _is_safe_public_url("http://172.16.0.1/x.png") is False
    assert _is_safe_public_url("http://192.168.1.1/x.png") is False


def test_blocks_cloud_metadata_link_local():
    # 雲端中繼資料位址（AWS/GCP 等）——SSRF 最常見的攻擊目標
    assert _is_safe_public_url("http://169.254.169.254/latest/meta-data/") is False


def test_allows_public_ip():
    # 字面公開 IP（8.8.8.8）→ 不需 DNS 即可判定為公開、放行
    assert _is_safe_public_url("http://8.8.8.8/x.png") is True
    assert _is_safe_public_url("https://8.8.8.8/x.png") is True
