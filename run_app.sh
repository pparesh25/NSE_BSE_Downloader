#!/bin/bash

# NSE/BSE Data Downloader - Quick Run Script
# This script activates the conda environment and runs the application

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}NSE/BSE Data Downloader${NC}"
echo "=========================="

# Check if conda is available
if ! command -v conda &> /dev/null; then
    echo -e "${RED}Error: Conda not found. Please install Miniconda first.${NC}"
    exit 1
fi

# Check if trading environment exists
if ! conda env list | grep -q "^trading "; then
    echo -e "${RED}Error: 'trading' environment not found.${NC}"
    echo "Please run the setup script first: ./setup_linux.sh"
    exit 1
fi

# Activate environment and run application
echo -e "${GREEN}Activating 'trading' environment...${NC}"
eval "$(conda shell.bash hook)"
conda activate trading

echo -e "${GREEN}Starting NSE/BSE Data Downloader...${NC}"
python main.py

echo -e "${BLUE}Application closed.${NC}"
