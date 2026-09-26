"""Model API keys in the OS credential store (Keychain, Credential Manager,
Secret Service). Keys never go into config files, sessions or logs."""
import logging

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "ftth-demo0"
NAMES = {"model": "主模型", "review": "独立审查模型"}
log = logging.getLogger("ftth.credentials")


def backend():
    store = keyring.get_keyring()
    name = type(store).__module__ + "." + type(store).__name__
    usable = getattr(store, "priority", 0) > 0 and "fail" not in name and "null" not in name
    return usable, name


def get(name):
    try:
        return keyring.get_password(SERVICE, name) or ""
    except (KeyringError, OSError) as exc:
        # A locked or absent store (e.g. a headless Linux session) means "no key".
        log.warning("credential store unavailable: %s", type(exc).__name__)
        return ""


def store(name, value):
    if name not in NAMES or not value:
        raise ValueError("请提供非空密钥；可用名称：" + "、".join(NAMES))
    usable, backend_name = backend()
    if not usable:
        raise ValueError(f"系统凭据库不可用（{backend_name}）；请改用环境变量 FTTH_API_KEY")
    keyring.set_password(SERVICE, name, value)


def delete(name):
    try:
        keyring.delete_password(SERVICE, name)
        return True
    except PasswordDeleteError:
        return False
