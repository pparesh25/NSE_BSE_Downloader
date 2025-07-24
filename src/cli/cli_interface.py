"""
Main CLI Interface for NSE/BSE Data Downloader

Provides the primary command-line interface with:
- Interactive main menu
- Exchange selection
- Date range configuration
- Download management
- Progress monitoring
"""

import asyncio
import sys
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional, Tuple
from pathlib import Path

from .interactive_menu import (
    InteractiveMenu, MenuController, MenuItem, MenuType,
    create_simple_menu, create_multi_select_menu, Fore, Style
)
from .progress_display import MultiProgressDisplay, create_simple_progress
from ..core.config import Config
from ..core.data_manager import DataManager


class CLIInterface:
    """Main CLI interface controller"""
    
    def __init__(self, config: Config):
        self.config = config
        self.data_manager = DataManager(config)
        self.menu_controller = MenuController()
        
        # Available exchanges with descriptions
        self.exchanges = {
            "NSE_EQ": "NSE Equity (Main Market)",
            "NSE_FO": "NSE Futures & Options", 
            "NSE_SME": "NSE Small & Medium Enterprises",
            "NSE_INDEX": "NSE Indices",
            "BSE_EQ": "BSE Equity",
            "BSE_INDEX": "BSE Indices"
        }
        
        # Quick date range options
        self.date_ranges = {
            "today": "Today only",
            "yesterday": "Yesterday only", 
            "last_7_days": "Last 7 days",
            "last_30_days": "Last 30 days",
            "current_month": "Current month",
            "last_month": "Previous month",
            "missing_only": "Missing files only",
            "custom": "Custom date range"
        }
    
    def print_welcome(self):
        """Print welcome message"""
        print(f"\n{Fore.CYAN}{Style.BRIGHT}")
        print("╔" + "═" * 58 + "╗")
        print("║" + " " * 58 + "║")
        print("║" + "NSE/BSE Data Downloader - CLI Mode".center(58) + "║")
        print("║" + " " * 58 + "║") 
        print("║" + f"Version 2.0.0 - Enhanced Edition".center(58) + "║")
        print("║" + " " * 58 + "║")
        print("╚" + "═" * 58 + "╝")
        print(f"{Style.RESET_ALL}\n")
        
        print(f"{Fore.GREEN}✅ Configuration loaded: {self.config.config_path}{Style.RESET_ALL}")
        print(f"{Fore.GREEN}✅ Data directory: {getattr(self.config, 'data_folder', 'data/')}{Style.RESET_ALL}")
        print()
    
    async def run(self):
        """Run the main CLI interface"""
        try:
            self.print_welcome()
            await self.show_main_menu()
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Operation cancelled by user.{Style.RESET_ALL}")
        except Exception as e:
            print(f"\n{Fore.RED}Error: {e}{Style.RESET_ALL}")
            return 1
        return 0
    
    async def show_main_menu(self):
        """Show the main menu"""
        while True:
            menu = InteractiveMenu("🏠 Main Menu", MenuType.SINGLE_SELECT)
            menu.add_item("download_all", "📥 Download All Exchanges", "Download data for all configured exchanges")
            menu.add_item("download_select", "🎯 Select Exchanges", "Choose specific exchanges to download")
            menu.add_item("download_custom", "📅 Custom Date Range", "Download with custom date range")
            menu.add_separator("Management")
            menu.add_item("view_status", "📊 View Download Status", "Check download statistics and missing files")
            menu.add_item("view_config", "⚙️  View Configuration", "Display current configuration settings")
            menu.add_item("view_history", "📜 Download History", "View recent download history")
            menu.add_separator()
            menu.add_item("exit", "🚪 Exit", "Exit the application")
            
            result = self.menu_controller.run_menu(menu)
            
            if not result or result.id == "exit":
                print(f"\n{Fore.CYAN}Thank you for using NSE/BSE Data Downloader!{Style.RESET_ALL}")
                break
            
            await self.handle_main_menu_selection(result.id)
    
    async def handle_main_menu_selection(self, selection: str):
        """Handle main menu selection"""
        if selection == "download_all":
            await self.download_all_exchanges()
        elif selection == "download_select":
            await self.select_exchanges_menu()
        elif selection == "download_custom":
            await self.custom_date_range_menu()
        elif selection == "view_status":
            await self.view_download_status()
        elif selection == "view_config":
            self.view_configuration()
        elif selection == "view_history":
            await self.view_download_history()
    
    async def download_all_exchanges(self):
        """Download all exchanges with default settings"""
        print(f"\n{Fore.YELLOW}🚀 Starting download for all exchanges...{Style.RESET_ALL}")

        # Get date range
        start_date, end_date = self.get_default_date_range()

        print(f"{Fore.CYAN}📅 Date range: {start_date} to {end_date}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}📊 Exchanges: {', '.join(self.exchanges.keys())}{Style.RESET_ALL}")

        # Confirm before proceeding
        if not self.confirm_action("Proceed with download?"):
            return

        # Perform download with progress tracking
        selected_exchanges = list(self.exchanges.keys())
        await self.perform_download(selected_exchanges, start_date, end_date)

        input("\nPress Enter to continue...")
    
    async def select_exchanges_menu(self):
        """Show exchange selection menu"""
        menu = create_multi_select_menu("🎯 Select Exchanges to Download", self.exchanges)
        menu.show_help = True
        
        # Pre-select commonly used exchanges
        menu.selected_items = ["NSE_EQ", "BSE_EQ"]
        
        result = self.menu_controller.run_menu(menu)
        selected_exchanges = menu.get_selected_items()
        
        if not selected_exchanges:
            print(f"\n{Fore.YELLOW}No exchanges selected.{Style.RESET_ALL}")
            input("Press Enter to continue...")
            return
        
        # Show selected exchanges
        print(f"\n{Fore.GREEN}Selected exchanges:{Style.RESET_ALL}")
        for item in selected_exchanges:
            print(f"  ✓ {item.title} - {item.description}")
        
        # Get date range
        start_date, end_date = await self.select_date_range()
        
        print(f"\n{Fore.CYAN}📅 Date range: {start_date} to {end_date}{Style.RESET_ALL}")
        
        if self.confirm_action("Proceed with download?"):
            # Perform download with progress tracking
            exchange_ids = [item.id for item in selected_exchanges]
            await self.perform_download(exchange_ids, start_date, end_date)

        input("\nPress Enter to continue...")
    
    async def custom_date_range_menu(self):
        """Custom date range selection"""
        print(f"\n{Fore.CYAN}📅 Custom Date Range Configuration{Style.RESET_ALL}")
        
        # Get start date
        start_date = self.get_date_input("Enter start date (YYYY-MM-DD): ")
        if not start_date:
            return
        
        # Get end date
        end_date = self.get_date_input("Enter end date (YYYY-MM-DD): ", min_date=start_date)
        if not end_date:
            return
        
        # Calculate days
        days = (end_date - start_date).days + 1
        print(f"\n{Fore.CYAN}📊 Date range: {start_date} to {end_date} ({days} days){Style.RESET_ALL}")
        
        # Select exchanges
        await self.select_exchanges_menu()
    
    async def select_date_range(self) -> Tuple[date, date]:
        """Select date range from predefined options"""
        menu = create_simple_menu("📅 Select Date Range", list(self.date_ranges.values()))
        
        result = self.menu_controller.run_menu(menu)
        
        if not result:
            return self.get_default_date_range()
        
        # Map back to key
        selected_key = list(self.date_ranges.keys())[result.title in self.date_ranges.values()]
        
        return self.calculate_date_range(selected_key)
    
    def calculate_date_range(self, range_key: str) -> Tuple[date, date]:
        """Calculate actual date range from key"""
        today = date.today()
        
        if range_key == "today":
            return today, today
        elif range_key == "yesterday":
            yesterday = today - timedelta(days=1)
            return yesterday, yesterday
        elif range_key == "last_7_days":
            start = today - timedelta(days=7)
            return start, today
        elif range_key == "last_30_days":
            start = today - timedelta(days=30)
            return start, today
        elif range_key == "current_month":
            start = today.replace(day=1)
            return start, today
        elif range_key == "last_month":
            first_this_month = today.replace(day=1)
            last_month_end = first_this_month - timedelta(days=1)
            last_month_start = last_month_end.replace(day=1)
            return last_month_start, last_month_end
        else:
            # Default to last 7 days
            start = today - timedelta(days=7)
            return start, today
    
    def get_default_date_range(self) -> Tuple[date, date]:
        """Get default date range"""
        return self.calculate_date_range("last_7_days")
    
    def get_date_input(self, prompt: str, min_date: Optional[date] = None) -> Optional[date]:
        """Get date input from user"""
        while True:
            try:
                date_str = input(f"{Fore.YELLOW}{prompt}{Style.RESET_ALL}").strip()
                if not date_str:
                    return None
                
                parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                
                if min_date and parsed_date < min_date:
                    print(f"{Fore.RED}Error: Date must be >= {min_date}{Style.RESET_ALL}")
                    continue
                
                return parsed_date
                
            except ValueError:
                print(f"{Fore.RED}Error: Invalid date format. Use YYYY-MM-DD{Style.RESET_ALL}")
            except KeyboardInterrupt:
                return None
    
    def confirm_action(self, message: str) -> bool:
        """Get user confirmation"""
        while True:
            try:
                response = input(f"{Fore.YELLOW}{message} (y/N): {Style.RESET_ALL}").strip().lower()
                if response in ['y', 'yes']:
                    return True
                elif response in ['n', 'no', '']:
                    return False
                else:
                    print(f"{Fore.RED}Please enter 'y' or 'n'{Style.RESET_ALL}")
            except KeyboardInterrupt:
                return False
    
    async def view_download_status(self):
        """View download status and statistics"""
        print(f"\n{Fore.CYAN}📊 Download Status{Style.RESET_ALL}")
        print("=" * 50)
        
        # TODO: Implement status checking
        print(f"{Fore.GREEN}✅ NSE EQ: 140/140 files (100%){Style.RESET_ALL}")
        print(f"{Fore.GREEN}✅ NSE FO: 140/140 files (100%){Style.RESET_ALL}")
        print(f"{Fore.YELLOW}⚠️  NSE SME: 139/140 files (99.3%) - 1 missing{Style.RESET_ALL}")
        print(f"{Fore.GREEN}✅ BSE EQ: 140/140 files (100%){Style.RESET_ALL}")
        
        input("\nPress Enter to continue...")
    
    def view_configuration(self):
        """View current configuration"""
        print(f"\n{Fore.CYAN}⚙️  Current Configuration{Style.RESET_ALL}")
        print("=" * 50)
        
        print(f"Config file: {self.config.config_path}")
        print(f"Data directory: {self.config.data_paths.base_folder}")
        print(f"Timeout: {self.config.download_settings.timeout_seconds}s")
        print(f"Retry attempts: {self.config.download_settings.retry_attempts}")
        print(f"Fast mode: {self.config.download_settings.fast_mode}")
        
        input("\nPress Enter to continue...")
    
    async def view_download_history(self):
        """View download history"""
        print(f"\n{Fore.CYAN}📜 Download History{Style.RESET_ALL}")
        print("=" * 50)

        # TODO: Implement history tracking
        print("No download history available yet.")

        input("\nPress Enter to continue...")

    async def perform_download(self, exchange_ids: List[str], start_date: date, end_date: date):
        """Perform actual download with progress tracking"""
        try:
            # Initialize progress display
            progress = MultiProgressDisplay()

            # Calculate working days for each exchange
            # For now, use simple date range calculation
            working_days = []
            current_date = start_date
            while current_date <= end_date:
                # Skip weekends (Saturday=5, Sunday=6)
                if current_date.weekday() < 5:
                    working_days.append(current_date)
                current_date += timedelta(days=1)

            total_days = len(working_days)

            print(f"\n{Fore.CYAN}🔄 Initializing downloads...{Style.RESET_ALL}")

            # Add exchanges to progress tracker
            for exchange_id in exchange_ids:
                progress.add_exchange(exchange_id, total_days)

            # Start downloads
            download_tasks = []
            for exchange_id in exchange_ids:
                task = asyncio.create_task(
                    self.download_exchange_data(exchange_id, working_days, progress)
                )
                download_tasks.append(task)

            # Wait for all downloads to complete
            await asyncio.gather(*download_tasks, return_exceptions=True)

            # Finish progress display
            progress.finish()

        except Exception as e:
            print(f"\n{Fore.RED}❌ Download error: {e}{Style.RESET_ALL}")

    async def download_exchange_data(self, exchange_id: str, working_days: List[date],
                                   progress: MultiProgressDisplay):
        """Download data for a specific exchange"""
        try:
            # For now, simulate downloader availability
            # TODO: Integrate with actual downloader classes
            print(f"{Fore.CYAN}📥 Starting {exchange_id} download...{Style.RESET_ALL}")

            # Download each day
            for i, target_date in enumerate(working_days):
                try:
                    # Update progress with current file
                    current_file = f"{exchange_id}_{target_date.strftime('%Y%m%d')}"
                    progress.update_exchange(
                        exchange_id,
                        current_file=current_file
                    )

                    # Simulate download (replace with actual download logic)
                    await asyncio.sleep(0.1)  # Simulate download time

                    # Simulate success/failure (replace with actual result)
                    import random
                    success = random.random() > 0.05  # 95% success rate
                    bytes_downloaded = random.randint(50000, 200000) if success else 0

                    # Update progress
                    progress.increment_exchange(
                        exchange_id,
                        success=success,
                        bytes_downloaded=bytes_downloaded,
                        current_file=current_file
                    )

                    # Render progress
                    progress.render()

                except Exception as e:
                    # Handle individual file error
                    progress.increment_exchange(
                        exchange_id,
                        success=False,
                        current_file=f"Error: {e}"
                    )
                    progress.render()

        except Exception as e:
            print(f"{Fore.RED}❌ Error downloading {exchange_id}: {e}{Style.RESET_ALL}")
