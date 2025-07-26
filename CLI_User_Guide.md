# NSE/BSE Data Downloader - CLI User Guide
## Complete Command Line Interface Reference

### Table of Contents
1. [Getting Started](#getting-started)
2. [Navigation Controls](#navigation-controls)
3. [Main Menu Options](#main-menu-options)
4. [Download Options](#download-options)
5. [Advanced Features](#advanced-features)
6. [Data Quality Tools](#data-quality-tools)
7. [Configuration Management](#configuration-management)
8. [Practical Usage Examples](#practical-usage-examples)

---

## Getting Started

### Launching CLI Mode
```bash
python main.py --cli
```

### Welcome Screen
Upon launch, you'll see:
- Application title and version
- Configuration status
- Data directory location
- Main menu

---

## Navigation Controls

### Keyboard Controls
| Key | Action | Description |
|-----|--------|-------------|
| `↑` / `w` | Move Up | Navigate to previous menu item |
| `↓` / `s` | Move Down | Navigate to next menu item |
| `Enter` | Select | Choose current menu item |
| `Space` | Toggle | Toggle selection (multi-select menus) |
| `q` / `Esc` | Back/Quit | Return to previous menu or quit |
| `a` | Select All | Select all items (multi-select) |
| `n` | Select None | Deselect all items (multi-select) |

### Menu Types
- **Single Select**: Choose one option (marked with `>>`)
- **Multi Select**: Choose multiple options (marked with `[ ]` / `[✓]`)

---

## Main Menu Options

### 🏠 Main Menu Structure
```
📥 Download All Exchanges      - Download data for all configured exchanges
🎯 Select Exchanges           - Choose specific exchanges to download
📅 Custom Date Range          - Download with custom date range

Advanced Options:
🔍 Advanced Filtering         - Smart filtering with patterns and missing files
📋 Missing Files Only         - Download only missing files

Data Quality:
📋 Data Quality Report        - Generate comprehensive data quality analysis
🔍 Validate Data Integrity    - Check file integrity and completeness
📊 Gap Analysis              - Identify and analyze missing data

Management:
📊 View Download Status       - Check download statistics and missing files
⚙️  View Configuration       - Display current configuration settings
🔧 Manage Configuration      - Update settings and manage profiles
📜 Download History          - View recent download history

🚪 Exit                      - Exit the application
```

---

## Download Options

### 1. 📥 Download All Exchanges
**Purpose**: Quick download for all configured exchanges
**Sub-options**:
- Date range selection (today, yesterday, last 7 days, etc.)
- Automatic progress tracking
- Success/failure reporting

**Commands Available**:
- Navigate date options with `↑`/`↓`
- Select with `Enter`
- Confirm download with `y`/`n`

### 2. 🎯 Select Exchanges
**Purpose**: Choose specific exchanges to download
**Available Exchanges**:
- `NSE_EQ` - NSE Equity (Main Market)
- `NSE_FO` - NSE Futures & Options
- `NSE_SME` - NSE Small & Medium Enterprises
- `NSE_INDEX` - NSE Indices
- `BSE_EQ` - BSE Equity
- `BSE_INDEX` - BSE Indices

**Multi-Select Commands**:
- `Space` - Toggle individual exchange
- `a` - Select all exchanges
- `n` - Deselect all exchanges
- `Enter` - Proceed with selected exchanges

**Sub-menu Flow**:
1. Exchange selection → 2. Date range → 3. Download confirmation

### 3. 📅 Custom Date Range
**Purpose**: Download with flexible date options
**Date Range Options**:
- `Today only`
- `Yesterday only`
- `Last 7 days`
- `Last 30 days`
- `Current month`
- `Previous month`
- `Missing files only`
- `Custom date range`
- `Advanced pattern` (e.g., last-15-days, this-quarter)

**Advanced Pattern Examples**:
- `last-7-days` - Last 7 trading days
- `this-month` - Current month
- `last-month` - Previous month
- `this-quarter` - Current quarter
- `2025-01` - Specific month
- `2025-01-01:2025-01-31` - Custom range

---

## Advanced Features

### 🔍 Advanced Filtering
**Purpose**: Smart filtering with patterns and missing files
**Features**:
- Exchange pattern matching
- Date range patterns
- Include/exclude weekends
- Missing files only option

**Pattern Examples**:
- `NSE_*` - All NSE exchanges
- `*_EQ` - All equity exchanges
- `BSE_*` - All BSE exchanges

### 📋 Missing Files Only
**Purpose**: Download only missing files
**Process**:
1. Scans existing data directory
2. Identifies missing files for date range
3. Downloads only missing files
4. Reports recovery statistics

**Commands**:
- Select exchanges (multi-select)
- Choose date range
- Confirm missing files download

---

## Data Quality Tools

### 📋 Data Quality Report
**Purpose**: Comprehensive data quality analysis
**Features**:
- File completeness analysis
- Data integrity checks
- Missing data identification
- Quality scoring

**Report Sections**:
- Exchange-wise completeness
- Date-wise gaps
- File size analysis
- Data validation results

**Export Options**:
- CSV (Spreadsheet compatible)
- JSON (API/Script friendly)
- Text (Human readable)

### 🔍 Validate Data Integrity
**Purpose**: Check file integrity and completeness
**Validation Options**:
- `Recent Files` - Last 7 days
- `Custom Range` - Specific date range
- `All Exchanges` - Last 30 days

**Validation Checks**:
- File existence
- File size validation
- Data format verification
- Column completeness

### 📊 Gap Analysis
**Purpose**: Identify and analyze missing data
**Analysis Types**:
- Date gaps in data
- Exchange-specific gaps
- Pattern-based gap detection
- Recovery recommendations

---

## Configuration Management

### ⚙️ View Configuration
**Purpose**: Display current settings
**Information Shown**:
- Data directory path
- Download timeout settings
- Retry configurations
- Exchange configurations

### 🔧 Manage Configuration
**Sub-menu Options**:
```
⚙️  Update Setting           - Modify a configuration setting
✅ Validate Configuration    - Check configuration for issues

Profiles:
📋 List Profiles            - View all download profiles
➕ Create Profile           - Create a new download profile
🎯 Use Profile              - Apply a download profile
🗑️  Delete Profile          - Remove a download profile

Import/Export:
📤 Export Configuration     - Export config and profiles
📥 Import Configuration     - Import config and profiles
```

#### Profile Management
**Create Profile**:
1. Enter profile name
2. Add description
3. Select exchanges (multi-select)
4. Configure timeout/retry settings
5. Set date pattern preferences

**Use Profile**:
- Select from available profiles
- Apply profile settings
- Execute download with profile

---

## Practical Usage Examples

### Example 1: Daily Data Download
```
1. Launch CLI: python main.py --cli
2. Select: 📥 Download All Exchanges
3. Choose: Today only
4. Confirm: y
5. Monitor progress
```

### Example 2: Specific Exchange Download
```
1. Select: 🎯 Select Exchanges
2. Toggle: Space on NSE_EQ and BSE_EQ
3. Select: Enter to proceed
4. Choose: Last 7 days
5. Confirm: y
```

### Example 3: Missing Files Recovery
```
1. Select: 📋 Missing Files Only
2. Choose exchanges: a (select all)
3. Set date range: Last 30 days
4. Confirm: y
5. Review recovery report
```

### Example 4: Data Quality Check
```
1. Select: 📋 Data Quality Report
2. Choose exchanges: Space to select specific ones
3. Set analysis period: Custom range
4. Review quality metrics
5. Export: CSV format
```

### Example 5: Advanced Pattern Download
```
1. Select: 🔍 Advanced Filtering
2. Enter pattern: NSE_*
3. Date pattern: last-15-days
4. Include weekends: n
5. Missing only: y
6. Confirm: y
```

---

## Tips and Best Practices

### Navigation Tips
- Use `w`/`s` if arrow keys don't work
- Multiple key presses (e.g., "sss") are treated as single key
- `q` always goes back to previous menu
- `Ctrl+C` exits application immediately

### Download Tips
- Start with "Missing Files Only" for existing data
- Use profiles for repeated download patterns
- Monitor progress bars for large downloads
- Check data quality after downloads

### Troubleshooting
- If menu hangs, try `Ctrl+C` and restart
- Use "Validate Configuration" if errors occur
- Check data directory permissions
- Review download history for patterns

---

*This guide covers all CLI functionality. For GUI interface, use `python main.py` without `--cli` flag.*
