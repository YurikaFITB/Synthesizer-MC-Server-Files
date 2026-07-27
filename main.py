import json
import os
import time
import urllib.request
import urllib.error
from endstone.event import event_handler, PlayerChatEvent, PlayerJoinEvent, PlayerQuitEvent
from endstone.plugin import Plugin

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1531060093527789729/j30btFC-vRGXMYEGAvGlYc8zooeJROzp1hDJwPRLgPl-VIII_ZCET_39AofA8Kq_kqIS"
WORKER_URL = "https://mc-player-tracker.synthesizer.workers.dev"
API_SECRET = "supersecretkey123"
JSON_OUTPUT_PATH = "players.json"

class DiscordLoggerPlugin(Plugin):
    name = "DiscordChatLogger"
    version = "1.0.0"
    api_version = "0.11"

    def on_enable(self):
        self.online_players = set()
        self.logger.info("DiscordChatLogger plugin successfully enabled!")
        self.logger.info(f"players.json will be written to: {os.path.abspath(JSON_OUTPUT_PATH)}")
        self.register_events(self)

        # Run an explicit startup self-test so you can see in the console
        # exactly which step (webhook vs worker vs file write) is failing.
        self.logger.info("Running startup connectivity self-test...")
        self.send_discord("✅ DiscordChatLogger plugin started and connected.")
        self.update_player_data()

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

        # 1. Write local players.json
        try:
            abs_path = os.path.abspath(JSON_OUTPUT_PATH)
            with open(abs_path, "w") as f:
                json.dump(data, f, indent=2)
            self.logger.info(f"players.json written successfully to {abs_path}")
        except Exception as e:
            self.logger.error(f"Failed to write local players.json: {type(e).__name__}: {e}")

        # 2. Sync to Cloudflare Worker
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            WORKER_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_SECRET}"
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
