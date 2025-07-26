"""
Interactive Menu System for CLI Mode

Provides rich interactive menus with:
- Navigation with arrow keys
- Multi-selection capabilities
- Visual feedback and animations
- User-friendly interface
"""

import sys
import os
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum

try:
    import keyboard
    import colorama
    from colorama import Fore, Back, Style
    colorama.init()
    # Enable rich terminal mode with arrow key support
    RICH_TERMINAL = True
except ImportError:
    RICH_TERMINAL = False
    # Fallback color codes
    class Fore:
        RED = '\033[91m'
        GREEN = '\033[92m'
        YELLOW = '\033[93m'
        BLUE = '\033[94m'
        MAGENTA = '\033[95m'
        CYAN = '\033[96m'
        WHITE = '\033[97m'
        RESET = '\033[0m'
    
    class Style:
        BRIGHT = '\033[1m'
        DIM = '\033[2m'
        RESET_ALL = '\033[0m'


class MenuType(Enum):
    """Types of menu interactions"""
    SINGLE_SELECT = "single"
    MULTI_SELECT = "multi"
    ACTION = "action"


@dataclass
class MenuItem:
    """Represents a menu item"""
    id: str
    title: str
    description: str = ""
    enabled: bool = True
    selected: bool = False
    action: Optional[Callable] = None
    submenu: Optional['InteractiveMenu'] = None


class InteractiveMenu:
    """Rich interactive menu system"""
    
    def __init__(self, title: str, menu_type: MenuType = MenuType.SINGLE_SELECT):
        self.title = title
        self.menu_type = menu_type
        self.items: List[MenuItem] = []
        self.current_index = 0
        self.selected_items: List[str] = []
        self.show_help = True
        
    def add_item(self, item_id: str, title: str, description: str = "", 
                 enabled: bool = True, action: Optional[Callable] = None) -> 'InteractiveMenu':
        """Add a menu item"""
        item = MenuItem(
            id=item_id,
            title=title,
            description=description,
            enabled=enabled,
            action=action
        )
        self.items.append(item)
        return self
    
    def add_separator(self, title: str = "") -> 'InteractiveMenu':
        """Add a visual separator"""
        separator = MenuItem(
            id=f"sep_{len(self.items)}",
            title=f"─── {title} ───" if title else "─" * 40,
            enabled=False
        )
        self.items.append(separator)
        return self
    
    def clear_screen(self):
        """Clear the terminal screen efficiently"""
        # Use ANSI escape codes for fast screen clearing
        print('\033[2J\033[H', end='', flush=True)
        # Additional optimization: clear scrollback buffer if supported
        print('\033[3J', end='', flush=True)
    
    def print_header(self):
        """Print menu header"""
        print(f"\n{Fore.CYAN}{Style.BRIGHT}{'=' * 60}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{Style.BRIGHT}{self.title:^60}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{Style.BRIGHT}{'=' * 60}{Style.RESET_ALL}\n")
    
    def print_item(self, index: int, item: MenuItem):
        """Print a single menu item"""
        is_current = index == self.current_index
        is_selected = item.id in self.selected_items
        
        # Choose colors and symbols
        if not item.enabled:
            # Separator or disabled item
            color = Fore.WHITE + Style.DIM
            symbol = "   "
            cursor = "   "
        elif is_current:
            # Current item (highlighted)
            color = Fore.YELLOW + Style.BRIGHT
            symbol = "►  " if self.menu_type == MenuType.SINGLE_SELECT else "[►]"
            cursor = ">> "
        elif is_selected and self.menu_type == MenuType.MULTI_SELECT:
            # Selected item in multi-select
            color = Fore.GREEN
            symbol = "[✓]"
            cursor = "   "
        else:
            # Normal item
            color = Fore.WHITE
            symbol = "   " if self.menu_type == MenuType.SINGLE_SELECT else "[ ]"
            cursor = "   "
        
        # Format the line
        if item.description:
            line = f"{cursor}{symbol} {item.title:<30} {Fore.WHITE + Style.DIM}({item.description}){Style.RESET_ALL}"
        else:
            line = f"{cursor}{symbol} {item.title}"
        
        print(f"{color}{line}{Style.RESET_ALL}")
    
    def print_help(self):
        """Print help instructions"""
        if not self.show_help:
            return
            
        print(f"\n{Fore.CYAN}Navigation:{Style.RESET_ALL}")
        
        if RICH_TERMINAL:
            print(f"  {Fore.WHITE}↑/↓{Style.RESET_ALL} Navigate  {Fore.WHITE}Enter{Style.RESET_ALL} Select  {Fore.WHITE}Esc{Style.RESET_ALL} Back")
            if self.menu_type == MenuType.MULTI_SELECT:
                print(f"  {Fore.WHITE}Space{Style.RESET_ALL} Toggle  {Fore.WHITE}A{Style.RESET_ALL} Select All  {Fore.WHITE}N{Style.RESET_ALL} Select None")
        else:
            print(f"  {Fore.WHITE}w/s{Style.RESET_ALL} Navigate  {Fore.WHITE}Enter{Style.RESET_ALL} Select  {Fore.WHITE}q{Style.RESET_ALL} Back")
            if self.menu_type == MenuType.MULTI_SELECT:
                print(f"  {Fore.WHITE}Space{Style.RESET_ALL} Toggle  {Fore.WHITE}a{Style.RESET_ALL} Select All  {Fore.WHITE}n{Style.RESET_ALL} Select None")
    
    def render(self):
        """Render the complete menu"""
        self.clear_screen()
        self.print_header()
        
        for i, item in enumerate(self.items):
            self.print_item(i, item)
        
        self.print_help()
        print()  # Extra line for spacing
    
    def move_up(self):
        """Move cursor up"""
        if self.current_index > 0:
            self.current_index -= 1
            # Skip disabled items
            while (self.current_index >= 0 and 
                   not self.items[self.current_index].enabled):
                self.current_index -= 1
            if self.current_index < 0:
                self.current_index = 0
    
    def move_down(self):
        """Move cursor down"""
        if self.current_index < len(self.items) - 1:
            self.current_index += 1
            # Skip disabled items
            while (self.current_index < len(self.items) and 
                   not self.items[self.current_index].enabled):
                self.current_index += 1
            if self.current_index >= len(self.items):
                self.current_index = len(self.items) - 1
    
    def toggle_selection(self):
        """Toggle selection for current item (multi-select mode)"""
        if self.menu_type != MenuType.MULTI_SELECT:
            return
            
        current_item = self.items[self.current_index]
        if not current_item.enabled:
            return
            
        if current_item.id in self.selected_items:
            self.selected_items.remove(current_item.id)
        else:
            self.selected_items.append(current_item.id)
    
    def select_all(self):
        """Select all enabled items (multi-select mode)"""
        if self.menu_type != MenuType.MULTI_SELECT:
            return
            
        self.selected_items = [item.id for item in self.items if item.enabled]
    
    def select_none(self):
        """Deselect all items (multi-select mode)"""
        if self.menu_type != MenuType.MULTI_SELECT:
            return
            
        self.selected_items = []
    
    def get_current_item(self) -> Optional[MenuItem]:
        """Get currently selected item"""
        if 0 <= self.current_index < len(self.items):
            return self.items[self.current_index]
        return None
    
    def get_selected_items(self) -> List[MenuItem]:
        """Get all selected items"""
        if self.menu_type == MenuType.MULTI_SELECT:
            return [item for item in self.items if item.id in self.selected_items]
        else:
            current = self.get_current_item()
            return [current] if current and current.enabled else []


def create_simple_menu(title: str, options: List[str]) -> InteractiveMenu:
    """Create a simple single-select menu"""
    menu = InteractiveMenu(title, MenuType.SINGLE_SELECT)
    for i, option in enumerate(options):
        menu.add_item(f"option_{i}", option)
    return menu


def create_multi_select_menu(title: str, options: Dict[str, str]) -> InteractiveMenu:
    """Create a multi-select menu with descriptions"""
    menu = InteractiveMenu(title, MenuType.MULTI_SELECT)
    for key, description in options.items():
        menu.add_item(key, key, description)
    return menu


class MenuController:
    """Handles menu navigation and input processing"""

    def __init__(self):
        self.running = True
        self.result = None

    def run_menu(self, menu: InteractiveMenu) -> Any:
        """Run interactive menu and return result"""
        self.running = True
        self.result = None

        while self.running:
            menu.render()

            if RICH_TERMINAL:
                self._handle_rich_input(menu)
            else:
                self._handle_simple_input(menu)

        return self.result

    def _handle_rich_input(self, menu: InteractiveMenu):
        """Handle input with rich terminal support"""
        try:
            if os.name == 'nt':
                import msvcrt

            if os.name == 'nt':
                # Windows
                key = msvcrt.getch()
                if key == b'\xe0':  # Arrow key prefix
                    key = msvcrt.getch()
                    if key == b'H':  # Up arrow
                        menu.move_up()
                        return  # Return to re-render menu
                    elif key == b'P':  # Down arrow
                        menu.move_down()
                        return  # Return to re-render menu
                elif key == b'\r':  # Enter
                    self._handle_selection(menu)
                elif key == b'\x1b':  # Escape
                    self.running = False
                elif key == b' ':  # Space
                    menu.toggle_selection()
                    return  # Return to re-render menu
                elif key.lower() == b'a':
                    menu.select_all()
                    return  # Return to re-render menu
                elif key.lower() == b'n':
                    menu.select_none()
                    return  # Return to re-render menu
                elif key.lower() == b'w':  # w for up navigation
                    menu.move_up()
                    return  # Return to re-render menu
                elif key.lower() == b's':  # s for down navigation
                    menu.move_down()
                    return  # Return to re-render menu
                elif key.lower() == b'q':  # q for quit
                    self.running = False
                else:
                    # Unhandled key - just return to re-render menu
                    return
            else:
                # Unix/Linux - Improved arrow key handling
                import termios
                import tty
                import select
                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)
                try:
                    tty.setraw(sys.stdin.fileno())

                    # Read first character
                    key = sys.stdin.read(1)

                    if key == '\x1b':  # Escape sequence start
                        # Check if more data is available (arrow keys) - reduced timeout for better responsiveness
                        if select.select([sys.stdin], [], [], 0.05)[0]:
                            # Read the bracket
                            bracket = sys.stdin.read(1)
                            if bracket == '[':
                                # Read the direction
                                direction = sys.stdin.read(1)
                                if direction == 'A':  # Up arrow
                                    menu.move_up()
                                    return  # Return to re-render menu
                                elif direction == 'B':  # Down arrow
                                    menu.move_down()
                                    return  # Return to re-render menu
                                elif direction == 'C':  # Right arrow (could be used for future features)
                                    return  # Return to re-render menu
                                elif direction == 'D':  # Left arrow (could be used for future features)
                                    return  # Return to re-render menu
                            else:
                                # Not an arrow key, treat as escape
                                self.running = False
                        else:
                            # Just escape key pressed
                            self.running = False
                    elif key == '\r' or key == '\n':  # Enter
                        self._handle_selection(menu)
                    elif key == ' ':  # Space
                        menu.toggle_selection()
                        return  # Return to re-render menu
                    elif key.lower() == 'a':
                        menu.select_all()
                        return  # Return to re-render menu
                    elif key.lower() == 'n':
                        menu.select_none()
                        return  # Return to re-render menu
                    elif key.lower() == 'w':  # w for up navigation
                        menu.move_up()
                        return  # Return to re-render menu
                    elif key.lower() == 's':  # s for down navigation
                        menu.move_down()
                        return  # Return to re-render menu
                    elif key == '\x03':  # Ctrl+C
                        raise KeyboardInterrupt
                    elif key == 'q':  # q for quit (fallback)
                        self.running = False
                    else:
                        # Unhandled key - just return to re-render menu
                        return
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        except (ImportError, KeyboardInterrupt):
            self._handle_simple_input(menu)

    def _handle_simple_input(self, menu: InteractiveMenu):
        """Handle input with simple terminal support"""
        try:
            print(f"{Fore.YELLOW}Enter command: {Style.RESET_ALL}", end="", flush=True)
            command = input().strip().lower()

            # Handle multiple consecutive characters (e.g., "ssss" -> "s")
            if command and len(command) > 1 and len(set(command)) == 1:
                command = command[0]

            if command in ['w', 'up']:
                menu.move_up()
            elif command in ['s', 'down']:
                menu.move_down()
            elif command in ['', 'enter', 'select']:
                self._handle_selection(menu)
            elif command in ['q', 'quit', 'exit', 'back']:
                self.running = False
            elif command in ['space', ' ', 'toggle'] and menu.menu_type == MenuType.MULTI_SELECT:
                menu.toggle_selection()
            elif command == 'a' and menu.menu_type == MenuType.MULTI_SELECT:
                menu.select_all()
            elif command == 'n' and menu.menu_type == MenuType.MULTI_SELECT:
                menu.select_none()
            elif command.isdigit():
                # Direct selection by number
                index = int(command) - 1
                if 0 <= index < len(menu.items) and menu.items[index].enabled:
                    menu.current_index = index
                    self._handle_selection(menu)
            else:
                print(f"{Fore.RED}Invalid command. Use w/s to navigate, Enter to select, q to quit.{Style.RESET_ALL}")
                input("Press Enter to continue...")

        except KeyboardInterrupt:
            self.running = False

    def _handle_selection(self, menu: InteractiveMenu):
        """Handle item selection"""
        current_item = menu.get_current_item()

        if not current_item or not current_item.enabled:
            return

        if menu.menu_type == MenuType.SINGLE_SELECT:
            # Single selection - return immediately
            self.result = current_item
            self.running = False
        elif menu.menu_type == MenuType.MULTI_SELECT:
            # Multi selection - toggle and continue
            menu.toggle_selection()
        elif menu.menu_type == MenuType.ACTION:
            # Action item - execute and continue or exit
            if current_item.action:
                result = current_item.action()
                if result is not None:
                    self.result = result
                    self.running = False
            else:
                self.result = current_item
                self.running = False
