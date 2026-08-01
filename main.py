import json
import os
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

# How often the plugin CHECKS whether it should sync (cheap, local only,
# no network call happens just from this timer firing).
CHECK_PERIOD_TICKS = 20 * 60  # every 60 seconds

# Minimum time between "keep alive" pings to the Worker, used ONLY when the
# player list hasn't changed. This is what stops us from spamming Cloudflare
# every 60s for no reason. Must stay comfortably below the Worker's
# STALE_THRESHOLD_SECONDS (245s in mc-player-tracker.js) or crash detection
# will start firing false alarms.
HEARTBEAT_INTERVAL_SECONDS = 180  # 3 minutes


class DiscordLoggerPlugin(Plugin):
    name = "DiscordChatLogger"
    version = "1.2.0"
    api_version = "0.11"

    def on_enable(self):
        self.online_players = set()
        self.last_synced_players = None  # None = "never synced yet", forces first sync
        self.last_sync_time = 0
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

        self.update_player_data(force=True)

        # This timer just CHECKS every 60s whether a sync is needed (either
        # the player list changed, or the heartbeat interval elapsed). It
        # does NOT mean a Worker write happens every time it fires.
        self.server.scheduler.run_task(
            self,
            self.update_player_data,
            delay=CHECK_PERIOD_TICKS,
            period=CHECK_PERIOD_TICKS,
        )

    def on_disable(self):
        # Fires during a graceful shutdown (e.g. the "stop" command), before
        # the process actually exits. NOTE: this will NOT fire on a hard
        # crash or forced power-off — the Worker's cron job covers that case.
        self.logger.info("DiscordChatLogger plugin disabling, sending OFFLINE status...")
        self.send_status_update(online=False)
        self.send_worker_status("shutdown")

    def send_worker_status(self, event_name):
        """Tell the Cloudflare Worker about a clean startup/shutdown so its
        crash-detection cron job doesn't fire a false alarm."""
        payload = json.dumps({"event": event_name}).encode("utf-8")
        req = urllib.request.Request(
            WORKER_STATUS_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_SECRET}",
                "User-Agent": "Mozilla/5.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                self.logger.info(f"Worker status ping OK ({event_name}, status {response.status})")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            self.logger.error(f"Worker status ping HTTP error ({event_name}): {e.code} - {body}")
        except urllib.error.URLError as e:
            self.logger.error(f"Worker status ping network/DNS error ({event_name}): {e.reason}")
        except Exception as e:
            self.logger.error(f"Worker status ping failed ({event_name}): {type(e).__name__}: {e}")

    def send_status_update(self, online: bool):
        message = (
            f"🟢 **Server Update:** {STATUS_PING} Synthesizer is now **ONLINE**!\n"
            f"*Server Status Website: {STATUS_WEBSITE}*"
            if online else
            f"🔴 **Server Update:** Synthesizer is now **OFFLINE**!\n"
            f"*Server Status Website: {STATUS_WEBSITE}*"
        )
        payload = json.dumps({"content": message}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_PUBLIC_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                self.logger.info(f"Status webhook sent OK (status {response.status})")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            self.logger.error(f"Status webhook HTTP error: {e.code} - {body}")
        except urllib.error.URLError as e:
            self.logger.error(f"Status webhook network/DNS error: {e.reason}")
        except Exception as e:
            self.logger.error(f"Status webhook failed: {type(e).__name__}: {e}")

    def send_discord(self, content):
        payload = json.dumps({"content": content, "username": "MC Endstone Logger"}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                self.logger.info(f"Discord webhook sent OK (status {response.status})")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            self.logger.error(f"Discord webhook HTTP error: {e.code} - {body}")
        except urllib.error.URLError as e:
            self.logger.error(f"Discord webhook network/DNS error (likely outbound connectivity issue on host): {e.reason}")
        except Exception as e:
            self.logger.error(f"Discord webhook failed: {type(e).__name__}: {e}")

    def update_player_data(self, force: bool = False):
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

        # 1. ALWAYS write local players.json — this is free, no network/limit involved.
        try:
            abs_path = os.path.abspath(JSON_OUTPUT_PATH)
            with open(abs_path, "w") as f:
                json.dump(data, f, indent=2)
            self.logger.info(f"players.json written successfully to {abs_path}")
        except Exception as e:
            self.logger.error(f"Failed to write local players.json: {type(e).__name__}: {e}")

        # 2. Decide whether to sync to the Cloudflare Worker.
        #    - Trigger A: the player list actually changed (join/quit).
        #    - Trigger B: it's been a while (heartbeat), so the Worker
        #      doesn't think we've gone silent/crashed.
        #    - force=True is used once on startup so the Worker always
        #      gets a fresh snapshot when the plugin comes up.
        list_changed = self.online_players != self.last_synced_players
        now = time.time()
        heartbeat_due = (now - self.last_sync_time) >= HEARTBEAT_INTERVAL_SECONDS

        if not (force or list_changed or heartbeat_due):
            self.logger.info("No player change and heartbeat not due yet — skipping Worker sync.")
            return

        reason = "startup" if force else ("player list changed" if list_changed else "heartbeat")
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            WORKER_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_SECRET}",
                "User-Agent": "Mozilla/5.0"
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                self.logger.info(f"Cloudflare Worker sync OK ({reason}, status {response.status})")
            # Only mark as synced on success, so a failed request gets retried.
            self.last_synced_players = set(self.online_players)
            self.last_sync_time = now
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            self.logger.error(f"Cloudflare Worker sync HTTP error: {e.code} - {body}")
        except urllib.error.URLError as e:
            self.logger.error(f"Cloudflare Worker sync network/DNS error: {e.reason}")
        except Exception as e:
            self.logger.error(f"Cloudflare Worker sync failed: {type(e).__name__}: {e}")

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
        # death_message is provided by Endstone (see PlayerDeathEvent docs).
        # Falling back to a generic message just in case it's ever empty.
        message = getattr(event, "death_message", None) or f"{event.player.name} died."
        self.logger.info(f"[Death] {message}")
        self.send_discord(f"☠️ {message}")

    @event_handler
    def on_broadcast_message(self, event: BroadcastMessageEvent):
        # BroadcastMessageEvent fires for lots of server broadcasts. We only
        # forward the ones prefixed "[Server]" (i.e. sent via the console
        # "say" command) — join/quit/death/chat are already logged above by
        # their own dedicated events, so this filter avoids double-posting.
        message = event.message
        if isinstance(message, str) and message.startswith("[Server]"):
            self.logger.info(f"[Server Chat] {message}")
            self.send_discord(f"📢 {message}")