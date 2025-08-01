

# Guide to Create Miniconda Virtual Environment and Run Python Application

This guide outlines the steps to download Miniconda, create a virtual environment, install dependencies, and run a Python application using a Windows batch file for daily execution.

## Step 1: Download Miniconda
1. Download the Miniconda installer for Windows from the following link:
   - [Miniconda3-latest-Windows-x86_64.exe](https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe)
2. Run the installer and follow the on-screen instructions to install Miniconda.

## Step 2: Verify Miniconda Installation
1. Open a Command Prompt.
2. Check the installed Miniconda version by running:
   ```
   conda --version
   ```
   This should display the installed version of Miniconda.

## Step 3: Create a Virtual Environment
1. Create a new virtual environment named `trading` with Python 3.11 by running:
   ```
   conda create -n trading python=3.11
   ```
2. Follow the prompts to confirm the creation of the environment.

## Step 4: Activate the Virtual Environment
1. Activate the `trading` environment by running:
   ```
   conda activate trading
   ```
   The command prompt should now show `(trading)` indicating the environment is active.

## Step 5: Install Required Libraries
1. Ensure you have a `requirements.txt` file with the necessary Python libraries for your application.
2. Install the libraries in the `trading` environment by running:
   ```
   pip install -r requirements.txt
   ```

## Step 6: Run the Python Application
1. Run your Python application (`main.py`) in the activated `trading` environment by executing:
   ```
   python "C:\Users\ppare\OneDrive\Documents\NSE_BSE_Downloader-main\main.py"
   ```

## Step 7: Create a Windows Batch File for Daily Execution
1. Open a text editor (e.g., Notepad, VS Code).
2. Copy and paste the following code into a new file:
   ```
   @echo off
   ECHO Activating Miniconda environment and running Python script...

   :: Activate Miniconda base environment
   call "C:\Users\ppare\miniconda3\Scripts\activate.bat" "C:\Users\ppare\miniconda3"

   :: Activate the trading environment
   call conda activate trading

   :: Run the Python application
   python "C:\Users\ppare\OneDrive\Documents\NSE_BSE_Downloader-main\main.py"

   :: Deactivate the environment
   call conda deactivate

   :: Pause to view output (optional)
   pause
   ```
3. Save the file with a `.bat` extension, e.g., `run_trading_script.bat`, ensuring "Save as type" is set to "All Files".
4. To run the script daily, double-click the `.bat` file or schedule it using Windows Task Scheduler.

## Notes
- Ensure the Miniconda installation path (`C:\Users\ppare\miniconda3`) and the Python script path (`C:\Users\ppare\OneDrive\Documents\NSE_BSE_Downloader-main\main.py`) are correct for your system.
- If you encounter errors, verify that the `trading` environment and `requirements.txt` are properly set up.
- For automation, use Windows Task Scheduler to run the `.bat` file at a specific time each day.

