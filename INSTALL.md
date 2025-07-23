# NSE/BSE Data Downloader - Installation Guide

## 🚀 Quick Install (3 Steps)

### Step 1: Setup Environment
```bash
cd NSE_BSE_Data_Downloader
python setup.py
```

### Step 2: Test Installation
```bash
python test_setup.py
```

### Step 3: Run Application
```bash
# GUI Mode (Recommended)
python run.py

# CLI Mode
python run.py --cli
```

## 📋 Detailed Installation

### Prerequisites
- **Python 3.8+** (Check: `python --version`)
- **pip** package manager
- **Internet connection** for downloads

### Manual Installation

#### 1. Install Core Dependencies
```bash
pip install pandas numpy aiohttp pyyaml requests python-dateutil psutil
```

#### 2. Install GUI Dependencies (Optional)
```bash
pip install PyQt6
```

#### 3. Create Data Directories
```bash
mkdir -p ~/NSE_BSE_Data/NSE/{EQ,FO,SME}
mkdir -p ~/NSE_BSE_Data/BSE/EQ
mkdir -p ~/NSE_BSE_Data/temp
```

## 🔧 Troubleshooting

### Common Issues:

#### 1. **Python Version Error**
```
Error: Python 3.8+ required
```
**Solution:** Update Python or use virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate     # Windows
```

#### 2. **PyQt6 Installation Failed**
```
Error: PyQt6 installation failed
```
**Solutions:**
- **Ubuntu/Debian:** `sudo apt-get install python3-pyqt6`
- **CentOS/RHEL:** `sudo yum install python3-qt6`
- **macOS:** `brew install pyqt6`
- **Windows:** Use pip: `pip install PyQt6`

#### 3. **Permission Errors**
```
Error: Permission denied creating directories
```
**Solution:** Check home directory permissions:
```bash
chmod 755 ~
mkdir -p ~/NSE_BSE_Data
```

#### 4. **Import Errors**
```
ModuleNotFoundError: No module named 'src'
```
**Solution:** Run from correct directory:
```bash
cd NSE_BSE_Data_Downloader
python run.py
```

#### 5. **Config File Not Found**
```
Error: Configuration file 'config.yaml' not found
```
**Solution:** Ensure you're in the project directory:
```bash
ls -la config.yaml  # Should exist
pwd                 # Should end with NSE_BSE_Data_Downloader
```

## 🐍 Virtual Environment Setup

### Create Virtual Environment
```bash
python -m venv nse_bse_env
```

### Activate Environment
```bash
# Linux/Mac
source nse_bse_env/bin/activate

# Windows
nse_bse_env\Scripts\activate
```

### Install in Virtual Environment
```bash
cd NSE_BSE_Data_Downloader
python setup.py
```

### Deactivate When Done
```bash
deactivate
```

## 📦 Package Installation

### Using pip (if available)
```bash
pip install nse-bse-downloader
```

### From Source
```bash
git clone <repository-url>
cd NSE_BSE_Data_Downloader
python setup.py
```

## 🔍 Verification

### Test All Components
```bash
python test_setup.py
```

### Expected Output
```
NSE/BSE Data Downloader - Setup Test
==================================================
Testing imports...
✓ All core modules imported successfully
Testing configuration...
✓ Configuration loaded
Testing data manager...
✓ Data summary generated
...
Test Summary:
Passed: 8/8
✓ All tests passed! The system is ready to use.
```

## 🚀 First Run

### GUI Mode
```bash
python run.py
```
- Select exchanges (NSE EQ, BSE EQ, etc.)
- Click "Start Download"
- Monitor progress

### CLI Mode
```bash
python run.py --cli
```
- View data summary
- Check available exchanges

## 📁 Data Location

Downloaded data will be stored in:
```
~/NSE_BSE_Data/
├── NSE/
│   ├── EQ/     # NSE Equity files
│   ├── FO/     # NSE F&O files
│   └── SME/    # NSE SME files
└── BSE/
    └── EQ/     # BSE Equity files
```

## ⚙️ Configuration

Edit `config.yaml` to customize:
- Data storage paths
- Download settings
- Exchange URLs

## 📞 Support

### If Installation Fails:
1. **Check Python version:** `python --version`
2. **Update pip:** `pip install --upgrade pip`
3. **Try virtual environment:** See section above
4. **Check permissions:** Ensure write access to home directory

### For Help:
- Run: `python test_setup.py` for diagnostics
- Check: `config.yaml` for settings
- Verify: Internet connection for downloads

---

**Ready to download market data! 📈**
