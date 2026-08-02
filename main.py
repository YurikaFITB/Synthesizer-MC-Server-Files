import json
import os
import threading
import time
import urllib.request
import urllib.error
from endstone.event import (
    event_handler,
    PlayerChatEvent,
    PlayerJoinEvent,
    PlayerQuitEvent,
    PlayerDeathEvent,
    BroadcastMessageEvent,
)
from endstone.plugin import Plugin

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1531060093527789729/j30btFC-vRGXMYEGAvGlYc8zooeJROzp1hDJwPRLgPl-VIII_ZCET_39AofA8Kq_kqIS"
DISCORD_PUBLIC_WEBHOOK = "https://discord.com/api/webhooks/1531036952822939657/GMJR2O_mV4k5wPw4dxswUaiecke5BAuZYflMyXYj0gRASvzB2ew1m7Jmb022h3Z5P_Zr"
STATUS_PING = "<@&1306303285401223188>"

WORKER_URL = "https://mc-player-tracker.synthesizer.workers.dev"
WORKER_STATUS_URL = f"{WORKER_URL}/status"
API_SECRET = "supersecretkey123"
JSON_OUTPUT_PATH = "players.json"
STATUS_WEBSITE = "https://synthesizer-status.synthesizer.workers.dev/"

# How often we tell the Worker "I'm still alive". The Worker's cron job
# treats a gap of a few missed heartbeats as a possible crash/power outage.
HEARTBEAT_PERIOD_TICKS = 20 * 60  # 60 seconds (20 ticks/sec)

# Shared timeout for outbound HTTP calls. These now all run off the main
# thread, so a slow/stuck request no longer freezes the server tick loop.
HTTP_TIMEOUT = 8


class DiscordLoggerPlugin(Plugin):
    name = "DiscordChatLogger"
    version = "1.2.0"
    api_version = "0.11"

    def on_enable(self):
        self.online_players = set()
        self.logger.info("DiscordChatLogger plugin successfully enabled!")
        self.logger.info(f"players.json will be written to: {os.path.abspath(JSON_OUTPUT_PATH)}")
        self.register_events(self)

        self.logger.info("Running startup connectivity self-test...")
        self.send_discord("✅ DiscordChatLogger plugin started and connected.")

        # Instant ONLINE announcement — fires the moment the plugin (and thus
        # the server) has actually come up, no polling delay involved.
        self.send_status_update(online=True)

        # Tell the Worker this was a clean startup: clears any leftover
        # "graceful shutdown" / "crash alerted" flags from last session.
        self.send_worker_status("startup")

        self.update_player_data()

        # Periodic heartbeat so the Worker can detect an *unexpected*
        # shutdown (crash, power outage, relatives turning off the PC).
        # on_disable below only fires on a graceful stop — it will NOT fire
        # if the process is killed or the machine loses power, which is
        # exactly the case this heartbeat exists to cover.
        self.server.scheduler.run_task(
            self,
            self.update_player_data,
            delay=HEARTBEAT_PERIOD_TICKS,
            period=HEARTBEAT_PERIOD_TICKS,
        )

    def on_disable(self):
        # Fires during a graceful shutdown (e.g. the "stop" command), before
        # the process actually exits. NOTE: this will NOT fire on a hard
        # crash or forced power-off — the Worker's cron job covers that case.
        self.logger.info("DiscordChatLogger plugin disabling, sending OFFLINE status...")
        # These are the only two calls we deliberately do NOT background,
        # since the process is about to exit and a daemon thread might get
        # killed mid-request. A shutdown-time blocking call is fine — the
        # server is already stopping, so there's no tick loop left to stall.
        self._post_json(DISCORD_PUBLIC_WEBHOOK, self._offline_payload(), "Status webhook (offline)")
        self._post_json(
            WORKER_STATUS_URL,
            {"event": "shutdown"},
            "Worker status ping (shutdown)",
            extra_headers={"Authorization": f"Bearer {API_SECRET}"},
        )

    # ------------------------------------------------------------------
    # Threading helper — every "fire and forget" network call goes through
    # this so it never blocks the main server thread / tick loop.
    # ------------------------------------------------------------------
    def _run_async(self, target, *args):
        threading.Thread(target=target, args=args, daemon=True).start()

    def _post_json(self, url, payload_dict, label, extra_headers=None):
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        if extra_headers:
            headers.update(extra_headers)
        payload = json.dumps(payload_dict).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
                self.logger.info(f"{label} OK (status {response.status})")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            self.logger.error(f"{label} HTTP error: {e.code} - {body}")
        except urllib.error.URLError as e:
            self.logger.error(f"{label} network/DNS error: {e.reason}")
        except Exception as e:
            self.logger.error(f"{label} failed: {type(e).__name__}: {e}")

    def send_worker_status(self, event_name):
        """Tell the Cloudflare Worker about a clean startup/shutdown so its
        crash-detection cron job doesn't fire a false alarm."""
        self._run_async(
            self._post_json,
            WORKER_STATUS_URL,
            {"event": event_name},
            f"Worker status ping ({event_name})",
            {"Authorization": f"Bearer {API_SECRET}"},
        )

    def _offline_payload(self):
        message = (
            f"🔴 **Server Update:** Synthesizer is now **OFFLINE**!\n"
            f"*Server Status Website: {STATUS_WEBSITE}*"
        )
        return {"content": message}

    def send_status_update(self, online: bool):
        message = (
            f"🟢 **Server Update:** {STATUS_PING} Synthesizer is now **ONLINE**!\n"
            f"*Server Status Website: {STATUS_WEBSITE}*"
            if online else
            f"🔴 **Server Update:** Synthesizer is now **OFFLINE**!\n"
            f"*Server Status Website: {STATUS_WEBSITE}*"
        )
        self._run_async(self._post_json, DISCORD_PUBLIC_WEBHOOK, {"content": message}, "Status webhook")

    def send_discord(self, content):
        payload = {"content": content, "username": "MC Endstone Logger"}
        self._run_async(self._post_json, DISCORD_WEBHOOK_URL, payload, "Discord webhook")

    def update_player_data(self):
        try:
            current_players = [p.name for p in self.server.online_players]
        except Exception as e:
            self.logger.error(f"Failed to fetch online players list: {type(e).__name__}: {e}")
            current_players = []

        self.online_players = set(current_players)

        data = {
            "count": len(self.online_players),
            "players": sorted(list(self.online_players)),
            "updated_at": int(time.time())
        }

        # Local file write stays on the main thread — it's fast disk I/O,
        # not network I/O, so it won't cause the freezes network calls do.
        try:
            abs_path = os.path.abspath(JSON_OUTPUT_PATH)
            with open(abs_path, "w") as f:
                json.dump(data, f, indent=2)
            self.logger.info(f"players.json written successfully to {abs_path}")
        except Exception as e:
            self.logger.error(f"Failed to write local players.json: {type(e).__name__}: {e}")

        # Cloudflare Worker sync — this is the one that ran every 60s on the
        # main thread before. Now backgrounded.
        self._run_async(
            self._post_json,
            WORKER_URL,
            data,
            "Cloudflare Worker sync",
            {"Authorization": f"Bearer {API_SECRET}"},
        )

    @event_handler
    def on_player_chat(self, event: PlayerChatEvent):
        player_name = event.player.name
        message = event.message
        self.logger.info(f"[Chat] {player_name}: {message}")
        self.send_discord(f"💬 **<{player_name}>** {message}")

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent):
        player_name = event.player.name
        self.logger.info(f"📥 {player_name} joined the server.")
        self.send_discord(f"📥 **{player_name}** joined the server.")
        self.update_player_data()

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent):
        player_name = event.player.name
        self.logger.info(f"📤 {player_name} left the server.")
        self.send_discord(f"📤 **{player_name}** left the server.")
        self.update_player_data()

    @event_handler
    def on_player_death(self, event: PlayerDeathEvent):
        message = getattr(event, "death_message", None) or f"{event.player.name} died."
        self.logger.info(f"[Death] {message}")
        self.send_discord(f"☠️ {message}")

    @event_handler
    def on_broadcast_message(self, event: BroadcastMessageEvent):
        message = event.message
        if isinstance(message, str) and message.startswith("[Server]"):
            self.logger.info(f"[Server Chat] {message}")
            self.send_discord(f"📢 {message}")