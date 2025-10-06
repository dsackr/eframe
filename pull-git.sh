#!/bin/bash

cd /home/dsackr/eframe

# Fetch latest changes
git fetch origin main

# Check if there are updates
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ $LOCAL != $REMOTE ]; then
    echo "$(date): Updates found, pulling changes..."
    
    # Pull latest changes
    git pull origin main
    
    # Restart the service
    sudo systemctl restart eframe.service
    
    echo "$(date): Service restarted with latest code"
else
    echo "$(date): No updates available"
fi
