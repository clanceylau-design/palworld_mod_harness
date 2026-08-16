[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ToolRoot,

    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$resolvedParent = Resolve-Path -LiteralPath (Split-Path -Parent $ToolRoot) -ErrorAction Stop
$root = [System.IO.Path]::GetFullPath((Join-Path $resolvedParent (Split-Path -Leaf $ToolRoot)))
$driveRoot = [System.IO.Path]::GetPathRoot($root)
if ([string]::IsNullOrWhiteSpace($root) -or $root -eq $driveRoot) {
    throw 'ToolRoot must be a dedicated directory below a drive root.'
}

$skillRoot = Split-Path -Parent $PSScriptRoot
$lockPath = Join-Path $skillRoot 'references\toolchain.lock.json'
$lock = Get-Content -Raw -LiteralPath $lockPath | ConvertFrom-Json
New-Item -ItemType Directory -Path $root -Force | Out-Null
$downloads = Join-Path $root 'downloads'
New-Item -ItemType Directory -Path $downloads -Force | Out-Null

foreach ($package in $lock.packages) {
    $archive = Join-Path $downloads $package.archive
    $download = $true
    if (Test-Path -LiteralPath $archive) {
        $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -eq $package.sha256) {
            $download = $false
        } elseif (-not $Force) {
            throw "Hash mismatch for $archive. Re-run with -Force to replace only this archive."
        }
    }
    if ($download) {
        $temporary = "$archive.partial"
        Invoke-WebRequest -Uri $package.url -OutFile $temporary
        $actualHash = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $package.sha256) {
            throw "Downloaded hash mismatch for $($package.name)."
        }
        Move-Item -LiteralPath $temporary -Destination $archive -Force
    }

    $destination = Join-Path $root $package.destination
    if (-not (Test-Path -LiteralPath $destination)) {
        New-Item -ItemType Directory -Path $destination | Out-Null
        Expand-Archive -LiteralPath $archive -DestinationPath $destination
    }
    [pscustomobject]@{
        Name        = $package.name
        Version     = $package.version
        Archive     = $archive
        Destination = $destination
        Verified    = $true
    }
}
