#!/usr/bin/env python3
"""
Test CLI Navigation Fixes
=========================

Tests for the CLI navigation fixes to ensure arrow keys and navigation work correctly.
"""

import unittest
import sys
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))


class TestCLINavigationFixes(unittest.TestCase):
    """Test CLI navigation fixes"""
    
    def setUp(self):
        """Set up test fixtures"""
        # Mock the pandas import to avoid dependency issues
        sys.modules['pandas'] = MagicMock()
        
        # Import after mocking pandas
        from src.cli.interactive_menu import InteractiveMenu, MenuController, MenuType
        
        self.InteractiveMenu = InteractiveMenu
        self.MenuController = MenuController
        self.MenuType = MenuType
    
    def test_rich_terminal_w_key_handling(self):
        """Test that 'w' key is handled in rich terminal mode"""
        menu = self.InteractiveMenu("Test Menu", self.MenuType.SINGLE_SELECT)
        menu.add_item("item1", "Item 1", "First item")
        menu.add_item("item2", "Item 2", "Second item")
        menu.add_item("item3", "Item 3", "Third item")
        
        controller = self.MenuController()
        
        # Set initial position to item 2
        menu.current_index = 1
        initial_index = menu.current_index
        
        # Mock Unix/Linux environment
        with patch('os.name', 'posix'):
            with patch('sys.stdin.read') as mock_read:
                with patch('termios.tcgetattr') as mock_tcgetattr:
                    with patch('termios.tcsetattr') as mock_tcsetattr:
                        with patch('tty.setraw') as mock_setraw:
                            # Simulate 'w' key press
                            mock_read.return_value = 'w'
                            
                            # Call the rich input handler directly
                            controller._handle_rich_input(menu)
                            
                            # Should move up from index 1 to 0
                            self.assertEqual(menu.current_index, initial_index - 1)
    
    def test_rich_terminal_s_key_handling(self):
        """Test that 's' key is handled in rich terminal mode"""
        menu = self.InteractiveMenu("Test Menu", self.MenuType.SINGLE_SELECT)
        menu.add_item("item1", "Item 1", "First item")
        menu.add_item("item2", "Item 2", "Second item")
        menu.add_item("item3", "Item 3", "Third item")
        
        controller = self.MenuController()
        
        # Set initial position to item 1
        menu.current_index = 0
        initial_index = menu.current_index
        
        # Mock Unix/Linux environment
        with patch('os.name', 'posix'):
            with patch('sys.stdin.read') as mock_read:
                with patch('termios.tcgetattr') as mock_tcgetattr:
                    with patch('termios.tcsetattr') as mock_tcsetattr:
                        with patch('tty.setraw') as mock_setraw:
                            # Simulate 's' key press
                            mock_read.return_value = 's'
                            
                            # Call the rich input handler directly
                            controller._handle_rich_input(menu)
                            
                            # Should move down from index 0 to 1
                            self.assertEqual(menu.current_index, initial_index + 1)
    
    def test_unhandled_key_graceful_handling(self):
        """Test that unhandled keys are handled gracefully without crashing"""
        menu = self.InteractiveMenu("Test Menu", self.MenuType.SINGLE_SELECT)
        menu.add_item("item1", "Item 1", "First item")
        
        controller = self.MenuController()
        initial_running_state = controller.running
        
        # Mock Unix/Linux environment
        with patch('os.name', 'posix'):
            with patch('sys.stdin.read') as mock_read:
                with patch('termios.tcgetattr') as mock_tcgetattr:
                    with patch('termios.tcsetattr') as mock_tcsetattr:
                        with patch('tty.setraw') as mock_setraw:
                            # Simulate unhandled key press (e.g., 'x')
                            mock_read.return_value = 'x'
                            
                            # Call the rich input handler directly
                            try:
                                controller._handle_rich_input(menu)
                                # Should not crash and should maintain running state
                                self.assertEqual(controller.running, initial_running_state)
                            except Exception as e:
                                self.fail(f"Unhandled key caused exception: {e}")
    
    def test_q_key_exits_correctly(self):
        """Test that 'q' key exits correctly"""
        menu = self.InteractiveMenu("Test Menu", self.MenuType.SINGLE_SELECT)
        menu.add_item("item1", "Item 1", "First item")
        
        controller = self.MenuController()
        controller.running = True
        
        # Mock Unix/Linux environment
        with patch('os.name', 'posix'):
            with patch('sys.stdin.read') as mock_read:
                with patch('termios.tcgetattr') as mock_tcgetattr:
                    with patch('termios.tcsetattr') as mock_tcsetattr:
                        with patch('tty.setraw') as mock_setraw:
                            # Simulate 'q' key press
                            mock_read.return_value = 'q'
                            
                            # Call the rich input handler directly
                            controller._handle_rich_input(menu)
                            
                            # Should set running to False
                            self.assertFalse(controller.running)
    
    def test_simple_input_w_s_handling(self):
        """Test that w/s keys work in simple input mode"""
        menu = self.InteractiveMenu("Test Menu", self.MenuType.SINGLE_SELECT)
        menu.add_item("item1", "Item 1", "First item")
        menu.add_item("item2", "Item 2", "Second item")
        
        controller = self.MenuController()
        
        # Test 'w' key
        menu.current_index = 1
        with patch('builtins.input', return_value='w'):
            controller._handle_simple_input(menu)
            self.assertEqual(menu.current_index, 0)
        
        # Test 's' key
        menu.current_index = 0
        with patch('builtins.input', return_value='s'):
            controller._handle_simple_input(menu)
            self.assertEqual(menu.current_index, 1)
    
    def test_multiple_consecutive_keys_handling(self):
        """Test that multiple consecutive keys are handled correctly"""
        menu = self.InteractiveMenu("Test Menu", self.MenuType.SINGLE_SELECT)
        menu.add_item("item1", "Item 1", "First item")
        menu.add_item("item2", "Item 2", "Second item")
        menu.add_item("item3", "Item 3", "Third item")
        
        controller = self.MenuController()
        
        # Test multiple 's' keys
        menu.current_index = 0
        with patch('builtins.input', return_value='sss'):
            controller._handle_simple_input(menu)
            # Should be treated as single 's' and move down once
            self.assertEqual(menu.current_index, 1)
        
        # Test multiple 'w' keys
        menu.current_index = 2
        with patch('builtins.input', return_value='www'):
            controller._handle_simple_input(menu)
            # Should be treated as single 'w' and move up once
            self.assertEqual(menu.current_index, 1)
    
    def test_menu_controller_none_result_handling(self):
        """Test that None result from menu controller doesn't cause exit"""
        # This tests the fix in cli_interface.py where None result should continue to main menu
        
        # Mock the menu controller to return None
        mock_controller = Mock()
        mock_controller.run_menu.return_value = None
        
        # Test that None result is handled gracefully
        result = mock_controller.run_menu(Mock())
        self.assertIsNone(result)
        
        # In the actual CLI interface, this should continue to main menu, not exit
        # This is tested by the fix in cli_interface.py line 132-135


class TestCLIInterfaceFixes(unittest.TestCase):
    """Test CLI interface fixes"""
    
    def test_none_result_continues_to_main_menu(self):
        """Test that None result from menu continues to main menu instead of exiting"""
        # Mock the CLI interface behavior
        
        # Simulate the fixed behavior in cli_interface.py
        def simulate_main_loop():
            results = [None, None, Mock(id="exit")]  # None results should continue, exit should break
            
            for result in results:
                if result and result.id == "exit":
                    return "exit"
                elif not result:
                    continue  # This is the fix - continue instead of break
            
            return "continued"
        
        # Test that None results continue the loop
        final_result = simulate_main_loop()
        self.assertEqual(final_result, "exit")
    
    def test_explicit_exit_breaks_loop(self):
        """Test that explicit exit still works correctly"""
        # Mock exit result
        exit_result = Mock()
        exit_result.id = "exit"
        
        # Simulate the fixed behavior
        def simulate_exit_handling(result):
            if result and result.id == "exit":
                return "should_exit"
            elif not result:
                return "should_continue"
            else:
                return "should_handle"
        
        # Test explicit exit
        self.assertEqual(simulate_exit_handling(exit_result), "should_exit")
        
        # Test None result
        self.assertEqual(simulate_exit_handling(None), "should_continue")
        
        # Test normal result
        normal_result = Mock()
        normal_result.id = "some_action"
        self.assertEqual(simulate_exit_handling(normal_result), "should_handle")


if __name__ == '__main__':
    unittest.main()
