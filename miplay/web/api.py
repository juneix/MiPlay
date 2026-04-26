"""Web API for MiPlay."""

from __future__ import annotations

import asyncio
import os
import sys

from aiohttp import web

from miplay.config import Config


def _restart_process():
    args = [sys.executable, "-m", "miplay.cli", *sys.argv[1:]]
    if sys.platform == "win32":
        import subprocess

        subprocess.Popen(args)
        os._exit(0)
    os.execv(sys.executable, args)


def create_web_app(config: Config, app_instance) -> web.Application:
    web_app = web.Application()

    async def handle_index(request: web.Request):
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        return web.FileResponse(os.path.join(static_dir, "index.html"))

    async def handle_get_setting(request: web.Request):
        need_device_list = request.query.get("need_device_list", "false") == "true"
        payload = {
            "xiaomi": {
                "account": config.xiaomi.account,
                "cookie": config.xiaomi.cookie,
                "has_credentials": bool(config.xiaomi.account or config.xiaomi.cookie),
            },
            "targets": [
                {
                    "id": target.id,
                    "did": target.did,
                    "name": target.name,
                    "airplay_name": target.airplay_name,
                    "enabled": target.enabled,
                    "device_id": target.device_id,
                    "hardware": target.hardware,
                }
                for target in config.targets
            ],
            "status": app_instance.get_status_snapshot(),
        }
        if need_device_list:
            try:
                payload["device_list"] = await app_instance.get_all_devices()
                payload["device_list_error"] = ""
            except Exception as exc:
                payload["device_list"] = []
                payload["device_list_error"] = str(exc)
        return web.json_response(payload)

    async def handle_save_setting(request: web.Request):
        data = await request.json()
        xiaomi = data.get("xiaomi", {})

        config.xiaomi.account = str(xiaomi.get("account", config.xiaomi.account)).strip()
        config.xiaomi.password = str(xiaomi.get("password", config.xiaomi.password)).strip()
        config.xiaomi.cookie = str(xiaomi.get("cookie", config.xiaomi.cookie)).strip()

        if "targets" in data:
            config.set_targets(data["targets"])

        config.save()
        response = web.json_response({"ok": True, "message": "Configuration saved; restarting MiPlay..."})
        await response.prepare(request)
        await response.write_eof()
        asyncio.get_running_loop().call_soon(_restart_process)
        return response

    async def handle_restart(request: web.Request):
        response = web.json_response({"ok": True, "message": "Restarting MiPlay..."})
        await response.prepare(request)
        await response.write_eof()
        asyncio.get_running_loop().call_soon(_restart_process)
        return response

    async def handle_get_devices(request: web.Request):
        devices = await app_instance.get_all_devices()
        return web.json_response({"devices": devices})

    async def handle_get_targets(request: web.Request):
        return web.json_response(app_instance.get_runtime_targets())

    async def handle_status(request: web.Request):
        return web.json_response(app_instance.get_status_snapshot())

    async def handle_control(request: web.Request):
        data = await request.json()
        target_id = data.get("id")
        action = data.get("action")
        if not target_id or not action:
            return web.json_response({"ok": False, "message": "Missing id or action"}, status=400)
        
        try:
            ok = await app_instance.control_target(target_id, action)
            return web.json_response({"ok": ok})
        except Exception as exc:
            return web.json_response({"ok": False, "message": str(exc)}, status=500)

    web_app.router.add_get("/", handle_index)
    web_app.router.add_get("/api/setting", handle_get_setting)
    web_app.router.add_post("/api/setting", handle_save_setting)
    web_app.router.add_post("/api/restart", handle_restart)
    web_app.router.add_get("/api/devices", handle_get_devices)
    web_app.router.add_get("/api/targets", handle_get_targets)
    web_app.router.add_get("/api/status", handle_status)
    web_app.router.add_post("/api/control", handle_control)

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        web_app.router.add_static("/static", static_dir)
    return web_app
