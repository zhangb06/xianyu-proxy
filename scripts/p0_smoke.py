"""P0 冒烟：扫码登录 → Cookie 加密落库 → 连 WS → 收消息 → 发消息。

用法：
    python scripts/p0_smoke.py login
    python scripts/p0_smoke.py listen --account latest [--reply 测试] [--count 1]
    python scripts/p0_smoke.py listen --count 3 --record data/fixtures/sync.jsonl
    python scripts/p0_smoke.py send --to <buyer_id> --text "测试"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import qrcode  # noqa: E402
import qrcode.constants  # noqa: E402
from loguru import logger  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.core.secrets import resolve_secret  # noqa: E402
from app.db.engine import Database  # noqa: E402
from app.db.repo.accounts import Account, AccountRepo  # noqa: E402
from app.db.repo.events import log_event  # noqa: E402
from app.platform import make_paths  # noqa: E402
from app.protocol.mtop import CookieJar, MtopError  # noqa: E402
from app.protocol.qrlogin import QRLogin, save_png  # noqa: E402
from app.protocol.token import fetch_im_token  # noqa: E402
from app.protocol.ws import XianyuWS, reject_reason  # noqa: E402


async def _open() -> tuple[Settings, Any, Database, AccountRepo]:
    settings = get_settings()
    paths = make_paths(settings.data_dir).ensure()
    setup_logging(settings.log_level, paths.logs / "p0.log")
    db = Database(paths.db)
    await db.connect()
    secret = await resolve_secret(db, settings.secret_key)
    return settings, paths, db, AccountRepo(db, secret)


def _print_qr(content: str) -> None:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=1, border=2)
    qr.add_data(content)
    qr.make(fit=True)
    try:
        qr.print_ascii(invert=True)
    except (UnicodeEncodeError, OSError):
        print("当前终端无法显示二维码，请打开保存的 PNG 文件扫码")


async def _pick_account(repo: AccountRepo, ref: str) -> Account | None:
    if ref == "latest":
        return await repo.latest()
    if ref.isdigit():
        return await repo.get(int(ref))
    return await repo.get_by_xianyu_id(ref)


async def cmd_login(args: argparse.Namespace) -> int:
    settings, paths, db, repo = await _open()
    try:
        client = QRLogin(timeout=settings.http_timeout)
        session = await client.generate()
        png_path = paths.qr / "login.png"
        save_png(session, png_path)
        _print_qr(session.code_content)
        print(f"二维码文件: {png_path}")
        print("请用手机闲鱼扫码并在手机上确认登录 ...")

        status = await client.wait(session, timeout=args.timeout)
        if status == "verification_required":
            await log_event(
                db,
                type="login.verification_required",
                level="warning",
                detail={"url": session.verification_url or ""},
            )
            print(f"需要人脸验证，请用手机打开: {session.verification_url}")
            return 2
        if status != "success":
            print(f"登录未完成: {status}")
            return 1

        unb = session.unb or str(session.jar.get("unb") or "")
        if not unb:
            print("登录成功但未取到 unb，Cookie 不完整")
            return 1

        account = await repo.upsert(unb)
        await repo.save_cookie(account.id, session.jar.as_dict())
        await repo.set_status(account.id, "online")
        await log_event(db, type="login.success", account_id=account.id)
        print(f"登录成功: account_id={account.id}, xianyu_id={unb}, device_id={account.device_id}")
        print("Cookie 已加密保存")

        try:
            token = await fetch_im_token(session.jar, account.device_id, timeout=settings.http_timeout)
        except MtopError as exc:
            print(f"IM Token 校验失败（不影响 Cookie 保存）: {exc}")
        else:
            print("IM Token 获取成功" if token else "IM Token 获取失败，Cookie 可能不完整")
        return 0
    finally:
        await db.close()


async def _connect(
    settings: Settings,
    repo: AccountRepo,
    account: Account,
    raw_sink: Callable[[dict[str, Any]], None] | None = None,
) -> XianyuWS:
    cookie = await repo.load_cookie(account.id)
    if not cookie:
        raise SystemExit(f"账号 {account.id} 没有 Cookie，请先执行 login")
    ws = XianyuWS(
        CookieJar(cookie),
        myid=account.xianyu_id,
        device_id=account.device_id,
        url=settings.ws_url,
        heartbeat_interval=settings.heartbeat_interval,
        http_timeout=settings.http_timeout,
        send_min_interval=settings.send_min_interval,
        raw_sink=raw_sink,
    )
    await ws.connect()
    return ws


async def cmd_listen(args: argparse.Namespace) -> int:
    settings, _, db, repo = await _open()
    record_file = None
    try:
        account = await _pick_account(repo, args.account)
        if account is None:
            print("未找到账号，请先执行 login")
            return 1

        record_sink: Callable[[dict[str, Any]], None] | None = None
        if args.record:
            record_path = Path(args.record)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_file = record_path.open("a", encoding="utf-8")

            def _write_frame(frame: dict[str, Any]) -> None:
                record_file.write(json.dumps(frame, ensure_ascii=False) + "\n")
                record_file.flush()

            record_sink = _write_frame
            print(f"原始下行帧将追加写入: {record_path}")

        ws = await _connect(settings, repo, account, raw_sink=record_sink)
        await repo.set_status(account.id, "online")
        await log_event(db, type="ws.connected", account_id=account.id)
        print(f"已连接账号 {account.id} ({account.xianyu_id})，开始监听（Ctrl+C 退出）...")

        replied = False
        received = 0
        try:
            async for message in ws.iter_messages():
                received += 1
                direction = "我" if message["is_self"] else "对方"
                print(
                    f"[{message['timestamp_ms']}] {direction} {message['sender_id']} "
                    f"chat={message['chat_id']} item={message['item_id']} "
                    f"kind={message['kind']}: {message['text']}"
                )
                if (
                    args.reply
                    and not replied
                    and not message["is_self"]
                    and message["kind"] == "text"
                    and message["text"]
                ):
                    mid = await ws.send_text(message["chat_id"], message["sender_id"], args.reply)
                    response = await ws.wait_result(mid, timeout=5.0)
                    reason = reject_reason(response)
                    await log_event(
                        db,
                        type="ws.reply",
                        account_id=account.id,
                        level="warning" if reason else "info",
                        detail={"mid": mid, "reason": reason or ""},
                    )
                    print(f"回复已发送 mid={mid} " + (f"被拦截: {reason}" if reason else "无拦截"))
                    replied = True
                if args.count and received >= args.count:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            await ws.close()
            await repo.set_status(account.id, "offline")
            await log_event(db, type="ws.closed", account_id=account.id)
        return 0
    finally:
        if record_file is not None:
            record_file.close()
        await db.close()


async def cmd_send(args: argparse.Namespace) -> int:
    settings, _, db, repo = await _open()
    try:
        account = await _pick_account(repo, args.account)
        if account is None:
            print("未找到账号，请先执行 login")
            return 1

        ws = await _connect(settings, repo, account)
        try:
            mid = await ws.send_text(args.to, args.to, args.text)
            response = await ws.wait_result(mid, timeout=5.0)
            reason = reject_reason(response)
            await log_event(
                db,
                type="ws.send",
                account_id=account.id,
                level="warning" if reason or response is None else "info",
                detail={"mid": mid, "reason": reason or "", "responded": response is not None},
            )
            print(f"mid={mid}")
            if response is None:
                print("未收到响应，请到客户端确认是否已送达")
                return 1
            print(json.dumps(response, ensure_ascii=False, indent=2))
            if reason:
                print(f"被拦截: {reason}")
                return 1
            return 0
        finally:
            await ws.close()
    finally:
        await db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="xianyu-lite P0 冒烟脚本")
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="扫码登录并保存 Cookie")
    login.add_argument("--timeout", type=float, default=300.0, help="等待扫码的秒数")
    login.set_defaults(func=cmd_login)

    listen = sub.add_parser("listen", help="连接 WebSocket 并打印消息")
    listen.add_argument("--account", default="latest", help="account id / xianyu_id / latest")
    listen.add_argument("--reply", default="", help="收到第一条对方文本时回复该内容")
    listen.add_argument("--count", type=int, default=0, help="收到 N 条后退出，0 表示持续监听")
    listen.add_argument("--record", default="", help="将原始下行帧追加写入 JSONL，用于 fixture")
    listen.set_defaults(func=cmd_listen)

    send = sub.add_parser("send", help="向指定用户发送一条文本")
    send.add_argument("--account", default="latest", help="account id / xianyu_id / latest")
    send.add_argument("--to", required=True, help="对方用户 ID")
    send.add_argument("--text", required=True, help="消息内容")
    send.set_defaults(func=cmd_send)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.exception("P0 执行失败")
        print(f"失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
