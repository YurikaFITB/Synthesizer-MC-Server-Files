import json
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

    def on_enable(self):
        self.online_players = set()
        self.logger.info("DiscordChatLogger plugin successfully enabled!")
        self.register_events(self)
        self.update_player_data()

    def send_discord(self, content):
        payload = json.dumps({"content": content, "username": "MC Endstone Logger"}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                pass
        except urllib.error.HTTPError as e:
            self.logger.error(f"Discord webhook HTTP error: {e.code} - {e.read().decode()}[cite: 1]")
        except Exception as e:
            self.logger.error(f"Discord webhook failed: {e}[cite: 1]")

    def update_player_data(self):
        try:
            # Endstone server online_players property lookup
            current_players = [p.name for p in self.server.online_players]
        except Exception as e:
            self.logger.error(f"Failed to fetch online players list: {e}[cite: 1]")
            current_players = []
            
        self.online_players = set(current_players)

        data = {
            "count": len(self.online_players),
            "players": sorted(list(self.online_players)),
            "updated_at": int(time.time())
        }

        # 1. Write local players.json with absolute clarity
        try:
            with open(JSON_OUTPUT_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to write local players.json: {e}[cite: 1]")

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
            with urllib.request.urlopen(req, timeout=5) as response:
                pass
        except Exception as e:
            self.logger.error(f"Cloudflare Worker sync failed: {e}[cite: 1]")

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