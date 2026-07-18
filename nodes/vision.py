"""讀圖員（圖片三件套，Adam 2026-07-17 拍板）。

用戶附圖 → 本節點抓圖、轉 base64、交視覺模型（role=vision，config/models.toml）出文字描述；
描述併進 user_message 後交原本的分診腦——決策與寫手完全不動，讀圖員只當翻譯。

防線：
- 每輪最多 3 張、單張 6MB、抓圖逾時 8 秒（每日 10 張/人的額度由 HiSupport 把關）。
- 任何一步失敗都不炸整輪：回空字串，orchestrator 附註「圖片無法讀取」照常走文字流程。
"""
from __future__ import annotations

import base64
import ipaddress
import logging
import socket
import urllib.parse
import urllib.request
from pathlib import Path

from core.llm_client import call_vision

logger = logging.getLogger(__name__)

MAX_IMAGES_PER_TURN = 3
MAX_IMAGE_BYTES = 6 * 1024 * 1024
FETCH_TIMEOUT = 8

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "vision_describe.txt"

# 副檔名 → MIME（Content-Type 缺席時的退路）
_EXT_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}


def _is_safe_public_url(url: str) -> bool:
    """SSRF 第二道防線（對抗健檢 2026-07-17）：只放行 http/https，且目標解析後不得落在私有／保留網段。

    上游本已把訪客圖網址鎖在自家 storage（HiSupport SelfStorageImageUrl）＋ /api/chat 需金鑰，
    但讀圖員自己不能零防護：一旦金鑰外洩或忘了設，urlopen 預設吃 file://／能連內網中繼資料位址。
    這裡當守門，擋掉 file://、localhost、10./172.16-31./192.168./169.254.（雲端中繼資料）等。

    ⚠️ 已知限制（對抗健檢 2026-07-18，非完整防線）：本檢查在此解析一次 DNS，urlopen 實際連線時會
    再解析一次、且預設跟隨 redirect——理論上可被 DNS rebinding／302 導向繞過（TOCTOU）。目前打不通，
    因為圖網址被上游鎖死自家 storage、主機名非攻擊者可控。**若日後開放非自家網址的圖片來源
    （FB/LINE CDN 等），要改成「解析一次→直接對該 IP 連線＋帶原 Host」或關掉 redirect 才算真正堵死。**
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        # 解析所有 A/AAAA，任一落在私有／保留網段就擋（涵蓋 DNS 指向內網的情況）
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception as e:  # noqa: BLE001 — 解析失敗＝當作不安全,不讀
        logger.warning("圖片網址安全檢查失敗(%s):%s", url, e)
        return False


def _fetch_as_data_uri(url: str) -> str | None:
    """抓一張圖轉 data URI；失敗回 None（記 log、不丟例外）。"""
    if not _is_safe_public_url(url):
        logger.warning("圖片網址未通過安全檢查(非公開 http/https),跳過:%s", url)
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HiBot-Vision/1.0"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            mime = (r.headers.get("Content-Type") or "").split(";")[0].strip()
            data = r.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            logger.warning("圖片超過 %dMB 上限,跳過:%s", MAX_IMAGE_BYTES // (1024 * 1024), url)
            return None
        if not mime.startswith("image/"):
            mime = _EXT_MIME.get(Path(url.split("?")[0]).suffix.lower(), "image/png")
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception as e:  # noqa: BLE001 — 讀圖失敗=退化為純文字,絕不擋客服主線
        logger.warning("抓圖失敗(%s):%s", url, e)
        return None


def describe_images(image_urls: list[str]) -> str:
    """把圖片清單轉成文字描述（多張依序標號）。全數失敗＝回空字串。"""
    uris = []
    for url in image_urls[:MAX_IMAGES_PER_TURN]:
        uri = _fetch_as_data_uri(url)
        if uri:
            uris.append(uri)
    if not uris:
        return ""

    prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    if len(uris) > 1:
        prompt += f"\n（共 {len(uris)} 張圖）"
    desc = call_vision(prompt, uris, max_tokens=400, fallback="")
    return desc.strip()
