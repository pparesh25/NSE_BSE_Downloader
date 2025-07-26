#!/usr/bin/env python3
"""
Test script to verify arrow key functionality in rich terminal mode
"""

import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path('.') / 'src'))

from src.cli.interactive_menu import InteractiveMenu, MenuController, MenuType

def test_arrow_keys():
    """Test arrow key functionality"""
    print("Testing Arrow Key Functionality")
    print("=" * 35)
    print("This test will check if arrow keys (↑↓) work in rich terminal mode")
    print("Instructions:")
    print("  - Use ↑/↓ arrow keys to navigate")
    print("  - Use w/s as fallback if arrow keys don't work")
    print("  - Press Enter to select 'Test Complete' to finish")
    print("  - Press 'q' to quit")
    print()
    
    # Create a simple menu for testing
    menu = InteractiveMenu("Arrow Key Test Menu", MenuType.SINGLE_SELECT)
    menu.add_item("option1", "Option 1", "First test option")
    menu.add_item("option2", "Option 2", "Second test option")
    menu.add_item("option3", "Option 3", "Third test option")
    menu.add_item("option4", "Option 4", "Fourth test option")
    menu.add_item("complete", "Test Complete", "Select this to finish test")
    
    # Set action for completion
    def complete_test():
        print(f"\n{'-'*40}")
        print("✅ Arrow key test completed!")
        print("If you were able to navigate using ↑/↓ arrow keys,")
        print("then rich terminal mode is working correctly.")
        print("If you had to use w/s keys, then arrow key detection")
        print("needs further debugging.")
        print(f"{'-'*40}")
        return True  # Exit menu
    
    menu.items[-1].action = complete_test
    
    # Create controller and run menu
    controller = MenuController()
    
    print("Starting arrow key test...")
    print("Navigate through the menu options and select 'Test Complete' when done.")
    print()
    
    try:
        result = controller.run_menu(menu)
        print(f"\nTest completed with result: {result}")
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"\nError during test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_arrow_keys()
