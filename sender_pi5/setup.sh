#!/bin/bash

echo "========================================="
echo "  Camera Sender Setup Script for RPi"
echo "========================================="

# Update package lists
echo "[1/4] Updating package lists..."
sudo apt update

# Install system dependencies (Picamera2 and OpenCV requirements)
echo "[2/4] Installing system dependencies..."
sudo apt install -y python3-picamera2 python3-pip libgl1-mesa-glx libglib2.0-0

# Install Python dependencies
echo "[3/4] Installing Python dependencies..."
pip3 install -r requirements.txt --break-system-packages 2>/dev/null || pip3 install -r requirements.txt

# Install Systemd Template Service
echo "[4/4] Installing Dual Camera Systemd Service..."
sudo systemctl stop camera-sender.service 2>/dev/null || true
sudo systemctl disable camera-sender.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/camera-sender.service

sudo cp camera-sender@.service /etc/systemd/system/
sudo systemctl daemon-reload

echo "========================================="
echo "Setup complete! The service is now template-based."
echo "To manage Camera 0 (config_cam0.json):"
echo "  sudo systemctl start camera-sender@0"
echo "  sudo systemctl enable camera-sender@0"
echo ""
echo "To manage Camera 1 (config_cam1.json):"
echo "  sudo systemctl start camera-sender@1"
echo "  sudo systemctl enable camera-sender@1"
echo "========================================="
