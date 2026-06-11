#!/usr/bin/env python3
"""
MTProto Tunnel — Telegram Bypass Tool for Censored VPS
========================================================
Routes Telegram MTProto traffic through an external proxy,
bypassing ISP-level IP blocks. Supports MTProto proxies AND
Tor SOCKS5 as automatic fallback.

Protocol:  MTProto (native Telegram) through proxy → Telegram DCs
Fallback:  Tor SOCKS5 proxy (127.0.0.1:9050)

Requirements:
    pip install telethon cryptg python-socks

Quick start:
    export TG_PROXY_SERVER="100.100.100.100"
    export TG_PROXY_PORT="443"
    export TG_PROXY_SECRET="00...your_secret...00"
    export TG_BOT_TOKEN="123456:ABCdef..."
    python3 mtproto_tunnel.py ping

Tor fallback:
    apt install tor obfs4proxy
    systemctl start tor
    python3 mtproto_tunnel.py --tor send @user "Hello"

Author: Hermes Agent
"""

import os
import sys
import asyncio
import argparse
import base64
import json
import logging
import time
from typing import Optional

# ── Telethon imports ──────────────────────────────────────────────
try:
    from telethon import TelegramClient
    from telethon.network import (
        ConnectionTcpMTProxyRandomizedIntermediate,
        ConnectionTcpMTProxyAbridged,
        ConnectionTcpMTProxyIntermediate,
    )
    from telethon.errors import RPCError
except ImportError:
    print("ERROR: telethon not installed. Run: pip install telethon cryptg python-socks")
    sys.exit(1)

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mtproto_tunnel")


# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════

DEFAULT_PROXY = {
    "server": "100.100.100.100",  # Replace with your proxy IP
    "port": 443,
    "secret": "00...your_secret...00",  # Replace with your proxy secret
}


def get_config():
    """Read configuration from environment."""
    return {
        "proxy_server": os.getenv("TG_PROXY_SERVER", DEFAULT_PROXY["server"]),
        "proxy_port": int(os.getenv("TG_PROXY_PORT", str(DEFAULT_PROXY["port"]))),
        "proxy_secret": os.getenv("TG_PROXY_SECRET", DEFAULT_PROXY["secret"]),
        "bot_token": os.getenv("TG_BOT_TOKEN", ""),
        "api_id": int(os.getenv("TG_API_ID", "0") or "0"),
        "api_hash": os.getenv("TG_API_HASH", ""),
        "tor_socks": os.getenv("TG_TOR_SOCKS", "127.0.0.1:9050"),
    }


# ═══════════════════════════════════════════════════════════════════
# Secret / Proxy helpers
# ═══════════════════════════════════════════════════════════════════

def parse_proxy_secret(raw: str) -> tuple[bytes, str | None]:
    """Parse MTProto proxy secret (hex or base64). Returns (secret_16B, domain_or_none)."""
    raw = raw.strip()
    decoded = None

    if all(c in "0123456789abcdefABCDEF" for c in raw) and len(raw) >= 32:
        try:
            decoded = bytes.fromhex(raw)
        except ValueError:
            pass

    if decoded is None:
        for decoder in [base64.b64decode, base64.urlsafe_b64decode]:
            try:
                padding = 4 - len(raw) % 4
                padded = raw + "=" * padding if padding != 4 else raw
                decoded = decoder(padded)
                break
            except Exception:
                continue

    if decoded is None or len(decoded) < 16:
        raise ValueError(f"Cannot parse secret: {raw!r}")

    secret = decoded[:16]
    domain = None
    if len(decoded) > 17:
        try:
            domain = decoded[17:].decode("ascii")
        except UnicodeDecodeError:
            domain = None
    return secret, domain


def make_telethon_secret(secret_16: bytes) -> str:
    """Format 16-byte secret for telethon (handles ee/dd prefix stripping)."""
    if secret_16[0] in (0xEE, 0xDD):
        return "dd" + secret_16.hex()
    return secret_16.hex()


# ═══════════════════════════════════════════════════════════════════
# Telegram Client
# ═══════════════════════════════════════════════════════════════════

class TelegramBypassClient:
    """
    Telegram client with automatic proxy selection.

    Priority:
      1. MTProto proxy (direct tunnel)
      2. Tor SOCKS5 (fallback)
    """

    def __init__(self, config: dict, use_tor: bool = False):
        self.config = config
        self.use_tor = use_tor
        self._secret_16: Optional[bytes] = None
        self._domain: Optional[str] = None
        self._client: Optional[TelegramClient] = None
        self._mode: str = "unknown"

    def _parse_secret(self):
        if self._secret_16 is not None:
            return
        self._secret_16, self._domain = parse_proxy_secret(
            self.config["proxy_secret"]
        )

    def _build_mtproto_client(self) -> Optional[TelegramClient]:
        """Build client with MTProto proxy transport."""
        self._parse_secret()
        try:
            proxy = (
                self.config["proxy_server"],
                self.config["proxy_port"],
                make_telethon_secret(self._secret_16),
            )
            log.info("MTProto proxy: %s:%s domain=%s",
                     proxy[0], proxy[1], self._domain or "none")

            return TelegramClient(
                session="tg_bypass_mtp",
                api_id=self.config["api_id"] or 6,
                api_hash=self.config["api_hash"] or "eb06d4abfb49dc3eeb1aeb98ae0f581e",
                connection=ConnectionTcpMTProxyRandomizedIntermediate,
                proxy=proxy,
            )
        except Exception as e:
            log.warning("MTProto client setup failed: %s", e)
            return None

    def _build_tor_client(self) -> TelegramClient:
        """Build client with Tor SOCKS5 proxy."""
        tor = self.config["tor_socks"]
        proxy = ("socks5", tor.split(":")[0], int(tor.split(":")[1]))
        log.info("Tor SOCKS5: %s:%s", proxy[1], proxy[2])

        return TelegramClient(
            session="tg_bypass_tor",
            api_id=self.config["api_id"] or 6,
            api_hash=self.config["api_hash"] or "eb06d4abfb49dc3eeb1aeb98ae0f581e",
            proxy=proxy,
        )

    async def connect(self) -> bool:
        """Try all available transports. Returns True on first success."""
        t0 = time.monotonic()

        # 1. Try MTProto proxy (unless --tor forced)
        if not self.use_tor:
            client = self._build_mtproto_client()
            if client:
                try:
                    log.info("Trying MTProto proxy...")
                    await asyncio.wait_for(client.connect(), timeout=8)
                    if await client.is_connected():
                        self._client = client
                        self._mode = "mtproto"
                        await self._auth()
                        return True
                except asyncio.TimeoutError:
                    log.warning("MTProto proxy timed out")
                except Exception as e:
                    log.warning("MTProto proxy failed: %s", e)

        # 2. Fall back to Tor
        try:
            log.info("Falling back to Tor...")
            client = self._build_tor_client()
            await asyncio.wait_for(client.connect(), timeout=30)
            if await client.is_connected():
                self._client = client
                self._mode = "tor"
                await self._auth()
                return True
        except asyncio.TimeoutError:
            log.error("Tor connection timed out")
        except Exception as e:
            log.error("Tor connection failed: %s", e)

        elapsed = time.monotonic() - t0
        log.error("All transports failed after %.1fs", elapsed)
        return False

    async def _auth(self):
        """Authenticate with bot token."""
        if not await self._client.is_user_authorized():
            token = self.config["bot_token"]
            if token:
                await self._client.sign_in(bot_token=token)
            else:
                raise RuntimeError("TG_BOT_TOKEN is required")

    async def disconnect(self):
        if self._client:
            await self._client.disconnect()

    async def send_message(self, chat_id, text: str) -> dict:
        """Send a message. Auto-connects if needed."""
        if not self._client or not self._client.is_connected():
            ok = await self.connect()
            if not ok:
                return {"ok": False, "error": "All transports failed"}

        try:
            msg = await self._client.send_message(chat_id, text)
            return {
                "ok": True,
                "mode": self._mode,
                "message_id": msg.id,
                "chat_id": str(chat_id),
                "text_preview": text[:100],
            }
        except RPCError as e:
            return {"ok": False, "mode": self._mode, "error": str(e)}

    async def ping(self) -> dict:
        """Test connectivity through all available transports."""
        ok = await self.connect()
        if not ok:
            return {
                "ok": False,
                "error": "All transports failed",
                "proxy": f"{self.config['proxy_server']}:{self.config['proxy_port']}",
                "tor": self.config["tor_socks"],
            }

        try:
            me = await self._client.get_me()
            return {
                "ok": True,
                "mode": self._mode,
                "username": f"@{me.username}" if me.username else str(me.id),
                "first_name": me.first_name,
                "proxy": f"{self.config['proxy_server']}:{self.config['proxy_port']}",
                "tor": self.config["tor_socks"],
            }
        except Exception as e:
            return {"ok": False, "mode": self._mode, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# CLI Commands
# ═══════════════════════════════════════════════════════════════════

async def cmd_ping(args):
    """Test all transports."""
    cfg = get_config()
    client = TelegramBypassClient(cfg, use_tor=args.tor)
    try:
        result = await client.ping()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["ok"] else 1)
    finally:
        await client.disconnect()


async def cmd_send(args):
    """Send a message."""
    cfg = get_config()
    if not cfg["bot_token"]:
        print("ERROR: TG_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    client = TelegramBypassClient(cfg, use_tor=args.tor)
    try:
        result = await client.send_message(args.chat_id, args.text)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["ok"] else 1)
    finally:
        await client.disconnect()


async def cmd_status(args):
    """Show current config and test connectivity."""
    cfg = get_config()
    print("Configuration:")
    print(f"  Proxy:  {cfg['proxy_server']}:{cfg['proxy_port']}")
    print(f"  Secret: {cfg['proxy_secret'][:16]}...")
    print(f"  Tor:    {cfg['tor_socks']}")
    print(f"  Token:  {'SET' if cfg['bot_token'] else 'NOT SET'}")
    print()
    if args.test:
        print("Testing connectivity...")
        client = TelegramBypassClient(cfg, use_tor=args.tor)
        try:
            result = await client.ping()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            await client.disconnect()


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def build_parser():
    p = argparse.ArgumentParser(
        description="MTProto Tunnel — Telegram bypass for censored VPS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  export TG_BOT_TOKEN="123456:ABCdef..."

  # Test all transports (MTProto proxy → Tor fallback)
  python3 mtproto_tunnel.py ping

  # Send message
  python3 mtproto_tunnel.py send @username "Hello from blocked VPS!"

  # Force Tor mode
  python3 mtproto_tunnel.py --tor ping

  # Show config
  python3 mtproto_tunnel.py status

Environment:
  TG_PROXY_SERVER  MTProto proxy host (default from DEFAULT_PROXY)
  TG_PROXY_PORT    MTProto proxy port (default: 443)
  TG_PROXY_SECRET  MTProto proxy secret (hex or base64 encoded)
  TG_BOT_TOKEN     Telegram bot token from @BotFather
  TG_TOR_SOCKS     Tor SOCKS5 address (default: 127.0.0.1:9050)
        """,
    )
    p.add_argument("--tor", action="store_true",
                   help="Force Tor mode (skip MTProto proxy)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable debug logging")

    sub = p.add_subparsers(dest="command", help="Commands")

    sp = sub.add_parser("ping", help="Test proxy connectivity")
    sp.set_defaults(func=cmd_ping)

    sp = sub.add_parser("send", help="Send a message")
    sp.add_argument("chat_id", help="Chat ID, @username, or phone")
    sp.add_argument("text", help="Message text")
    sp.set_defaults(func=cmd_send)

    sp = sub.add_parser("status", help="Show config and optionally test")
    sp.add_argument("--test", action="store_true", help="Test connectivity")
    sp.set_defaults(func=cmd_status)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
