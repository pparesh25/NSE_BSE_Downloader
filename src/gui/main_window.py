"""
Main Window for NSE/BSE Data Downloader

PyQt6-based main window with exchange selection, progress tracking,
and background download management.
"""

import sys
import asyncio
from datetime import date
from typing import Dict, List, Optional
import logging

try:
    from PyQt6.QtWidgets import (
        QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
        QPushButton, QLabel, QCheckBox, QProgressBar, QTextEdit,
        QGroupBox, QFrame, QSplitter, QMessageBox, QApplication,
        QStatusBar, QMenuBar, QMenu, QSizePolicy
    )
    from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer
    from PyQt6.QtGui import QFont, QIcon, QAction
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False
    # Create dummy classes for when PyQt6 is not available
    class QMainWindow: pass
    class QThread: pass
    class pyqtSignal: 
        def __init__(self, *args): pass
        def connect(self, *args): pass
        def emit(self, *args): pass

from ..core.config import Config
from ..core.data_manager import DataManager
from ..downloaders.nse_eq_downloader import NSEEQDownloader
from ..downloaders.nse_fo_downloader import NSEFODownloader
from ..downloaders.nse_sme_downloader import NSESMEDownloader
from ..downloaders.nse_index_downloader import NSEIndexDownloader
from ..downloaders.bse_eq_downloader import BSEEQDownloader
from ..downloaders.bse_index_downloader import BSEIndexDownloader
from ..utils.update_checker import UpdateChecker
from .update_dialog import UpdateDialog
from ..utils.user_preferences import UserPreferences
from ..core.base_downloader import ProgressCallback
from ..core.exceptions import GUIError


def get_version():
    """Get current application version"""
    try:
        import sys
        import os
        # Add project root to path
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        sys.path.insert(0, project_root)
        from version import get_version as _get_version
        return _get_version()
    except ImportError:
        return "2.0.0"  # Fallback version


class UpdateCheckWorker(QThread):
    """Worker thread for checking updates"""

    update_checked = pyqtSignal(dict)  # Update result

    def __init__(self, update_checker: UpdateChecker):
        super().__init__()
        self.update_checker = update_checker
        self.logger = logging.getLogger(__name__)

    def run(self):
        """Check for updates in background"""
        try:
            result = self.update_checker.check_for_updates()
            self.update_checked.emit(result)
        except Exception as e:
            self.logger.error(f"Error in update check worker: {e}")
            self.update_checked.emit({"update_available": False, "error": str(e)})


class DownloadWorker(QThread):
    """Background worker thread for downloads"""

    progress_updated = pyqtSignal(str, int, str)  # exchange, percentage, message
    status_updated = pyqtSignal(str, str)         # exchange, status
    error_occurred = pyqtSignal(str, str)         # exchange, error
    download_completed = pyqtSignal(str, bool)    # exchange, success
    all_downloads_completed = pyqtSignal(bool)    # overall success

    def __init__(self, config: Config, selected_exchanges: List[str], include_weekends: bool = False, timeout_seconds: int = 5):
        super().__init__()
        self.config = config
        self.selected_exchanges = selected_exchanges
        self.include_weekends = include_weekends
        self.timeout_seconds = timeout_seconds
        self.downloaders = {}
        self.logger = logging.getLogger(__name__)

        # Stop flag for graceful shutdown
        self.stop_requested = False

        # Update config timeout
        self.config.download_settings.timeout_seconds = timeout_seconds

        # Initialize downloaders
        self._initialize_downloaders()
    
    def _initialize_downloaders(self):
        """Initialize downloader instances"""
        downloader_classes = {
            'NSE_EQ': NSEEQDownloader,
            'NSE_FO': NSEFODownloader,
            'NSE_SME': NSESMEDownloader,
            'NSE_INDEX': NSEIndexDownloader,
            'BSE_EQ': BSEEQDownloader,
            'BSE_INDEX': BSEIndexDownloader
        }
        
        for exchange in self.selected_exchanges:
            if exchange in downloader_classes:
                try:
                    downloader = downloader_classes[exchange](self.config)
                    
                    # Set up progress callback
                    progress_callback = ProgressCallback(
                        on_progress=lambda ex, pct, msg, e=exchange: self.progress_updated.emit(e, pct, msg),
                        on_status=lambda ex, msg, e=exchange: self.status_updated.emit(e, msg),
                        on_error=lambda ex, err, e=exchange: self.error_occurred.emit(e, err)
                    )
                    downloader.set_progress_callback(progress_callback)
                    
                    self.downloaders[exchange] = downloader
                    
                except Exception as e:
                    self.logger.error(f"Failed to initialize {exchange} downloader: {e}")
    
    def run(self):
        """Run downloads in background thread"""
        try:
            # Set up asyncio event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Run downloads
            overall_success = loop.run_until_complete(self._run_downloads())
            
            # Emit completion signal
            self.all_downloads_completed.emit(overall_success)
            
        except Exception as e:
            self.logger.error(f"Error in download worker: {e}")
            self.all_downloads_completed.emit(False)
        finally:
            # Clean up event loop
            try:
                loop.close()
            except Exception:
                pass
    
    async def _run_downloads(self) -> bool:
        """Run all downloads asynchronously"""
        download_tasks = []

        for exchange, downloader in self.downloaders.items():
            try:
                # Check if stop requested
                if self.stop_requested:
                    self.logger.info("Download stopped by user request")
                    return False
                # Create download task
                task = asyncio.create_task(
                    self._download_exchange_data(exchange, downloader)
                )
                download_tasks.append(task)
                
            except Exception as e:
                self.error_occurred.emit(exchange, f"Failed to start download: {e}")
        
        if not download_tasks:
            return False
        
        # Wait for all downloads to complete
        results = await asyncio.gather(*download_tasks, return_exceptions=True)
        
        # Check results
        success_count = 0
        for i, result in enumerate(results):
            exchange = list(self.downloaders.keys())[i]
            
            if isinstance(result, Exception):
                self.error_occurred.emit(exchange, f"Download failed: {result}")
                self.download_completed.emit(exchange, False)
            else:
                success = bool(result)
                self.download_completed.emit(exchange, success)
                if success:
                    success_count += 1
        
        return success_count > 0
    
    async def _download_exchange_data(self, exchange: str, downloader) -> bool:
        """Download data for a specific exchange"""
        try:
            # Check if stop requested
            if self.stop_requested:
                self.status_updated.emit(exchange, "Download stopped")
                return False

            self.status_updated.emit(exchange, "Starting download...")

            # Get date range
            start_date, end_date = downloader.get_date_range()

            if start_date > end_date:
                self.status_updated.emit(exchange, "No new data to download")
                return True

            # Check stop again before processing
            if self.stop_requested:
                self.status_updated.emit(exchange, "Download stopped")
                return False

            # Get working days with weekend option
            working_days = downloader.get_working_days(start_date, end_date, self.include_weekends)

            if not working_days:
                self.status_updated.emit(exchange, "No working days in date range")
                return True

            # Update total files for progress tracking
            downloader.total_files = len(working_days)
            downloader.completed_files = 0

            # Start download with working days
            success = await downloader._download_implementation(working_days)

            if success:
                self.status_updated.emit(exchange, "Download completed successfully")
            else:
                self.status_updated.emit(exchange, "Download completed with errors")

            return success

        except Exception as e:
            self.error_occurred.emit(exchange, f"Download error: {e}")
            return False


class MainWindow(QMainWindow):
    """
    Main application window
    
    Provides GUI interface for NSE/BSE data downloader with:
    - Exchange selection checkboxes
    - Progress tracking
    - Status updates
    - Download management
    """
    
    def __init__(self, config: Config):
        super().__init__()
        
        if not GUI_AVAILABLE:
            raise GUIError("PyQt6 is not available. Cannot create GUI.")
        
        self.config = config
        self.data_manager = DataManager(config)
        self.logger = logging.getLogger(__name__)
        
        # GUI components
        self.exchange_checkboxes: Dict[str, QCheckBox] = {}
        self.progress_bars: Dict[str, QProgressBar] = {}
        self.status_labels: Dict[str, QLabel] = {}
        self.weekend_checkbox: Optional[QCheckBox] = None

        # Dynamic options (shown based on exchange selection)
        self.sme_suffix_checkbox: Optional[QCheckBox] = None
        self.sme_append_checkbox: Optional[QCheckBox] = None
        self.index_append_checkbox: Optional[QCheckBox] = None
        self.bse_index_append_checkbox: Optional[QCheckBox] = None

        # Timeout option
        self.timeout_spinbox = None

        # Update checker (debug mode enabled for development)
        self.update_checker = UpdateChecker(get_version(), debug=True)  # Current version
        self.update_worker = None

        # User preferences
        self.user_prefs = UserPreferences()
        self.logger.info(f"User preferences loaded from: {self.user_prefs.get_config_file_path()}")
        
        # Download management
        self.download_worker: Optional[DownloadWorker] = None
        self.download_button: Optional[QPushButton] = None
        self.stop_button: Optional[QPushButton] = None
        
        # Status tracking
        self.download_status: Dict[str, str] = {}

        # Update throttling to prevent flickering
        self.last_update_time: Dict[str, float] = {}
        self.update_interval = 0.5  # Minimum 0.5 seconds between updates

        # Batch updates to reduce flickering
        self.pending_updates: Dict[str, tuple] = {}
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.process_pending_updates)
        self.update_timer.start(100)  # Process updates every 100ms
        
        # Initialize UI
        self.init_ui()
        self.load_data_summary()

        # Update dynamic options based on initial selection
        self.update_dynamic_options()

        # Check for updates after UI is loaded (delayed start)
        QTimer.singleShot(3000, self.check_for_updates)  # Check after 3 seconds
        
        # Set up status update timer
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status_display)
        self.status_timer.start(1000)  # Update every second
    
    def init_ui(self):
        """Initialize user interface"""
        try:
            # Set window properties
            gui_settings = self.config.gui_settings
            self.setWindowTitle(gui_settings.window_title)

            # Load window size from user preferences
            width, height = self.user_prefs.get_window_size()
            self.logger.info(f"Loading window size from preferences: {width}x{height}")
            self.setGeometry(100, 100, width, height)
            
            # Create central widget
            central_widget = QWidget()
            self.setCentralWidget(central_widget)
            
            # Create main layout
            main_layout = QVBoxLayout(central_widget)
            
            # Create menu bar
            self.create_menu_bar()
            
            # Create exchange selection area
            exchange_group = self.create_exchange_selection()
            main_layout.addWidget(exchange_group, 0)  # No stretch

            # Create options area
            options_group = self.create_options_area()
            main_layout.addWidget(options_group, 0)  # No stretch

            # Create progress tracking area
            progress_group = self.create_progress_tracking()
            main_layout.addWidget(progress_group, 0)  # No stretch

            # Create control buttons
            button_layout = self.create_control_buttons()
            main_layout.addLayout(button_layout, 0)  # No stretch

            # Create status area (expandable)
            status_group = self.create_status_area()
            main_layout.addWidget(status_group, 1)  # Stretch factor 1 - will expand
            
            # Create status bar
            self.create_status_bar()
            
            self.logger.info("GUI initialized successfully")
            
        except Exception as e:
            raise GUIError(f"Failed to initialize GUI: {e}")
    
    def create_menu_bar(self):
        """Create application menu bar"""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu('File')
        
        refresh_action = QAction('Refresh Data Summary', self)
        refresh_action.triggered.connect(self.load_data_summary)
        file_menu.addAction(refresh_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction('Exit', self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # Help menu
        help_menu = menubar.addMenu('Help')
        
        about_action = QAction('About', self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
    
    def create_exchange_selection(self) -> QGroupBox:
        """Create exchange selection area"""
        group = QGroupBox("Select Exchanges to Download")
        layout = QGridLayout(group)
        
        # Get available exchanges
        available_exchanges = self.config.get_available_exchanges()
        default_exchanges = self.config.gui_settings.default_exchanges
        
        row, col = 0, 0
        for exchange in available_exchanges:
            checkbox = QCheckBox(exchange.replace('_', ' '))

            # Set selection based on user preferences (fallback to config defaults)
            if self.user_prefs.is_exchange_selected(exchange):
                checkbox.setChecked(True)
            elif exchange in default_exchanges:
                checkbox.setChecked(True)

            # Connect to update dynamic options and save preferences
            checkbox.stateChanged.connect(self.on_exchange_selection_changed)

            self.exchange_checkboxes[exchange] = checkbox
            layout.addWidget(checkbox, row, col)

            col += 1
            if col >= 2:  # 2 columns
                col = 0
                row += 1
        
        return group

    def create_options_area(self) -> QGroupBox:
        """Create download options area"""
        group = QGroupBox("Download Options")
        layout = QVBoxLayout(group)

        # Basic options row
        basic_row = QHBoxLayout()

        # Weekend download option
        self.weekend_checkbox = QCheckBox("Include Weekend Downloads")
        self.weekend_checkbox.setToolTip("Check this to attempt downloads on weekends (for rare cases when markets are open)")
        self.weekend_checkbox.setChecked(self.user_prefs.get_include_weekends())  # Load from preferences
        self.weekend_checkbox.stateChanged.connect(self.on_weekend_option_changed)
        basic_row.addWidget(self.weekend_checkbox)

        # Response timeout option
        from PyQt6.QtWidgets import QSpinBox
        timeout_label = QLabel("Response Timeout (sec):")
        self.timeout_spinbox = QSpinBox()
        self.timeout_spinbox.setMinimum(1)
        self.timeout_spinbox.setMaximum(30)
        self.timeout_spinbox.setValue(self.user_prefs.get_timeout_seconds())  # Load from preferences
        self.timeout_spinbox.setToolTip("Server response timeout in seconds (default: 5)")
        self.timeout_spinbox.valueChanged.connect(self.on_timeout_changed)

        basic_row.addWidget(timeout_label)
        basic_row.addWidget(self.timeout_spinbox)
        basic_row.addStretch()

        layout.addLayout(basic_row)

        # Dynamic options for NSE SME (initially hidden)
        self.sme_options_row = QHBoxLayout()

        self.sme_suffix_checkbox = QCheckBox("Add '_sme' suffix to symbol names")
        self.sme_suffix_checkbox.setToolTip("Add '_sme' suffix to symbol names in NSE SME data")
        self.sme_suffix_checkbox.setChecked(False)
        self.sme_suffix_checkbox.setVisible(False)
        self.sme_options_row.addWidget(self.sme_suffix_checkbox)

        self.sme_append_checkbox = QCheckBox("Append NSE SME data to NSE EQ file")
        self.sme_append_checkbox.setToolTip("Combine NSE SME data with NSE EQ data in single file")
        self.sme_append_checkbox.setChecked(False)
        self.sme_append_checkbox.setVisible(False)
        self.sme_options_row.addWidget(self.sme_append_checkbox)

        self.sme_options_row.addStretch()
        layout.addLayout(self.sme_options_row)

        # Dynamic options for NSE INDEX (initially hidden)
        self.index_options_row = QHBoxLayout()

        self.index_append_checkbox = QCheckBox("Add NSE Index data to NSE EQ file")
        self.index_append_checkbox.setToolTip("Append NSE Index data to NSE EQ files")
        self.index_append_checkbox.setChecked(False)
        self.index_append_checkbox.setVisible(False)
        self.index_options_row.addWidget(self.index_append_checkbox)

        self.index_options_row.addStretch()
        layout.addLayout(self.index_options_row)

        # Dynamic options for BSE INDEX (initially hidden)
        self.bse_index_options_row = QHBoxLayout()

        self.bse_index_append_checkbox = QCheckBox("Add BSE Index data to BSE EQ file")
        self.bse_index_append_checkbox.setToolTip("Append BSE Index data to BSE EQ files")
        self.bse_index_append_checkbox.setChecked(False)
        self.bse_index_append_checkbox.setVisible(False)
        self.bse_index_options_row.addWidget(self.bse_index_append_checkbox)

        self.bse_index_options_row.addStretch()
        layout.addLayout(self.bse_index_options_row)

        return group

    def update_dynamic_options(self):
        """Update visibility of dynamic options based on exchange selection"""
        # Check if NSE SME is selected
        nse_sme_selected = self.exchange_checkboxes.get('NSE_SME', QCheckBox()).isChecked()

        # Show/hide NSE SME options
        self.sme_suffix_checkbox.setVisible(nse_sme_selected)
        self.sme_append_checkbox.setVisible(nse_sme_selected)

        # Check if NSE INDEX is selected
        nse_index_selected = self.exchange_checkboxes.get('NSE_INDEX', QCheckBox()).isChecked()

        # Show/hide NSE INDEX options
        self.index_append_checkbox.setVisible(nse_index_selected)

        # Check if BSE INDEX is selected
        bse_index_selected = self.exchange_checkboxes.get('BSE_INDEX', QCheckBox()).isChecked()

        # Show/hide BSE INDEX options
        self.bse_index_append_checkbox.setVisible(bse_index_selected)

        # Update layout to accommodate changes
        self.update()

    def create_progress_tracking(self) -> QGroupBox:
        """Create progress tracking area"""
        group = QGroupBox("Download Progress")
        layout = QGridLayout(group)

        # Set fixed column widths to prevent layout changes
        layout.setColumnMinimumWidth(0, 100)  # Exchange name column
        layout.setColumnMinimumWidth(1, 200)  # Progress bar column
        layout.setColumnMinimumWidth(2, 300)  # Status text column
        layout.setColumnStretch(0, 0)  # Don't stretch exchange column
        layout.setColumnStretch(1, 0)  # Don't stretch progress column
        layout.setColumnStretch(2, 1)  # Allow status column to expand
        
        # Create progress bars and status labels for each exchange
        available_exchanges = self.config.get_available_exchanges()

        for i, exchange in enumerate(available_exchanges):
            # Exchange label with fixed width
            exchange_label = QLabel(exchange.replace('_', ' '))
            exchange_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            exchange_label.setMinimumWidth(100)  # Fixed width to prevent layout changes
            layout.addWidget(exchange_label, i, 0)

            # Progress bar with fixed size
            progress_bar = QProgressBar()
            progress_bar.setVisible(False)  # Hidden initially
            progress_bar.setMinimumWidth(200)  # Fixed width
            progress_bar.setMaximumHeight(20)   # Fixed height
            self.progress_bars[exchange] = progress_bar
            layout.addWidget(progress_bar, i, 1)

            # Status label with fixed width and alignment
            status_label = QLabel("Ready")
            status_label.setStyleSheet("color: gray;")
            status_label.setMinimumWidth(300)  # Fixed width to prevent text jumping
            status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)  # Left align
            self.status_labels[exchange] = status_label
            layout.addWidget(status_label, i, 2)
        
        return group
    
    def create_control_buttons(self) -> QHBoxLayout:
        """Create control buttons"""
        layout = QHBoxLayout()
        
        # Download button
        self.download_button = QPushButton("Start Download")
        self.download_button.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.download_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        self.download_button.clicked.connect(self.start_download)
        layout.addWidget(self.download_button)
        
        # Stop button
        self.stop_button = QPushButton("Stop Download")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_download)
        layout.addWidget(self.stop_button)
        
        # Refresh button
        refresh_button = QPushButton("Refresh Status")
        refresh_button.clicked.connect(self.load_data_summary)
        layout.addWidget(refresh_button)
        
        layout.addStretch()  # Add stretch to push buttons to left
        
        return layout

    def create_status_area(self) -> QGroupBox:
        """Create status display area"""
        group = QGroupBox("Status & Information")
        layout = QVBoxLayout(group)

        # Status text area
        self.status_text = QTextEdit()
        self.status_text.setMinimumHeight(100)  # Minimum height
        # Remove maximum height to allow expansion
        self.status_text.setReadOnly(True)

        # Set size policy to expand vertically
        self.status_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.status_text.setStyleSheet("""
            QTextEdit {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                font-family: 'Courier New', monospace;
                font-size: 9pt;
            }
        """)
        layout.addWidget(self.status_text)

        return group

    def create_status_bar(self):
        """Create status bar"""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def load_data_summary(self, clear_console: bool = True):
        """Load and display data summary"""
        try:
            summary = self.data_manager.get_data_summary()

            status_text = "Data Summary:\n"
            status_text += "=" * 50 + "\n"

            for exchange, info in summary.items():
                if 'error' in info:
                    status_text += f"{exchange}: ERROR - {info['error']}\n"
                else:
                    last_date = info['last_date'] or 'No data'
                    file_count = info['file_count']
                    is_first = "Yes" if info['is_first_run'] else "No"

                    status_text += f"{exchange}:\n"
                    status_text += f"  Last Date: {last_date}\n"
                    status_text += f"  File Count: {file_count}\n"
                    status_text += f"  First Run: {is_first}\n"
                    status_text += "\n"

            # Only clear console if explicitly requested
            if clear_console:
                self.status_text.setText(status_text)
            else:
                # Append data summary without clearing existing content
                self.append_status_message("\n" + status_text)

            self.status_bar.showMessage("Data summary loaded")

        except Exception as e:
            self.logger.error(f"Error loading data summary: {e}")
            self.status_text.setText(f"Error loading data summary: {e}")

    def get_selected_exchanges(self) -> List[str]:
        """Get list of selected exchanges"""
        selected = []
        for exchange, checkbox in self.exchange_checkboxes.items():
            if checkbox.isChecked():
                selected.append(exchange)
        return selected

    def start_download(self):
        """Start download process"""
        try:
            selected_exchanges = self.get_selected_exchanges()

            if not selected_exchanges:
                QMessageBox.warning(self, "Warning", "Please select at least one exchange to download.")
                return

            # Disable download button and enable stop button
            self.download_button.setEnabled(False)
            self.download_button.setText("Downloading...")
            self.stop_button.setEnabled(True)

            # Show progress bars for selected exchanges with stable layout
            for exchange in selected_exchanges:
                if exchange in self.progress_bars:
                    # Set initial state without causing layout changes
                    progress_bar = self.progress_bars[exchange]
                    progress_bar.setValue(0)
                    progress_bar.setVisible(True)
                    progress_bar.setFormat("%p% - Preparing...")  # Fixed format

                if exchange in self.status_labels:
                    # Use fixed-width text to prevent jumping
                    self.status_labels[exchange].setText("  0% - Preparing...          ")
                    self.status_labels[exchange].setStyleSheet("color: blue;")

            # Get weekend option
            include_weekends = self.weekend_checkbox.isChecked() if self.weekend_checkbox else False

            # Get timeout option
            timeout_seconds = self.timeout_spinbox.value() if self.timeout_spinbox else 5

            # Create and start download worker
            self.download_worker = DownloadWorker(self.config, selected_exchanges, include_weekends, timeout_seconds)

            # Connect signals
            self.download_worker.progress_updated.connect(self.update_progress)
            self.download_worker.status_updated.connect(self.update_status)
            self.download_worker.error_occurred.connect(self.handle_error)
            self.download_worker.download_completed.connect(self.handle_download_completed)
            self.download_worker.all_downloads_completed.connect(self.handle_all_downloads_completed)

            # Start worker thread
            self.download_worker.start()

            self.status_bar.showMessage("Download started...")
            self.append_status_message("Download started for selected exchanges")

        except Exception as e:
            self.logger.error(f"Error starting download: {e}")
            QMessageBox.critical(self, "Error", f"Failed to start download: {e}")
            self.reset_download_ui()

    def stop_download(self):
        """Stop download process gracefully"""
        if self.download_worker and self.download_worker.isRunning():
            try:
                # Set stop flag for graceful shutdown
                self.download_worker.stop_requested = True
                self.append_status_message("Stopping download... Please wait.")

                # Disable stop button to prevent multiple clicks
                self.stop_button.setEnabled(False)
                self.stop_button.setText("Stopping...")

                # Use QTimer to check if worker stopped gracefully
                self.stop_timer = QTimer()
                self.stop_timer.timeout.connect(self.check_worker_stopped)
                self.stop_timer.start(500)  # Check every 500ms

                # Set timeout for forced termination
                self.stop_timeout = QTimer()
                self.stop_timeout.timeout.connect(self.force_stop_worker)
                self.stop_timeout.setSingleShot(True)
                self.stop_timeout.start(5000)  # Force stop after 5 seconds

            except Exception as e:
                self.logger.error(f"Error stopping download: {e}")
                self.force_stop_worker()

    def check_worker_stopped(self):
        """Check if worker stopped gracefully"""
        if not self.download_worker or not self.download_worker.isRunning():
            # Worker stopped gracefully
            self.stop_timer.stop()
            self.stop_timeout.stop()
            self.append_status_message("Download stopped successfully")
            self.reset_download_ui()

    def force_stop_worker(self):
        """Force stop worker if graceful stop failed"""
        try:
            if self.download_worker and self.download_worker.isRunning():
                self.append_status_message("Force stopping download...")
                self.download_worker.terminate()

                # Don't wait() in main thread - use QTimer
                self.force_timer = QTimer()
                self.force_timer.timeout.connect(self.finalize_stop)
                self.force_timer.setSingleShot(True)
                self.force_timer.start(1000)  # Wait 1 second then finalize
            else:
                self.finalize_stop()
        except Exception as e:
            self.logger.error(f"Error force stopping: {e}")
            self.finalize_stop()

    def finalize_stop(self):
        """Finalize stop process"""
        try:
            # Stop all timers
            if hasattr(self, 'stop_timer'):
                self.stop_timer.stop()
            if hasattr(self, 'stop_timeout'):
                self.stop_timeout.stop()
            if hasattr(self, 'force_timer'):
                self.force_timer.stop()

            self.append_status_message("Download stopped")
            self.reset_download_ui()

        except Exception as e:
            self.logger.error(f"Error finalizing stop: {e}")
            self.reset_download_ui()

    def check_for_updates(self):
        """Check for application updates in background"""
        try:
            self.logger.info("Checking for updates...")

            # Create update worker thread
            self.update_worker = UpdateCheckWorker(self.update_checker)
            self.update_worker.update_checked.connect(self.handle_update_result)
            self.update_worker.start()

        except Exception as e:
            self.logger.error(f"Error starting update check: {e}")

    def handle_update_result(self, result: dict):
        """Handle update check result"""
        try:
            if result.get("update_available", False):
                update_info = result.get("update_info")
                if update_info:
                    self.logger.info(f"Update available: {update_info.get('latest_version')}")
                    self.show_update_dialog(update_info)
            else:
                self.logger.info("No updates available")

        except Exception as e:
            self.logger.error(f"Error handling update result: {e}")

    def show_update_dialog(self, update_info: dict):
        """Show update dialog to user"""
        try:
            dialog = UpdateDialog(update_info, self)
            dialog.exec()

        except Exception as e:
            self.logger.error(f"Error showing update dialog: {e}")
            # Fallback to simple message box
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "Update Available",
                f"A new version is available: {update_info.get('latest_version', 'Unknown')}\n"
                f"Please visit GitHub to download the update."
            )

    def on_exchange_selection_changed(self):
        """Handle exchange selection changes"""
        try:
            # Get current selections
            selections = {}
            for exchange, checkbox in self.exchange_checkboxes.items():
                selections[exchange] = checkbox.isChecked()

            # Save to user preferences
            self.user_prefs.set_exchange_selection(selections)

            # Update dynamic options
            self.update_dynamic_options()

        except Exception as e:
            self.logger.error(f"Error handling exchange selection change: {e}")

    def on_weekend_option_changed(self):
        """Handle weekend option change"""
        try:
            include_weekends = self.weekend_checkbox.isChecked()
            self.user_prefs.set_include_weekends(include_weekends)
            self.logger.debug(f"Weekend option changed: {include_weekends}")
        except Exception as e:
            self.logger.error(f"Error handling weekend option change: {e}")

    def on_timeout_changed(self):
        """Handle timeout option change"""
        try:
            timeout = self.timeout_spinbox.value()
            self.user_prefs.set_timeout_seconds(timeout)
            self.logger.debug(f"Timeout changed: {timeout}")
        except Exception as e:
            self.logger.error(f"Error handling timeout change: {e}")

    def closeEvent(self, event):
        """Handle window close event"""
        try:
            # Check if download is in progress
            if self.download_worker and self.download_worker.isRunning():
                reply = QMessageBox.question(
                    self,
                    "Confirm Exit",
                    "Download is in progress. Are you sure you want to exit?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )

                if reply == QMessageBox.StandardButton.No:
                    event.ignore()
                    return
                else:
                    # User confirmed exit, stop download
                    self.download_worker.terminate()
                    self.download_worker.wait()

            # Save window size to preferences
            size = self.size()
            self.logger.info(f"Current window size before saving: {size.width()}x{size.height()}")
            self.user_prefs.set_window_size(size.width(), size.height())

            # Save current download options
            download_options = {
                "include_weekends": self.weekend_checkbox.isChecked(),
                "timeout_seconds": self.timeout_spinbox.value()
            }
            self.user_prefs.set_download_options(download_options)

            self.logger.info(f"Saved user preferences on exit - Window size: {size.width()}x{size.height()}")

        except Exception as e:
            self.logger.error(f"Error saving preferences on exit: {e}")

        # Accept the close event
        event.accept()

    def update_progress(self, exchange: str, percentage: int, message: str):
        """Update progress for specific exchange with batching"""
        # Add to pending updates for batched processing
        self.pending_updates[exchange] = ("progress", (percentage, message))

    def update_status(self, exchange: str, status: str):
        """Update status for specific exchange with batching"""
        self.download_status[exchange] = status

        # Add to pending updates for batched processing
        self.pending_updates[f"{exchange}_status"] = ("status", status)

        # Still append to status message immediately for logging
        self.append_status_message(f"[{exchange}] {status}")

    def handle_error(self, exchange: str, error: str):
        """Handle error for specific exchange"""
        if exchange in self.status_labels:
            self.status_labels[exchange].setText(f"Error: {error}")
            self.status_labels[exchange].setStyleSheet("color: red;")

        self.append_status_message(f"[{exchange}] ERROR: {error}")

    def handle_download_completed(self, exchange: str, success: bool):
        """Handle completion of download for specific exchange"""
        if success:
            if exchange in self.status_labels:
                self.status_labels[exchange].setText("Completed")
                self.status_labels[exchange].setStyleSheet("color: green;")
            if exchange in self.progress_bars:
                self.progress_bars[exchange].setValue(100)

            self.append_status_message(f"[{exchange}] Download completed successfully")
        else:
            if exchange in self.status_labels:
                self.status_labels[exchange].setText("Failed")
                self.status_labels[exchange].setStyleSheet("color: red;")

            self.append_status_message(f"[{exchange}] Download failed")

    def handle_all_downloads_completed(self, overall_success: bool):
        """Handle completion of all downloads"""
        self.reset_download_ui()

        if overall_success:
            self.status_bar.showMessage("All downloads completed successfully")
            self.append_status_message("All downloads completed successfully")
            QMessageBox.information(self, "Success", "All downloads completed successfully!")
        else:
            self.status_bar.showMessage("Downloads completed with errors")
            self.append_status_message("Downloads completed with errors")
            QMessageBox.warning(self, "Warning", "Some downloads failed. Check the status for details.")

        # Refresh data summary without clearing console
        self.load_data_summary(clear_console=False)

    def reset_download_ui(self):
        """Reset download UI to initial state"""
        self.download_button.setEnabled(True)
        self.download_button.setText("Start Download")
        self.stop_button.setEnabled(False)
        self.stop_button.setText("Stop Download")

        # Reset progress bars and status labels to initial state
        for exchange, progress_bar in self.progress_bars.items():
            progress_bar.setVisible(False)
            progress_bar.setValue(0)

        for exchange, status_label in self.status_labels.items():
            status_label.setText("Ready")
            status_label.setStyleSheet("color: gray;")

        # Clear update throttling and pending updates
        self.last_update_time.clear()
        self.pending_updates.clear()

    def process_pending_updates(self):
        """Process batched updates to reduce flickering"""
        if not self.pending_updates:
            return

        # Process all pending updates at once
        for exchange, (update_type, data) in self.pending_updates.items():
            if update_type == "progress":
                percentage, message = data
                self._update_progress_immediate(exchange, percentage, message)
            elif update_type == "status":
                status = data
                self._update_status_immediate(exchange, status)

        # Clear processed updates
        self.pending_updates.clear()

    def _update_progress_immediate(self, exchange: str, percentage: int, message: str):
        """Immediate progress update without throttling"""
        if exchange in self.progress_bars:
            progress_bar = self.progress_bars[exchange]
            progress_bar.setValue(percentage)
            # Set fixed format to prevent size changes
            progress_bar.setFormat(f"%p% - {message[:20]:<20}")  # Truncate and pad message

        if exchange in self.status_labels:
            # Use fixed-width formatting to prevent text jumping
            truncated_message = message[:30] if len(message) > 30 else message
            status_text = f"{percentage:3d}% - {truncated_message:<30}"
            self.status_labels[exchange].setText(status_text)

    def _update_status_immediate(self, exchange: str, status: str):
        """Immediate status update without throttling"""
        if exchange in self.status_labels:
            # Use fixed width to prevent layout changes
            truncated_status = status[:40] if len(status) > 40 else status
            padded_status = f"{truncated_status:<40}"  # Left-align with padding
            self.status_labels[exchange].setText(padded_status)
            self.status_labels[exchange].setStyleSheet("color: blue;")

    def append_status_message(self, message: str):
        """Append message to status text area"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"

        self.status_text.append(formatted_message)

        # Auto-scroll to bottom
        cursor = self.status_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.status_text.setTextCursor(cursor)

    def update_status_display(self):
        """Update status display periodically"""
        # This can be used for periodic updates if needed
        pass

    def show_about(self):
        """Show about dialog"""
        about_text = """
        NSE/BSE Data Downloader v1.0.0

        A comprehensive data downloader for NSE and BSE market data.

        Features:
        • Concurrent downloads for faster processing
        • Memory optimization for large datasets
        • Smart date management
        • Real-time progress tracking

        Developed with PyQt6 and modern Python architecture.
        """

        QMessageBox.about(self, "About NSE/BSE Data Downloader", about_text)
