import os
import time
import logging
import requests
import json
from typing import Dict, Any, Optional, List
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from datetime import datetime, timedelta, timezone
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager
from src.config_manager import ConfigManager
from src.odds_manager import OddsManager
from src.background_data_service import get_background_service
import pytz
from src.display_functions import Football, SportsRecent, SportsUpcoming, FootballLive
# Constants
ESPN_NFL_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

class BaseNFLManager(Football): # Renamed class
    """Base class for NFL managers with common functionality."""
    # Class variables for warning tracking
    _no_data_warning_logged = False
    _last_warning_time = 0
    _warning_cooldown = 60  # Only log warnings once per minute
    _shared_data = None
    _last_shared_update = 0
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        self.logger = logging.getLogger('NCAAFB') # Changed logger name
        super().__init__(config=config, display_manager=display_manager, cache_manager=cache_manager, logger=self.logger, sport_key="ncaa_fb")

        # Check display modes to determine what data to fetch
        display_modes = self.mode_config.get("display_modes", {})
        self.recent_enabled = display_modes.get("nfl_recent", False)
        self.upcoming_enabled = display_modes.get("nfl_upcoming", False)
        self.live_enabled = display_modes.get("nfl_live", False)

        self.logger.info(f"Initialized NFL manager with display dimensions: {self.display_width}x{self.display_height}")
        self.logger.info(f"Logo directory: {self.logo_dir}")
        self.logger.info(f"Display modes - Recent: {self.recent_enabled}, Upcoming: {self.upcoming_enabled}, Live: {self.live_enabled}")


    def _fetch_odds(self, game: Dict) -> None:
        super()._fetch_odds(game, "college-football")
    
    def _fetch_nfl_api_data(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Fetches the full season schedule for NFL using background threading.
        Returns cached data immediately if available, otherwise starts background fetch.
        """
        now = datetime.now(pytz.utc)
        season_year = now.year
        if now.month < 8:
            season_year = now.year - 1
        datestring = f"{season_year}0801-{season_year+1}0301"
        cache_key = f"nfl_schedule_{season_year}"

        # Check cache first
        if use_cache:
            cached_data = self.cache_manager.get(cache_key)
            if cached_data:
                # Validate cached data structure
                if isinstance(cached_data, dict) and 'events' in cached_data:
                    self.logger.info(f"[NFL] Using cached schedule for {season_year}")
                    return cached_data
                elif isinstance(cached_data, list):
                    # Handle old cache format (list of events)
                    self.logger.info(f"[NFL] Using cached schedule for {season_year} (legacy format)")
                    return {'events': cached_data}
                else:
                    self.logger.warning(f"[NFL] Invalid cached data format for {season_year}: {type(cached_data)}")
                    # Clear invalid cache
                    self.cache_manager.delete(cache_key)
        
        # If background service is disabled, fall back to synchronous fetch
        if not self.background_enabled or not self.background_service:
            return self._fetch_nfl_api_data_sync(use_cache)
        
        # Check if we already have a background fetch in progress
        if season_year in self.background_fetch_requests:
            request_id = self.background_fetch_requests[season_year]
            result = self.background_service.get_result(request_id)
            
            if result and result.success:
                self.logger.info(f"[NFL] Background fetch completed for {season_year}")
                # Validate result data structure
                if isinstance(result.data, dict) and 'events' in result.data:
                    return result.data
                elif isinstance(result.data, list):
                    # Handle case where result.data is just the events list
                    return {'events': result.data}
                else:
                    self.logger.error(f"[NFL] Invalid background fetch result format: {type(result.data)}")
                    return None
            elif result and not result.success:
                self.logger.warning(f"[NFL] Background fetch failed for {season_year}: {result.error}")
                # Remove failed request and try again
                del self.background_fetch_requests[season_year]
            else:
                self.logger.info(f"[NFL] Background fetch in progress for {season_year}, using partial data")
                # Return partial data if available, or None to indicate no data yet
                partial_data = self._get_partial_nfl_data(season_year)
                if partial_data:
                    return {'events': partial_data}
                return None
        
        # Start background fetch
        self.logger.info(f"[NFL] Starting background fetch for {season_year} season schedule...")
        
        def fetch_callback(result):
            """Callback when background fetch completes."""
            if result.success:
                self.logger.info(f"[NFL] Background fetch completed for {season_year}: {len(result.data)} events")
            else:
                self.logger.error(f"[NFL] Background fetch failed for {season_year}: {result.error}")
            
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
            sport="nfl",
            year=season_year,
            url=ESPN_NFL_SCOREBOARD_URL,
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
        partial_data = self._get_partial_nfl_data(now.year)
        if partial_data:
            return {'events': partial_data}
        
        return None
    
    def _fetch_nfl_api_data_sync(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Synchronous fallback for fetching NFL data when background service is disabled.
        """
        now = datetime.now(pytz.utc)
        current_year = now.year
        cache_key = f"nfl_schedule_{current_year}"

        self.logger.info(f"[NFL] Fetching full {current_year} season schedule from ESPN API (sync mode)...")
        try:
            response = self.session.get(ESPN_NFL_SCOREBOARD_URL, params={"dates": current_year, "limit":1000}, headers=self.headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            events = data.get('events', [])
            
            if use_cache:
                self.cache_manager.set(cache_key, events)
            
            self.logger.info(f"[NFL] Successfully fetched {len(events)} events for the {current_year} season.")
            return {'events': events}
        except requests.exceptions.RequestException as e:
            self.logger.error(f"[NFL] API error fetching full schedule: {e}")
            return None
    
    def _get_partial_nfl_data(self, year: int) -> Optional[List]:
        """
        Get partial NFL data for immediate display while background fetch is in progress.
        This fetches current/recent games only for quick response.
        """
        try:
            # Fetch current week and next few days for immediate display
            now = datetime.now(pytz.utc)
            immediate_events = []
            
            start_date = now + timedelta(days=-1)
            end_date = now + timedelta(days=7)
            date_str = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
            
            response = self.session.get(ESPN_NFL_SCOREBOARD_URL, params={"dates": date_str},headers=self.headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            immediate_events = data.get('events', [])
                
            if immediate_events:
                self.logger.info(f"[NFL] Using {len(immediate_events)} immediate events while background fetch completes")
                return immediate_events
                
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"[NFL] Error fetching immediate games for {year}: {e}")
        
        return None

    def _fetch_data(self) -> Optional[Dict]:
        """Fetch data using shared data mechanism or direct fetch for live."""
        if isinstance(self, NFLLiveManager):
            return self._fetch_nfl_api_data(use_cache=False)
        else:
            return self._fetch_nfl_api_data(use_cache=True)

class NFLLiveManager(BaseNFLManager, FootballLive): # Renamed class
    """Manager for live NFL games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)

        if self.test_mode:
            # More detailed test game for NFL
            self.current_game = {
                "id": "test001",
                "home_abbr": "TB", "away_abbr": "DAL",
                "home_score": "21", "away_score": "17",
                "period": 4, "period_text": "Q4", "clock": "02:35",
                "down_distance_text": "1st & 10", 
                "possession": "TB", # Placeholder ID for home team
                "possession_indicator": "home", # Explicitly set for test
                "home_timeouts": 2, "away_timeouts": 3,
                "home_logo_path": os.path.join(self.logo_dir, "TB.png"),
                "away_logo_path": os.path.join(self.logo_dir, "DAL.png"),
                "is_live": True, "is_final": False, "is_upcoming": False, "is_halftime": False,
                "status_text": "Q4 02:35"
            }
            self.live_games = [self.current_game]
            logging.info("[NFL] Initialized NFLLiveManager with test game: BUF vs KC")
        else:
            logging.info("[NFL] Initialized NFLLiveManager in live mode")

class NFLRecentManager(BaseNFLManager, SportsRecent): # Renamed class
    """Manager for recently completed NFL games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info(f"Initialized NFLRecentManager with {len(self.favorite_teams)} favorite teams")

class NFLUpcomingManager(BaseNFLManager, SportsUpcoming): # Renamed class
    """Manager for upcoming NFL games."""
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.logger.info(f"Initialized NFLUpcomingManager with {len(self.favorite_teams)} favorite teams")
