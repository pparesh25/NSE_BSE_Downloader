"""
Data Quality and Validation System

Essential features for production-ready stock market data management:
- Data completeness validation
- File integrity checking  
- Missing files detection
- Quality reporting
- Automated gap recovery
"""

import os
import csv
import json
import hashlib
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Dict, List, Set, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from enum import Enum

try:
    from colorama import Fore, Style
except ImportError:
    class Fore:
        RED = '\033[91m'
        GREEN = '\033[92m'
        YELLOW = '\033[93m'
        BLUE = '\033[94m'
        CYAN = '\033[96m'
        WHITE = '\033[97m'
        RESET = '\033[0m'
    
    class Style:
        BRIGHT = '\033[1m'
        RESET_ALL = '\033[0m'


class FileStatus(Enum):
    """File status enumeration"""
    PRESENT = "present"
    MISSING = "missing"
    CORRUPTED = "corrupted"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


class QualityLevel(Enum):
    """Data quality levels"""
    EXCELLENT = "excellent"  # 98%+
    GOOD = "good"           # 95-98%
    FAIR = "fair"           # 90-95%
    POOR = "poor"           # <90%


@dataclass
class FileInfo:
    """Information about a data file"""
    exchange: str
    date: date
    expected_path: Path
    actual_path: Optional[Path] = None
    status: FileStatus = FileStatus.MISSING
    size_bytes: int = 0
    expected_size_range: Tuple[int, int] = (0, 0)
    checksum: Optional[str] = None
    last_modified: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class QualityReport:
    """Data quality report"""
    exchange: str
    period_start: date
    period_end: date
    total_expected: int
    total_present: int
    total_missing: int
    total_corrupted: int
    completeness_rate: float
    quality_level: QualityLevel
    missing_dates: List[date]
    corrupted_files: List[FileInfo]
    recommendations: List[str]
    generated_at: datetime


class DataQualityValidator:
    """Core data quality validation system"""
    
    def __init__(self, base_data_path: Path):
        self.base_data_path = Path(base_data_path)
        self.exchange_configs = self._load_exchange_configs()
        
        # Expected file size ranges (in bytes) - based on historical data
        self.size_ranges = {
            "NSE_EQ": (50000, 500000),    # 50KB - 500KB
            "NSE_FO": (100000, 1000000),  # 100KB - 1MB
            "NSE_SME": (5000, 50000),     # 5KB - 50KB
            "NSE_INDEX": (10000, 100000), # 10KB - 100KB
            "BSE_EQ": (80000, 800000),    # 80KB - 800KB
            "BSE_INDEX": (5000, 50000),   # 5KB - 50KB
        }
    
    def _load_exchange_configs(self) -> Dict[str, Any]:
        """Load exchange configuration for file patterns"""
        # Simplified config - in real implementation, load from config file
        return {
            "NSE_EQ": {
                "file_pattern": "*NSE-EQ*",  # Match our actual file format: YYYY-MM-DD-NSE-EQ.txt
                "date_format": "%Y-%m-%d",   # Our actual date format
                "required_headers": ["SYMBOL", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
            },
            "NSE_FO": {
                "file_pattern": "*NSE-FO*",
                "date_format": "%Y-%m-%d",
                "required_headers": ["SYMBOL", "EXPIRY_DT", "STRIKE_PR", "OPTION_TYP"]
            },
            "NSE_SME": {
                "file_pattern": "*NSE-SME*",
                "date_format": "%Y-%m-%d",
                "required_headers": ["SYMBOL", "OPEN", "HIGH", "LOW", "CLOSE"]
            },
            "BSE_EQ": {
                "file_pattern": "*BSE-EQ*",
                "date_format": "%Y-%m-%d",
                "required_headers": ["SC_CODE", "SC_NAME", "OPEN", "HIGH", "LOW", "CLOSE"]
            },
            "BSE_INDEX": {
                "file_pattern": "*BSE-INDEX*",
                "date_format": "%Y-%m-%d",
                "required_headers": ["Index Name", "Index Value"]
            },
            "NSE_INDEX": {
                "file_pattern": "*NSE-INDEX*",
                "date_format": "%Y-%m-%d",
                "required_headers": ["Index Name", "Index Value"]
            }
        }
    
    def get_trading_days(self, start_date: date, end_date: date, 
                        include_weekends: bool = False) -> List[date]:
        """Get list of expected trading days"""
        trading_days = []
        current_date = start_date
        
        # Indian market holidays (simplified - should be loaded from config)
        holidays = {
            date(2025, 1, 26),  # Republic Day
            date(2025, 3, 14),  # Holi
            date(2025, 8, 15),  # Independence Day
            date(2025, 10, 2),  # Gandhi Jayanti
            # Add more holidays as needed
        }
        
        while current_date <= end_date:
            # Skip weekends unless explicitly included
            if not include_weekends and current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue
            
            # Skip holidays
            if current_date not in holidays:
                trading_days.append(current_date)
            
            current_date += timedelta(days=1)
        
        return trading_days
    
    def check_file_exists(self, exchange: str, target_date: date) -> FileInfo:
        """Check if file exists for given exchange and date"""
        # Convert exchange format (NSE_EQ -> NSE/EQ)
        if '_' in exchange:
            exchange_parts = exchange.split('_', 1)
            exchange_path = self.base_data_path / exchange_parts[0] / exchange_parts[1]
        else:
            exchange_path = self.base_data_path / exchange
        file_info = FileInfo(
            exchange=exchange,
            date=target_date,
            expected_path=exchange_path,
            expected_size_range=self.size_ranges.get(exchange, (0, 0))
        )
        
        if not exchange_path.exists():
            file_info.error_message = f"Exchange directory not found: {exchange_path}"
            return file_info
        
        # Look for files matching the date pattern
        config = self.exchange_configs.get(exchange, {})
        file_pattern = config.get("file_pattern", "*")
        date_format = config.get("date_format", "%Y%m%d")
        
        # Generate possible date strings (prioritize our actual format)
        date_strings = [
            target_date.strftime("%Y-%m-%d"),  # Our primary format: 2025-07-23
            target_date.strftime(date_format),  # Config specified format
            target_date.strftime("%Y%m%d"),     # Compact: 20250723
            target_date.strftime("%d%m%y"),     # DD/MM/YY: 230725
            target_date.strftime("%d%m%Y")      # DD/MM/YYYY: 23072025
        ]
        
        # Search for matching files
        for date_str in date_strings:
            # Create pattern - combine date and exchange pattern
            # Our files are in format: YYYY-MM-DD-EXCHANGE-SEGMENT.txt
            # So we need pattern: *date_str*exchange_pattern*
            if file_pattern == "*":
                pattern_with_date = f"*{date_str}*"
            elif "*" in file_pattern:
                # Replace first * with *date_str* to get: *date_str*NSE-EQ*
                pattern_with_date = file_pattern.replace("*", f"*{date_str}*", 1)
            else:
                # No wildcards in pattern, just add date
                pattern_with_date = f"*{date_str}*{file_pattern}*"

            matching_files = list(exchange_path.glob(pattern_with_date))
            
            if matching_files:
                # Use the first matching file
                actual_file = matching_files[0]
                file_info.actual_path = actual_file
                file_info.status = FileStatus.PRESENT
                
                # Get file stats
                try:
                    stat = actual_file.stat()
                    file_info.size_bytes = stat.st_size
                    file_info.last_modified = datetime.fromtimestamp(stat.st_mtime)
                    
                    # Validate file size
                    min_size, max_size = file_info.expected_size_range
                    if min_size > 0 and (file_info.size_bytes < min_size or file_info.size_bytes > max_size):
                        file_info.status = FileStatus.INVALID
                        file_info.error_message = f"File size {file_info.size_bytes} outside expected range {min_size}-{max_size}"
                    
                    # Check if file is empty
                    if file_info.size_bytes == 0:
                        file_info.status = FileStatus.CORRUPTED
                        file_info.error_message = "File is empty"
                    
                except Exception as e:
                    file_info.status = FileStatus.CORRUPTED
                    file_info.error_message = f"Error reading file: {e}"
                
                return file_info
        
        # No matching file found
        file_info.status = FileStatus.MISSING
        return file_info
    
    def validate_file_content(self, file_info: FileInfo) -> FileInfo:
        """Validate file content and structure"""
        if file_info.status != FileStatus.PRESENT or not file_info.actual_path:
            return file_info
        
        try:
            config = self.exchange_configs.get(file_info.exchange, {})
            required_headers = config.get("required_headers", [])
            
            # Handle compressed files
            file_path = file_info.actual_path
            if file_path.suffix.lower() == '.zip':
                import zipfile
                with zipfile.ZipFile(file_path, 'r') as zip_file:
                    csv_files = [f for f in zip_file.namelist() if f.endswith('.csv')]
                    if not csv_files:
                        file_info.status = FileStatus.INVALID
                        file_info.error_message = "No CSV file found in ZIP"
                        return file_info
                    
                    # Read first CSV file
                    with zip_file.open(csv_files[0]) as csv_file:
                        content = csv_file.read().decode('utf-8')
                        reader = csv.reader(content.splitlines())
                        headers = next(reader, [])
            else:
                # Read CSV file directly
                with open(file_path, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    headers = next(reader, [])
            
            # Validate headers
            if required_headers:
                missing_headers = [h for h in required_headers if h not in headers]
                if missing_headers:
                    file_info.status = FileStatus.INVALID
                    file_info.error_message = f"Missing required headers: {missing_headers}"
                    return file_info
            
            # Check if file has data rows
            if len(headers) == 0:
                file_info.status = FileStatus.CORRUPTED
                file_info.error_message = "File has no headers"
                return file_info
            
            # Generate checksum for integrity
            file_info.checksum = self._calculate_checksum(file_path)
            
        except Exception as e:
            file_info.status = FileStatus.CORRUPTED
            file_info.error_message = f"Content validation error: {e}"
        
        return file_info
    
    def _calculate_checksum(self, file_path: Path) -> str:
        """Calculate MD5 checksum of file"""
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception:
            return ""
    
    def generate_completeness_report(self, exchanges: List[str], 
                                   start_date: date, end_date: date) -> List[QualityReport]:
        """Generate comprehensive data completeness report"""
        reports = []
        
        for exchange in exchanges:
            print(f"{Fore.CYAN}📊 Analyzing {exchange} data quality...{Style.RESET_ALL}")
            
            # Get expected trading days
            trading_days = self.get_trading_days(start_date, end_date)
            total_expected = len(trading_days)
            
            # Check each file
            file_infos = []
            for trading_day in trading_days:
                file_info = self.check_file_exists(exchange, trading_day)
                file_info = self.validate_file_content(file_info)
                file_infos.append(file_info)
            
            # Calculate statistics
            present_files = [f for f in file_infos if f.status == FileStatus.PRESENT]
            missing_files = [f for f in file_infos if f.status == FileStatus.MISSING]
            corrupted_files = [f for f in file_infos if f.status in [FileStatus.CORRUPTED, FileStatus.INVALID]]
            
            total_present = len(present_files)
            total_missing = len(missing_files)
            total_corrupted = len(corrupted_files)
            
            completeness_rate = (total_present / total_expected * 100) if total_expected > 0 else 0
            
            # Determine quality level
            if completeness_rate >= 98:
                quality_level = QualityLevel.EXCELLENT
            elif completeness_rate >= 95:
                quality_level = QualityLevel.GOOD
            elif completeness_rate >= 90:
                quality_level = QualityLevel.FAIR
            else:
                quality_level = QualityLevel.POOR
            
            # Generate recommendations
            recommendations = self._generate_recommendations(
                exchange, completeness_rate, missing_files, corrupted_files
            )
            
            # Create report
            report = QualityReport(
                exchange=exchange,
                period_start=start_date,
                period_end=end_date,
                total_expected=total_expected,
                total_present=total_present,
                total_missing=total_missing,
                total_corrupted=total_corrupted,
                completeness_rate=completeness_rate,
                quality_level=quality_level,
                missing_dates=[f.date for f in missing_files],
                corrupted_files=corrupted_files,
                recommendations=recommendations,
                generated_at=datetime.now()
            )
            
            reports.append(report)
        
        return reports
    
    def _generate_recommendations(self, exchange: str, completeness_rate: float,
                                missing_files: List[FileInfo], 
                                corrupted_files: List[FileInfo]) -> List[str]:
        """Generate actionable recommendations"""
        recommendations = []
        
        if completeness_rate < 95:
            recommendations.append(f"⚠️  {exchange} completeness below 95% - investigate server reliability")
        
        if missing_files:
            recent_missing = [f for f in missing_files if (date.today() - f.date).days <= 7]
            if recent_missing:
                recommendations.append(f"🔄 Re-download {len(recent_missing)} recent missing files")
        
        if corrupted_files:
            recommendations.append(f"🔧 Validate and re-download {len(corrupted_files)} corrupted files")
        
        if completeness_rate >= 98:
            recommendations.append(f"✅ {exchange} data quality is excellent - no action needed")
        
        return recommendations
