from typing import Dict, Any, Optional, List
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager
from datetime import datetime, timezone, timedelta
import logging
from PIL import Image, ImageDraw, ImageFont
import time
import pytz
from src.base_classes.sports import SportsCore
import requests

class BaseBall(SportsCore):
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager, logger: logging.Logger, sport_key: str):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)

    def _fetch_game_odds(self, _: Dict) -> None:
        pass

    def _fetch_odds(self, game: Dict, league: str) -> None:
        super()._fetch_odds(game, "baseball", league)

    def _extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract relevant game details from ESPN MLB API response."""
        details, home_team, away_team, status, situation = self._extract_game_details_common(game_event)
        if details is None or home_team is None or away_team is None or status is None:
            return

        try:
            competition = game_event["competitions"][0]
            status = competition["status"]
            status_state = status['type']['state'].lower()

            inning = 1
            inning_half = 'top'
            balls = 0
            strikes = 0
            outs = 0
            bases_occupied = [False, False, False]

            if situation and status["type"]["state"] == "in":                
                # Detect scoring events from status detail
                status_detail = status["type"].get("detail", "").lower()
                status_short = status["type"].get("shortDetail", "").lower()
                # For live games, get detailed state
                inning = status.get('period', 1)  # Get inning from status period

                # Determine inning half from status information
                inning_half = 'top'  # Default

                # Handle end of inning: next inning is top
                if 'end' in status_detail or 'end' in status_short:
                    inning_half = 'top'
                    inning = status.get('period', 1) + 1 # Use period and increment for next inning
                # Handle middle of inning: next is bottom of current inning
                elif 'mid' in status_detail or 'mid' in status_short:
                    inning_half = 'bottom'
                # Handle bottom of inning
                elif 'bottom' in status_detail or 'bot' in status_detail or 'bottom' in status_short or 'bot' in status_short:
                    inning_half = 'bottom'
                # Handle top of inning
                elif 'top' in status_detail or 'top' in status_short:
                    inning_half = 'top'
                
                # Get count from the correct location in the API response
                count = situation.get('count', {})
                balls = count.get('balls', 0)
                strikes = count.get('strikes', 0)
                outs = situation.get('outs', 0)
                
                # Try alternative locations for count data
                if balls == 0 and strikes == 0:
                    # First try the summary field
                    if 'summary' in situation:
                        try:
                            count_summary = situation['summary']
                            balls, strikes = map(int, count_summary.split('-'))
                        except (ValueError, AttributeError):
                            self.logger.debug(f"[MLB] Could not parse summary count for game {details.get('id')}")
                    else:
                        # Check if count is directly in situation
                        balls = situation.get('balls', 0)
                        strikes = situation.get('strikes', 0)
                # Get base runners
                bases_occupied = [
                    situation.get('onFirst', False),
                    situation.get('onSecond', False),
                    situation.get('onThird', False)
                ]
            details.update({
                'status': status,
                'status_state': status_state,
                'inning': inning,
                'inning_half': inning_half,
                'balls': balls,
                'strikes': strikes,
                'outs': outs,
                'bases_occupied': bases_occupied,
                'start_time': game_event['date']
            })

            # Basic validation (can be expanded)
            if not details['home_abbr'] or not details['away_abbr']:
                 self.logger.warning(f"Missing team abbreviation in event: {details['id']}")
                 return None

            self.logger.debug(f"Extracted: {details['away_abbr']}@{details['home_abbr']}, Status: {status['type']['name']}, Live: {details['is_live']}, Final: {details['is_final']}, Upcoming: {details['is_upcoming']}")

            return details
        except Exception as e:
            # Log the problematic event structure if possible
            logging.error(f"Error extracting game details: {e} from event: {game_event.get('id')}", exc_info=True)
            return None

    def _fetch_todays_games(self, league: str) -> Optional[Dict]:
        """Fetch only today's games for live updates (not entire season)."""
        return super()._fetch_todays_games("baseball", league)
        
    def _get_weeks_data(self, league: str) -> Optional[Dict]:
        """
        Get partial data for immediate display while background fetch is in progress.
        This fetches current/recent games only for quick response.
        """
        return super()._get_weeks_data("baseball", league)

class BaseBallLive(BaseBall):
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager, logger: logging.Logger, sport_key: str):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.update_interval = self.mode_config.get("live_update_interval", 15)
        self.no_data_interval = 300
        self.last_update = 0
        self.live_games = []
        self.current_game_index = 0
        self.last_game_switch = 0
        self.game_display_duration = self.mode_config.get("live_game_duration", 20)
        self.last_display_update = 0
        self.last_log_time = 0
        self.log_interval = 300

    def update(self):
        """Update live game data."""
        current_time = time.time()
        # Use longer interval if no game data
        interval = self.no_data_interval if not self.live_games else self.update_interval
        
        if current_time - self.last_update >= interval:
            self.last_update = current_time
            
            if self.test_mode:
                # For testing, we'll just update the game state to show it's working
                if self.current_game:
                    # Update inning half
                    if self.current_game["inning_half"] == "top":
                        self.current_game["inning_half"] = "bottom"
                    else:
                        self.current_game["inning_half"] = "top"
                        self.current_game["inning"] += 1
                    
                    # Update count
                    self.current_game["balls"] = (self.current_game["balls"] + 1) % 4
                    self.current_game["strikes"] = (self.current_game["strikes"] + 1) % 3
                    
                    # Update outs
                    self.current_game["outs"] = (self.current_game["outs"] + 1) % 3
                    
                    # Update bases
                    self.current_game["bases_occupied"] = [
                        not self.current_game["bases_occupied"][0],
                        not self.current_game["bases_occupied"][1],
                        not self.current_game["bases_occupied"][2]
                    ]
                    
                    # Update score occasionally
                    if self.current_game["inning"] % 2 == 0:
                        self.current_game["home_score"] = str(int(self.current_game["home_score"]) + 1)
                    else:
                        self.current_game["away_score"] = str(int(self.current_game["away_score"]) + 1)
            else:
                # Fetch live game data from MLB API, bypassing the cache
                data = self._fetch_data()
                new_live_games = []
                if data and "events" in data:
                    for event in data["events"]:
                        details = self._extract_game_details(event)
                        # Only process games that are actually in progress
                        if details and details["is_live"]:
                            if self.mode_config.get("show_favorite_teams_only", False):
                                if details["home_abbr"] in self.favorite_teams or details["away_abbr"] in self.favorite_teams:
                                    if self.show_odds:
                                        self._fetch_game_odds(details)
                                    new_live_games.append(details)
                            else:
                                if self.show_odds:
                                    self._fetch_game_odds(details)
                                new_live_games.append(details)
                    # Only log if there's a change in games or enough time has passed
                    should_log = (
                        current_time - self.last_log_time >= self.log_interval or
                        len(new_live_games) != len(self.live_games) or
                        not self.live_games  # Log if we had no games before
                    )
                    
                    if should_log:
                        if new_live_games:
                            self.logger.info(f"[MLB] Found {len(new_live_games)} live games")
                            for game in new_live_games:
                                self.logger.info(f"[MLB] Live game: {game['away_abbr']} vs {game['home_abbr']} - {game['inning_half']}{game['inning']}, {game['balls']}-{game['strikes']}")
                        else:
                            self.logger.info("[MLB] No live games found")
                        self.last_log_time = current_time

                    # Update game list and current game
                    if new_live_games:
                        # Check if the games themselves changed, not just scores/time
                        new_game_ids = {g['id'] for g in new_live_games}
                        current_game_ids = {g['id'] for g in self.live_games}

                        if new_game_ids != current_game_ids:
                            self.live_games = sorted(new_live_games, key=lambda g: g.get('start_time_utc') or datetime.now(timezone.utc)) # Sort by start time
                            # Reset index if current game is gone or list is new
                            if not self.current_game or self.current_game['id'] not in new_game_ids:
                                self.current_game_index = 0
                                self.current_game = self.live_games[0] if self.live_games else None
                                self.last_game_switch = current_time
                            else:
                                # Find current game's new index if it still exists
                                try:
                                     self.current_game_index = next(i for i, g in enumerate(self.live_games) if g['id'] == self.current_game['id'])
                                     self.current_game = self.live_games[self.current_game_index] # Update current_game with fresh data
                                except StopIteration: # Should not happen if check above passed, but safety first
                                     self.current_game_index = 0
                                     self.current_game = self.live_games[0]
                                     self.last_game_switch = current_time

                        else:
                             # Just update the data for the existing games
                             temp_game_dict = {g['id']: g for g in new_live_games}
                             self.live_games = [temp_game_dict.get(g['id'], g) for g in self.live_games] # Update in place
                             if self.current_game:
                                  self.current_game = temp_game_dict.get(self.current_game['id'], self.current_game)
                    else:
                        # No live games found
                        if self.live_games: # Were there games before?
                            self.logger.info("Live games previously showing have ended or are no longer live.") # Changed log prefix
                        self.live_games = []
                        self.current_game = None
                        self.current_game_index = 0

                else:
                    # Error fetching data or no events
                     if self.live_games: # Were there games before?
                         self.logger.warning("Could not fetch update; keeping existing live game data for now.") # Changed log prefix
                     else:
                         self.logger.warning("Could not fetch data and no existing live games.") # Changed log prefix
                         self.current_game = None # Clear current game if fetch fails and no games were active

            # Handle game switching (outside test mode check)
            if not self.test_mode and len(self.live_games) > 1 and (current_time - self.last_game_switch) >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.live_games)
                self.current_game = self.live_games[self.current_game_index]
                self.last_game_switch = current_time
                self.logger.info(f"Switched live view to: {self.current_game['away_abbr']}@{self.current_game['home_abbr']}") # Changed log prefix
                # Force display update via flag or direct call if needed, but usually let main loop handle


    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Create a display image for a live MLB game."""
        try:
            main_img = Image.new('RGBA', (self.display_width, self.display_height), (0, 0, 0, 255))
            overlay = Image.new('RGBA', (self.display_width, self.display_height), (0, 0, 0, 0))
            draw_overlay = ImageDraw.Draw(overlay)
            
            # Load and place team logos
            home_logo = self._load_and_resize_logo(game["home_id"], game["home_abbr"], game["home_logo_path"], game.get("home_logo_url"))
            away_logo = self._load_and_resize_logo(game["away_id"], game["away_abbr"], game["away_logo_path"], game.get("away_logo_url"))

            if not home_logo or not away_logo:
                self.logger.error(f"Failed to load logos for game: {game.get('id')}") # Changed log prefix
                draw_final = ImageDraw.Draw(main_img.convert('RGB'))
                self._draw_text_with_outline(draw_final, "Logo Error", (5,5), self.fonts['status'])
                self.display_manager.image.paste(main_img.convert('RGB'), (0, 0))
                self.display_manager.update_display()
                return

            # Calculate vertical center line for alignment
            center_y = self.display_height // 2

            # Draw home team logo (far right, extending beyond screen)
            home_x = self.display_width - home_logo.width + 2
            home_y = center_y - (home_logo.height // 2)
            main_img.paste(home_logo, (home_x, home_y), home_logo)

            away_x = -2
            away_y = center_y - (away_logo.height // 2)
            main_img.paste(away_logo, (away_x, away_y), away_logo)
            
            # Define default text color
            text_color = (255, 255, 255)
            
            # Draw Inning (Top Center)
            inning_half = game['inning_half']
            inning_num = game['inning']
            if game['status'] in ['status_final', 'final', 'completed']:
                inning_text = "FINAL"
            else:
                inning_half_indicator = "▲" if game['inning_half'].lower() == 'top' else "▼"
                inning_num = game['inning']
                inning_text = f"{inning_half_indicator}{inning_num}"
            
            inning_bbox = draw_overlay.textbbox((0, 0), inning_text, font=self.display_manager.font)
            inning_width = inning_bbox[2] - inning_bbox[0]
            inning_x = (self.display_width - inning_width) // 2
            inning_y = 1 # Position near top center
            # draw_overlay.text((inning_x, inning_y), inning_text, fill=(255, 255, 255), font=self.display_manager.font)
            self._draw_text_with_outline(draw_overlay, inning_text, (inning_x, inning_y), self.display_manager.font)
            
            # --- REVISED BASES AND OUTS DRAWING --- 
            bases_occupied = game['bases_occupied'] # [1st, 2nd, 3rd]
            outs = game.get('outs', 0)
            inning_half = game['inning_half']
            
            # Define geometry
            base_diamond_size = 7
            out_circle_diameter = 3
            out_vertical_spacing = 2 # Space between out circles
            spacing_between_bases_outs = 3 # Horizontal space between base cluster and out column
            base_vert_spacing = 1 # Internal vertical space in base cluster
            base_horiz_spacing = 1 # Internal horizontal space in base cluster
            
            # Calculate cluster dimensions
            base_cluster_height = base_diamond_size + base_vert_spacing + base_diamond_size
            base_cluster_width = base_diamond_size + base_horiz_spacing + base_diamond_size
            out_cluster_height = 3 * out_circle_diameter + 2 * out_vertical_spacing
            out_cluster_width = out_circle_diameter
            
            # Calculate overall start positions
            overall_start_y = inning_bbox[3] + 0 # Start immediately below inning text (moved up 3 pixels)
            
            # Center the BASE cluster horizontally
            bases_origin_x = (self.display_width - base_cluster_width) // 2
            
            # Determine relative positions for outs based on inning half
            if inning_half == 'top': # Away batting, outs on left
                outs_column_x = bases_origin_x - spacing_between_bases_outs - out_cluster_width
            else: # Home batting, outs on right
                outs_column_x = bases_origin_x + base_cluster_width + spacing_between_bases_outs
            
            # Calculate vertical alignment offset for outs column (center align with bases cluster)
            outs_column_start_y = overall_start_y + (base_cluster_height // 2) - (out_cluster_height // 2)

            # --- Draw Bases (Diamonds) ---
            base_color_occupied = (255, 255, 255)
            base_color_empty = (255, 255, 255) # Outline color
            h_d = base_diamond_size // 2 
            
            # 2nd Base (Top center relative to bases_origin_x)
            c2x = bases_origin_x + base_cluster_width // 2 
            c2y = overall_start_y + h_d
            poly2 = [(c2x, overall_start_y), (c2x + h_d, c2y), (c2x, c2y + h_d), (c2x - h_d, c2y)]
            if bases_occupied[1]: draw_overlay.polygon(poly2, fill=base_color_occupied)
            else: draw_overlay.polygon(poly2, outline=base_color_empty)
            
            base_bottom_y = c2y + h_d # Bottom Y of 2nd base diamond
            
            # 3rd Base (Bottom left relative to bases_origin_x)
            c3x = bases_origin_x + h_d 
            c3y = base_bottom_y + base_vert_spacing + h_d
            poly3 = [(c3x, base_bottom_y + base_vert_spacing), (c3x + h_d, c3y), (c3x, c3y + h_d), (c3x - h_d, c3y)]
            if bases_occupied[2]: draw_overlay.polygon(poly3, fill=base_color_occupied)
            else: draw_overlay.polygon(poly3, outline=base_color_empty)

            # 1st Base (Bottom right relative to bases_origin_x)
            c1x = bases_origin_x + base_cluster_width - h_d
            c1y = base_bottom_y + base_vert_spacing + h_d
            poly1 = [(c1x, base_bottom_y + base_vert_spacing), (c1x + h_d, c1y), (c1x, c1y + h_d), (c1x - h_d, c1y)]
            if bases_occupied[0]: draw_overlay.polygon(poly1, fill=base_color_occupied)
            else: draw_overlay.polygon(poly1, outline=base_color_empty)
            
            # --- Draw Outs (Vertical Circles) ---
            circle_color_out = (255, 255, 255) 
            circle_color_empty_outline = (100, 100, 100) 

            for i in range(3):
                cx = outs_column_x
                cy = outs_column_start_y + i * (out_circle_diameter + out_vertical_spacing)
                coords = [cx, cy, cx + out_circle_diameter, cy + out_circle_diameter]
                if i < outs:
                    draw_overlay.ellipse(coords, fill=circle_color_out)
                else:
                    draw_overlay.ellipse(coords, outline=circle_color_empty_outline)

            # --- Draw Balls-Strikes Count (BDF Font) --- 
            balls = game.get('balls', 0)
            strikes = game.get('strikes', 0)
            
            # Add debug logging for count with cooldown
            # current_time = time.time()
            # if (game['home_team'] in self.favorite_teams or game['away_team'] in self.favorite_teams) and \
            #    current_time - self.last_count_log_time >= self.count_log_interval:
            #     self.logger.debug(f"[MLB] Displaying count: {balls}-{strikes}")
            #     self.logger.debug(f"[MLB] Raw count data: balls={game.get('balls')}, strikes={game.get('strikes')}")
            #     self.last_count_log_time = current_time
            
            count_text = f"{balls}-{strikes}"
            bdf_font = self.display_manager.calendar_font
            bdf_font.set_char_size(height=7*64) # Set 7px height
            count_text_width = self.display_manager.get_text_width(count_text, bdf_font)
            
            # Position below the base/out cluster
            cluster_bottom_y = overall_start_y + base_cluster_height # Find the bottom of the taller part (bases)
            count_y = cluster_bottom_y + 2 # Start 2 pixels below cluster
            
            # Center horizontally within the BASE cluster width
            count_x = bases_origin_x + (base_cluster_width - count_text_width) // 2
            
            # Ensure draw object is set and draw text
            self.display_manager.draw = draw_overlay 
            # self.display_manager._draw_bdf_text(count_text, count_x, count_y, text_color, font=bdf_font)
            # Use _draw_text_with_outline for count text
            # self._draw_text_with_outline(draw, count_text, (count_x, count_y), bdf_font, fill=text_color)

            # Draw Balls-Strikes Count with outline using BDF font
            # Define outline color (consistent with _draw_text_with_outline default)
            outline_color_for_bdf = (0, 0, 0)
            
            # Draw outline
            for dx_offset, dy_offset in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
                self.display_manager._draw_bdf_text(count_text, count_x + dx_offset, count_y + dy_offset, color=outline_color_for_bdf, font=bdf_font)
            
            # Draw main text
            self.display_manager._draw_bdf_text(count_text, count_x, count_y, color=text_color, font=bdf_font)

            # Draw Team:Score at the bottom (matching main branch format)
            score_font = self.display_manager.font  # Use PressStart2P
            outline_color = (0, 0, 0)
            score_text_color = (255, 255, 255)  # Use a specific name for score text color

            # Helper function for outlined text
            def draw_bottom_outlined_text(x, y, text):
                self._draw_text_with_outline(draw_overlay, text, (x, y), score_font, fill=score_text_color, outline_color=outline_color)

            away_abbr = game['away_abbr']
            home_abbr = game['home_abbr']
            away_score_str = str(game['away_score'])
            home_score_str = str(game['home_score'])

            away_text = f"{away_abbr}:{away_score_str}"
            home_text = f"{home_abbr}:{home_score_str}"
            
            # Calculate Y position (bottom edge)
            # Get font height (approximate or precise)
            try:
                font_height = score_font.getbbox("A")[3] - score_font.getbbox("A")[1]
            except AttributeError:
                font_height = 8  # Fallback for default font
            score_y = self.display_height - font_height - 2  # 2 pixels padding from bottom
            
            # Away Team:Score (Bottom Left)
            away_score_x = 2  # 2 pixels padding from left
            draw_bottom_outlined_text(away_score_x, score_y, away_text)
            
            # Home Team:Score (Bottom Right)
            home_text_bbox = draw_overlay.textbbox((0, 0), home_text, font=score_font)
            home_text_width = home_text_bbox[2] - home_text_bbox[0]
            home_score_x = self.display_width - home_text_width - 2  # 2 pixels padding from right
            draw_bottom_outlined_text(home_score_x, score_y, home_text)

            # Draw gambling odds if available
            if 'odds' in game and game['odds']:
                self._draw_dynamic_odds(draw_overlay, game['odds'], self.display_width, self.display_height)

            # Composite the text overlay onto the main image
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert('RGB') # Convert for display

            # Display the final image
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display() # Update display here for live
        except Exception as e:
            self.logger.error(f"Error displaying upcoming game: {e}", exc_info=True) # Changed log prefix


    def _draw_base_indicators(self, draw: ImageDraw.Draw, bases_occupied: List[bool], center_x: int, y: int) -> None:
        """Draw base indicators on the display."""
        base_size = 8  # Increased from 6 to 8 for better visibility
        base_spacing = 10  # Increased from 8 to 10 for better spacing
        
        # Draw diamond outline with thicker lines
        diamond_points = [
            (center_x, y),  # Home
            (center_x - base_spacing, y - base_spacing),  # First
            (center_x, y - 2 * base_spacing),  # Second
            (center_x + base_spacing, y - base_spacing)  # Third
        ]
        
        # Draw thicker diamond outline
        for i in range(len(diamond_points)):
            start = diamond_points[i]
            end = diamond_points[(i + 1) % len(diamond_points)]
            draw.line([start, end], fill=(255, 255, 255), width=2)  # Added width parameter for thicker lines
        
        # Draw occupied bases with larger circles and outline
        for i, occupied in enumerate(bases_occupied):
            x = diamond_points[i+1][0] - base_size//2
            y = diamond_points[i+1][1] - base_size//2
            
            # Draw base circle with outline
            if occupied:
                # Draw white outline
                draw.ellipse([x-1, y-1, x + base_size+1, y + base_size+1], fill=(255, 255, 255))
                # Draw filled circle
                draw.ellipse([x+1, y+1, x + base_size-1, y + base_size-1], fill=(0, 0, 0))
            else:
                # Draw empty base with outline
                draw.ellipse([x, y, x + base_size, y + base_size], outline=(255, 255, 255), width=1)

    def _format_game_time(self, game_time: str) -> str:
        """Format game time for display."""
        try:
            # Get timezone from config
            timezone_str = self.config.get('timezone', 'UTC')
            try:
                tz = pytz.timezone(timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                self.logger.warning(f"Unknown timezone: {timezone_str}, falling back to UTC")
                tz = pytz.UTC
            
            # Convert game time to local timezone
            dt = datetime.fromisoformat(game_time.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            local_dt = dt.astimezone(tz)
            
            return local_dt.strftime("%I:%M%p").lstrip('0')
        except Exception as e:
            self.logger.error(f"Error formatting game time: {e}")
            return "TBD"