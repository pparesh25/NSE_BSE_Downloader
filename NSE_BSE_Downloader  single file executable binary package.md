# NSE_BSE_Downloader – Single File Executable User Guide

## Overview

NSE_BSE_Downloader is now available as a single-file executable binary package for both **Linux x86-64** and **Windows x86-64** platforms.  
- **Linux users** can download and use the binary directly without any additional steps.
- **Windows users** may encounter a "false positive" detection from Microsoft Defender due to the nature of the compressed executable. Please follow the steps below to ensure smooth installation and usage.

---

## Download Links

- **Linux x86-64**: [Download NSE_BSE_Downloader for Linux](https://github.com/pparesh25/NSE_BSE_Downloader/releases/download/1.0.0/NSE_BSE_Downloader_Linux_x86-64)  
- **Windows x86-64**: [Download NSE_BSE_Downloader for Windows](https://github.com/pparesh25/NSE_BSE_Downloader/releases/download/1.0.0/NSE_BSE_Downloader_Windows_x86-64.exe)

---

## Important Notice for Windows Users

**Microsoft Defender may flag the Windows binary as a false positive.**  
To use the package safely and without interruption, you must exclude a folder from Microsoft Defender and always run the application from that folder.

### Steps to Safely Use the Windows Binary

1. **Create an Exclusion Folder**
   - Create a new folder on your Desktop or any preferred location (e.g., `C:\Users\<yourname>\Desktop\NSE_BSE_Downloader`).

2. **Exclude the Folder from Microsoft Defender**
   - Open PowerShell as Administrator and run:
     ```powershell
     Add-MpPreference -ExclusionPath "C:\Users\<yourname>\Desktop\NSE_BSE_Downloader"
     ```
   - Replace `<yourname>` with your actual Windows username or use your chosen folder path.

3. **Download the Executable**
   - Right-click the download link for the Windows package and select **"Save link as..."**.
   - Save the file directly into the folder you just excluded from Defender.

4. **Run the Application**
   - Always run the executable from the excluded folder.
   - Double-click the `.exe` file to launch the application.

---

## Additional Notes

- **All Python dependencies are included** in the binary. No separate Python installation is required.
- The executable is **heavily compressed**. On first launch, it may take **7 to 10 seconds** (or more, depending on your machine) for the GUI to appear. This is normal and only affects startup time.
- Once running, the application will perform normally with no performance issues.

---

## Linux Users

- Download the Linux binary and run it directly. No special steps are required.

---

## Troubleshooting

- If you see a warning or block from Microsoft Defender, ensure you have correctly excluded the folder and are running the executable from within that folder.
- For any other issues, please refer to the official documentation or contact support.

---


**Thank you for using NSE_BSE_Downloader!**

