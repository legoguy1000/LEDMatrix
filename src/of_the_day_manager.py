import os
import json
import logging
from datetime import date
from PIL import ImageDraw, ImageFont
from src.config_manager import ConfigManager
import time
import freetype

# Configure logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class OfTheDayManager:
    def __init__(self, display_manager, config):
        logger.info("Initializing OfTheDayManager")
        self.display_manager = display_manager
        self.config = config
        self.of_the_day_config = config.get('of_the_day', {})
        self.enabled = self.of_the_day_config.get('enabled', False)
        self.update_interval = self.of_the_day_config.get('update_interval', 3600)  # 1 hour default
        self.subtitle_rotate_interval = self.of_the_day_config.get('subtitle_rotate_interval', 10)  # 10 seconds default
        self.display_rotate_interval = self.of_the_day_config.get('display_rotate_interval', 30)  # 30 seconds default
        self.last_update = 0
        self.last_display_log = 0
        self.current_day = None
        self.current_items = {}
        self.current_item_index = 0
        self.current_category_index = 0
        self.last_drawn_category_index = -1
        self.last_drawn_day = None
        self.force_clear = False
        self.rotation_state = 0  # 0 = subtitle, 1 = description
        self.last_rotation_time = time.time()
        self.last_category_rotation_time = time.time()

        # Load fonts with robust path resolution and fallbacks
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            font_dir = os.path.abspath(os.path.join(script_dir, '..', 'assets', 'fonts'))

            def _safe_load_bdf_font(filename):
                try:
                    font_path = os.path.abspath(os.path.join(font_dir, filename))
                    if not os.path.exists(font_path):
                        raise FileNotFoundError(f"Font file not found: {font_path}")
                    return freetype.Face(font_path)
                except Exception as e:
                    logger.error(f"Failed to load BDF font '{filename}': {e}")
                    return None

            self.title_font = _safe_load_bdf_font('ic8x8u.bdf')
            self.body_font = _safe_load_bdf_font('MatrixLight6.bdf')

            # Fallbacks if BDF fonts aren't available
            if self.title_font is None:
                self.title_font = getattr(self.display_manager, 'bdf_5x7_font', None) or getattr(self.display_manager, 'small_font', ImageFont.load_default())
                logger.warning("Using fallback font for title in OfTheDayManager")
            if self.body_font is None:
                self.body_font = getattr(self.display_manager, 'bdf_5x7_font', None) or getattr(self.display_manager, 'small_font', ImageFont.load_default())
                logger.warning("Using fallback font for body in OfTheDayManager")

            # Log font types for debugging
            logger.debug(f"Title font type: {type(self.title_font).__name__}")
            logger.debug(f"Body font type: {type(self.body_font).__name__}")
        except Exception as e:
            logger.error(f"Unexpected error during font initialization: {e}")
            # Last-resort fallback
            self.title_font = ImageFont.load_default()
            self.body_font = ImageFont.load_default()

        # Load categories and their data
        self.categories = self.of_the_day_config.get('categories', {})
        self.category_order = self.of_the_day_config.get('category_order', [])
        
        # Display properties
        self.title_color = (255, 255, 255)  # White
        self.subtitle_color = (200, 200, 200)  # Light gray
        self.background_color = (0, 0, 0)  # Black
        
        # State management
        self.force_clear = False
        self.last_drawn_category_index = -1
        self.last_drawn_day = None
        
        # Load data files
        self.data_files = {}
        logger.info("Loading data files for OfTheDayManager...")
        self._load_data_files()
        logger.info(f"Loaded {len(self.data_files)} data files: {list(self.data_files.keys())}")
        
        logger.info(f"OfTheDayManager configuration: enabled={self.enabled}, categories={list(self.categories.keys())}")
        
        if self.enabled:
            logger.info("OfTheDayManager is enabled, loading today's items...")
            self._load_todays_items()
            logger.info(f"After loading, current_items has {len(self.current_items)} items: {list(self.current_items.keys())}")
        else:
            logger.warning("OfTheDayManager is disabled in configuration")
    
    def _load_data_files(self):
        """Load all data files for enabled categories."""
        if not self.enabled:
            logger.debug("OfTheDayManager is disabled, skipping data file loading")
            return
            
        logger.info(f"Loading data files for {len(self.categories)} categories")
        logger.info(f"Current working directory: {os.getcwd()}")
        logger.info(f"Script directory: {os.path.dirname(__file__)}")
            
        for category_name, category_config in self.categories.items():
            logger.debug(f"Processing category: {category_name}")
            if not category_config.get('enabled', True):
                logger.debug(f"Skipping disabled category: {category_name}")
                continue
                
            data_file = category_config.get('data_file')
            if not data_file:
                logger.warning(f"No data file specified for category: {category_name}")
                continue
                
            try:
                # Try relative path first, then absolute
                file_path = data_file
                if not os.path.isabs(file_path):
                    # If data_file already contains 'of_the_day/', use it as is
                    if data_file.startswith('of_the_day/'):
                        file_path = os.path.join(os.path.dirname(__file__), '..', data_file)
                    else:
                        file_path = os.path.join(os.path.dirname(__file__), '..', 'of_the_day', data_file)
                
                # Convert to absolute path for better logging
                file_path = os.path.abspath(file_path)
                logger.debug(f"Attempting to load {category_name} from: {file_path}")
                
                if os.path.exists(file_path):
                    logger.debug(f"File exists, checking permissions...")
                    if not os.access(file_path, os.R_OK):
                        logger.error(f"File exists but is not readable: {file_path}")
                        self.data_files[category_name] = {}
                        continue
                    
                    # Get file size for debugging
                    file_size = os.path.getsize(file_path)
                    logger.debug(f"File size: {file_size} bytes")
                    
                    with open(file_path, 'r', encoding='utf-8') as f:
                        self.data_files[category_name] = json.load(f)
                    
                    logger.info(f"Loaded data file for {category_name}: {len(self.data_files[category_name])} items")
                    logger.debug(f"Sample keys from {category_name}: {list(self.data_files[category_name].keys())[:5]}")
                    
                    # Validate that we have data
                    if not self.data_files[category_name]:
                        logger.warning(f"Loaded data file for {category_name} but it's empty!")
                    
                else:
                    logger.error(f"Data file not found for {category_name}: {file_path}")
                    logger.error(f"Directory contents: {os.listdir(os.path.dirname(file_path)) if os.path.exists(os.path.dirname(file_path)) else 'Parent directory does not exist'}")
                    self.data_files[category_name] = {}
                    
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error loading data file for {category_name}: {e}")
                logger.error(f"File path: {file_path}")
                self.data_files[category_name] = {}
            except UnicodeDecodeError as e:
                logger.error(f"Unicode decode error loading data file for {category_name}: {e}")
                logger.error(f"File path: {file_path}")
                self.data_files[category_name] = {}
            except Exception as e:
                logger.error(f"Unexpected error loading data file for {category_name}: {e}")
                logger.error(f"File path: {file_path}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                self.data_files[category_name] = {}
    
    def _load_todays_items(self):
        """Load items for today based on the current date."""
        if not self.enabled:
            return
            
        today = date.today()
        day_of_year = today.timetuple().tm_yday
        logger.info(f"Loading items for day {day_of_year} of the year")
        logger.debug(f"Available data files: {list(self.data_files.keys())}")
        
        self.current_items = {}
        
        for category_name, category_config in self.categories.items():
            if not category_config.get('enabled', True):
                logger.debug(f"Skipping disabled category: {category_name}")
                continue
                
            data = self.data_files.get(category_name, {})
            if not data:
                logger.warning(f"No data loaded for category: {category_name}")
                logger.debug(f"Data files available: {list(self.data_files.keys())}")
                logger.debug(f"Category config: {category_config}")
                continue
                
            logger.debug(f"Checking category {category_name} for day {day_of_year}")
            logger.debug(f"Data file contains {len(data)} items")
            
            # Get item for today (day of year)
            item = data.get(str(day_of_year))
            if item:
                self.current_items[category_name] = item
                logger.info(f"Loaded {category_name} item for day {day_of_year}: {item.get('title', 'No title')}")
            else:
                logger.warning(f"No item found for {category_name} on day {day_of_year}")
                # Show more detailed information about available days
                available_days = [k for k in data.keys() if k.isdigit()]
                nearby_days = [k for k in available_days if abs(int(k) - day_of_year) <= 5]
                logger.debug(f"Available days in {category_name}: {sorted(available_days)[:10]}...")
                logger.debug(f"Days near {day_of_year}: {sorted(nearby_days)}")
        
        self.current_day = today
        self.current_category_index = 0
        self.current_item_index = 0
    
    def update(self, current_time):
        """Update items if needed (daily or on interval)."""
        if not self.enabled:
            logger.debug("OfTheDayManager is disabled, skipping update")
            return
        
        today = date.today()
        
        # Check if we need to load new items (new day or first time)
        if self.current_day != today:
            logger.info("New day detected, loading new items")
            self._load_todays_items()
        
        # Check if we need to update based on interval
        if current_time - self.last_update > self.update_interval:
            logger.debug("OfTheDayManager update interval reached")
            self.last_update = current_time
    
    def _draw_bdf_text(self, draw, face, text, x, y, color=(255,255,255)):
        """Draw text for both BDF (FreeType Face) and PIL TTF fonts."""
        try:
            # If we have a PIL font, use native text rendering
            if not isinstance(face, freetype.Face):
                draw.text((x, y), text, fill=color, font=face)
                return

            # Otherwise, render BDF glyphs manually
            for char in text:
                face.load_char(char)
                bitmap = face.glyph.bitmap
                
                for i in range(bitmap.rows):
                    for j in range(bitmap.width):
                        try:
                            byte_index = i * bitmap.pitch + (j // 8)
                            if byte_index < len(bitmap.buffer):
                                byte = bitmap.buffer[byte_index]
                                if byte & (1 << (7 - (j % 8))):
                                    draw_y = y - face.glyph.bitmap_top + i
                                    draw_x = x + face.glyph.bitmap_left + j
                                    draw.point((draw_x, draw_y), fill=color)
                        except IndexError:
                            logger.warning(f"Index out of range for char '{char}' at position ({i}, {j})")
                            continue
                x += face.glyph.advance.x >> 6
        except Exception as e:
            logger.error(f"Error in _draw_bdf_text: {e}", exc_info=True)

    def draw_item(self, category_name, item):
        try:
            title = item.get('title', 'No Title')
            subtitle = item.get('subtitle', '')
            description = item.get('description', '')
            draw = ImageDraw.Draw(self.display_manager.image)
            matrix_width = self.display_manager.matrix.width
            matrix_height = self.display_manager.matrix.height
            title_font = self.title_font
            body_font = self.body_font
            
            # Get font heights using DisplayManager helpers (handles BDF and PIL fonts)
            try:
                title_height = self.display_manager.get_font_height(title_font)
            except Exception:
                title_height = 8
            try:
                body_height = self.display_manager.get_font_height(body_font)
            except Exception:
                body_height = 8
            
            # --- Draw Title (always at top) ---
            title_y = title_height  # Position title so its bottom is at title_height
            
            # Calculate title width for centering (robust to font type)
            try:
                title_width = self.display_manager.get_text_width(title, title_font)
            except Exception:
                title_width = len(title) * 6
            
            # Center the title
            title_x = (matrix_width - title_width) // 2
            self._draw_bdf_text(draw, title_font, title, title_x, title_y, color=self.title_color)
            
            # Underline below title (centered)
            underline_y = title_height + 1
            underline_x_start = title_x
            underline_x_end = title_x + title_width
            draw.line([(underline_x_start, underline_y), (underline_x_end, underline_y)], fill=self.title_color, width=1)

            # --- Draw Subtitle or Description (rotating) ---
            # Start subtitle/description below the title and underline
            # Account for title height + underline + spacing
            y_start = title_height + body_height + 4  # Space for underline
            available_height = matrix_height - y_start
            available_width = matrix_width - 2
            
            if self.rotation_state == 0 and subtitle:
                # Show subtitle
                wrapped = self._wrap_text(subtitle, available_width, body_font, max_lines=3, line_height=body_height, max_height=available_height)
                for i, line in enumerate(wrapped):
                    if line.strip():  # Only draw non-empty lines
                        # Center each line of body text
                        try:
                            line_width = self.display_manager.get_text_width(line, body_font)
                        except Exception:
                            line_width = len(line) * 6
                        line_x = (matrix_width - line_width) // 2
                        # Add one pixel buffer between lines
                        line_y = y_start + i * (body_height + 1)
                        self._draw_bdf_text(draw, body_font, line, line_x, line_y, color=self.subtitle_color)
            elif self.rotation_state == 1 and description:
                # Show description
                wrapped = self._wrap_text(description, available_width, body_font, max_lines=3, line_height=body_height, max_height=available_height)
                for i, line in enumerate(wrapped):
                    if line.strip():  # Only draw non-empty lines
                        # Center each line of body text
                        try:
                            line_width = self.display_manager.get_text_width(line, body_font)
                        except Exception:
                            line_width = len(line) * 6
                        line_x = (matrix_width - line_width) // 2
                        # Add one pixel buffer between lines
                        line_y = y_start + i * (body_height + 1)
                        self._draw_bdf_text(draw, body_font, line, line_x, line_y, color=self.subtitle_color)
            # else: nothing to show
            return True
        except Exception as e:
            logger.error(f"Error drawing 'of the day' item: {e}", exc_info=True)
            return False

    def _wrap_text(self, text, max_width, face, max_lines=3, line_height=8, max_height=24):
        if not text:
            return [""]
        lines = []
        current_line = []
        words = text.split()
        for word in words:
            test_line = ' '.join(current_line + [word]) if current_line else word
            try:
                text_width = self.display_manager.get_text_width(test_line, face)
            except Exception:
                text_width = len(test_line) * 6
            if text_width <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                    current_line = [word]
                else:
                    truncated = word
                    while len(truncated) > 0:
                        try:
                            test_width = self.display_manager.get_text_width(truncated + "...", face)
                        except Exception:
                            test_width = len(truncated) * 6
                        if test_width <= max_width:
                            lines.append(truncated + "...")
                            break
                        truncated = truncated[:-1]
                    if not truncated:
                        lines.append(word[:10] + "...")
            # Check if we've filled all lines (accounting for line spacing)
            if len(lines) * line_height >= max_height or len(lines) >= max_lines:
                break
        if current_line and (len(lines) * line_height < max_height and len(lines) < max_lines):
            lines.append(' '.join(current_line))
        while len(lines) < max_lines:
            lines.append("")
        return lines[:max_lines]
    
    def display(self, force_clear=False):
        if not self.enabled:
            return
        if not self.current_items:
            current_time = time.time()
            if not hasattr(self, 'last_warning_time') or current_time - self.last_warning_time > 10:
                logger.warning(f"OfTheDayManager has no current items.")
                self.last_warning_time = current_time
            return
        now = time.time()
        # Handle subtitle/description rotation
        if now - self.last_rotation_time > self.subtitle_rotate_interval:
            self.rotation_state = (self.rotation_state + 1) % 2
            self.last_rotation_time = now
            # Force redraw
            self.last_drawn_category_index = -1
            self.last_drawn_day = None
        # Handle OTD category rotation
        if now - self.last_category_rotation_time > self.display_rotate_interval:
            # Find the next category with valid data
            original_index = self.current_category_index
            self.current_category_index = (self.current_category_index + 1) % len(self.current_items)
            
            # If we've cycled through all categories and none have data, reset to first
            if self.current_category_index == original_index:
                logger.warning("No categories have valid data, staying on current category")
            else:
                logger.info(f"Internal rotation: from category index {original_index} to {self.current_category_index}")
                logger.info(f"Available categories with data: {list(self.current_items.keys())}")
            
            self.last_category_rotation_time = now
            # Reset subtitle/description rotation when switching category
            self.rotation_state = 0
            self.last_rotation_time = now
            # Force redraw
            self.last_drawn_category_index = -1
            self.last_drawn_day = None
        content_has_changed = self.current_category_index != self.last_drawn_category_index or self.current_day != self.last_drawn_day
        if not content_has_changed and not force_clear:
            return
        try:
            category_names = list(self.current_items.keys())
            if not category_names or self.current_category_index >= len(category_names):
                self.current_category_index = 0
                if not category_names: return
            current_category = category_names[self.current_category_index]
            current_item = self.current_items[current_category]
            current_time = time.time()
            if current_time - self.last_display_log > 5:
                logger.info(f"Displaying {current_category}: {current_item.get('title', 'No Title')}")
                self.last_display_log = current_time
            self.display_manager.clear()
            self.draw_item(current_category, current_item)
            self.display_manager.update_display()
            self.last_drawn_category_index = self.current_category_index
            self.last_drawn_day = self.current_day
        except Exception as e:
            logger.error(f"Error displaying 'of the day' item: {e}", exc_info=True)
    
    def advance_item(self):
        """Advance to the next item. Called by DisplayController when display time is up."""
        if not self.enabled:
            logger.debug("OfTheDayManager is disabled, skipping item advance")
            return
            
        # Check if internal rotation should happen first
        now = time.time()
        if now - self.last_category_rotation_time > self.display_rotate_interval:
            # Let the internal rotation handle it
            logger.debug("Internal rotation timer triggered, skipping external advance")
            return
            
        # Only advance if internal rotation hasn't happened recently
        # Add a buffer to prevent conflicts
        if now - self.last_category_rotation_time < self.display_rotate_interval - 5:
            logger.debug("Too close to internal rotation time, skipping external advance")
            return
            
        category_names = list(self.current_items.keys())
        if not category_names:
            return
            
        # Advance to next category
        original_index = self.current_category_index
        self.current_category_index = (self.current_category_index + 1) % len(category_names)
        
        # Update rotation time to prevent immediate internal rotation
        self.last_category_rotation_time = now
        
        # Reset subtitle/description rotation when switching category
        self.rotation_state = 0
        self.last_rotation_time = now
        
        # Force redraw
        self.last_drawn_category_index = -1
        self.last_drawn_day = None
        
        logger.debug(f"OfTheDayManager externally advanced from category index {original_index} to {self.current_category_index}") 