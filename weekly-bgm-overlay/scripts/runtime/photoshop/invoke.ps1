param([Parameter(Mandatory=$true)][string]$RunDir,
      [Parameter(Mandatory=$true)][string]$Photoshop)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$folder = (Resolve-Path -LiteralPath $RunDir).Path
$driver = Join-Path $folder 'run.jsx'
$connection = @{transport='powershell51-com'; status='connecting'; launched=$false}
function Save-Connection {
    $connection | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $folder 'connection.json') -Encoding utf8
}
try {
    $job = Get-Content -LiteralPath (Join-Path $folder 'job.json') -Raw -Encoding utf8 | ConvertFrom-Json
    $connection.run_id = $job.run_id
    Save-Connection
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    $app = $null
    $lastError = 'Photoshop not ready'
    while ($null -eq $app -and [DateTime]::UtcNow -lt $deadline) {
        try {
            $candidate = [Runtime.InteropServices.Marshal]::GetActiveObject('Photoshop.Application')
            $version = [string]$candidate.Version
            if ($version) { $app = $candidate }
        } catch {
            $lastError = $_.Exception.Message
            $hr = $_.Exception.GetBaseException().HResult
            if (-not $connection.launched -and $hr -eq -2147221021) {
                # Photoshop itself needs its normal desktop initialization.
                # The PowerShell helper has no console window; no UI clicks occur.
                $null = Start-Process -FilePath $Photoshop -PassThru
                $connection.launched = $true
                Save-Connection
            }
        }
        if ($null -eq $app) { Start-Sleep -Milliseconds 500 }
    }
    if ($null -eq $app) { throw ('Cannot connect to Photoshop: ' + $lastError) }
    $connection.version = $version
    $connection.documents_before = $app.Documents.Count
    $connection.status = 'connected'
    Save-Connection
    # Exactly one invocation. An ambiguous COM failure must never cause a replay.
    $null = $app.DoJavaScriptFile($driver)
    $connection.documents_after = $app.Documents.Count
    $connection.status = 'returned'
    Save-Connection
} catch {
    $connection.status = 'failed'
    $connection.error = $_.Exception.Message
    Save-Connection
    Write-Error $connection.error
    exit 1
}
