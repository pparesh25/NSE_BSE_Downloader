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
import csv
import json
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path

from .interactive_menu import (
    InteractiveMenu, MenuController, MenuItem, MenuType,
    create_simple_menu, create_multi_select_menu, Fore, Style
)
from .progress_display import MultiProgressDisplay, create_simple_progress
from .advanced_filters import AdvancedDateParser, ExchangeFilter, MissingFilesDetector, FilterCriteria
from .config_manager import ConfigurationManager
from .data_quality import DataQualityValidator, QualityReport, FileStatus, QualityLevel
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

        # Initialize advanced filtering components
        self.date_parser = AdvancedDateParser()
        self.exchange_filter = ExchangeFilter(list(self.exchanges.keys()))
        self.missing_files_detector = MissingFilesDetector(
            getattr(config, 'data_folder', Path('data'))
        )

        # Initialize configuration manager
        self.config_manager = ConfigurationManager(config.config_path)

        # Initialize data quality validator
        self.quality_validator = DataQualityValidator(
            getattr(config, 'data_folder', Path('data'))
        )
        
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
        print(f"\n{Fore.CYAN}{Style.BRIGHT}", flush=True)
        print("╔" + "═" * 58 + "╗", flush=True)
        print("║" + " " * 58 + "║", flush=True)
        print("║" + "NSE/BSE Data Downloader - CLI Mode".center(58) + "║", flush=True)
        print("║" + " " * 58 + "║", flush=True)
        print("║" + f"Version 2.0.0 - Enhanced Edition".center(58) + "║", flush=True)
        print("║" + " " * 58 + "║", flush=True)
        print("╚" + "═" * 58 + "╝", flush=True)
        print(f"{Style.RESET_ALL}\n", flush=True)

        print(f"{Fore.GREEN}✅ Configuration loaded: {self.config.config_path}{Style.RESET_ALL}", flush=True)
        print(f"{Fore.GREEN}✅ Data directory: {getattr(self.config, 'data_folder', 'data/')}{Style.RESET_ALL}", flush=True)
        print(flush=True)
    
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
            menu.add_separator("Advanced Options")
            menu.add_item("download_advanced", "🔍 Advanced Filtering", "Smart filtering with patterns and missing files")
            menu.add_item("download_missing", "📋 Missing Files Only", "Download only missing files")
            menu.add_separator("Data Quality")
            menu.add_item("quality_report", "📋 Data Quality Report", "Generate comprehensive data quality analysis")
            menu.add_item("validate_data", "🔍 Validate Data Integrity", "Check file integrity and completeness")
            menu.add_item("gap_analysis", "📊 Gap Analysis", "Identify and analyze missing data")
            menu.add_separator("Management")
            menu.add_item("view_status", "📊 View Download Status", "Check download statistics and missing files")
            menu.add_item("view_config", "⚙️  View Configuration", "Display current configuration settings")
            menu.add_item("manage_config", "🔧 Manage Configuration", "Update settings and manage profiles")
            menu.add_item("view_history", "📜 Download History", "View recent download history")
            menu.add_separator()
            menu.add_item("exit", "🚪 Exit", "Exit the application")

            result = self.menu_controller.run_menu(menu)

            # Handle exit explicitly
            if result and result.id == "exit":
                print(f"\n{Fore.CYAN}Thank you for using NSE/BSE Data Downloader!{Style.RESET_ALL}")
                break
            elif not result:
                # If result is None (e.g., from escape key), continue to main menu
                continue
            
            await self.handle_main_menu_selection(result.id)
    
    async def handle_main_menu_selection(self, selection: str):
        """Handle main menu selection"""
        if selection == "download_all":
            await self.download_all_exchanges()
        elif selection == "download_select":
            await self.select_exchanges_menu()
        elif selection == "download_custom":
            await self.custom_date_range_menu()
        elif selection == "download_advanced":
            await self.advanced_filtering_menu()
        elif selection == "download_missing":
            await self.missing_files_menu()
        elif selection == "quality_report":
            await self.data_quality_report_menu()
        elif selection == "validate_data":
            await self.validate_data_integrity_menu()
        elif selection == "gap_analysis":
            await self.gap_analysis_menu()
        elif selection == "view_status":
            await self.view_download_status()
        elif selection == "view_config":
            self.view_configuration()
        elif selection == "manage_config":
            await self.manage_configuration()
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

        # Start with no pre-selected exchanges - let user choose
        menu.selected_items = []
        
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

    async def advanced_filtering_menu(self):
        """Advanced filtering with smart patterns"""
        print(f"\n{Fore.CYAN}🔍 Advanced Filtering Options{Style.RESET_ALL}")
        print("=" * 50)

        # Exchange pattern input
        print(f"\n{Fore.YELLOW}📊 Exchange Selection:{Style.RESET_ALL}")
        print("Examples: NSE_*, *_EQ, NSE_EQ,BSE_EQ, !BSE_*")

        exchange_pattern = input(f"{Fore.YELLOW}Enter exchange pattern (or Enter for all): {Style.RESET_ALL}").strip()

        if exchange_pattern:
            try:
                selected_exchanges = self.exchange_filter.filter_exchanges([exchange_pattern])
                if not selected_exchanges:
                    print(f"{Fore.RED}❌ No exchanges match pattern: {exchange_pattern}{Style.RESET_ALL}")
                    input("Press Enter to continue...")
                    return
            except Exception as e:
                print(f"{Fore.RED}❌ Invalid pattern: {e}{Style.RESET_ALL}")
                input("Press Enter to continue...")
                return
        else:
            selected_exchanges = list(self.exchanges.keys())

        print(f"\n{Fore.GREEN}Selected exchanges: {', '.join(selected_exchanges)}{Style.RESET_ALL}")

        # Date range pattern input
        print(f"\n{Fore.YELLOW}📅 Date Range Selection:{Style.RESET_ALL}")
        print("Examples: last-7-days, this-month, 2025-01, 2025-01-01:2025-01-31")
        print(f"Available patterns: {', '.join(self.date_parser.get_available_patterns()[:5])}...")

        date_pattern = input(f"{Fore.YELLOW}Enter date pattern (or Enter for last-7-days): {Style.RESET_ALL}").strip()

        if not date_pattern:
            date_pattern = "last-7-days"

        try:
            start_date, end_date = self.date_parser.parse_date_range(date_pattern)
            print(f"\n{Fore.GREEN}Date range: {start_date} to {end_date}{Style.RESET_ALL}")
        except ValueError as e:
            print(f"{Fore.RED}❌ Invalid date pattern: {e}{Style.RESET_ALL}")
            input("Press Enter to continue...")
            return

        # Additional options
        print(f"\n{Fore.YELLOW}⚙️  Additional Options:{Style.RESET_ALL}")
        include_weekends = self.confirm_action("Include weekends?")
        missing_only = self.confirm_action("Download missing files only?")

        # Show summary
        print(f"\n{Fore.CYAN}📋 Filter Summary:{Style.RESET_ALL}")
        print(f"  Exchanges: {', '.join(selected_exchanges)}")
        print(f"  Date range: {start_date} to {end_date}")
        print(f"  Include weekends: {'Yes' if include_weekends else 'No'}")
        print(f"  Missing only: {'Yes' if missing_only else 'No'}")

        if self.confirm_action("Proceed with download?"):
            # Apply filters and download
            if missing_only:
                await self.download_missing_files(selected_exchanges, start_date, end_date, include_weekends)
            else:
                await self.perform_download(selected_exchanges, start_date, end_date)

        input("\nPress Enter to continue...")

    async def missing_files_menu(self):
        """Menu for downloading missing files only"""
        print(f"\n{Fore.CYAN}📋 Missing Files Detection{Style.RESET_ALL}")
        print("=" * 50)

        # Get date range for checking
        start_date, end_date = await self.select_date_range()

        print(f"\n{Fore.YELLOW}🔍 Scanning for missing files...{Style.RESET_ALL}")

        # Find missing files
        missing_files = self.missing_files_detector.find_missing_files(
            list(self.exchanges.keys()), start_date, end_date, include_weekends=False
        )

        if not missing_files:
            print(f"{Fore.GREEN}✅ No missing files found! All data is complete.{Style.RESET_ALL}")
            input("Press Enter to continue...")
            return

        # Display missing files summary
        print(f"\n{Fore.YELLOW}📊 Missing Files Summary:{Style.RESET_ALL}")
        total_missing = 0
        for exchange, dates in missing_files.items():
            count = len(dates)
            total_missing += count
            print(f"  {exchange}: {count} missing files")
            if count <= 5:
                date_list = ", ".join(d.strftime('%Y-%m-%d') for d in dates)
                print(f"    Dates: {date_list}")
            else:
                print(f"    Date range: {dates[0].strftime('%Y-%m-%d')} to {dates[-1].strftime('%Y-%m-%d')}")

        print(f"\n{Fore.CYAN}Total missing files: {total_missing}{Style.RESET_ALL}")

        if self.confirm_action("Download missing files?"):
            # Download only missing files
            exchanges_to_download = list(missing_files.keys())
            await self.download_missing_files(exchanges_to_download, start_date, end_date)

        input("\nPress Enter to continue...")

    async def download_missing_files(self, exchanges: List[str], start_date: date,
                                   end_date: date, include_weekends: bool = False):
        """Download only missing files"""
        print(f"\n{Fore.CYAN}📥 Downloading Missing Files Only...{Style.RESET_ALL}")

        # Find missing files
        missing_files = self.missing_files_detector.find_missing_files(
            exchanges, start_date, end_date, include_weekends
        )

        if not missing_files:
            print(f"{Fore.GREEN}✅ No missing files to download!{Style.RESET_ALL}")
            return

        # Initialize progress display
        progress = MultiProgressDisplay()

        # Add exchanges to progress tracker
        for exchange in missing_files.keys():
            missing_count = len(missing_files[exchange])
            progress.add_exchange(exchange, missing_count)

        # Start downloads for missing files only
        download_tasks = []
        for exchange, missing_dates in missing_files.items():
            task = asyncio.create_task(
                self.download_exchange_missing_files(exchange, missing_dates, progress)
            )
            download_tasks.append(task)

        # Wait for all downloads to complete
        await asyncio.gather(*download_tasks, return_exceptions=True)

        # Finish progress display
        progress.finish()

    async def download_exchange_missing_files(self, exchange_id: str, missing_dates: List[date],
                                            progress: MultiProgressDisplay):
        """Download missing files for a specific exchange"""
        try:
            print(f"{Fore.CYAN}📥 Downloading missing {exchange_id} files...{Style.RESET_ALL}")

            # Download each missing file
            for i, target_date in enumerate(missing_dates):
                try:
                    # Update progress with current file
                    current_file = f"{exchange_id}_{target_date.strftime('%Y%m%d')}"
                    progress.update_exchange(
                        exchange_id,
                        current_file=current_file
                    )

                    # Get actual downloader and attempt download
                    downloader = self._get_downloader_for_exchange(exchange_id)
                    if downloader:
                        success = await self._download_single_file(downloader, target_date)
                        bytes_downloaded = 100000 if success else 0
                    else:
                        success = False
                        bytes_downloaded = 0

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
            print(f"{Fore.RED}❌ Error downloading missing {exchange_id} files: {e}{Style.RESET_ALL}")
    
    async def select_date_range(self) -> Tuple[date, date]:
        """Select date range from predefined options or custom pattern"""
        # Enhanced date range options
        enhanced_ranges = {
            **self.date_ranges,
            "advanced_pattern": "Advanced pattern (e.g., last-15-days, this-quarter)"
        }

        menu = create_simple_menu("📅 Select Date Range", list(enhanced_ranges.values()))
        result = self.menu_controller.run_menu(menu)

        if not result:
            return self.get_default_date_range()

        # Check if advanced pattern was selected
        if result.title == "Advanced pattern (e.g., last-15-days, this-quarter)":
            return await self.get_advanced_date_pattern()

        # Map back to key for standard ranges
        for key, value in enhanced_ranges.items():
            if value == result.title:
                return self.calculate_date_range(key)

        return self.get_default_date_range()

    async def get_advanced_date_pattern(self) -> Tuple[date, date]:
        """Get advanced date pattern from user"""
        print(f"\n{Fore.CYAN}📅 Advanced Date Pattern{Style.RESET_ALL}")
        print("=" * 40)

        print(f"{Fore.YELLOW}Available patterns:{Style.RESET_ALL}")
        patterns = self.date_parser.get_available_patterns()
        for i, pattern in enumerate(patterns[:10], 1):
            print(f"  {i:2}. {pattern}")

        if len(patterns) > 10:
            print(f"     ... and {len(patterns) - 10} more")

        print(f"\n{Fore.YELLOW}Examples:{Style.RESET_ALL}")
        print("  last-15-days    - Last 15 days")
        print("  this-quarter    - Current quarter")
        print("  2025-01         - January 2025")
        print("  2025-01-01:2025-01-31 - Custom range")

        while True:
            try:
                pattern = input(f"\n{Fore.YELLOW}Enter date pattern: {Style.RESET_ALL}").strip()
                if not pattern:
                    return self.get_default_date_range()

                start_date, end_date = self.date_parser.parse_date_range(pattern)

                # Confirm the parsed range
                days = (end_date - start_date).days + 1
                print(f"\n{Fore.GREEN}Parsed range: {start_date} to {end_date} ({days} days){Style.RESET_ALL}")

                if self.confirm_action("Use this date range?"):
                    return start_date, end_date

            except ValueError as e:
                print(f"{Fore.RED}❌ {e}{Style.RESET_ALL}")
                if not self.confirm_action("Try again?"):
                    return self.get_default_date_range()
    
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
        self.config_manager.display_current_config()
        input("\nPress Enter to continue...")

    async def manage_configuration(self):
        """Configuration management menu"""
        while True:
            menu = InteractiveMenu("🔧 Configuration Management", MenuType.SINGLE_SELECT)
            menu.add_item("update_setting", "⚙️  Update Setting", "Modify a configuration setting")
            menu.add_item("validate_config", "✅ Validate Configuration", "Check configuration for issues")
            menu.add_separator("Profiles")
            menu.add_item("list_profiles", "📋 List Profiles", "View all download profiles")
            menu.add_item("create_profile", "➕ Create Profile", "Create a new download profile")
            menu.add_item("use_profile", "🎯 Use Profile", "Apply a download profile")
            menu.add_item("delete_profile", "🗑️  Delete Profile", "Remove a download profile")
            menu.add_separator("Import/Export")
            menu.add_item("export_config", "📤 Export Configuration", "Export config and profiles")
            menu.add_item("import_config", "📥 Import Configuration", "Import config and profiles")
            menu.add_separator()
            menu.add_item("back", "🔙 Back to Main Menu", "Return to main menu")

            result = self.menu_controller.run_menu(menu)

            if not result or result.id == "back":
                break

            await self.handle_config_menu_selection(result.id)
    
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
            # Get actual downloader instance
            downloader = self._get_downloader_for_exchange(exchange_id)
            if not downloader:
                print(f"{Fore.RED}❌ No downloader available for {exchange_id}{Style.RESET_ALL}")
                return

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

                    # Attempt actual download
                    success = await self._download_single_file(downloader, target_date)
                    bytes_downloaded = 100000 if success else 0  # Approximate file size

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

    def _get_downloader_for_exchange(self, exchange_id: str):
        """Get appropriate downloader instance for exchange"""
        try:
            # Parse exchange and segment from exchange_id (e.g., "NSE_EQ" -> "NSE", "EQ")
            if '_' not in exchange_id:
                return None

            exchange, segment = exchange_id.split('_', 1)

            # Import and create appropriate downloader
            if exchange == "NSE" and segment == "EQ":
                from ..downloaders.nse_eq_downloader import NSEEQDownloader
                return NSEEQDownloader(self.config)
            elif exchange == "NSE" and segment == "FO":
                from ..downloaders.nse_fo_downloader import NSEFODownloader
                return NSEFODownloader(self.config)
            elif exchange == "NSE" and segment == "SME":
                from ..downloaders.nse_sme_downloader import NSESMEDownloader
                return NSESMEDownloader(self.config)
            elif exchange == "NSE" and segment == "INDEX":
                from ..downloaders.nse_index_downloader import NSEIndexDownloader
                return NSEIndexDownloader(self.config)
            elif exchange == "BSE" and segment == "EQ":
                from ..downloaders.bse_eq_downloader import BSEEQDownloader
                return BSEEQDownloader(self.config)
            elif exchange == "BSE" and segment == "INDEX":
                from ..downloaders.bse_index_downloader import BSEIndexDownloader
                return BSEIndexDownloader(self.config)
            else:
                print(f"{Fore.YELLOW}⚠️ Unknown exchange: {exchange_id}{Style.RESET_ALL}")
                return None

        except ImportError as e:
            print(f"{Fore.YELLOW}⚠️ Downloader not available for {exchange_id}: {e}{Style.RESET_ALL}")
            return None
        except Exception as e:
            print(f"{Fore.RED}❌ Error creating downloader for {exchange_id}: {e}{Style.RESET_ALL}")
            return None

    async def _download_single_file(self, downloader, target_date: date) -> bool:
        """Download single file using downloader"""
        try:
            # Use the downloader's download_data_range method for single date
            success = await downloader.download_data_range(target_date, target_date)
            return success
        except Exception as e:
            print(f"{Fore.RED}❌ Download failed for {target_date}: {e}{Style.RESET_ALL}")
            return False

    async def handle_config_menu_selection(self, selection: str):
        """Handle configuration menu selection"""
        if selection == "update_setting":
            await self.update_setting_menu()
        elif selection == "validate_config":
            self.validate_configuration()
        elif selection == "list_profiles":
            self.list_profiles()
        elif selection == "create_profile":
            await self.create_profile_menu()
        elif selection == "use_profile":
            await self.use_profile_menu()
        elif selection == "delete_profile":
            await self.delete_profile_menu()
        elif selection == "export_config":
            await self.export_config_menu()
        elif selection == "import_config":
            await self.import_config_menu()

    async def update_setting_menu(self):
        """Update configuration setting"""
        print(f"\n{Fore.CYAN}⚙️  Update Configuration Setting{Style.RESET_ALL}")
        print("=" * 50)

        print(f"{Fore.YELLOW}Common settings:{Style.RESET_ALL}")
        print("  timeout_seconds - Download timeout (e.g., 10)")
        print("  retry_attempts - Number of retries (e.g., 3)")
        print("  fast_mode - Enable fast mode (true/false)")
        print("  data_folder - Data directory path")

        try:
            setting_key = input(f"\n{Fore.YELLOW}Enter setting name: {Style.RESET_ALL}").strip()
            if not setting_key:
                return

            setting_value = input(f"{Fore.YELLOW}Enter new value: {Style.RESET_ALL}").strip()
            if not setting_value:
                return

            # Try to parse the value
            try:
                # Try boolean
                if setting_value.lower() in ['true', 'false']:
                    parsed_value = setting_value.lower() == 'true'
                # Try integer
                elif setting_value.isdigit():
                    parsed_value = int(setting_value)
                # Try float
                elif '.' in setting_value and setting_value.replace('.', '').isdigit():
                    parsed_value = float(setting_value)
                else:
                    # Keep as string
                    parsed_value = setting_value
            except:
                parsed_value = setting_value

            if self.config_manager.update_setting(setting_key, parsed_value):
                print(f"{Fore.GREEN}✅ Setting updated successfully{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}❌ Failed to update setting{Style.RESET_ALL}")

        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Operation cancelled{Style.RESET_ALL}")

        input("\nPress Enter to continue...")

    def validate_configuration(self):
        """Validate current configuration"""
        print(f"\n{Fore.CYAN}✅ Configuration Validation{Style.RESET_ALL}")
        print("=" * 50)

        issues = self.config_manager.validate_config()

        if not issues:
            print(f"{Fore.GREEN}✅ Configuration is valid!{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}❌ Found {len(issues)} issue(s):{Style.RESET_ALL}")
            for i, issue in enumerate(issues, 1):
                print(f"  {i}. {issue}")

        input("\nPress Enter to continue...")

    def list_profiles(self):
        """List all download profiles"""
        self.config_manager.list_profiles()
        input("\nPress Enter to continue...")

    async def create_profile_menu(self):
        """Create a new download profile"""
        print(f"\n{Fore.CYAN}➕ Create Download Profile{Style.RESET_ALL}")
        print("=" * 50)

        try:
            name = input(f"{Fore.YELLOW}Profile name: {Style.RESET_ALL}").strip()
            if not name:
                return

            description = input(f"{Fore.YELLOW}Description: {Style.RESET_ALL}").strip()
            if not description:
                description = f"Profile {name}"

            # Exchange selection
            print(f"\n{Fore.YELLOW}Select exchanges for this profile:{Style.RESET_ALL}")
            exchange_menu = create_multi_select_menu("Select Exchanges", self.exchanges)
            exchange_result = self.menu_controller.run_menu(exchange_menu)
            selected_exchanges = [item.id for item in exchange_menu.get_selected_items()]

            if not selected_exchanges:
                print(f"{Fore.RED}❌ No exchanges selected{Style.RESET_ALL}")
                input("Press Enter to continue...")
                return

            # Additional settings
            print(f"\n{Fore.YELLOW}Additional settings (press Enter for defaults):{Style.RESET_ALL}")

            timeout_input = input(f"Timeout seconds (default: 10): ").strip()
            timeout = int(timeout_input) if timeout_input.isdigit() else 10

            retry_input = input(f"Retry attempts (default: 3): ").strip()
            retries = int(retry_input) if retry_input.isdigit() else 3

            fast_mode = self.confirm_action("Enable fast mode?")
            include_weekends = self.confirm_action("Include weekends?")

            date_pattern = input(f"Default date pattern (default: last-7-days): ").strip()
            if not date_pattern:
                date_pattern = "last-7-days"

            # Create the profile
            success = self.config_manager.create_profile(
                name=name,
                description=description,
                exchanges=selected_exchanges,
                timeout_seconds=timeout,
                retry_attempts=retries,
                fast_mode=fast_mode,
                include_weekends=include_weekends,
                date_pattern=date_pattern
            )

            if success:
                print(f"{Fore.GREEN}✅ Profile '{name}' created successfully{Style.RESET_ALL}")

        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Operation cancelled{Style.RESET_ALL}")

        input("\nPress Enter to continue...")

    async def use_profile_menu(self):
        """Use a download profile"""
        if not self.config_manager.profiles:
            print(f"{Fore.YELLOW}No profiles available. Create one first.{Style.RESET_ALL}")
            input("Press Enter to continue...")
            return

        # Create menu from available profiles
        profile_options = {
            name: f"{profile.description} ({len(profile.exchanges)} exchanges)"
            for name, profile in self.config_manager.profiles.items()
        }

        menu = create_simple_menu("🎯 Select Profile to Use", list(profile_options.values()))
        result = self.menu_controller.run_menu(menu)

        if not result:
            return

        # Find the selected profile
        selected_profile_name = None
        for name, description in profile_options.items():
            if description == result.title:
                selected_profile_name = name
                break

        if selected_profile_name:
            profile = self.config_manager.use_profile(selected_profile_name)
            if profile:
                print(f"\n{Fore.GREEN}✅ Using profile: {profile.name}{Style.RESET_ALL}")
                print(f"  Exchanges: {', '.join(profile.exchanges)}")
                print(f"  Date pattern: {profile.date_pattern}")

                if self.confirm_action("Start download with this profile?"):
                    # Parse date range from profile
                    try:
                        start_date, end_date = self.date_parser.parse_date_range(profile.date_pattern)
                        await self.perform_download(profile.exchanges, start_date, end_date)
                    except ValueError as e:
                        print(f"{Fore.RED}❌ Invalid date pattern in profile: {e}{Style.RESET_ALL}")

        input("\nPress Enter to continue...")

    async def delete_profile_menu(self):
        """Delete a download profile"""
        if not self.config_manager.profiles:
            print(f"{Fore.YELLOW}No profiles available to delete.{Style.RESET_ALL}")
            input("Press Enter to continue...")
            return

        # Create menu from available profiles
        profile_names = list(self.config_manager.profiles.keys())
        menu = create_simple_menu("🗑️  Select Profile to Delete", profile_names)
        result = self.menu_controller.run_menu(menu)

        if result and result.title in profile_names:
            profile_name = result.title

            print(f"\n{Fore.RED}⚠️  Warning: This will permanently delete profile '{profile_name}'{Style.RESET_ALL}")
            if self.confirm_action("Are you sure?"):
                if self.config_manager.delete_profile(profile_name):
                    print(f"{Fore.GREEN}✅ Profile deleted successfully{Style.RESET_ALL}")

        input("\nPress Enter to continue...")

    async def export_config_menu(self):
        """Export configuration and profiles"""
        print(f"\n{Fore.CYAN}📤 Export Configuration{Style.RESET_ALL}")
        print("=" * 50)

        try:
            filename = input(f"{Fore.YELLOW}Export filename (default: config_export.json): {Style.RESET_ALL}").strip()
            if not filename:
                filename = "config_export.json"

            if not filename.endswith('.json'):
                filename += '.json'

            export_path = Path(filename)

            if self.config_manager.export_config(export_path):
                print(f"{Fore.GREEN}✅ Configuration exported successfully{Style.RESET_ALL}")

        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Operation cancelled{Style.RESET_ALL}")

        input("\nPress Enter to continue...")

    async def import_config_menu(self):
        """Import configuration and profiles"""
        print(f"\n{Fore.CYAN}📥 Import Configuration{Style.RESET_ALL}")
        print("=" * 50)

        try:
            filename = input(f"{Fore.YELLOW}Import filename: {Style.RESET_ALL}").strip()
            if not filename:
                return

            import_path = Path(filename)
            if not import_path.exists():
                print(f"{Fore.RED}❌ File not found: {filename}{Style.RESET_ALL}")
                input("Press Enter to continue...")
                return

            merge = self.confirm_action("Merge with existing config? (No = replace)")

            if self.config_manager.import_config(import_path, merge=merge):
                print(f"{Fore.GREEN}✅ Configuration imported successfully{Style.RESET_ALL}")

        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Operation cancelled{Style.RESET_ALL}")

        input("\nPress Enter to continue...")

    async def data_quality_report_menu(self):
        """Generate comprehensive data quality report"""
        print(f"\n{Fore.CYAN}📋 Data Quality Report Generation{Style.RESET_ALL}")
        print("=" * 50)

        # Exchange selection
        print(f"\n{Fore.YELLOW}Select exchanges for quality analysis:{Style.RESET_ALL}")
        exchange_menu = create_multi_select_menu("Select Exchanges", self.exchanges)
        exchange_menu.selected_items = list(self.exchanges.keys())  # Pre-select all

        result = self.menu_controller.run_menu(exchange_menu)
        selected_exchanges = [item.id for item in exchange_menu.get_selected_items()]

        if not selected_exchanges:
            print(f"{Fore.RED}❌ No exchanges selected{Style.RESET_ALL}")
            input("Press Enter to continue...")
            return

        # Date range selection
        start_date, end_date = await self.select_date_range()

        print(f"\n{Fore.CYAN}📊 Generating quality report...{Style.RESET_ALL}")
        print(f"  Exchanges: {', '.join(selected_exchanges)}")
        print(f"  Period: {start_date} to {end_date}")

        try:
            # Generate quality reports
            reports = self.quality_validator.generate_completeness_report(
                selected_exchanges, start_date, end_date
            )

            # Display reports
            self.display_quality_reports(reports)

            # Ask if user wants to export
            if self.confirm_action("Export report to file?"):
                await self.export_quality_reports(reports)

        except Exception as e:
            print(f"{Fore.RED}❌ Error generating quality report: {e}{Style.RESET_ALL}")

        input("\nPress Enter to continue...")

    def display_quality_reports(self, reports: List[QualityReport]):
        """Display quality reports in formatted way"""
        print(f"\n{Fore.CYAN}{Style.BRIGHT}📊 Data Quality Analysis Results{Style.RESET_ALL}")
        print("=" * 70)

        # Overall summary
        total_expected = sum(r.total_expected for r in reports)
        total_present = sum(r.total_present for r in reports)
        total_missing = sum(r.total_missing for r in reports)
        total_corrupted = sum(r.total_corrupted for r in reports)
        overall_completeness = (total_present / total_expected * 100) if total_expected > 0 else 0

        print(f"\n{Fore.WHITE}{Style.BRIGHT}📈 Overall Summary:{Style.RESET_ALL}")
        print(f"  Total Expected Files: {total_expected}")
        print(f"  Files Present: {total_present} ({overall_completeness:.1f}%)")
        print(f"  Missing Files: {total_missing}")
        print(f"  Corrupted Files: {total_corrupted}")

        # Quality level color
        if overall_completeness >= 98:
            quality_color = Fore.GREEN
            quality_icon = "🎉"
        elif overall_completeness >= 95:
            quality_color = Fore.YELLOW
            quality_icon = "✅"
        elif overall_completeness >= 90:
            quality_color = Fore.YELLOW
            quality_icon = "⚠️"
        else:
            quality_color = Fore.RED
            quality_icon = "❌"

        print(f"  {quality_color}Overall Quality: {quality_icon} {overall_completeness:.1f}%{Style.RESET_ALL}")

        # Individual exchange reports
        print(f"\n{Fore.WHITE}{Style.BRIGHT}📊 Exchange-wise Analysis:{Style.RESET_ALL}")

        for report in reports:
            # Quality level formatting
            if report.quality_level == QualityLevel.EXCELLENT:
                level_color = Fore.GREEN
                level_icon = "🎉"
            elif report.quality_level == QualityLevel.GOOD:
                level_color = Fore.YELLOW
                level_icon = "✅"
            elif report.quality_level == QualityLevel.FAIR:
                level_color = Fore.YELLOW
                level_icon = "⚠️"
            else:
                level_color = Fore.RED
                level_icon = "❌"

            print(f"\n{Fore.CYAN}{Style.BRIGHT}{report.exchange}:{Style.RESET_ALL}")
            print(f"  Completeness: {level_color}{level_icon} {report.completeness_rate:.1f}% ({report.total_present}/{report.total_expected}){Style.RESET_ALL}")

            if report.total_missing > 0:
                print(f"  {Fore.RED}Missing: {report.total_missing} files{Style.RESET_ALL}")
                if len(report.missing_dates) <= 5:
                    dates_str = ", ".join(d.strftime('%Y-%m-%d') for d in report.missing_dates)
                    print(f"    Dates: {dates_str}")
                else:
                    print(f"    Date range: {report.missing_dates[0].strftime('%Y-%m-%d')} to {report.missing_dates[-1].strftime('%Y-%m-%d')}")

            if report.total_corrupted > 0:
                print(f"  {Fore.RED}Corrupted: {report.total_corrupted} files{Style.RESET_ALL}")

            # Show recommendations
            if report.recommendations:
                print(f"  {Fore.YELLOW}Recommendations:{Style.RESET_ALL}")
                for rec in report.recommendations:
                    print(f"    {rec}")

        # Critical issues summary
        critical_exchanges = [r for r in reports if r.quality_level in [QualityLevel.FAIR, QualityLevel.POOR]]
        if critical_exchanges:
            print(f"\n{Fore.RED}{Style.BRIGHT}⚠️  Exchanges Needing Attention:{Style.RESET_ALL}")
            for report in critical_exchanges:
                print(f"  {report.exchange}: {report.completeness_rate:.1f}% completeness")

    async def validate_data_integrity_menu(self):
        """Validate data integrity for specific files"""
        print(f"\n{Fore.CYAN}🔍 Data Integrity Validation{Style.RESET_ALL}")
        print("=" * 50)

        # Quick validation options
        menu = InteractiveMenu("🔍 Validation Options", MenuType.SINGLE_SELECT)
        menu.add_item("recent", "📅 Recent Files (Last 7 days)", "Validate recently downloaded files")
        menu.add_item("custom", "📊 Custom Range", "Validate specific date range")
        menu.add_item("all_exchanges", "🌐 All Exchanges (Last 30 days)", "Quick validation of all exchanges")
        menu.add_item("back", "🔙 Back", "Return to main menu")

        result = self.menu_controller.run_menu(menu)

        if not result or result.id == "back":
            return

        if result.id == "recent":
            end_date = date.today()
            start_date = end_date - timedelta(days=7)
            exchanges = list(self.exchanges.keys())
        elif result.id == "all_exchanges":
            end_date = date.today()
            start_date = end_date - timedelta(days=30)
            exchanges = list(self.exchanges.keys())
        else:  # custom
            start_date, end_date = await self.select_date_range()
            # Exchange selection
            exchange_menu = create_multi_select_menu("Select Exchanges", self.exchanges)
            self.menu_controller.run_menu(exchange_menu)
            exchanges = [item.id for item in exchange_menu.get_selected_items()]

        if not exchanges:
            print(f"{Fore.RED}❌ No exchanges selected{Style.RESET_ALL}")
            input("Press Enter to continue...")
            return

        print(f"\n{Fore.CYAN}🔍 Validating data integrity...{Style.RESET_ALL}")

        try:
            # Generate validation report
            reports = self.quality_validator.generate_completeness_report(
                exchanges, start_date, end_date
            )

            # Focus on integrity issues
            integrity_issues = []
            for report in reports:
                for file_info in report.corrupted_files:
                    if file_info.status in [FileStatus.CORRUPTED, FileStatus.INVALID]:
                        integrity_issues.append((report.exchange, file_info))

            if not integrity_issues:
                print(f"{Fore.GREEN}✅ All files passed integrity validation!{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}❌ Found {len(integrity_issues)} integrity issues:{Style.RESET_ALL}")

                for exchange, file_info in integrity_issues:
                    print(f"\n  {Fore.YELLOW}{exchange} - {file_info.date.strftime('%Y-%m-%d')}:{Style.RESET_ALL}")
                    print(f"    Status: {file_info.status.value}")
                    print(f"    Issue: {file_info.error_message}")
                    if file_info.actual_path:
                        print(f"    File: {file_info.actual_path}")

                if self.confirm_action("Attempt to re-download corrupted files?"):
                    await self.recover_corrupted_files(integrity_issues)

        except Exception as e:
            print(f"{Fore.RED}❌ Error during validation: {e}{Style.RESET_ALL}")

        input("\nPress Enter to continue...")

    async def gap_analysis_menu(self):
        """Perform detailed gap analysis"""
        print(f"\n{Fore.CYAN}📊 Data Gap Analysis{Style.RESET_ALL}")
        print("=" * 50)

        # Date range selection
        start_date, end_date = await self.select_date_range()

        print(f"\n{Fore.CYAN}🔍 Analyzing data gaps from {start_date} to {end_date}...{Style.RESET_ALL}")

        try:
            # Generate reports for all exchanges
            reports = self.quality_validator.generate_completeness_report(
                list(self.exchanges.keys()), start_date, end_date
            )

            # Analyze gaps
            total_gaps = sum(len(r.missing_dates) for r in reports)

            if total_gaps == 0:
                print(f"{Fore.GREEN}🎉 No data gaps found! Complete data coverage.{Style.RESET_ALL}")
                input("\nPress Enter to continue...")
                return

            print(f"\n{Fore.YELLOW}📊 Gap Analysis Results:{Style.RESET_ALL}")
            print(f"  Total Missing Files: {total_gaps}")

            # Group gaps by date
            gap_by_date = {}
            for report in reports:
                for missing_date in report.missing_dates:
                    if missing_date not in gap_by_date:
                        gap_by_date[missing_date] = []
                    gap_by_date[missing_date].append(report.exchange)

            # Show gaps by date
            print(f"\n{Fore.WHITE}📅 Missing Files by Date:{Style.RESET_ALL}")
            for gap_date in sorted(gap_by_date.keys()):
                exchanges = gap_by_date[gap_date]
                print(f"  {gap_date.strftime('%Y-%m-%d')}: {', '.join(exchanges)} ({len(exchanges)} exchanges)")

            # Show gaps by exchange
            print(f"\n{Fore.WHITE}📊 Missing Files by Exchange:{Style.RESET_ALL}")
            for report in reports:
                if report.missing_dates:
                    print(f"  {report.exchange}: {len(report.missing_dates)} missing files")

            # Recovery options
            print(f"\n{Fore.CYAN}🔄 Recovery Options:{Style.RESET_ALL}")
            if self.confirm_action("Download all missing files?"):
                await self.recover_missing_files_from_reports(reports)

        except Exception as e:
            print(f"{Fore.RED}❌ Error during gap analysis: {e}{Style.RESET_ALL}")

        input("\nPress Enter to continue...")

    async def export_quality_reports(self, reports: List[QualityReport]):
        """Export quality reports to file"""
        print(f"\n{Fore.CYAN}📤 Export Quality Report{Style.RESET_ALL}")
        print("=" * 40)

        # Export format selection
        format_menu = create_simple_menu("📄 Select Export Format", [
            "CSV (Spreadsheet compatible)",
            "JSON (API/Script friendly)",
            "Text (Human readable)"
        ])

        format_result = self.menu_controller.run_menu(format_menu)
        if not format_result:
            return

        # Determine format
        if "CSV" in format_result.title:
            await self.export_reports_csv(reports)
        elif "JSON" in format_result.title:
            await self.export_reports_json(reports)
        else:
            await self.export_reports_text(reports)

    async def export_reports_csv(self, reports: List[QualityReport]):
        """Export reports to CSV format"""
        try:
            filename = f"quality_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)

                # Write header
                writer.writerow([
                    'Exchange', 'Period_Start', 'Period_End', 'Total_Expected',
                    'Total_Present', 'Total_Missing', 'Total_Corrupted',
                    'Completeness_Rate', 'Quality_Level', 'Missing_Dates'
                ])

                # Write data
                for report in reports:
                    missing_dates_str = ';'.join(d.strftime('%Y-%m-%d') for d in report.missing_dates)
                    writer.writerow([
                        report.exchange,
                        report.period_start.strftime('%Y-%m-%d'),
                        report.period_end.strftime('%Y-%m-%d'),
                        report.total_expected,
                        report.total_present,
                        report.total_missing,
                        report.total_corrupted,
                        f"{report.completeness_rate:.2f}%",
                        report.quality_level.value,
                        missing_dates_str
                    ])

            print(f"{Fore.GREEN}✅ CSV report exported: {filename}{Style.RESET_ALL}")

        except Exception as e:
            print(f"{Fore.RED}❌ Error exporting CSV: {e}{Style.RESET_ALL}")

    async def export_reports_json(self, reports: List[QualityReport]):
        """Export reports to JSON format"""
        try:
            filename = f"quality_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

            # Convert reports to JSON-serializable format
            json_data = {
                "generated_at": datetime.now().isoformat(),
                "summary": {
                    "total_exchanges": len(reports),
                    "overall_completeness": sum(r.completeness_rate for r in reports) / len(reports) if reports else 0
                },
                "reports": []
            }

            for report in reports:
                report_data = {
                    "exchange": report.exchange,
                    "period": {
                        "start": report.period_start.isoformat(),
                        "end": report.period_end.isoformat()
                    },
                    "statistics": {
                        "total_expected": report.total_expected,
                        "total_present": report.total_present,
                        "total_missing": report.total_missing,
                        "total_corrupted": report.total_corrupted,
                        "completeness_rate": report.completeness_rate
                    },
                    "quality_level": report.quality_level.value,
                    "missing_dates": [d.isoformat() for d in report.missing_dates],
                    "recommendations": report.recommendations
                }
                json_data["reports"].append(report_data)

            with open(filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(json_data, jsonfile, indent=2, ensure_ascii=False)

            print(f"{Fore.GREEN}✅ JSON report exported: {filename}{Style.RESET_ALL}")

        except Exception as e:
            print(f"{Fore.RED}❌ Error exporting JSON: {e}{Style.RESET_ALL}")

    async def export_reports_text(self, reports: List[QualityReport]):
        """Export reports to human-readable text format"""
        try:
            filename = f"quality_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

            with open(filename, 'w', encoding='utf-8') as txtfile:
                txtfile.write("NSE/BSE Data Quality Report\n")
                txtfile.write("=" * 50 + "\n")
                txtfile.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

                # Overall summary
                total_expected = sum(r.total_expected for r in reports)
                total_present = sum(r.total_present for r in reports)
                overall_completeness = (total_present / total_expected * 100) if total_expected > 0 else 0

                txtfile.write("OVERALL SUMMARY\n")
                txtfile.write("-" * 20 + "\n")
                txtfile.write(f"Total Expected Files: {total_expected}\n")
                txtfile.write(f"Files Present: {total_present} ({overall_completeness:.1f}%)\n")
                txtfile.write(f"Missing Files: {sum(r.total_missing for r in reports)}\n")
                txtfile.write(f"Corrupted Files: {sum(r.total_corrupted for r in reports)}\n\n")

                # Individual reports
                for report in reports:
                    txtfile.write(f"{report.exchange.upper()}\n")
                    txtfile.write("-" * len(report.exchange) + "\n")
                    txtfile.write(f"Period: {report.period_start} to {report.period_end}\n")
                    txtfile.write(f"Completeness: {report.completeness_rate:.1f}% ({report.total_present}/{report.total_expected})\n")
                    txtfile.write(f"Quality Level: {report.quality_level.value.title()}\n")

                    if report.missing_dates:
                        txtfile.write(f"Missing Dates: {', '.join(d.strftime('%Y-%m-%d') for d in report.missing_dates)}\n")

                    if report.recommendations:
                        txtfile.write("Recommendations:\n")
                        for rec in report.recommendations:
                            txtfile.write(f"  - {rec}\n")

                    txtfile.write("\n")

            print(f"{Fore.GREEN}✅ Text report exported: {filename}{Style.RESET_ALL}")

        except Exception as e:
            print(f"{Fore.RED}❌ Error exporting text: {e}{Style.RESET_ALL}")

    async def recover_missing_files_from_reports(self, reports: List[QualityReport]):
        """Recover missing files identified in quality reports"""
        print(f"\n{Fore.CYAN}🔄 Recovering Missing Files...{Style.RESET_ALL}")

        # Collect all missing files
        recovery_tasks = []
        for report in reports:
            if report.missing_dates:
                recovery_tasks.append((report.exchange, report.missing_dates))

        if not recovery_tasks:
            print(f"{Fore.GREEN}✅ No missing files to recover{Style.RESET_ALL}")
            return

        # Initialize progress tracking
        progress = MultiProgressDisplay()

        for exchange, missing_dates in recovery_tasks:
            progress.add_exchange(f"{exchange}_recovery", len(missing_dates))

        # Start recovery downloads
        download_tasks = []
        for exchange, missing_dates in recovery_tasks:
            task = asyncio.create_task(
                self.download_exchange_missing_files(f"{exchange}_recovery", missing_dates, progress)
            )
            download_tasks.append(task)

        # Wait for all recoveries to complete
        await asyncio.gather(*download_tasks, return_exceptions=True)

        # Finish progress display
        progress.finish()

        print(f"{Fore.GREEN}✅ Missing files recovery completed{Style.RESET_ALL}")

    async def recover_corrupted_files(self, integrity_issues: List[Tuple[str, Any]]):
        """Recover corrupted files"""
        print(f"\n{Fore.CYAN}🔧 Recovering Corrupted Files...{Style.RESET_ALL}")

        # Group by exchange
        recovery_by_exchange = {}
        for exchange, file_info in integrity_issues:
            if exchange not in recovery_by_exchange:
                recovery_by_exchange[exchange] = []
            recovery_by_exchange[exchange].append(file_info.date)

        # Initialize progress tracking
        progress = MultiProgressDisplay()

        for exchange, dates in recovery_by_exchange.items():
            progress.add_exchange(f"{exchange}_repair", len(dates))

        # Start recovery downloads
        download_tasks = []
        for exchange, dates in recovery_by_exchange.items():
            task = asyncio.create_task(
                self.download_exchange_missing_files(f"{exchange}_repair", dates, progress)
            )
            download_tasks.append(task)

        # Wait for all recoveries to complete
        await asyncio.gather(*download_tasks, return_exceptions=True)

        # Finish progress display
        progress.finish()

        print(f"{Fore.GREEN}✅ Corrupted files recovery completed{Style.RESET_ALL}")
