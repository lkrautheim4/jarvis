@echo off
start wsl -e bash -c "cd /mnt/c/Users/lkrau/Downloads/jarvis && python3 -m http.server 8080"
timeout /t 2
start chrome "http://localhost:8080/JARVIS_6.html"