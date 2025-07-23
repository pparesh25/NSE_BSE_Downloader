"""
Update Dialog

GUI dialog for showing available updates and handling user choices.
"""

import logging
from typing import Dict, Optional
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QScrollArea, QWidget, QProgressBar, QTextEdit,
    QMessageBox, QApplication, QFileDialog, QLineEdit
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QPixmap, QIcon

from ..utils.update_checker import UpdateChecker


class UpdateDownloadWorker(QThread):
    """Worker thread for downloading updates"""

    progress_updated = pyqtSignal(str)  # Progress message
    download_completed = pyqtSignal(bool, str)  # Success, message/path

    def __init__(self, update_checker: UpdateChecker, download_location: Path = None):
        super().__init__()
        self.update_checker = update_checker
        self.download_location = download_location
        self.logger = logging.getLogger(__name__)
    
    def run(self):
        """Download and extract update"""
        try:
            # Create download location if specified
            if self.download_location:
                self.download_location.mkdir(parents=True, exist_ok=True)
                download_path = self.download_location / "update.zip"
            else:
                download_path = None

            # Download update
            self.progress_updated.emit("📥 Downloading update...")
            success, result = self.update_checker.download_update(download_path)

            if not success:
                self.download_completed.emit(False, result)
                return

            download_path = Path(result)

            # Extract update to same location
            if self.download_location:
                extract_to = self.download_location / "extracted"
            else:
                extract_to = None

            self.progress_updated.emit("📦 Extracting update...")
            success, result = self.update_checker.extract_update(download_path, extract_to)

            if success:
                self.progress_updated.emit("✅ Update ready!")
                self.download_completed.emit(True, result)
            else:
                self.download_completed.emit(False, result)
                
        except Exception as e:
            self.logger.error(f"Error in update download worker: {e}")
            self.download_completed.emit(False, str(e))


class UpdateDialog(QDialog):
    """
    Dialog for showing available updates and handling user actions
    """
    
    def __init__(self, update_info: Dict, parent=None):
        super().__init__(parent)
        self.update_info = update_info
        self.update_checker = UpdateChecker()
        self.download_worker = None
        self.logger = logging.getLogger(__name__)
        
        self.setup_ui()
        self.setModal(True)
    
    def setup_ui(self):
        """Setup the user interface"""
        self.setWindowTitle("🚀 Update Available")
        self.setFixedSize(600, 500)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        
        # Main layout
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # Header section
        self.create_header_section(layout)
        
        # Content area with scroll
        scroll_area = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        
        # Update information
        self.create_update_info_section(scroll_layout)
        
        # Features section
        self.create_features_section(scroll_layout)
        
        # Bug fixes section
        self.create_bug_fixes_section(scroll_layout)
        
        scroll_area.setWidget(scroll_widget)
        scroll_area.setWidgetResizable(True)
        layout.addWidget(scroll_area)
        
        # Download location section
        self.create_location_section(layout)

        # Progress section (initially hidden)
        self.create_progress_section(layout)

        # Buttons section
        self.create_buttons_section(layout)
    
    def create_header_section(self, layout: QVBoxLayout):
        """Create header with title and version info"""
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        
        # Title
        title = QLabel(f"🎉 Update Available!")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #2196F3; margin: 10px 0;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(title)
        
        # Version info
        current_version = self.update_checker.get_current_version()
        latest_version = self.update_info.get("latest_version", "Unknown")
        
        version_info = QLabel(f"Current: v{current_version} → Latest: v{latest_version}")
        version_info.setStyleSheet("color: #666; font-size: 14px; margin-bottom: 10px;")
        version_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(version_info)
        
        # Update message
        update_message = self.update_info.get("update_message", "New features and improvements available!")
        message_label = QLabel(update_message)
        message_label.setWordWrap(True)
        message_label.setStyleSheet("color: #333; font-size: 12px; padding: 10px; background: #f0f8ff; border-radius: 5px;")
        header_layout.addWidget(message_label)
        
        layout.addWidget(header_widget)
    
    def create_update_info_section(self, layout: QVBoxLayout):
        """Create update information section"""
        changelog = self.update_info.get("changelog", {})
        release_date = changelog.get("release_date", "Unknown")
        
        info_group = QGroupBox(f"📋 Release Information")
        info_layout = QVBoxLayout(info_group)
        
        info_text = f"Release Date: {release_date}\n"
        info_text += f"Version: {changelog.get('version', 'Unknown')}"
        
        info_label = QLabel(info_text)
        info_label.setStyleSheet("color: #555; padding: 5px;")
        info_layout.addWidget(info_label)
        
        layout.addWidget(info_group)
    
    def create_features_section(self, layout: QVBoxLayout):
        """Create new features section"""
        changelog = self.update_info.get("changelog", {})
        features = changelog.get("features", [])
        
        if features:
            features_group = QGroupBox("🆕 New Features")
            features_layout = QVBoxLayout(features_group)
            
            for feature in features:
                feature_label = QLabel(f"• {feature}")
                feature_label.setWordWrap(True)
                feature_label.setStyleSheet("color: #2e7d32; padding: 2px 0; margin-left: 10px;")
                features_layout.addWidget(feature_label)
            
            layout.addWidget(features_group)
    
    def create_bug_fixes_section(self, layout: QVBoxLayout):
        """Create bug fixes section"""
        changelog = self.update_info.get("changelog", {})
        bug_fixes = changelog.get("bug_fixes", [])
        
        if bug_fixes:
            bugs_group = QGroupBox("🐛 Bug Fixes")
            bugs_layout = QVBoxLayout(bugs_group)
            
            for fix in bug_fixes:
                fix_label = QLabel(f"• {fix}")
                fix_label.setWordWrap(True)
                fix_label.setStyleSheet("color: #d32f2f; padding: 2px 0; margin-left: 10px;")
                bugs_layout.addWidget(fix_label)
            
            layout.addWidget(bugs_group)

    def create_location_section(self, layout: QVBoxLayout):
        """Create download location selection section"""
        location_group = QGroupBox("📁 Download Location")
        location_layout = QVBoxLayout(location_group)

        # Description
        desc_label = QLabel("Select where to download and extract the update:")
        desc_label.setStyleSheet("color: #666; font-size: 12px; margin-bottom: 5px;")
        location_layout.addWidget(desc_label)

        # Location input and browse button
        location_widget = QWidget()
        location_widget_layout = QHBoxLayout(location_widget)
        location_widget_layout.setContentsMargins(0, 0, 0, 0)

        # Default location (from user preferences or Downloads folder)
        try:
            from ..utils.user_preferences import UserPreferences
            user_prefs = UserPreferences()
            default_location = user_prefs.get_last_download_location()
        except:
            default_location = str(Path.home() / "Downloads" / "NSE_BSE_Update")

        self.location_input = QLineEdit(default_location)
        self.location_input.setStyleSheet("padding: 8px; border: 1px solid #ccc; border-radius: 4px;")
        location_widget_layout.addWidget(self.location_input)

        browse_btn = QPushButton("📂 Browse")
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                border: 1px solid #ccc;
                padding: 8px 15px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
        """)
        browse_btn.clicked.connect(self.browse_location)
        location_widget_layout.addWidget(browse_btn)

        location_layout.addWidget(location_widget)
        layout.addWidget(location_group)

    def browse_location(self):
        """Open file dialog to select download location"""
        try:
            current_path = self.location_input.text()
            if not current_path:
                current_path = str(Path.home() / "Downloads")

            # Open directory selection dialog
            selected_dir = QFileDialog.getExistingDirectory(
                self,
                "Select Download Location",
                current_path,
                QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks
            )

            if selected_dir:
                # Add subfolder for update
                update_path = Path(selected_dir) / "NSE_BSE_Update"
                self.location_input.setText(str(update_path))

        except Exception as e:
            self.logger.error(f"Error in browse location: {e}")
            QMessageBox.warning(self, "Error", f"Could not open location dialog: {e}")

    def create_progress_section(self, layout: QVBoxLayout):
        """Create progress section for download"""
        self.progress_widget = QWidget()
        progress_layout = QVBoxLayout(self.progress_widget)
        
        self.progress_label = QLabel("Preparing download...")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress_layout.addWidget(self.progress_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate progress
        progress_layout.addWidget(self.progress_bar)
        
        self.progress_widget.setVisible(False)
        layout.addWidget(self.progress_widget)
    
    def create_buttons_section(self, layout: QVBoxLayout):
        """Create buttons section"""
        button_widget = QWidget()
        button_layout = QHBoxLayout(button_widget)
        
        # Download button
        self.download_btn = QPushButton("📥 Download & Install Update")
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """)
        self.download_btn.clicked.connect(self.download_update)
        button_layout.addWidget(self.download_btn)
        
        # Remind later button
        remind_btn = QPushButton("⏰ Remind Me Later")
        remind_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                padding: 10px 20px;
                font-size: 14px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """)
        remind_btn.clicked.connect(self.remind_later)
        button_layout.addWidget(remind_btn)
        
        # Skip button
        skip_btn = QPushButton("❌ Skip This Version")
        skip_btn.setStyleSheet("""
            QPushButton {
                background-color: #757575;
                color: white;
                border: none;
                padding: 10px 20px;
                font-size: 14px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #616161;
            }
        """)
        skip_btn.clicked.connect(self.skip_version)
        button_layout.addWidget(skip_btn)
        
        layout.addWidget(button_widget)
    
    def download_update(self):
        """Start update download process"""
        try:
            # Get selected location
            location_text = self.location_input.text().strip()
            if not location_text:
                QMessageBox.warning(self, "Error", "Please select a download location.")
                return

            download_location = Path(location_text)

            # Show progress
            self.progress_widget.setVisible(True)
            self.download_btn.setEnabled(False)

            # Start download worker with selected location
            self.download_worker = UpdateDownloadWorker(self.update_checker, download_location)
            self.download_worker.progress_updated.connect(self.update_progress)
            self.download_worker.download_completed.connect(self.download_finished)
            self.download_worker.start()
            
        except Exception as e:
            self.logger.error(f"Error starting download: {e}")
            QMessageBox.critical(self, "Error", f"Failed to start download: {e}")
    
    def update_progress(self, message: str):
        """Update progress display"""
        self.progress_label.setText(message)
    
    def download_finished(self, success: bool, result: str):
        """Handle download completion"""
        self.progress_widget.setVisible(False)
        self.download_btn.setEnabled(True)
        
        if success:
            # Save download location to preferences
            try:
                from ..utils.user_preferences import UserPreferences
                user_prefs = UserPreferences()
                location = self.location_input.text().strip()
                if location:
                    user_prefs.set_last_download_location(location)
            except Exception as e:
                self.logger.warning(f"Could not save download location: {e}")

            # Show success message with instructions
            msg = QMessageBox(self)
            msg.setWindowTitle("Update Downloaded")
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setText("✅ Update downloaded successfully!")
            msg.setInformativeText(
                f"Update has been extracted to:\n{result}\n\n"
                "Please close the application and manually replace the files, "
                "or restart the application to apply the update."
            )
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()

            self.accept()  # Close dialog
        else:
            # Show error message
            QMessageBox.critical(self, "Download Failed", f"Failed to download update:\n{result}")
    
    def remind_later(self):
        """Remind user later"""
        self.reject()
    
    def skip_version(self):
        """Skip this version"""
        # TODO: Implement version skipping logic
        self.reject()
