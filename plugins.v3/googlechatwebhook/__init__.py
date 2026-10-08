from typing import Any

from app.schemas.types import EventType, NotificationType
from app.sdk import scheduler as scheduler_sdk
from app.sdk.events import Event, eventmanager
from app.sdk.logging import logger
from app.sdk.network import AsyncRequestUtils
from app.sdk.plugin import _PluginBase


class GoogleChatWebhook(_PluginBase):
    """监听 MoviePilot 的消息通知事件，并转发到 Google Chat Webhook。"""

    plugin_name = "GoogleChat Webhook"
    plugin_desc = "消息通知转发到GoogleChat"
    plugin_icon = "https://raw.githubusercontent.com/umuchan/MoviePilot-Plugins/main/icons/Google_A.png"
    plugin_version = "3.0.0"
    plugin_author = "Claude"
    author_url = "https://github.com/UmUchan"
    plugin_config_prefix = "googlechat_webhook_"
    plugin_order = 30
    auth_level = 1

    # Google Chat Webhook 固定域名前缀，用来挡住误填的其它地址
    _WEBHOOK_PREFIX = "https://chat.googleapis.com/"
    # Google Chat 单条文本上限约 4096 字符，留出余量
    _MAX_LENGTH = 4000
    # 一次性测试任务 ID，stop_service 时用它取消未执行的任务
    _TEST_JOB_ID = "googlechat_test_once"

    _enabled = False
    _google_chat_url = ""
    _msgtypes: list[str] = []

    def init_plugin(self, config: dict | None = None) -> None:
        """读取配置；勾选“保存并测试”时交给宿主调度器延迟发送一条测试消息。"""
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._google_chat_url = str(config.get("google_chat_url") or "").strip()
        self._msgtypes = list(config.get("msgtypes") or [])

        # Webhook 地址带有密钥，只接受官方域名；仅在运行态清空，不改动用户已保存的输入
        if self._google_chat_url and not self._google_chat_url.startswith(self._WEBHOOK_PREFIX):
            logger.error(f"[GoogleChat] Webhook URL 必须以 {self._WEBHOOK_PREFIX} 开头，插件暂不生效")
            self._google_chat_url = ""

        if config.get("onlyonce"):
            if self.get_state():
                added = scheduler_sdk.add_plugin_once_job(
                    self.__class__.__name__,
                    self._TEST_JOB_ID,
                    self._send_test,
                    "GoogleChat 测试发送",
                    delay_seconds=3,
                )
                if not added:
                    logger.warning("[GoogleChat] 调度器未运行，测试消息未能安排")
            else:
                logger.warning("[GoogleChat] 插件未启用或 Webhook 未配置，已跳过测试发送")
            # 复位开关并写回，避免每次重载都重复发送测试消息
            config["onlyonce"] = False
            self.update_config(config)

    def get_state(self) -> bool:
        """已启用且 Webhook 有效时才算运行中。"""
        return self._enabled and bool(self._google_chat_url)

    @eventmanager.register(EventType.NoticeMessage)
    async def send(self, event: Event) -> None:
        """收到通知事件后按消息类型过滤，并转发到 Google Chat。"""
        if not self.get_state():
            return

        data = event.event_data
        # 带 channel 的是发给某个具体渠道的交互回复，不属于广播通知，跳过
        if not data or data.get("channel"):
            return

        type_name = getattr(data.get("type"), "name", None)
        if type_name and self._msgtypes and type_name not in self._msgtypes:
            return

        await self._push(data.get("title"), data.get("text"))

    async def _send_test(self) -> None:
        """发送一条测试消息，用来验证 Webhook 配置。"""
        await self._push("GoogleChat 通知测试", "✅ 配置正确，之后的通知会转发到这里。")

    @staticmethod
    def _build_content(title: Any, text: Any) -> str:
        """组装消息文本：粗体标题 + 换行 + 内容，跳过空值和字符串 None。"""
        parts = []
        title = str(title or "").strip()
        text = str(text or "").strip()
        if title and title.lower() != "none":
            parts.append(f"*{title}*")
        if text and text.lower() != "none":
            parts.append(text)
        return "\n".join(parts)

    async def _push(self, title: Any, text: Any) -> bool:
        """向 Webhook 发送一条文本消息，返回是否成功；失败只记日志，不抛异常。"""
        content = self._build_content(title, text)
        if not content:
            return False
        if len(content) > self._MAX_LENGTH:
            content = content[: self._MAX_LENGTH] + "…"

        try:
            res = await AsyncRequestUtils(timeout=10).post_res(
                self._google_chat_url, json={"text": content}
            )
        except Exception as err:
            logger.error(f"[GoogleChat] 推送异常：{err}")
            return False

        if res is None:
            logger.error("[GoogleChat] 推送失败：没有收到响应（网络、代理或超时）")
            return False
        if res.status_code != 200:
            logger.error(f"[GoogleChat] 推送失败：HTTP {res.status_code} {res.text[:200]}")
            return False
        return True

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        """本插件不注册远程命令。"""
        return []

    def get_api(self) -> list[dict[str, Any]]:
        """本插件不注册后端 API。"""
        return []

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        """返回配置页面和默认配置。"""
        msg_type_options = [
            {"title": item.value, "value": item.name} for item in NotificationType
        ]
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "onlyonce", "label": "保存并测试"},
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "google_chat_url",
                                            "label": "Webhook URL",
                                            "placeholder": "https://chat.googleapis.com/v1/spaces/...",
                                            "hint": "在 Google Chat 空间的“应用和集成”中创建 Webhook",
                                            "persistent-hint": True,
                                            "clearable": True,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "multiple": True,
                                            "chips": True,
                                            "model": "msgtypes",
                                            "label": "消息类型",
                                            "hint": "不选表示转发全部类型",
                                            "persistent-hint": True,
                                            "items": msg_type_options,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "google_chat_url": "",
            "msgtypes": [],
        }

    def get_page(self) -> list[dict]:
        """本插件没有详情页。"""
        return []

    def stop_service(self) -> None:
        """取消尚未执行的测试任务；插件没有自建线程或客户端。"""
        try:
            scheduler_sdk.remove_plugin_once_job(self.__class__.__name__, self._TEST_JOB_ID)
        except Exception as err:
            logger.debug(f"[GoogleChat] 取消测试任务时忽略异常：{err}")
        self._enabled = False
