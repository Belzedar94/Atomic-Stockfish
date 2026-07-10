param(
    [string]$Emsdk = 'C:\emsdk\emsdk'
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDir '..\..'))
$first = Join-Path $repoRoot 'build\wasm-engine-repro-a'
$second = Join-Path $repoRoot 'build\wasm-engine-repro-b'
$build = Join-Path $scriptDir 'build.ps1'

& $build -OutDir $first -Emsdk $Emsdk
& $build -OutDir $second -Emsdk $Emsdk

$firstFiles = Get-ChildItem -LiteralPath $first -File |
    Where-Object { $_.Name -like 'atomic-stockfish-nnue*' -or $_.Name -eq 'manifest.json' } |
    Sort-Object Name
$secondFiles = Get-ChildItem -LiteralPath $second -File |
    Where-Object { $_.Name -like 'atomic-stockfish-nnue*' -or $_.Name -eq 'manifest.json' } |
    Sort-Object Name

if (($firstFiles.Name -join "`n") -ne ($secondFiles.Name -join "`n")) {
    throw 'Reproducibility failure: artifact file sets differ'
}

foreach ($file in $firstFiles) {
    $other = Join-Path $second $file.Name
    $firstHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
    $secondHash = (Get-FileHash -LiteralPath $other -Algorithm SHA256).Hash
    if ($firstHash -ne $secondHash) {
        throw "Reproducibility failure for $($file.Name): $firstHash != $secondHash"
    }
    Write-Host "$($file.Name): $firstHash"
}

Write-Host 'WASM engine reproducibility: PASS'
