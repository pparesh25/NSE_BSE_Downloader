#!/usr/bin/env python3
"""
Test script to debug space toggle functionality in multi-select menus
"""

import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path('.') / 'src'))

from src.cli.interactive_menu import InteractiveMenu, MenuController, MenuType

def test_space_toggle():
    """Test space toggle functionality"""
    print("Testing Space Toggle Functionality")
    print("=" * 40)
    
    # Create a multi-select menu
    menu = InteractiveMenu("Test Multi-Select Menu", MenuType.MULTI_SELECT)
    menu.add_item("nse_eq", "NSE_EQ", "NSE Equity (Main Market)")
    menu.add_item("nse_fo", "NSE_FO", "NSE Futures & Options")
    menu.add_item("bse_eq", "BSE_EQ", "BSE Equity")
    
    print(f"Menu type: {menu.menu_type}")
    print(f"Initial selected items: {menu.selected_items}")
    
    # Test toggle_selection method directly
    print("\n1. Testing toggle_selection() method directly:")
    menu.current_index = 0  # Select first item (NSE_EQ)
    print(f"Current index: {menu.current_index}")
    print(f"Current item: {menu.items[menu.current_index].id}")
    print(f"Selected items before toggle: {menu.selected_items}")
    
    menu.toggle_selection()
    print(f"Selected items after toggle: {menu.selected_items}")
    
    # Toggle again
    menu.toggle_selection()
    print(f"Selected items after second toggle: {menu.selected_items}")
    
    # Test with different item
    print("\n2. Testing with second item (NSE_FO):")
    menu.current_index = 1
    print(f"Current index: {menu.current_index}")
    print(f"Current item: {menu.items[menu.current_index].id}")
    print(f"Selected items before toggle: {menu.selected_items}")
    
    menu.toggle_selection()
    print(f"Selected items after toggle: {menu.selected_items}")
    
    # Test rendering
    print("\n3. Testing menu rendering:")
    menu.render()
    
    print("\n4. Testing get_selected_items():")
    selected = menu.get_selected_items()
    print(f"Selected items: {[item.id for item in selected]}")

if __name__ == "__main__":
    test_space_toggle()
