import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import os
import pytz
from PIL import ImageDraw
import numpy as np
from pathlib import Path
from src.cache_manager import CacheManager
from src.display_manager import DisplayManager
from src.base_classes.baseball import BaseBall, BaseBallLive
from src.base_classes.sports import SportsRecent, SportsUpcoming
# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass

ESPN_MLB_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"

class BaseMLBManager(BaseBall):
    """Base class for MLB managers with common functionality."""

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        self.logger = logging.getLogger('MLB') # Changed logger name
        super().__init__(config=config, display_manager=display_manager, cache_manager=cache_manager, logger=self.logger, sport_key="mlb")
        print(self.is_enabled)
        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("mlb_recent", False)
        self.upcoming_enabled = display_modes.get("mlb_upcoming", False)
        self.live_enabled = display_modes.get("mlb_live", False)
        # self.logger.setLevel(logging.DEBUG)

        self.logger.info(f"Initialized MLB manager with display dimensions: {self.display_width}x{self.display_height}")
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}")
    


    def _fetch_mlb_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetch MLB game data from the ESPN API.
        Updated to use background service cache for Recent/Upcoming managers.
        """
        # Define cache key based on dates
        now = datetime.now(pytz.utc)
        season_year = now.year
        if now.month < 8:
            season_year = now.year - 1
        datestring = f"{season_year}0201-{season_year}1231"
        cache_key = f"mlb_schedule_{datestring}"

        if use_cache:
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                # Validate cached data structure
                if isinstance(cached_data, dict) and 'events' in cached_data:
                    self.logger.info(f"Using cached schedule for {season_year}")
                    return cached_data
                elif isinstance(cached_data, list):
                    # Handle old cache format (list of events)
                    self.logger.info(f"Using cached schedule for {season_year} (legacy format)")
                    return {'events': cached_data}
                else:
                    self.logger.warning(f"Invalid cached data format for {season_year}: {type(cached_data)}")
                    # Clear invalid cache
                    self.cache_manager.clear_cache(cache_key)
        
        # If background service is disabled, fall back to synchronous fetch
        if not self.background_enabled or not self.background_service:
            # return self._fetch_ncaa_api_data_sync(use_cache)
            return
        
        self.logger.info(f"Fetching full {season_year} season schedule from ESPN API...")

        # Start background fetch
        self.logger.info(f"Starting background fetch for {season_year} season schedule...")
        
        def fetch_callback(result):
            """Callback when background fetch completes."""
            if result.success:
                self.logger.info(f"Background fetch completed for {season_year}: {len(result.data.get('events'))} events")
            else:
                self.logger.error(f"Background fetch failed for {season_year}: {result.error}")
            
            # Clean up request tracking
            if season_year in self.background_fetch_requests:
                del self.background_fetch_requests[season_year]
        
        # Get background service configuration
        background_config = self.mode_config.get("background_service", {})
        timeout = background_config.get("request_timeout", 30)
        max_retries = background_config.get("max_retries", 3)
        priority = background_config.get("priority", 2)
        
        # Submit background fetch request
        request_id = self.background_service.submit_fetch_request(
            sport="mlb",
            year=season_year,
            url=ESPN_MLB_SCOREBOARD_URL,
            cache_key=cache_key,
            params={"dates": datestring, "limit": 1000},
            headers=self.headers,
            timeout=timeout,
            max_retries=max_retries,
            priority=priority,
            callback=fetch_callback
        )
        
        # Track the request
        self.background_fetch_requests[season_year] = request_id
        
        # For immediate response, try to get partial data
        partial_data = self._get_weeks_data("mlb")
        if partial_data:
            return partial_data
        return None
    
    def _fetch_data(self) -> Optional[Dict]:
        """Fetch data using shared data mechanism or direct fetch for live."""
        if isinstance(self, MLBLiveManager):
            return self._fetch_todays_games("mlb")
        else:
            return self._fetch_mlb_api_data(use_cache=True)
        
    def _fetch_game_odds(self, game: Dict) -> None:
        super()._fetch_odds(game, "mlb")

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
            self.logger.debug(f"Home team favored with spread: {favored_spread}")
        elif away_favored:
            favored_spread = away_spread
            favored_side = 'away'
            self.logger.debug(f"Away team favored with spread: {favored_spread}")
        else:
            self.logger.debug("No clear favorite - spreads: home={home_spread}, away={away_spread}")
        
        # Show the negative spread on the appropriate side
        if favored_spread is not None:
            spread_text = str(favored_spread)
            font = self.display_manager.extra_small_font
            
            if favored_side == 'home':
                # Home team is favored, show spread on right side
                spread_width = draw.textlength(spread_text, font=font)
                spread_x = width - spread_width  # Top right
                spread_y = 0
                self._draw_text_with_outline(draw, spread_text, (spread_x, spread_y), font, fill=(0, 255, 0))
                self.logger.debug(f"Showing home spread '{spread_text}' on right side")
            else:
                # Away team is favored, show spread on left side
                spread_x = 0  # Top left
                spread_y = 0
                self._draw_text_with_outline(draw, spread_text, (spread_x, spread_y), font, fill=(0, 255, 0))
                self.logger.debug(f"Showing away spread '{spread_text}' on left side")
        
        # Show over/under on the opposite side of the favored team
        over_under = odds.get('over_under')
        if over_under is not None:
            ou_text = f"O/U: {over_under}"
            font = self.display_manager.extra_small_font
            ou_width = draw.textlength(ou_text, font=font)
            
            if favored_side == 'home':
                # Home team is favored, show O/U on left side (opposite of spread)
                ou_x = 0  # Top left
                ou_y = 0
                self.logger.debug(f"Showing O/U '{ou_text}' on left side (home favored)")
            elif favored_side == 'away':
                # Away team is favored, show O/U on right side (opposite of spread)
                ou_x = width - ou_width  # Top right
                ou_y = 0
                self.logger.debug(f"Showing O/U '{ou_text}' on right side (away favored)")
            else:
                # No clear favorite, show O/U in center
                ou_x = (width - ou_width) // 2
                ou_y = 0
                self.logger.debug(f"Showing O/U '{ou_text}' in center (no clear favorite)")
            
            self._draw_text_with_outline(draw, ou_text, (ou_x, ou_y), font, fill=(0, 255, 0))

class MLBLiveManager(BaseMLBManager, BaseBallLive):
    """Manager for displaying live MLB games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config=config, display_manager=display_manager, cache_manager=cache_manager)
        self.logger = logging.getLogger('MLBLiveManager') # Changed logger name

        # Initialize with test game only if test mode is enabled
        if self.test_mode:
            self.current_game = {
                "home_abbr": "TB",
                "away_abbr": "TEX",
                "home_id": "343", "away_id": "567",
                "home_score": "3",
                "away_score": "2",
                "status": "live",
                "status_state": "live",
                "inning": 5,
                "inning_half": "top",
                "balls": 2,
                "strikes": 1,
                "outs": 1,
                "bases_occupied": [True, False, True],
                "home_logo_path": os.path.join(self.logo_dir, "TB.png"),
                "away_logo_path": os.path.join(self.logo_dir, "TEX.png"),
                "start_time": datetime.now(timezone.utc).isoformat(),
            }
            self.live_games = [self.current_game]
            self.logger.info("Initialized MLBLiveManager with test game: TB vs TEX")
        else:
            self.logger.info("Initialized MLBLiveManager in live mode")   

class MLBRecentManager(BaseMLBManager, SportsRecent):
    """Manager for displaying recent MLB games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('MLBRecentManager') # Changed logger name
        self.logger.info(f"Initialized MLBRecentManager with {len(self.favorite_teams)} favorite teams") # Changed log prefix


class MLBUpcomingManager(BaseMLBManager, SportsUpcoming):
    """Manager for displaying upcoming MLB games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger = logging.getLogger('MLBUpcomingManager') # Changed logger name
        self.logger.info(f"Initialized MLBUpcomingManager with {len(self.favorite_teams)} favorite teams") # Changed log prefix
