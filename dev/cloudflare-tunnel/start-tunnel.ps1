param(
    [Parameter(Mandatory = $false)]
    [ValidateSet('api', 'ui')]
    [string]$Target = 'ui'
)

$port = if ($Target -eq 'api') { 8000 } else { 5173 }
$url = "http://127.0.0.1:$port"

Write-Host "Cloudflare quick tunnel -> $url ($Target)" -ForegroundColor Cyan
Write-Host "Copy the https://....trycloudflare.com URL: frontend/.env VITE_API_URL; backend/.env CORS_ORIGINS (quick tunnels change each run)." -ForegroundColor DarkGray
Write-Host ""

& cloudflared tunnel --url $url
