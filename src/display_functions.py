from typing import Dict, Any, Optional
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager
from datetime import datetime
import logging
import os
from src.odds_manager import OddsManager
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image, ImageDraw, ImageFont
import pytz
import time


class SportsCore:
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager, logger: logging.Logger, mode_key: str):
        self.logger = logger
        self.config = config
        self.cache_manager = cache_manager
        self.config_manager = self.cache_manager.config_manager
        self.odds_manager = OddsManager(
            self.cache_manager, self.config_manager)
        self.display_manager = display_manager
        self.display_width = self.display_manager.matrix.width
        self.display_height = self.display_manager.matrix.height

        self.mode_key = mode_key
        self.mode_config = config.get(mode_key, {})  # Changed config key
        self.is_enabled = self.mode_config.get("enabled", False)
        self.show_odds = self.mode_config.get("show_odds", False)
        self.test_mode = self.mode_config.get("test_mode", False)
        self.logo_dir = self.mode_config.get(
            "logo_dir", self.get_logo_dir())  # Changed logo dir
        self.update_interval = self.mode_config.get(
            "update_interval_seconds", 60)
        self.show_records = self.mode_config.get('show_records', False)
        self.show_ranking = self.mode_config.get('show_ranking', False)
        self.season_cache_duration = self.mode_config.get(
            "season_cache_duration_seconds", 86400)  # 24 hours default
        # Number of games to show (instead of time-based windows)
        self.recent_games_to_show = self.mode_config.get(
            "recent_games_to_show", 5)  # Show last 5 games
        self.upcoming_games_to_show = self.mode_config.get(
            "upcoming_games_to_show", 10)  # Show next 10 games

        self.session = requests.Session()
        retry_strategy = Retry(
            total=5,  # increased number of retries
            backoff_factor=1,  # increased backoff factor
            # added 429 to retry list
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self._logo_cache = {}

        # Set up headers
        self.headers = {
            'User-Agent': 'LEDMatrix/1.0 (https://github.com/yourusername/LEDMatrix; contact@example.com)',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }
        self.last_update = 0
        self.current_game = None
        self.fonts = self._load_fonts()
        self.favorite_teams = self.mode_config.get("favorite_teams", [])

    def get_logo_dir(self):
        league = self.mode_key.split("_")[0]
        if league.startswith("ncaa"):
            league = "ncaa"
        return f"assets/sports/{league}_logos"

    def _load_fonts(self):
        """Load fonts used by the scoreboard."""
        fonts = {}
        try:
            fonts['score'] = ImageFont.truetype(
                "assets/fonts/PressStart2P-Regular.ttf", 10)
            fonts['time'] = ImageFont.truetype(
                "assets/fonts/PressStart2P-Regular.ttf", 8)
            fonts['team'] = ImageFont.truetype(
                "assets/fonts/PressStart2P-Regular.ttf", 8)
            fonts['status'] = ImageFont.truetype(
                "assets/fonts/4x6-font.ttf", 6)  # Using 4x6 for status
            fonts['detail'] = ImageFont.truetype(
                "assets/fonts/4x6-font.ttf", 6)  # Added detail font
            fonts['rank'] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
            # Changed log prefix
            logging.info("[NCAAFB] Successfully loaded fonts")
        except IOError:
            # Changed log prefix
            logging.warning(
                "[NCAAFB] Fonts not found, using default PIL font.")
            fonts['score'] = ImageFont.load_default()
            fonts['time'] = ImageFont.load_default()
            fonts['team'] = ImageFont.load_default()
            fonts['status'] = ImageFont.load_default()
            fonts['detail'] = ImageFont.load_default()
            fonts['rank'] = ImageFont.load_default()
        return fonts

    def _get_timezone(self):
        try:
            timezone_str = self.config.get('timezone', 'UTC')
            return pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _should_log(self, warning_type: str, cooldown: int = 60) -> bool:
        """Check if we should log a warning based on cooldown period."""
        current_time = time.time()
        if current_time - self._last_warning_time > cooldown:
            self._last_warning_time = current_time
            return True
        return False

    def _fetch_odds(self, game: Dict, sport: str, league: str) -> None:
        """Fetch odds for a specific game if conditions are met."""
        # Check if odds should be shown for this sport
        if not self.show_odds:
            return

        # Check if we should only fetch for favorite teams
        is_favorites_only = self.mode_config.get(
            "show_favorite_teams_only", False)
        if is_favorites_only:
            home_abbr = game.get('home_abbr')
            away_abbr = game.get('away_abbr')
            if not (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams):
                self.logger.debug(
                    f"Skipping odds fetch for non-favorite game in favorites-only mode: {away_abbr}@{home_abbr}")
                return

        self.logger.debug(
            f"Proceeding with odds fetch for game: {game.get('id', 'N/A')}")

        # Fetch odds using OddsManager (ESPN API)
        try:
            # Determine update interval based on game state
            is_live = game.get('status', '').lower() == 'in'
            update_interval = self.mode_config.get("live_odds_update_interval", 60) if is_live \
                else self.mode_config.get("odds_update_interval", 3600)

            odds_data = self.odds_manager.get_odds(
                sport=sport,
                league=league,
                event_id=game['id'],
                update_interval_seconds=update_interval
            )

            if odds_data:
                game['odds'] = odds_data
                self.logger.debug(
                    f"Successfully fetched and attached odds for game {game['id']}")
            else:
                self.logger.debug(
                    f"No odds data returned for game {game['id']}")

        except Exception as e:
            self.logger.error(
                f"Error fetching odds for game {game.get('id', 'N/A')}: {e}")

    def _draw_text_with_outline(self, draw, text, position, font, fill=(255, 255, 255), outline_color=(0, 0, 0)):
        """Draw text with a black outline for better readability."""
        x, y = position
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        draw.text((x, y), text, font=font, fill=fill)

    def _load_and_resize_logo(self, team_abbrev: str, team_name: str = None) -> Optional[Image.Image]:
        pass

    def _draw_dynamic_odds(self, draw: ImageDraw.Draw, odds: Dict[str, Any], width: int, height: int) -> None:
        """Draw odds with dynamic positioning - only show negative spread and position O/U based on favored team."""
        home_team_odds = odds.get('home_team_odds', {})
        away_team_odds = odds.get('away_team_odds', {})
        home_spread = home_team_odds.get('spread_odds')
        away_spread = away_team_odds.get('spread_odds')

        # Get top-level spread as fallback
        top_level_spread = odds.get('spread')

        # If we have a top-level spread and the individual spreads are None or 0, use the top-level
        if top_level_spread is not None:
            if home_spread is None or home_spread == 0.0:
                home_spread = top_level_spread
            if away_spread is None:
                away_spread = -top_level_spread

        # Determine which team is favored (has negative spread)
        home_favored = home_spread is not None and home_spread < 0
        away_favored = away_spread is not None and away_spread < 0

        # Only show the negative spread (favored team)
        favored_spread = None
        favored_side = None

        if home_favored:
            favored_spread = home_spread
            favored_side = 'home'
            self.logger.debug(
                f"Home team favored with spread: {favored_spread}")
        elif away_favored:
            favored_spread = away_spread
            favored_side = 'away'
            self.logger.debug(
                f"Away team favored with spread: {favored_spread}")
        else:
            self.logger.debug(
                "No clear favorite - spreads: home={home_spread}, away={away_spread}")

        # Show the negative spread on the appropriate side
        if favored_spread is not None:
            spread_text = str(favored_spread)
            font = self.fonts['detail']  # Use detail font for odds

            if favored_side == 'home':
                # Home team is favored, show spread on right side
                spread_width = draw.textlength(spread_text, font=font)
                spread_x = width - spread_width  # Top right
                spread_y = 0
                self._draw_text_with_outline(
                    draw, spread_text, (spread_x, spread_y), font, fill=(0, 255, 0))
                self.logger.debug(
                    f"Showing home spread '{spread_text}' on right side")
            else:
                # Away team is favored, show spread on left side
                spread_x = 0  # Top left
                spread_y = 0
                self._draw_text_with_outline(
                    draw, spread_text, (spread_x, spread_y), font, fill=(0, 255, 0))
                self.logger.debug(
                    f"Showing away spread '{spread_text}' on left side")

        # Show over/under on the opposite side of the favored team
        over_under = odds.get('over_under')
        if over_under is not None:
            ou_text = f"O/U: {over_under}"
            font = self.fonts['detail']  # Use detail font for odds
            ou_width = draw.textlength(ou_text, font=font)

            if favored_side == 'home':
                # Home team is favored, show O/U on left side (opposite of spread)
                ou_x = 0  # Top left
                ou_y = 0
                self.logger.debug(
                    f"Showing O/U '{ou_text}' on left side (home favored)")
            elif favored_side == 'away':
                # Away team is favored, show O/U on right side (opposite of spread)
                ou_x = width - ou_width  # Top right
                ou_y = 0
                self.logger.debug(
                    f"Showing O/U '{ou_text}' on right side (away favored)")
            else:
                # No clear favorite, show O/U in center
                ou_x = (width - ou_width) // 2
                ou_y = 0
                self.logger.debug(
                    f"Showing O/U '{ou_text}' in center (no clear favorite)")

            self._draw_text_with_outline(
                draw, ou_text, (ou_x, ou_y), font, fill=(0, 255, 0))

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Placeholder draw method - subclasses should override."""
        # This base method will be simple, subclasses provide specifics
        try:
            img = Image.new('RGB', (self.display_width,
                            self.display_height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            status = game.get("status_text", "N/A")
            self._draw_text_with_outline(
                draw, status, (2, 2), self.fonts['status'])
            self.display_manager.image.paste(img, (0, 0))
            # Don't call update_display here, let subclasses handle it after drawing
        except Exception as e:
            self.logger.error(
                f"Error in base _draw_scorebug_layout: {e}", exc_info=True)

    def display(self, force_clear: bool = False) -> None:
        """Common display method for all NCAA FB managers"""  # Updated docstring
        if not self.is_enabled:  # Check if module is enabled
            return

        if not self.current_game:
            current_time = time.time()
            if not hasattr(self, '_last_warning_time'):
                self._last_warning_time = 0
            if current_time - getattr(self, '_last_warning_time', 0) > 300:
                self.logger.warning(
                    f"[NCAAFB] No game data available to display in {self.__class__.__name__}")
                setattr(self, '_last_warning_time', current_time)
            return

        try:
            self._draw_scorebug_layout(self.current_game, force_clear)
            # display_manager.update_display() should be called within subclass draw methods
            # or after calling display() in the main loop. Let's keep it out of the base display.
        except Exception as e:
            self.logger.error(
                f"[NCAAFB] Error during display call in {self.__class__.__name__}: {e}", exc_info=True)


class Football(SportsCore):
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager, logger: logging.Logger, mode_key: str):
        super().__init__(config, display_manager, cache_manager, logger, mode_key)

    def _fetch_odds(self, game: Dict, league: str) -> None:
        super()._fetch_odds(game, "football", league)

    def _extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract relevant game details from ESPN NCAA FB API response."""
        # --- THIS METHOD MAY NEED ADJUSTMENTS FOR NCAA FB API DIFFERENCES ---
        if not game_event:
            return None

        try:
            competition = game_event["competitions"][0]
            status = competition["status"]
            competitors = competition["competitors"]
            game_date_str = game_event["date"]

            start_time_utc = None
            try:
                start_time_utc = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
            except ValueError:
                logging.warning(f"[NCAAFB] Could not parse game date: {game_date_str}")

            home_team = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away_team = next((c for c in competitors if c.get("homeAway") == "away"), None)

            if not home_team or not away_team:
                 self.logger.warning(f"[NCAAFB] Could not find home or away team in event: {game_event.get('id')}")
                 return None

            home_abbr = home_team["team"]["abbreviation"]
            away_abbr = away_team["team"]["abbreviation"]
            
            # Check if this is a favorite team game BEFORE doing expensive logging
            is_favorite_game = (home_abbr in self.favorite_teams or away_abbr in self.favorite_teams)
            
            # Only log debug info for favorite team games
            if is_favorite_game:
                self.logger.debug(f"[NCAAFB] Processing favorite team game: {game_event.get('id')}")
                self.logger.debug(f"[NCAAFB] Found teams: {away_abbr}@{home_abbr}, Status: {status['type']['name']}, State: {status['type']['state']}")
            
            home_record = home_team.get('records', [{}])[0].get('summary', '') if home_team.get('records') else ''
            away_record = away_team.get('records', [{}])[0].get('summary', '') if away_team.get('records') else ''
            
            # Don't show "0-0" records - set to blank instead
            if home_record == "0-0":
                home_record = ''
            if away_record == "0-0":
                away_record = ''

            # Remove early filtering - let individual managers handle their own filtering
            # This allows shared data to contain all games, and each manager can filter as needed

            game_time, game_date = "", ""
            if start_time_utc:
                local_time = start_time_utc.astimezone(self._get_timezone())
                game_time = local_time.strftime("%I:%M%p").lstrip('0')
                
                # Check date format from config
                use_short_date_format = self.config.get('display', {}).get('use_short_date_format', False)
                if use_short_date_format:
                    game_date = local_time.strftime("%-m/%-d")
                else:
                    game_date = self.display_manager.format_date_with_ordinal(local_time)

            # --- Football Specific Details (Likely same for NFL/NCAAFB) ---
            situation = competition.get("situation")
            down_distance_text = ""
            possession_indicator = None # Default to None
            scoring_event = ""  # Track scoring events
            
            if situation and status["type"]["state"] == "in":
                down = situation.get("down")
                distance = situation.get("distance")
                # Validate down and distance values before formatting
                if (down is not None and isinstance(down, int) and 1 <= down <= 4 and 
                    distance is not None and isinstance(distance, int) and distance >= 0):
                    down_str = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(down, f"{down}th")
                    dist_str = f"& {distance}" if distance > 0 else "& Goal"
                    down_distance_text = f"{down_str} {dist_str}"
                elif situation.get("isRedZone"):
                     down_distance_text = "Red Zone" # Simplified if down/distance not present but in redzone
                
                # Detect scoring events from status detail
                status_detail = status["type"].get("detail", "").lower()
                status_short = status["type"].get("shortDetail", "").lower()
                
                # Check for scoring events in status text
                if any(keyword in status_detail for keyword in ["touchdown", "td"]):
                    scoring_event = "TOUCHDOWN"
                elif any(keyword in status_detail for keyword in ["field goal", "fg"]):
                    scoring_event = "FIELD GOAL"
                elif any(keyword in status_detail for keyword in ["extra point", "pat", "point after"]):
                    scoring_event = "PAT"
                elif any(keyword in status_short for keyword in ["touchdown", "td"]):
                    scoring_event = "TOUCHDOWN"
                elif any(keyword in status_short for keyword in ["field goal", "fg"]):
                    scoring_event = "FIELD GOAL"
                elif any(keyword in status_short for keyword in ["extra point", "pat"]):
                    scoring_event = "PAT"

                # Determine possession based on team ID
                possession_team_id = situation.get("possession")
                if possession_team_id:
                    if possession_team_id == home_team.get("id"):
                        possession_indicator = "home"
                    elif possession_team_id == away_team.get("id"):
                        possession_indicator = "away"

            # Format period/quarter
            period = status.get("period", 0)
            period_text = ""
            if status["type"]["state"] == "in":
                 if period == 0: period_text = "Start" # Before kickoff
                 elif period == 1: period_text = "Q1"
                 elif period == 2: period_text = "Q2"
                 elif period == 3: period_text = "Q3" # Fixed: period 3 is 3rd quarter, not halftime
                 elif period == 4: period_text = "Q4"
                 elif period > 4: period_text = "OT" # OT starts after Q4
            elif status["type"]["state"] == "halftime" or status["type"]["name"] == "STATUS_HALFTIME": # Check explicit halftime state
                period_text = "HALF"
            elif status["type"]["state"] == "post":
                 if period > 4 : period_text = "Final/OT"
                 else: period_text = "Final"
            elif status["type"]["state"] == "pre":
                period_text = game_time # Show time for upcoming

            # Timeouts (assuming max 3 per half, not carried over well in standard API)
            # API often provides 'timeouts' directly under team, but reset logic is tricky
            # We might need to simplify this or just use a fixed display if API is unreliable
            home_timeouts = home_team.get("timeouts", 3) # Default to 3 if not specified
            away_timeouts = away_team.get("timeouts", 3) # Default to 3 if not specified

            # For upcoming games, we'll show based on number of games, not time window
            # For recent games, we'll show based on number of games, not time window
            is_within_window = True  # Always include games, let the managers filter by count

            details = {
                "id": game_event.get("id"),
                "start_time_utc": start_time_utc,
                "status_text": status["type"]["shortDetail"], # e.g., "Final", "7:30 PM", "Q1 12:34"
                "period": period,
                "period_text": period_text, # Formatted quarter/status
                "clock": status.get("displayClock", "0:00"),
                "is_live": status["type"]["state"] == "in",
                "is_final": status["type"]["state"] == "post",
                "is_upcoming": (status["type"]["state"] == "pre" or 
                               status["type"]["name"].lower() in ['scheduled', 'pre-game', 'status_scheduled']),
                "is_halftime": status["type"]["state"] == "halftime" or status["type"]["name"] == "STATUS_HALFTIME", # Added halftime check
                "home_abbr": home_abbr,
                "home_score": home_team.get("score", "0"),
                "home_record": home_record,
                "home_logo_path": os.path.join(self.logo_dir, f"{home_abbr}.png"),
                "home_timeouts": home_timeouts,
                "away_abbr": away_abbr,
                "away_score": away_team.get("score", "0"),
                "away_record": away_record,
                "away_logo_path": os.path.join(self.logo_dir, f"{away_abbr}.png"),
                "away_timeouts": away_timeouts,
                "game_time": game_time,
                "game_date": game_date,
                "down_distance_text": down_distance_text, # Added Down/Distance
                "possession": situation.get("possession") if situation else None, # ID of team with possession
                "possession_indicator": possession_indicator, # Added for easy home/away check
                "scoring_event": scoring_event, # Track scoring events (TOUCHDOWN, FIELD GOAL, PAT)
                "is_within_window": is_within_window, # Whether game is within display window
            }

            # Basic validation (can be expanded)
            if not details['home_abbr'] or not details['away_abbr']:
                 self.logger.warning(f"[NCAAFB] Missing team abbreviation in event: {details['id']}")
                 return None

            self.logger.debug(f"[NCAAFB] Extracted: {details['away_abbr']}@{details['home_abbr']}, Status: {status['type']['name']}, Live: {details['is_live']}, Final: {details['is_final']}, Upcoming: {details['is_upcoming']}")

            # Logo validation (optional but good practice)
            for team in ["home", "away"]:
                logo_path = details[f"{team}_logo_path"]
                # No need to check file existence here, _load_and_resize_logo handles it

            return details
        except Exception as e:
            # Log the problematic event structure if possible
            logging.error(f"[NCAAFB] Error extracting game details: {e} from event: {game_event.get('id')}", exc_info=True)
            return None
        
class FootballLive:
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.update_interval = self.mode_config.get("live_update_interval", 15)
        self.no_data_interval = 300
        self.last_update = 0
        self.live_games = []
        self.current_game_index = 0
        self.last_game_switch = 0
        self.game_display_duration = self.mode_config.get(
            "live_game_duration", 20)
        self.last_display_update = 0
        self.last_log_time = 0
        self.log_interval = 300
