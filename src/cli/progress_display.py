"""
Progress Display and Visualization for CLI Mode

Provides rich progress bars, animations, and real-time statistics:
- Multi-line progress bars
- Download speed calculation
- ETA estimation
- Success/failure tracking
- Animated indicators
"""

import time
import sys
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

try:
    import colorama
    from colorama import Fore, Back, Style
    colorama.init()
    COLORS_AVAILABLE = True
except ImportError:
    COLORS_AVAILABLE = False
    # Fallback color codes
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


@dataclass
class ProgressStats:
    """Statistics for progress tracking"""
    total_files: int = 0
    completed_files: int = 0
    failed_files: int = 0
    skipped_files: int = 0
    total_bytes: int = 0
    start_time: Optional[datetime] = None
    current_file: str = ""
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage"""
        if self.total_files == 0:
            return 0.0
        return (self.completed_files / self.total_files) * 100
    
    @property
    def completion_rate(self) -> float:
        """Calculate completion rate percentage"""
        if self.total_files == 0:
            return 0.0
        processed = self.completed_files + self.failed_files + self.skipped_files
        return (processed / self.total_files) * 100
    
    @property
    def elapsed_time(self) -> timedelta:
        """Get elapsed time since start"""
        if not self.start_time:
            return timedelta(0)
        return datetime.now() - self.start_time
    
    @property
    def download_speed(self) -> float:
        """Calculate download speed in MB/s"""
        elapsed = self.elapsed_time.total_seconds()
        if elapsed == 0:
            return 0.0
        return (self.total_bytes / (1024 * 1024)) / elapsed
    
    @property
    def eta(self) -> Optional[timedelta]:
        """Estimate time to completion"""
        if self.completion_rate == 0:
            return None
        
        elapsed = self.elapsed_time.total_seconds()
        if elapsed == 0:
            return None
        
        remaining_rate = 100 - self.completion_rate
        eta_seconds = (elapsed / self.completion_rate) * remaining_rate
        return timedelta(seconds=int(eta_seconds))


class ProgressBar:
    """Individual progress bar for an exchange"""
    
    def __init__(self, name: str, width: int = 40):
        self.name = name
        self.width = width
        self.stats = ProgressStats()
        self.last_update = time.time()
    
    def start(self, total_files: int):
        """Start progress tracking"""
        self.stats.total_files = total_files
        self.stats.start_time = datetime.now()
        self.stats.completed_files = 0
        self.stats.failed_files = 0
        self.stats.skipped_files = 0
        self.stats.total_bytes = 0
    
    def update(self, completed: int = None, failed: int = None, skipped: int = None, 
               bytes_downloaded: int = None, current_file: str = ""):
        """Update progress statistics"""
        if completed is not None:
            self.stats.completed_files = completed
        if failed is not None:
            self.stats.failed_files = failed
        if skipped is not None:
            self.stats.skipped_files = skipped
        if bytes_downloaded is not None:
            self.stats.total_bytes = bytes_downloaded
        if current_file:
            self.stats.current_file = current_file
        
        self.last_update = time.time()
    
    def increment(self, success: bool = True, bytes_downloaded: int = 0, current_file: str = ""):
        """Increment counters"""
        if success:
            self.stats.completed_files += 1
        else:
            self.stats.failed_files += 1
        
        self.stats.total_bytes += bytes_downloaded
        self.stats.current_file = current_file
        self.last_update = time.time()
    
    def render(self) -> str:
        """Render progress bar as string"""
        completion_rate = self.stats.completion_rate
        success_rate = self.stats.success_rate
        
        # Calculate filled portion
        filled_width = int((completion_rate / 100) * self.width)
        success_width = int((success_rate / 100) * self.width)
        
        # Create progress bar
        bar = ""
        for i in range(self.width):
            if i < success_width:
                bar += "█"  # Successful downloads
            elif i < filled_width:
                bar += "▓"  # Failed/skipped downloads
            else:
                bar += "░"  # Remaining
        
        # Choose color based on success rate
        if success_rate >= 95:
            color = Fore.GREEN
        elif success_rate >= 80:
            color = Fore.YELLOW
        else:
            color = Fore.RED
        
        # Format statistics
        processed = self.stats.completed_files + self.stats.failed_files + self.stats.skipped_files
        stats_text = f"{processed}/{self.stats.total_files}"
        
        if self.stats.failed_files > 0 or self.stats.skipped_files > 0:
            stats_text += f" (✓{self.stats.completed_files}"
            if self.stats.failed_files > 0:
                stats_text += f" ✗{self.stats.failed_files}"
            if self.stats.skipped_files > 0:
                stats_text += f" ⊝{self.stats.skipped_files}"
            stats_text += ")"
        
        percentage = f"{completion_rate:5.1f}%"
        
        return f"{self.name:<12} {color}{bar}{Style.RESET_ALL} {percentage} {stats_text}"


class MultiProgressDisplay:
    """Multi-line progress display for multiple exchanges"""
    
    def __init__(self):
        self.progress_bars: Dict[str, ProgressBar] = {}
        self.start_time = datetime.now()
        self.animation_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self.animation_index = 0
        self.last_render = 0
        
    def add_exchange(self, exchange: str, total_files: int):
        """Add an exchange to track"""
        progress_bar = ProgressBar(exchange)
        progress_bar.start(total_files)
        self.progress_bars[exchange] = progress_bar
    
    def update_exchange(self, exchange: str, **kwargs):
        """Update progress for an exchange"""
        if exchange in self.progress_bars:
            self.progress_bars[exchange].update(**kwargs)
    
    def increment_exchange(self, exchange: str, **kwargs):
        """Increment counters for an exchange"""
        if exchange in self.progress_bars:
            self.progress_bars[exchange].increment(**kwargs)
    
    def render(self, show_details: bool = True):
        """Render complete progress display"""
        current_time = time.time()
        
        # Throttle rendering to avoid flickering
        if current_time - self.last_render < 0.1:
            return
        
        self.last_render = current_time
        
        # Clear previous output
        if hasattr(self, '_last_line_count'):
            for _ in range(self._last_line_count):
                sys.stdout.write('\033[F\033[K')  # Move up and clear line
        
        lines = []
        
        # Header
        elapsed = datetime.now() - self.start_time
        elapsed_str = str(elapsed).split('.')[0]  # Remove microseconds
        
        # Animation
        spinner = self.animation_chars[self.animation_index % len(self.animation_chars)]
        self.animation_index += 1
        
        lines.append(f"\n{Fore.CYAN}{Style.BRIGHT}📥 Download Progress {spinner}{Style.RESET_ALL}")
        lines.append(f"{Fore.CYAN}{'─' * 60}{Style.RESET_ALL}")
        
        # Individual progress bars
        total_files = 0
        total_completed = 0
        total_failed = 0
        total_skipped = 0
        total_bytes = 0
        
        for exchange, progress_bar in self.progress_bars.items():
            lines.append(progress_bar.render())
            
            # Aggregate statistics
            total_files += progress_bar.stats.total_files
            total_completed += progress_bar.stats.completed_files
            total_failed += progress_bar.stats.failed_files
            total_skipped += progress_bar.stats.skipped_files
            total_bytes += progress_bar.stats.total_bytes
        
        if show_details and self.progress_bars:
            lines.append(f"{Fore.CYAN}{'─' * 60}{Style.RESET_ALL}")
            
            # Overall statistics
            overall_success_rate = (total_completed / total_files * 100) if total_files > 0 else 0
            overall_completion = ((total_completed + total_failed + total_skipped) / total_files * 100) if total_files > 0 else 0
            
            # Calculate speed
            elapsed_seconds = elapsed.total_seconds()
            speed_mbps = (total_bytes / (1024 * 1024)) / elapsed_seconds if elapsed_seconds > 0 else 0
            
            # ETA calculation
            if overall_completion > 0 and overall_completion < 100:
                eta_seconds = (elapsed_seconds / overall_completion) * (100 - overall_completion)
                eta = str(timedelta(seconds=int(eta_seconds)))
            else:
                eta = "N/A"
            
            lines.append(f"{Fore.WHITE}📊 Overall: {total_completed + total_failed + total_skipped}/{total_files} files ({overall_completion:.1f}%)")
            lines.append(f"✅ Success: {total_completed} ({overall_success_rate:.1f}%) | ❌ Failed: {total_failed} | ⊝ Skipped: {total_skipped}")
            lines.append(f"⏱️  Elapsed: {elapsed_str} | 🚀 Speed: {speed_mbps:.1f} MB/s | ⏳ ETA: {eta}{Style.RESET_ALL}")
        
        # Print all lines
        output = '\n'.join(lines) + '\n'
        sys.stdout.write(output)
        sys.stdout.flush()
        
        self._last_line_count = len(lines) + 1
    
    def finish(self):
        """Finish progress display with summary"""
        self.render(show_details=True)
        
        # Final summary
        total_files = sum(pb.stats.total_files for pb in self.progress_bars.values())
        total_completed = sum(pb.stats.completed_files for pb in self.progress_bars.values())
        total_failed = sum(pb.stats.failed_files for pb in self.progress_bars.values())
        total_skipped = sum(pb.stats.skipped_files for pb in self.progress_bars.values())
        
        elapsed = datetime.now() - self.start_time
        elapsed_str = str(elapsed).split('.')[0]
        
        print(f"\n{Fore.CYAN}{Style.BRIGHT}📋 Download Summary{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'═' * 50}{Style.RESET_ALL}")
        print(f"⏱️  Duration: {elapsed_str}")
        print(f"📁 Total Files: {total_files}")
        print(f"{Fore.GREEN}✅ Success: {total_completed}{Style.RESET_ALL}")
        if total_failed > 0:
            print(f"{Fore.RED}❌ Failed: {total_failed}{Style.RESET_ALL}")
        if total_skipped > 0:
            print(f"{Fore.YELLOW}⊝ Skipped: {total_skipped}{Style.RESET_ALL}")
        
        success_rate = (total_completed / total_files * 100) if total_files > 0 else 0
        if success_rate >= 95:
            print(f"{Fore.GREEN}🎉 Excellent success rate: {success_rate:.1f}%{Style.RESET_ALL}")
        elif success_rate >= 80:
            print(f"{Fore.YELLOW}⚠️  Good success rate: {success_rate:.1f}%{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}⚠️  Low success rate: {success_rate:.1f}% - Check connection{Style.RESET_ALL}")


def create_simple_progress(message: str) -> None:
    """Create a simple progress indicator"""
    animation_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    
    for i in range(20):  # Show for 2 seconds
        char = animation_chars[i % len(animation_chars)]
        sys.stdout.write(f'\r{Fore.CYAN}{char} {message}{Style.RESET_ALL}')
        sys.stdout.flush()
        time.sleep(0.1)
    
    sys.stdout.write(f'\r{Fore.GREEN}✅ {message} - Done{Style.RESET_ALL}\n')
    sys.stdout.flush()
