"""
Donate Dialog for NSE/BSE Data Downloader

Provides a professional donation interface with:
- QR code generation for UPI payments
- Copyable UPI ID
- Thank you message
- Professional styling
"""

import logging
from pathlib import Path
from typing import Optional

try:
    from PyQt6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
        QLineEdit, QFrame, QMessageBox, QApplication
    )
    from PyQt6.QtCore import Qt, pyqtSignal
    from PyQt6.QtGui import QPixmap, QFont, QClipboard
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

# Dynamic QR generation removed - using static QR image only


class DonateDialog(QDialog):
    """Professional donate dialog with QR code and UPI integration"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger(__name__)
        self.setup_ui()
        
    def setup_ui(self):
        """Setup the donate dialog UI"""
        self.setWindowTitle("💝 Support Development")
        # Make dialog resizable and set reasonable size
        self.resize(420, 850)
        self.setMinimumSize(420, 830)
        self.setMaximumSize(450, 900)
        self.setModal(True)

        # Main layout with reduced spacing
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Header section
        self.create_header_section(layout)
        
        # QR Code section
        self.create_qr_section(layout)
        
        # UPI ID section
        self.create_upi_section(layout)
        
        # Thank you section
        self.create_thanks_section(layout)
        
        # Close button
        self.create_close_button(layout)
        
        # Apply styling
        self.apply_styling()

        # Load QR code image after all UI elements are created
        self.load_qr_image()
        
    def create_header_section(self, layout):
        """Create header with title and description"""
        # Title
        title = QLabel("💝 Support NSE/BSE Data Downloader")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Arial", 15, QFont.Weight.Bold))
        title.setStyleSheet("color: #2c3e50; margin-bottom: 8px;")
        layout.addWidget(title)
    def create_qr_section(self, layout):
        """Create QR code section"""
        qr_frame = QFrame()
        qr_frame.setStyleSheet("""
            QFrame {
                background-color: white;
                border: 2px solid #ecf0f1;
                border-radius: 5px;
                padding: 10px;
            }
        """)
        qr_layout = QVBoxLayout(qr_frame)
        
        # QR Code label
        qr_title = QLabel("📱 Scan QR Code to Donate")
        qr_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_title.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        qr_title.setStyleSheet("color: #34495e; margin-bottom: 10px;")
        qr_layout.addWidget(qr_title)
        
        # QR Code image with proper centering
        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(250, 250)
        self.qr_label.setStyleSheet("""
            QLabel {
                border: 1px solid #bdc3c7;
                border-radius: 5px;
                background-color: white;
            }
        """)

        # Placeholder text until QR code is generated
        self.qr_label.setText("Loading QR Code...")
        self.qr_label.setStyleSheet("""
            QLabel {
                border: 1px solid #bdc3c7;
                border-radius: 5px;
                background-color: white;
                color: #666;
                font-size: 12px;
            }
        """)

        # Center the QR label in the layout
        qr_center_layout = QHBoxLayout()
        qr_center_layout.addStretch()
        qr_center_layout.addWidget(self.qr_label)
        qr_center_layout.addStretch()
        qr_layout.addLayout(qr_center_layout)
        
        layout.addWidget(qr_frame)
        
    def create_upi_section(self, layout):
        """Create UPI ID section with copy functionality"""
        upi_frame = QFrame()
        upi_frame.setStyleSheet("""
            QFrame {
                background-color: #f8f9fa;
                border: 1px solid #e9ecef;
                border-radius: 6px;
                padding: 12px;
            }
        """)
        upi_layout = QVBoxLayout(upi_frame)
        
        # UPI label
        upi_title = QLabel("💳 UPI ID:")
        upi_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        upi_title.setStyleSheet("color: #495057; margin-bottom: 8px;")
        upi_layout.addWidget(upi_title)
        
        # UPI ID and copy button
        upi_row = QHBoxLayout()
        
        self.upi_input = QLineEdit("p.paresh25@oksbi")  # Real UPI ID from QR image
        self.upi_input.setReadOnly(True)
        self.upi_input.setFont(QFont("Trebuchet", 14, QFont.Weight.Bold))
        self.upi_input.setStyleSheet("""
            QLineEdit {
                background-color: white;
                border: 2px solid #007bff;
                border-radius: 6px;
                padding: 10px;
                selection-background-color: #007bff;
                color: #007bff;
                font-weight: bold;
            }
        """)
        
        copy_btn = QPushButton("📋 Copy")
        copy_btn.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                min-width: 90px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
            QPushButton:pressed {
                background-color: #1e7e34;
            }
        """)
        copy_btn.clicked.connect(self.copy_upi_id)
        
        upi_row.addWidget(self.upi_input)
        upi_row.addWidget(copy_btn)
        upi_layout.addLayout(upi_row)
        
        layout.addWidget(upi_frame)
        
    def create_thanks_section(self, layout):
        """Create thank you message section"""
        thanks_frame = QFrame()
        thanks_frame.setStyleSheet("""
            QFrame {
                background-color: #e8f5e8;
                border: 1px solid #c3e6c3;
                border-radius: 6px;
                padding: 12px;
            }
        """)
        thanks_layout = QVBoxLayout(thanks_frame)
        
        thanks_msg = QLabel("🙏 Thank you for supporting open source development!")
        thanks_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thanks_msg.setFont(QFont("Arial", 12))
        thanks_msg.setStyleSheet("color: #155724;")
        thanks_msg.setWordWrap(True)
        thanks_layout.addWidget(thanks_msg)
        
        support_msg = QLabel("Your contribution helps keep this project free for everyone.")
        support_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        support_msg.setFont(QFont("Arial", 11))
        support_msg.setStyleSheet("color: #155724; margin-top: 5px;")
        support_msg.setWordWrap(True)
        thanks_layout.addWidget(support_msg)
        
        layout.addWidget(thanks_frame)
        
    def create_close_button(self, layout):
        """Create close button"""
        close_btn = QPushButton("Close")
        close_btn.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 24px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #545b62;
            }
            QPushButton:pressed {
                background-color: #3d4449;
            }
        """)
        close_btn.clicked.connect(self.accept)
        
        # Center the button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(close_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
    def load_qr_image(self):
        """Load static QR code image for UPI payment"""
        try:
            # Load the QR image from resources folder
            qr_image_path = Path(__file__).parent / "resources" / "QR_UPI.jpeg"

            if qr_image_path.exists():
                # Load the QR image
                pixmap = QPixmap(str(qr_image_path))

                if not pixmap.isNull():
                    # Scale the image to fit the label while maintaining aspect ratio
                    scaled_pixmap = pixmap.scaled(
                        240, 240,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )

                    # Set the pixmap with proper styling
                    self.qr_label.setStyleSheet("""
                        QLabel {
                            border: 1px solid #bdc3c7;
                            border-radius: 5px;
                            background-color: white;
                        }
                    """)
                    self.qr_label.setPixmap(scaled_pixmap)
                    self.qr_label.setScaledContents(False)

                    self.logger.info("QR image loaded successfully")
                    return
                else:
                    raise Exception("Failed to load QR image file")
            else:
                raise Exception("QR image file not found")

        except Exception as e:
            # Show error message if QR image cannot be loaded
            error_msg = f"QR Code not available\n{str(e)}"
            self.logger.error(f"Error loading QR code: {e}")
            self.qr_label.setText(error_msg)
            self.qr_label.setStyleSheet("""
                QLabel {
                    color: #dc3545;
                    text-align: center;
                    font-size: 11px;
                    border: 1px solid #dc3545;
                    border-radius: 5px;
                    background-color: #f8d7da;
                    padding: 20px;
                }
            """)
            
    def copy_upi_id(self):
        """Copy UPI ID to clipboard"""
        try:
            clipboard = QApplication.clipboard()
            clipboard.setText(self.upi_input.text())
            
            # Show confirmation
            QMessageBox.information(
                self,
                "Copied!",
                f"UPI ID copied to clipboard:\n{self.upi_input.text()}",
                QMessageBox.StandardButton.Ok
            )
            self.logger.info("UPI ID copied to clipboard")
            
        except Exception as e:
            self.logger.error(f"Error copying UPI ID: {e}")
            QMessageBox.warning(
                self,
                "Error",
                f"Failed to copy UPI ID: {str(e)}",
                QMessageBox.StandardButton.Ok
            )
            
    def apply_styling(self):
        """Apply overall dialog styling"""
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
                border-radius: 10px;
            }
        """)
