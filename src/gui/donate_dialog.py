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

try:
    import qrcode
    from PIL import Image, ImageQt
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False


class DonateDialog(QDialog):
    """Professional donate dialog with QR code and UPI integration"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger(__name__)
        self.setup_ui()
        
    def setup_ui(self):
        """Setup the donate dialog UI"""
        self.setWindowTitle("💝 Support Development")
        self.setFixedSize(450, 600)
        self.setModal(True)
        
        # Main layout
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
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
        
    def create_header_section(self, layout):
        """Create header with title and description"""
        # Title
        title = QLabel("💝 Support NSE/BSE Data Downloader")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #2c3e50; margin-bottom: 10px;")
        layout.addWidget(title)
        
        # Description
        desc = QLabel("Help keep this project free and open source!")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setFont(QFont("Arial", 11))
        desc.setStyleSheet("color: #7f8c8d; margin-bottom: 20px;")
        layout.addWidget(desc)
        
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("color: #bdc3c7;")
        layout.addWidget(separator)
        
    def create_qr_section(self, layout):
        """Create QR code section"""
        qr_frame = QFrame()
        qr_frame.setStyleSheet("""
            QFrame {
                background-color: white;
                border: 2px solid #ecf0f1;
                border-radius: 10px;
                padding: 20px;
            }
        """)
        qr_layout = QVBoxLayout(qr_frame)
        
        # QR Code label
        qr_title = QLabel("📱 Scan QR Code to Donate")
        qr_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        qr_title.setStyleSheet("color: #34495e; margin-bottom: 15px;")
        qr_layout.addWidget(qr_title)
        
        # QR Code image
        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(250, 250)
        self.qr_label.setStyleSheet("""
            QLabel {
                border: 1px solid #bdc3c7;
                border-radius: 5px;
                background-color: white;
            }
        """)
        
        # Generate QR code
        self.generate_qr_code()
        qr_layout.addWidget(self.qr_label)
        
        layout.addWidget(qr_frame)
        
    def create_upi_section(self, layout):
        """Create UPI ID section with copy functionality"""
        upi_frame = QFrame()
        upi_frame.setStyleSheet("""
            QFrame {
                background-color: #f8f9fa;
                border: 1px solid #e9ecef;
                border-radius: 8px;
                padding: 15px;
            }
        """)
        upi_layout = QVBoxLayout(upi_frame)
        
        # UPI label
        upi_title = QLabel("💳 UPI ID:")
        upi_title.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        upi_title.setStyleSheet("color: #495057; margin-bottom: 8px;")
        upi_layout.addWidget(upi_title)
        
        # UPI ID and copy button
        upi_row = QHBoxLayout()
        
        self.upi_input = QLineEdit("developer@paytm")  # Replace with actual UPI ID
        self.upi_input.setReadOnly(True)
        self.upi_input.setFont(QFont("Courier", 11))
        self.upi_input.setStyleSheet("""
            QLineEdit {
                background-color: white;
                border: 1px solid #ced4da;
                border-radius: 4px;
                padding: 8px;
                selection-background-color: #007bff;
            }
        """)
        
        copy_btn = QPushButton("📋 Copy")
        copy_btn.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
            QPushButton:pressed {
                background-color: #004085;
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
                border-radius: 8px;
                padding: 15px;
            }
        """)
        thanks_layout = QVBoxLayout(thanks_frame)
        
        thanks_msg = QLabel("🙏 Thank you for supporting open source development!")
        thanks_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thanks_msg.setFont(QFont("Arial", 11))
        thanks_msg.setStyleSheet("color: #155724;")
        thanks_msg.setWordWrap(True)
        thanks_layout.addWidget(thanks_msg)
        
        support_msg = QLabel("Your contribution helps keep this project free for everyone.")
        support_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        support_msg.setFont(QFont("Arial", 10))
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
        
    def generate_qr_code(self):
        """Generate QR code for UPI payment"""
        try:
            if not QR_AVAILABLE:
                self.qr_label.setText("QR Code generation not available\n(qrcode library not installed)")
                self.qr_label.setStyleSheet("color: #dc3545; text-align: center;")
                return
                
            # UPI payment URL
            upi_id = self.upi_input.text() if hasattr(self, 'upi_input') else "developer@paytm"
            upi_url = f"upi://pay?pa={upi_id}&pn=NSE BSE Data Downloader&cu=INR"
            
            # Generate QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=8,
                border=4,
            )
            qr.add_data(upi_url)
            qr.make(fit=True)
            
            # Create QR code image
            qr_img = qr.make_image(fill_color="black", back_color="white")
            
            # Convert PIL image to QPixmap
            qr_img = qr_img.resize((220, 220), Image.Resampling.LANCZOS)
            qt_img = ImageQt.ImageQt(qr_img)
            pixmap = QPixmap.fromImage(qt_img)
            
            self.qr_label.setPixmap(pixmap)
            self.logger.info("QR code generated successfully")
            
        except Exception as e:
            self.logger.error(f"Error generating QR code: {e}")
            self.qr_label.setText(f"Error generating QR code:\n{str(e)}")
            self.qr_label.setStyleSheet("color: #dc3545; text-align: center;")
            
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
