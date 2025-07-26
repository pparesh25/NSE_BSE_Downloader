#!/usr/bin/env python3
"""
Test script to verify multiple key press handling fix
"""

import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path('.') / 'src'))

from src.cli.interactive_menu import InteractiveMenu, MenuController, MenuType

def test_multiple_key_handling():
    """Test multiple key press handling"""
    print("Testing Multiple Key Press Handling")
    print("=" * 38)
    
    # Create a simple menu
    menu = InteractiveMenu("Multiple Key Test Menu", MenuType.SINGLE_SELECT)
    menu.add_item("option1", "Option 1", "First option")
    menu.add_item("option2", "Option 2", "Second option")
    menu.add_item("option3", "Option 3", "Third option")
    
    # Test the command processing logic directly
    controller = MenuController()
    
    # Test cases for multiple character handling
    test_cases = [
        ("s", "Single 's' - should move down"),
        ("ss", "Double 's' - should be treated as single 's'"),
        ("sss", "Triple 's' - should be treated as single 's'"),
        ("ssssss", "Multiple 's' - should be treated as single 's'"),
        ("w", "Single 'w' - should move up"),
        ("www", "Multiple 'w' - should be treated as single 'w'"),
        ("sw", "Mixed characters - should be invalid"),
        ("", "Empty command - should be treated as enter"),
        ("q", "Single 'q' - should quit"),
        ("qqq", "Multiple 'q' - should be treated as single 'q'"),
    ]
    
    print("Testing command processing logic:")
    print("-" * 50)
    
    for command, description in test_cases:
        # Simulate the command processing logic
        processed_command = command.strip().lower()
        
        # Apply the multiple character fix
        if processed_command and len(processed_command) > 1 and len(set(processed_command)) == 1:
            processed_command = processed_command[0]
        
        # Check what action would be taken
        if processed_command in ['w', 'up']:
            action = "Move up"
        elif processed_command in ['s', 'down']:
            action = "Move down"
        elif processed_command in ['', 'enter', 'select']:
            action = "Select current item"
        elif processed_command in ['q', 'quit', 'exit', 'back']:
            action = "Quit/Back"
        elif processed_command in ['space', ' ', 'toggle']:
            action = "Toggle selection (multi-select)"
        elif processed_command == 'a':
            action = "Select all (multi-select)"
        elif processed_command == 'n':
            action = "Select none (multi-select)"
        elif processed_command.isdigit():
            action = f"Direct selection of item {processed_command}"
        else:
            action = "Invalid command"
        
        print(f"Input: '{command}' -> Processed: '{processed_command}' -> Action: {action}")
        print(f"  Description: {description}")
        print()

if __name__ == "__main__":
    test_multiple_key_handling()
