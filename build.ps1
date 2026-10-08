$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path 'dist/server','dist/static','dist/.openai' | Out-Null
 $worker = Get-Content 'worker/index.js' -Raw
 $index = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'index.html')))
 $worker.Replace('__INDEX_HTML_BASE64__', $index) | Set-Content 'dist/server/index.js' -NoNewline
Copy-Item 'index.html' 'dist/static/index.html' -Force
Copy-Item '.openai/hosting.json' 'dist/.openai/hosting.json' -Force
