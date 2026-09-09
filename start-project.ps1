docker compose up -d

Write-Host "Starting services..."
Start-Sleep -Seconds 15

Start-Process "http://localhost:8000"