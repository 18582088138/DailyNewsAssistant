"""
TTS service 的 HTTP 客户端 / The HTTP client for the TTS service.

只做「发请求、认状态码、把 JSON 变成 dict」这一件事。**没有任何业务判断**——
音色怎么选、失败要不要重试、产物往哪拷，都在 `service.py` 与 `produce/` 里。
Wire-level only: requests, status codes and JSON. No policy lives here.

服务端契约（Agent_TTS_Module）/ The service contract:
    GET  /health                  在不在线
    GET  /info                    引擎、设备、精度（记进台账）
    GET  /voices                  可用音色
    POST /tts/synthesize          **单段**合成，`encoding=base64` 直接回音频
    POST /gui/handoff             把稿子交给 TTS 图形界面精修
    GET  /gui/handoff/{token}     查那张交接单（生成完会写回产物清单）
    GET  /outputs/{run}/{file}    取产物

为什么本机地址要关掉代理 / Why the proxy is bypassed for local addresses:
    公司网络下 `trust_env=True` 会把 `127.0.0.1` 的请求也送去代理，代理再回
    502 —— 报错看起来像「TTS 服务挂了」，而服务其实好得很。
    `sources/http.py` 里踩过同一个坑，这里沿用同样的处置。
    With a corporate proxy, loopback requests get routed to it and come back 502, which
    reads as "the TTS service is down" when it is perfectly healthy.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dna.core.logging import get_logger
from dna.tts.base import TTSError

logger = get_logger("tts.client")

# 探活要**快**：界面上「服务在不在」这个问题不能让人等 20 秒
# Liveness must be fast; the UI cannot wait twenty seconds to learn the service is down.
HEALTH_TIMEOUT = 3.0

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


def is_local_url(url: str) -> bool:
    """这个地址是不是本机 / Whether the address is on this machine."""
    return (urlparse(url).hostname or "") in _LOCAL_HOSTS


class TTSServiceClient:
    """
    一个 TTS service 的连接 / One connection to a TTS service.

    无状态、可反复构造；`httpx.Client` 每次调用现开现关 —— 合成一段要几十秒到几分钟，
    长连接省下的那几毫秒毫无意义，而一个常驻的 client 会在服务重启后带着死连接。
    Stateless by design: a persistent client would hold a dead connection across a
    service restart, and the saved handshake is noise next to a minute of synthesis.
    """

    def __init__(self, base_url: str, *, timeout: float = 900.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.trust_env = not is_local_url(self.base_url)

    # ------------------------------------------------------------ 查询 / info

    def health(self, timeout: float = HEALTH_TIMEOUT) -> bool:
        """在线吗 / Is it up? 任何异常都算不在线（这就是这个方法要回答的问题）。"""
        try:
            return self._get("/health", timeout=timeout).get("status") == "ok"
        except Exception:  # noqa: BLE001 - 连不上、超时、502 都只说明「不在线」
            return False

    def info(self) -> dict[str, Any]:
        """引擎身份 / Engine identity —— 记进台账的那份。"""
        return self._get("/info", timeout=30.0)

    def speakers(self) -> list[str]:
        """可用音色名 / Available voice names（内置 speaker + 服务端档案）。"""
        body = self._get("/voices", timeout=30.0)
        return [v["name"] for v in body.get("voices", [])]

    # ------------------------------------------------------- 合成 / synthesis

    def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "Chinese",
        instruct: str | None = None,
        role: str | None = None,
        run: str | None = None,
        save: bool = True,
        mode: str | None = None,
        ref_audio: str | None = None,
        ref_text: str | None = None,
        x_vector_only: bool = False,
    ) -> dict[str, Any]:
        """
        合成一段 / Synthesise one piece，返回服务端的元信息 + `wav` 字节。

        走**单段**端点而不是 `/tts/batch`：一次一段才能逐段报进度、逐段容错，
        而整批一个请求下去，界面只能干等，中间失败一段就整批失败。
        The one-piece endpoint is what makes per-piece progress and per-piece failure
        tolerance possible; a single batch request offers neither.
        """
        payload: dict[str, Any] = {
            "text": text,
            "language": language,
            "encoding": "base64",
            "save": save,
        }
        if voice:
            payload["voice"] = voice
        if instruct:
            payload["instruct"] = instruct
        if role:
            payload["role"] = role
        if run:
            payload["name"] = run
        if mode:
            payload["mode"] = mode
        if ref_audio:
            payload["ref_audio"] = self._ref_audio_payload(ref_audio)
            payload["x_vector_only"] = bool(x_vector_only)
            if ref_text:
                payload["ref_text"] = ref_text

        body = self._post("/tts/synthesize", payload, timeout=self.timeout)
        encoded = body.pop("audio_base64", "")
        if not encoded:
            raise TTSError("TTS 服务没有回音频（检查服务端日志）")
        body["wav"] = base64.b64decode(encoded)
        return body

    def _ref_audio_payload(self, ref_audio: str) -> str:
        """
        参考音频怎么送过去 / How the reference audio travels.

        **本机服务传路径，远程服务传 base64。** 路径是在**服务端**解析的：
        服务在另一台机器上时，这边的 `C:/…/ref_audio/x.wav` 在那边根本不存在，
        而报出来的错会是「参考音频不存在」—— 看起来像文件丢了，其实是机器不对。
        The path is resolved server-side, so a remote service would report "file not
        found" for a file that exists here, which reads as a missing file rather than
        the wrong machine.

        服务端只认本地路径与 `data:audio` base64（URL 会被它拒绝，因为上游的
        加载器不读代理配置）。
        """
        if self.trust_env:      # 非本机 = 远程，把字节带过去
            path = Path(ref_audio)
            if path.is_file():
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                suffix = "mpeg" if path.suffix.lower() == ".mp3" else "wav"
                logger.debug("远程服务，参考音频改用 base64 传输：%s", path.name)
                return f"data:audio/{suffix};base64,{encoded}"
        return ref_audio

    # ------------------------------------------------- 交接给界面 / hand-off

    def handoff(
        self,
        segments: list[str],
        *,
        title: str = "",
        source: str = "DailyNewsAssistant",
        voice: str | None = None,
        instruct: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        把稿子交给 TTS 图形界面 / Hand the script to the TTS workbench.

        返回 `{"token", "gui_url"}`：打开那个 URL，文本已经填在多段界面里。
        """
        return self._post("/gui/handoff", {
            "segments": segments, "title": title, "source": source,
            "voice": voice, "instruct": instruct, "meta": meta or {},
        }, timeout=30.0)

    def handoff_state(self, token: str) -> dict[str, Any]:
        """查交接单 / Poll one hand-off record（`status` 变 `done` 就有产物了）。"""
        return self._get(f"/gui/handoff/{token}", timeout=30.0)

    def download(self, relative: str, dest: Path) -> Path:
        """
        取一个产物 / Fetch one produced file，`relative` 形如 `gui-xxx/merged.wav`。

        走 HTTP 而不是直接读盘：服务可能在另一台机器上，那边的路径在这里不存在。
        Over HTTP rather than by path: the service may be on another machine.
        """
        import httpx

        url = f"{self.base_url}/outputs/{relative.lstrip('/')}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with httpx.Client(timeout=120.0, trust_env=self.trust_env) as http:
                response = http.get(url)
                response.raise_for_status()
                dest.write_bytes(response.content)
        except Exception as exc:  # noqa: BLE001 - 统一成 TTSError 交给上层显示
            raise TTSError(f"取产物失败 {relative}：{exc}") from exc
        return dest

    # ------------------------------------------------------ 内部 / internals

    def _get(self, path: str, *, timeout: float) -> dict[str, Any]:
        return self._request("GET", path, None, timeout)

    def _post(self, path: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        return self._request("POST", path, payload, timeout)

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None, timeout: float
    ) -> dict[str, Any]:
        """
        发一次请求 / Issue one request.

        **服务端的错误原文一定要带出来。** 它已经把原因分好类了（400 改参数、
        409 换引擎、422 合成失败、503 环境），把这些压成一句「TTS 调用失败」
        等于把唯一有用的信息扔掉。
        The service already classifies its failures; flattening them into a generic
        message would discard the only useful part.
        """
        import httpx

        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=timeout, trust_env=self.trust_env) as http:
                response = http.request(method, url, json=payload)
        except httpx.HTTPError as exc:
            raise TTSError(f"连不上 TTS 服务 {self.base_url}：{exc}") from exc

        if response.status_code >= 400:
            raise TTSError(f"TTS 服务返回 {response.status_code}：{_detail(response)}")
        try:
            return response.json()
        except ValueError as exc:
            raise TTSError(f"TTS 服务返回的不是 JSON：{response.text[:200]}") from exc


def _detail(response: Any) -> str:
    """取服务端的 detail 字段 / Pull the service's own error message out."""
    try:
        body = response.json()
        return str(body.get("detail") or body)[:300]
    except ValueError:
        return response.text[:300]


__all__ = ["HEALTH_TIMEOUT", "TTSServiceClient", "is_local_url"]
