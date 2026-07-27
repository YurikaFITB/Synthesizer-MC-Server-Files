import json
import os
import time
import urllib.request
import urllib.error
from endstone.event import event_handler, PlayerChatEvent, PlayerJoinEvent, PlayerQuitEvent
from endstone.plugin import Plugin

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1531060093527789729/j30btFC-vRGXMYEGAvGlYc8zooeJROzp1hDJwPRLgPl-VIII_ZCET_39AofA8Kq_kqIS"
DISCORD_PUBLIC_WEBHOOK = "https://discord.com/api/webhooks/1531036952822939657/GMJR2O_mV4k5wPw4dxswUaiecke5BAuZYflMyXYj0gRASvzB2ew1m7Jmb022h3Z5P_Zr"
STATUS_PING = "<@&1306303285401223188>"

WORKER_URL = "https://mc-player-tracker.synthesizer.workers.dev"
API_SECRET = "supersecretkey123"
JSON_OUTPUT_PATH = "players.json"
STATUS_WEBSITE = "https://synthesizer-status.synthesizer.workers.dev/"

class DiscordLoggerPlugin(Plugin):
    name = "DiscordChatLogger"
    version = "1.0.0"
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

        self.update_player_data()

    def on_disable(self):
        # Fires during a graceful shutdown (e.g. the "stop" command), before
        # the process actually exits. NOTE: this will NOT fire on a hard
        # crash or a forcibly killed process — see the caveat below.
        self.logger.info("DiscordChatLogger plugin disabling, sending OFFLINE status...")
        self.send_status_update(online=False)

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

        try:
            abs_path = os.path.abspath(JSON_OUTPUT_PATH)
            with open(abs_path, "w") as f:
                json.dump(data, f, indent=2)
            self.logger.info(f"players.json written successfully to {abs_path}")
        except Exception as e:
            self.logger.error(f"Failed to write local players.json: {type(e).__name__}: {e}")

        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            WORKER_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_SECRET}",
                "User-Agent": "Mozilla/5.0"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                self.logger.info(f"Cloudflare Worker sync OK (status {response.status})")
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