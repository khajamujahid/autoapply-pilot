Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { & .\install.ps1 }
$port = 8077
if (Test-Path .env) { $m = Select-String -Path .env -Pattern '^PORT=(\d+)'; if ($m) { $port = $m.Matches[0].Groups[1].Value } }
Write-Host "ApplyPilot -> http://localhost:$port"
& .\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port $port
