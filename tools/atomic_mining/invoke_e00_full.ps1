[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $AuditReceiptPath,

    [AllowNull()]
    [AllowEmptyString()]
    [string] $RepositoryRoot,
    [string] $DesignRoot =
        'F:\Atomic-V3-E00\e00-src-v3-launch5-design',
    [string] $SmokeOutputRoot =
        'F:\Atomic-V3-E00\e00-src-v3-launch5-smoke',
    [string] $FullOutputRoot =
        'F:\Atomic-V3-E00\e00-src-v3-launch5-full',
    [string] $CaptureRoot =
        'F:\Atomic-V3-E00\e00-src-v3-launch5-captures',
    [string] $PythonPath =
        'C:\Users\djime\AppData\Local\Programs\Python\Python312\python.exe',
    [string] $BookPath =
        'C:\Users\djime\Documents\Chess_variants\Match script\books\atomic.epd',
    [string] $EnginePath =
        'C:\Users\djime\Documents\Chess_variants\Codex\Fairy-Stockfish organization\Atomic Project\Atomic-Stockfish-teacher-syzygy-v2-build-launch1\src\atomic-stockfish.exe',
    [string] $CurrentNetPath =
        'D:\NNUE training\Atomic-v2\campaign-28eaed5-high-lambda\lambda-100\artifacts\atomic-v3-lambda-100-epoch-37.nnue',
    [string] $TeacherNetPath =
        'C:\Users\djime\Documents\Chess_variants\Codex\Fairy-Stockfish organization\Atomic Project\atomic_run3b_e202_l05.nnue',
    [string] $VariantConfigPath =
        'C:\Users\djime\Documents\Chess_variants\Match script\variants.ini',

    [switch] $ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ExperimentId = 'atomic-e00-src-v3-launch5-20260725'
$SmokeBatteryId = 'atomic-e00-src-v3-launch5-smoke'
$FullBatteryId = 'atomic-e00-src-v3-launch5-full'
$AuditSchema = 'atomic-e00-full-authorization-v1'
$ExecutionReceiptSchema = 'atomic-e00-execution-receipt-v3'
$RuntimeBuildReceiptSchema =
    'atomic-e00-runtime-manifest-build-receipt-v1'
$RuntimeManifestSchema = 'atomic-e00-runtime-manifest-v2'
$ScheduleReceiptSchema = 'atomic-e00-schedule-receipt-v1'
$ValidationSchema = 'atomic-e00-full-launch-validation-v1'

function Get-AbsolutePath([string] $Path) {
    return [System.IO.Path]::GetFullPath($Path)
}

function Resolve-RepositoryRoot(
    [AllowNull()]
    [AllowEmptyString()]
    [string] $RequestedRoot,
    [AllowNull()]
    [AllowEmptyString()]
    [string] $LauncherPath,
    [string] $ExpectedLauncherName
) {
    if (-not [string]::IsNullOrEmpty($RequestedRoot)) {
        if ([string]::IsNullOrWhiteSpace($RequestedRoot)) {
            throw "repository root must not be whitespace"
        }
        return Get-AbsolutePath $RequestedRoot
    }
    if ([string]::IsNullOrWhiteSpace($LauncherPath)) {
        throw "PSCommandPath is unavailable; RepositoryRoot is required"
    }
    $resolvedLauncher = Get-AbsolutePath $LauncherPath
    if (-not (Test-Path -LiteralPath $resolvedLauncher -PathType Leaf)) {
        throw "launcher path is not an existing file"
    }
    if (
        [System.IO.Path]::GetFileName($resolvedLauncher) -cne
        $ExpectedLauncherName
    ) {
        throw "launcher filename differs from the expected entrypoint"
    }
    $atomicMiningRoot = Split-Path -Parent $resolvedLauncher
    if ((Split-Path -Leaf $atomicMiningRoot) -cne 'atomic_mining') {
        throw "launcher is not inside the exact tools\atomic_mining path"
    }
    $toolsRoot = Split-Path -Parent $atomicMiningRoot
    if ((Split-Path -Leaf $toolsRoot) -cne 'tools') {
        throw "launcher is not inside the exact tools\atomic_mining path"
    }
    $derivedRoot = Get-AbsolutePath (Split-Path -Parent $toolsRoot)
    $expectedLauncher = Get-AbsolutePath (
        Join-Path (
            Join-Path $derivedRoot 'tools\atomic_mining'
        ) $ExpectedLauncherName
    )
    if (
        -not [string]::Equals(
            $resolvedLauncher,
            $expectedLauncher,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "launcher path does not bind to the derived repository root"
    }
    return $derivedRoot
}

function Assert-PathEqual(
    [string] $Actual,
    [string] $Expected,
    [string] $Label
) {
    if (
        [string]::IsNullOrWhiteSpace($Actual) -or
        -not [string]::Equals(
            (Get-AbsolutePath $Actual),
            (Get-AbsolutePath $Expected),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "$Label path differs"
    }
}

function Assert-PathOutside(
    [string] $Path,
    [string] $ForbiddenRoot,
    [string] $Label
) {
    $candidate = (Get-AbsolutePath $Path).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $root = (Get-AbsolutePath $ForbiddenRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    if (
        [string]::Equals(
            $candidate,
            $root,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        $candidate.StartsWith(
            $root + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "$Label must remain outside the sealed design root"
    }
}

function Assert-LowerSha256([object] $Value, [string] $Label) {
    if (
        -not ($Value -is [string]) -or
        $Value -cnotmatch '^[0-9a-f]{64}$'
    ) {
        throw "$Label must be one lowercase SHA-256"
    }
    return [string] $Value
}

function Assert-GitIdentity([object] $Value, [string] $Label) {
    if (
        -not ($Value -is [string]) -or
        $Value -cnotmatch '^[0-9a-f]{40}$'
    ) {
        throw "$Label must be one lowercase Git object ID"
    }
    return [string] $Value
}

function Assert-ExactProperties(
    [object] $Value,
    [string[]] $Expected,
    [string] $Label
) {
    if ($null -eq $Value) {
        throw "$Label must be an object"
    }
    $actual = @(
        $Value.PSObject.Properties.Name |
            Sort-Object
    )
    $wanted = @($Expected | Sort-Object)
    if (
        $actual.Count -ne $wanted.Count -or
        (Compare-Object -ReferenceObject $wanted -DifferenceObject $actual)
    ) {
        throw "$Label fields differ"
    }
}

function Assert-JsonString(
    [object] $Value,
    [string] $Label,
    [switch] $NonEmpty
) {
    if (-not ($Value -is [string])) {
        throw "$Label must be a JSON string"
    }
    if ($NonEmpty -and $Value.Length -eq 0) {
        throw "$Label must be a non-empty JSON string"
    }
    return [string] $Value
}

function Assert-JsonStringEquals(
    [object] $Value,
    [string] $Expected,
    [string] $Label
) {
    $actual = Assert-JsonString $Value $Label
    if ($actual -cne $Expected) {
        throw "$Label differs"
    }
}

function Assert-JsonBooleanEquals(
    [object] $Value,
    [bool] $Expected,
    [string] $Label
) {
    if (-not ($Value -is [bool])) {
        throw "$Label must be a JSON boolean"
    }
    if ($Value -ne $Expected) {
        throw "$Label differs"
    }
}

function Test-JsonIntegralType([object] $Value) {
    if ($null -eq $Value -or $Value -is [bool]) {
        return $false
    }
    return (
        $Value -is [byte] -or
        $Value -is [sbyte] -or
        $Value -is [int16] -or
        $Value -is [uint16] -or
        $Value -is [int32] -or
        $Value -is [uint32] -or
        $Value -is [int64] -or
        $Value -is [uint64]
    )
}

function Get-JsonInt64([object] $Value, [string] $Label) {
    if (-not (Test-JsonIntegralType $Value)) {
        throw "$Label must be a JSON integer"
    }
    $decimal = [decimal] $Value
    if (
        $decimal -lt [decimal] [long]::MinValue -or
        $decimal -gt [decimal] [long]::MaxValue
    ) {
        throw "$Label is outside signed 64-bit range"
    }
    return [long] $decimal
}

function Assert-JsonIntegerEquals(
    [object] $Value,
    [long] $Expected,
    [string] $Label
) {
    $actual = Get-JsonInt64 $Value $Label
    if ($actual -ne $Expected) {
        throw "$Label differs"
    }
}

function Assert-JsonIntegerMinimum(
    [object] $Value,
    [long] $Minimum,
    [string] $Label
) {
    $actual = Get-JsonInt64 $Value $Label
    if ($actual -lt $Minimum) {
        throw "$Label is below its minimum"
    }
    return $actual
}

function Test-JsonNumericType([object] $Value) {
    return (
        (Test-JsonIntegralType $Value) -or
        $Value -is [single] -or
        $Value -is [double] -or
        $Value -is [decimal]
    )
}

function Get-JsonDouble([object] $Value, [string] $Label) {
    if (-not (Test-JsonNumericType $Value)) {
        throw "$Label must be a JSON number"
    }
    $actual = [System.Convert]::ToDouble(
        $Value,
        [System.Globalization.CultureInfo]::InvariantCulture
    )
    if ([double]::IsNaN($actual) -or [double]::IsInfinity($actual)) {
        throw "$Label must be a finite JSON number"
    }
    return $actual
}

function Assert-JsonNumberEquals(
    [object] $Value,
    [double] $Expected,
    [string] $Label
) {
    $actual = Get-JsonDouble $Value $Label
    if ($actual -ne $Expected) {
        throw "$Label differs"
    }
}

function Assert-JsonArray([object] $Value, [string] $Label) {
    if (-not ($Value -is [System.Array])) {
        throw "$Label must be a JSON array"
    }
}

function Assert-JsonScalarEquals(
    [object] $Value,
    [object] $Expected,
    [string] $Label
) {
    if ($Expected -is [bool]) {
        Assert-JsonBooleanEquals $Value ([bool] $Expected) $Label
        return
    }
    if (Test-JsonIntegralType $Expected) {
        Assert-JsonIntegerEquals (
            $Value
        ) (Get-JsonInt64 $Expected "$Label expected") $Label
        return
    }
    if ($Expected -is [string]) {
        Assert-JsonStringEquals $Value ([string] $Expected) $Label
        return
    }
    throw "$Label has an unsupported expected scalar type"
}

function Get-StableFileState([string] $Path, [string] $Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is absent"
    }
    $before = Get-Item -LiteralPath $Path -Force
    $sha256 = (
        Get-FileHash -Algorithm SHA256 -LiteralPath $Path
    ).Hash.ToLowerInvariant()
    $after = Get-Item -LiteralPath $Path -Force
    if (
        $before.Length -ne $after.Length -or
        $before.LastWriteTimeUtc.Ticks -ne $after.LastWriteTimeUtc.Ticks -or
        $before.CreationTimeUtc.Ticks -ne $after.CreationTimeUtc.Ticks
    ) {
        throw "$Label changed while hashing"
    }
    return [pscustomobject]@{
        Path = $after.FullName
        Sha256 = $sha256
        SizeBytes = [long] $after.Length
    }
}

function Get-ByteArraySha256([byte[]] $Payload) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $algorithm.ComputeHash($Payload)
    }
    finally {
        $algorithm.Dispose()
    }
    return (
        [System.BitConverter]::ToString($digest).Replace(
            '-', ''
        ).ToLowerInvariant()
    )
}

function ConvertTo-WindowsCommandLineArgument([string] $Argument) {
    if ($null -eq $Argument) {
        throw "process argument must not be null"
    }
    if (
        $Argument.IndexOf([char] 0) -ge 0 -or
        $Argument.IndexOf("`r") -ge 0 -or
        $Argument.IndexOf("`n") -ge 0
    ) {
        throw "process argument contains a forbidden control character"
    }
    if ($Argument.Length -eq 0) {
        return '""'
    }
    if ($Argument -cnotmatch '[\s"]') {
        return $Argument
    }

    # This is the inverse of CommandLineToArgvW / the Microsoft CRT parser:
    # backslashes are doubled only when they precede a quote or the closing
    # delimiter; a literal quote receives one additional escaping backslash.
    $builder = New-Object System.Text.StringBuilder
    [void] $builder.Append('"')
    $backslashes = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq [char] '\') {
            $backslashes++
            continue
        }
        if ($character -eq [char] '"') {
            if ($backslashes -gt 0) {
                [void] $builder.Append(
                    [char] '\',
                    (2 * $backslashes)
                )
            }
            [void] $builder.Append('\')
            [void] $builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void] $builder.Append([char] '\', $backslashes)
            $backslashes = 0
        }
        [void] $builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void] $builder.Append([char] '\', (2 * $backslashes))
    }
    [void] $builder.Append('"')
    return $builder.ToString()
}

function Invoke-CapturedProcessCreateNew(
    [string] $FilePath,
    [string[]] $ArgumentList,
    [string] $WorkingDirectory,
    [string] $StandardOutputPath,
    [string] $StandardErrorPath
) {
    $quotedArguments = @(
        $ArgumentList |
            ForEach-Object {
                ConvertTo-WindowsCommandLineArgument ([string] $_)
            }
    )
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = $quotedArguments -join ' '
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $stdout = $null
    $stderr = $null
    $process = $null
    $stdoutTask = $null
    $stderrTask = $null
    try {
        $stdout = New-Object System.IO.FileStream(
            $StandardOutputPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $stderr = New-Object System.IO.FileStream(
            $StandardErrorPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "full runner process did not start"
        }
        $stdoutTask = $process.StandardOutput.BaseStream.CopyToAsync($stdout)
        $stderrTask = $process.StandardError.BaseStream.CopyToAsync($stderr)
        $process.WaitForExit()
        $null = $stdoutTask.GetAwaiter().GetResult()
        $null = $stderrTask.GetAwaiter().GetResult()
        $stdout.Flush($true)
        $stderr.Flush($true)
        return [int] $process.ExitCode
    }
    finally {
        if ($null -ne $stdout) {
            $stdout.Dispose()
        }
        if ($null -ne $stderr) {
            $stderr.Dispose()
        }
        if ($null -ne $process) {
            $process.Dispose()
        }
    }
}

function Assert-FileBinding(
    [string] $Path,
    [object] $ExpectedSha256,
    [object] $ExpectedSizeBytes,
    [string] $Label
) {
    $expectedHash = Assert-LowerSha256 $ExpectedSha256 "$Label SHA-256"
    $expectedSize = Get-JsonInt64 $ExpectedSizeBytes "$Label size"
    if ($expectedSize -lt 0) {
        throw "$Label size is invalid"
    }
    $state = Get-StableFileState $Path $Label
    if (
        $state.Sha256 -cne $expectedHash -or
        $state.SizeBytes -ne $expectedSize
    ) {
        throw "$Label binding differs"
    }
    return $state
}

function Read-StrictJson([string] $Path, [string] $Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is absent"
    }
    $before = Get-Item -LiteralPath $Path -Force
    if (($before.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw "$Label must not be a reparse point"
    }
    $payload = [System.IO.File]::ReadAllBytes($before.FullName)
    $after = Get-Item -LiteralPath $Path -Force
    if (
        $before.Length -ne $after.Length -or
        $before.LastWriteTimeUtc.Ticks -ne $after.LastWriteTimeUtc.Ticks -or
        $before.CreationTimeUtc.Ticks -ne $after.CreationTimeUtc.Ticks -or
        $payload.Length -ne $after.Length
    ) {
        throw "$Label changed while reading"
    }
    $state = [pscustomobject]@{
        Path = $after.FullName
        Sha256 = Get-ByteArraySha256 $payload
        SizeBytes = [long] $payload.Length
    }
    if (
        $payload.Length -eq 0 -or
        (
            $payload.Length -ge 3 -and
            $payload[0] -eq 0xef -and
            $payload[1] -eq 0xbb -and
            $payload[2] -eq 0xbf
        ) -or
        $payload[-1] -ne 0x0a -or
        $payload -contains 0x0d
    ) {
        throw "$Label is not strict newline-terminated UTF-8 JSON"
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    try {
        $text = $utf8.GetString($payload)
        $value = $text | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "$Label is not strict UTF-8 JSON"
    }
    if ($null -eq $value -or $value -is [System.Array]) {
        throw "$Label must contain one JSON object"
    }
    return [pscustomobject]@{
        State = $state
        Text = $text
        Value = $value
    }
}

function Read-CanonicalAuditReceipt([string] $Path) {
    $document = Read-StrictJson $Path 'full authorization receipt'
    $value = $document.Value
    Assert-ExactProperties $value @(
        'audit',
        'experiment_id',
        'full',
        'schema',
        'smoke',
        'source'
    ) 'full authorization receipt'
    Assert-ExactProperties $value.audit @(
        'p0', 'p1', 'verdict'
    ) 'full authorization audit'
    Assert-ExactProperties $value.full @(
        'battery_id',
        'runtime_build_receipt_sha256',
        'schedule_receipt_sha256'
    ) 'full authorization full binding'
    Assert-ExactProperties $value.smoke @(
        'battery_id', 'execution_receipt_sha256'
    ) 'full authorization smoke binding'
    Assert-ExactProperties $value.source @(
        'commit', 'tree'
    ) 'full authorization source binding'

    try {
        Assert-JsonStringEquals (
            $value.schema
        ) $AuditSchema 'full authorization schema'
        Assert-JsonStringEquals (
            $value.experiment_id
        ) $ExperimentId 'full authorization experiment_id'
        Assert-JsonStringEquals (
            $value.full.battery_id
        ) $FullBatteryId 'full authorization full battery_id'
        Assert-JsonStringEquals (
            $value.smoke.battery_id
        ) $SmokeBatteryId 'full authorization smoke battery_id'
        Assert-JsonStringEquals (
            $value.audit.verdict
        ) 'GO' 'full authorization verdict'
        Assert-JsonIntegerEquals (
            $value.audit.p0
        ) 0 'full authorization P0'
        Assert-JsonIntegerEquals (
            $value.audit.p1
        ) 0 'full authorization P1'
    }
    catch {
        throw "full authorization is not GO with P0=0 and P1=0: $($_.Exception.Message)"
    }
    $commit = Assert-GitIdentity $value.source.commit 'authorized source commit'
    $tree = Assert-GitIdentity $value.source.tree 'authorized source tree'
    $runtimeReceiptSha = Assert-LowerSha256 (
        $value.full.runtime_build_receipt_sha256
    ) 'authorized runtime build receipt'
    $scheduleReceiptSha = Assert-LowerSha256 (
        $value.full.schedule_receipt_sha256
    ) 'authorized schedule receipt'
    $smokeReceiptSha = Assert-LowerSha256 (
        $value.smoke.execution_receipt_sha256
    ) 'authorized smoke receipt'

    $canonical = (
        '{"audit":{"p0":0,"p1":0,"verdict":"GO"},' +
        '"experiment_id":"' + $ExperimentId + '",' +
        '"full":{"battery_id":"' + $FullBatteryId + '",' +
        '"runtime_build_receipt_sha256":"' + $runtimeReceiptSha + '",' +
        '"schedule_receipt_sha256":"' + $scheduleReceiptSha + '"},' +
        '"schema":"' + $AuditSchema + '",' +
        '"smoke":{"battery_id":"' + $SmokeBatteryId + '",' +
        '"execution_receipt_sha256":"' + $smokeReceiptSha + '"},' +
        '"source":{"commit":"' + $commit + '","tree":"' + $tree + '"}}' +
        "`n"
    )
    if ($document.Text -cne $canonical) {
        throw "full authorization receipt is not canonical"
    }
    return $document
}

function Assert-RepositoryState(
    [string] $Root,
    [string] $ExpectedCommit,
    [string] $ExpectedTree
) {
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "repository root is absent"
    }
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $dirty = @(
        & git -C $resolved status --porcelain=v1 --untracked-files=all
    )
    if ($LASTEXITCODE -ne 0) {
        throw "cannot inspect repository status"
    }
    if ($dirty.Count -ne 0) {
        throw "repository is not exactly clean"
    }
    $commit = (
        & git -C $resolved rev-parse HEAD
    ).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) {
        throw "cannot inspect repository commit"
    }
    $tree = (
        & git -C $resolved rev-parse 'HEAD^{tree}'
    ).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) {
        throw "cannot inspect repository tree"
    }
    if ($commit -cne $ExpectedCommit -or $tree -cne $ExpectedTree) {
        throw "repository commit/tree differs from authorization"
    }
    return [pscustomobject]@{
        Root = $resolved
        Commit = $commit
        Tree = $tree
    }
}

function Assert-NoIgnoredPythonBytecode([string] $Root) {
    $toolsRoot = Join-Path $Root 'tools'
    if (-not (Test-Path -LiteralPath $toolsRoot -PathType Container)) {
        throw "required source package root is absent"
    }
    $forbidden = @(
        Get-ChildItem -LiteralPath $toolsRoot -Recurse -Force |
            Where-Object {
                ($_.PSIsContainer -and $_.Name -ceq '__pycache__') -or
                (
                    -not $_.PSIsContainer -and
                    (
                        $_.Extension -ceq '.pyc' -or
                        $_.Extension -ceq '.pyo'
                    )
                )
            }
    )
    if ($forbidden.Count -ne 0) {
        throw "ignored Python bytecode cache or file is present"
    }
}

function Assert-ExecutionContract(
    [object] $Execution,
    [long] $MaximumWallSeconds,
    [string] $Label
) {
    Assert-ExactProperties $Execution @(
        'clock_policy',
        'command_timeout_seconds',
        'maximum_game_wall_seconds',
        'maximum_plies',
        'maximum_wall_seconds',
        'threads',
        'uci_options'
    ) "$Label execution"
    if (
        $null -eq $Execution.clock_policy -or
        $Execution.clock_policy -is [System.Array]
    ) {
        throw "$Label clock policy must be a JSON object"
    }
    Assert-JsonIntegerEquals (
        $Execution.threads
    ) 1 "$Label execution threads"
    Assert-JsonIntegerEquals (
        $Execution.maximum_plies
    ) 1024 "$Label execution maximum plies"
    Assert-JsonNumberEquals (
        $Execution.command_timeout_seconds
    ) 120.0 "$Label execution command timeout"
    Assert-JsonNumberEquals (
        $Execution.maximum_wall_seconds
    ) ([double] $MaximumWallSeconds) "$Label execution maximum wall seconds"
    Assert-JsonNumberEquals (
        $Execution.maximum_game_wall_seconds
    ) 1800.0 "$Label execution maximum game wall seconds"
    $expectedOptions = @(
        @('UCI_Variant', 'atomic'),
        @('Threads', 1),
        @('Hash', 512),
        @('MultiPV', 1),
        @('Ponder', $false),
        @('SyzygyPath', ''),
        @('SyzygyProbeLimit', 0),
        @('Use NNUE', 'true')
    )
    Assert-JsonArray $Execution.uci_options "$Label UCI options"
    $actualOptions = @($Execution.uci_options)
    if ($actualOptions.Count -ne $expectedOptions.Count) {
        throw "$Label UCI option count differs"
    }
    for ($index = 0; $index -lt $expectedOptions.Count; $index++) {
        Assert-ExactProperties $actualOptions[$index] @(
            'name', 'value'
        ) "$Label UCI option $index"
        Assert-JsonStringEquals (
            $actualOptions[$index].name
        ) $expectedOptions[$index][0] "$Label UCI option $index name"
        Assert-JsonScalarEquals (
            $actualOptions[$index].value
        ) $expectedOptions[$index][1] "$Label UCI option $index value"
    }
}

function Assert-ScheduleBundle(
    [string] $SchedulePath,
    [string] $ReceiptPath,
    [string] $ExpectedReceiptSha256,
    [long] $ExpectedPairs,
    [long] $ExpectedGames,
    [string] $ExpectedSeed,
    [string] $Label
) {
    $receiptDocument = Read-StrictJson $ReceiptPath "$Label receipt"
    if ($receiptDocument.State.Sha256 -cne $ExpectedReceiptSha256) {
        throw "$Label receipt SHA-256 differs from authorization"
    }
    $receipt = $receiptDocument.Value
    Assert-ExactProperties $receipt @(
        'book',
        'counts',
        'derivation',
        'schedule',
        'schedule_schema',
        'schema',
        'seed',
        'selected_root_identities',
        'time_controls'
    ) "$Label receipt"
    Assert-ExactProperties $receipt.counts @(
        'games', 'pairs'
    ) "$Label counts"
    Assert-ExactProperties $receipt.schedule @(
        'row_count', 'sha256', 'size_bytes'
    ) "$Label schedule binding"
    Assert-JsonStringEquals (
        $receipt.schema
    ) $ScheduleReceiptSchema "$Label receipt schema"
    Assert-JsonStringEquals (
        $receipt.schedule_schema
    ) 'atomic-e00-schedule-v1' "$Label schedule schema"
    Assert-JsonStringEquals $receipt.seed $ExpectedSeed "$Label seed"
    Assert-JsonIntegerEquals (
        $receipt.counts.pairs
    ) $ExpectedPairs "$Label pair count"
    Assert-JsonIntegerEquals (
        $receipt.counts.games
    ) $ExpectedGames "$Label game count"
    Assert-JsonArray (
        $receipt.selected_root_identities
    ) "$Label selected root identities"
    Assert-JsonArray $receipt.time_controls "$Label time controls"
    $scheduleState = Assert-FileBinding (
        $SchedulePath
    ) $receipt.schedule.sha256 $receipt.schedule.size_bytes "$Label schedule"
    Assert-JsonIntegerEquals (
        $receipt.schedule.row_count
    ) $ExpectedGames "$Label schedule row count"
    return [pscustomobject]@{
        Receipt = $receipt
        ReceiptState = $receiptDocument.State
        ScheduleState = $scheduleState
    }
}

function Assert-ArtifactMap(
    [object] $Value,
    [string] $Label,
    [string[]] $ExpectedKeys = $null,
    [switch] $RequireNonEmpty
) {
    if ($null -eq $Value) {
        throw "$Label must be an object"
    }
    $properties = @($Value.PSObject.Properties)
    if ($RequireNonEmpty -and $properties.Count -eq 0) {
        throw "$Label must be non-empty"
    }
    if ($null -ne $ExpectedKeys) {
        Assert-ExactProperties $Value $ExpectedKeys $Label
    }
    foreach ($property in $properties) {
        $binding = $property.Value
        Assert-ExactProperties $binding @(
            'identity', 'path', 'sha256', 'size_bytes'
        ) "$Label $($property.Name)"
        if (
            $null -eq $binding.identity -or
            @($binding.identity.PSObject.Properties).Count -eq 0
        ) {
            throw "$Label $($property.Name) identity must be non-empty"
        }
        $bindingPath = Assert-JsonString (
            $binding.path
        ) "$Label $($property.Name) path" -NonEmpty
        Assert-FileBinding (
            $bindingPath
        ) $binding.sha256 $binding.size_bytes "$Label $($property.Name)" |
            Out-Null
    }
}

function Get-ExpectedExecutionInputKeys([object] $Receipt) {
    Assert-ExactProperties $Receipt.runtime @(
        'manifest', 'observed_pre_and_post_equal'
    ) 'full runtime receipt'
    Assert-JsonBooleanEquals (
        $Receipt.runtime.observed_pre_and_post_equal
    ) $true 'full runtime pre/post equality'
    $manifest = $Receipt.runtime.manifest
    Assert-JsonStringEquals (
        $manifest.schema
    ) $RuntimeManifestSchema 'full runtime manifest schema'
    $fixed = @(
        'atomic_mining_init',
        'atomic_outcome_helper',
        'binding_builder',
        'book',
        'common',
        'current_net',
        'engine',
        'owned_process',
        'pyffish',
        'pyffish_build_manifest',
        'runner',
        'schedule',
        'schedule_builder',
        'schedule_receipt',
        'teacher_net',
        'tools_init',
        'uci_session',
        'variant_config'
    )
    Assert-ExactProperties $manifest.inputs $fixed (
        'full runtime manifest inputs'
    )
    $expected = @($fixed) + @('runtime_manifest', 'python_executable')
    $python = $manifest.runtime.python
    if ($null -eq $python) {
        throw "full runtime Python identity is absent"
    }
    if (
        $null -eq $python.runtime_libraries -or
        $python.runtime_libraries -is [System.Array]
    ) {
        throw "full runtime Python libraries must be an object"
    }
    $libraries = @($python.runtime_libraries.PSObject.Properties)
    if ($libraries.Count -eq 0) {
        throw "full runtime Python libraries must be non-empty"
    }
    for ($index = 1; $index -le $libraries.Count; $index++) {
        $expected += ('python_runtime_library_{0:D3}' -f $index)
    }
    Assert-JsonArray (
        $python.child_import_inventory
    ) 'full runtime child import inventory'
    $installationCount = @(
        $python.child_import_inventory | Where-Object {
            $_.source -ceq 'python-installation'
        }
    ).Count
    for ($index = 1; $index -le $installationCount; $index++) {
        $expected += ('python_imported_module_{0:D3}' -f $index)
    }
    return $expected
}

function Assert-NamespaceGuards(
    [object] $Value,
    [string] $Label
) {
    $keys = @(
        'native_build',
        'output',
        'output_parent',
        'runtime_package',
        'snapshots'
    )
    Assert-ExactProperties $Value $keys $Label
    foreach ($key in $keys) {
        $guard = $Value.$key
        Assert-ExactProperties $guard @(
            'enumeration', 'identity', 'parent_identity', 'path'
        ) "$Label $key"
        Assert-JsonString (
            $guard.path
        ) "$Label $key path" -NonEmpty | Out-Null
        if (
            $null -eq $guard.identity -or
            $null -eq $guard.parent_identity -or
            @($guard.identity.PSObject.Properties).Count -eq 0 -or
            @($guard.parent_identity.PSObject.Properties).Count -eq 0
        ) {
            throw "$Label $key identities must be non-empty"
        }
        if ($key -cin @('native_build', 'runtime_package', 'snapshots')) {
            Assert-JsonArray $guard.enumeration "$Label $key enumeration"
            if (@($guard.enumeration).Count -eq 0) {
                throw "$Label $key enumeration must be non-empty"
            }
            foreach ($row in @($guard.enumeration)) {
                $kind = Assert-JsonString (
                    $row.kind
                ) "$Label $key inventory kind" -NonEmpty
                if ($kind -ceq 'file') {
                    Assert-ExactProperties $row @(
                        'device', 'inode', 'kind', 'path', 'size_bytes'
                    ) "$Label $key file inventory"
                    Assert-JsonIntegerMinimum (
                        $row.size_bytes
                    ) 0 "$Label $key file size" | Out-Null
                }
                elseif ($kind -ceq 'directory') {
                    Assert-ExactProperties $row @(
                        'device', 'inode', 'kind', 'path'
                    ) "$Label $key directory inventory"
                }
                else {
                    throw "$Label $key inventory kind differs"
                }
                Assert-JsonIntegerMinimum (
                    $row.device
                ) 0 "$Label $key device" | Out-Null
                Assert-JsonIntegerMinimum (
                    $row.inode
                ) 0 "$Label $key inode" | Out-Null
                Assert-JsonString (
                    $row.path
                ) "$Label $key inventory path" -NonEmpty | Out-Null
            }
        }
        elseif ($null -ne $guard.enumeration) {
            throw "$Label $key enumeration must be null"
        }
    }
}

function Assert-ReceiptExecutionContract(
    [object] $Value,
    [object] $Receipt,
    [double] $MaximumWallSeconds,
    [string] $Label
) {
    Assert-ExactProperties $Value @(
        'clock_policy',
        'command_timeout_seconds',
        'maximum_game_wall_seconds',
        'maximum_plies',
        'maximum_wall_seconds',
        'network_options',
        'owned_process_policy',
        'pair_order',
        'referee_policy',
        'retry_policy',
        'threads',
        'time_loss_rule',
        'uci_options',
        'verifier_policy'
    ) "$Label execution"
    Assert-JsonIntegerEquals $Value.threads 1 "$Label execution threads"
    Assert-JsonIntegerEquals (
        $Value.maximum_plies
    ) 1024 "$Label execution maximum plies"
    Assert-JsonNumberEquals (
        $Value.command_timeout_seconds
    ) 120.0 "$Label execution command timeout"
    Assert-JsonNumberEquals (
        $Value.maximum_wall_seconds
    ) $MaximumWallSeconds "$Label execution maximum wall seconds"
    Assert-JsonNumberEquals (
        $Value.maximum_game_wall_seconds
    ) 1800.0 "$Label execution maximum game wall seconds"
    Assert-JsonStringEquals (
        $Value.retry_policy
    ) 'none' "$Label execution retry policy"
    Assert-JsonStringEquals (
        $Value.pair_order
    ) 'schedule-ordinal-then-leg' "$Label execution pair order"
    Assert-JsonStringEquals (
        $Value.time_loss_rule
    ) 'draw-if-nonflagging-side-has-insufficient-atomic-mating-material-v1' (
        "$Label execution time-loss rule"
    )
    Assert-JsonStringEquals (
        $Value.owned_process_policy
    ) 'windows-job-create-suspended-assign-resume-active-zero-v1' (
        "$Label execution owned-process policy"
    )
    Assert-JsonStringEquals (
        $Value.referee_policy
    ) 'fresh-exact-pyffish-root-plus-history-v2' (
        "$Label execution referee policy"
    )
    Assert-JsonStringEquals (
        $Value.verifier_policy
    ) 'independent-fresh-pyffish-owned-process-replay-v2' (
        "$Label execution verifier policy"
    )
    Assert-ExactProperties $Value.clock_policy @(
        'charged_interval', 'equality', 'uci_millisecond_conversion'
    ) "$Label execution clock policy"
    Assert-JsonStringEquals (
        $Value.clock_policy.charged_interval
    ) 'complete-go-write-flush-to-complete-bestmove-newline' (
        "$Label execution charged interval"
    )
    Assert-JsonStringEquals (
        $Value.clock_policy.equality
    ) 'elapsed-ns-equal-remaining-ns-is-on-time' (
        "$Label execution clock equality"
    )
    Assert-JsonStringEquals (
        $Value.clock_policy.uci_millisecond_conversion
    ) 'floor-nanoseconds' "$Label execution clock conversion"
    $expectedOptions = @(
        @('UCI_Variant', 'atomic'),
        @('Threads', 1),
        @('Hash', 512),
        @('MultiPV', 1),
        @('Ponder', $false),
        @('SyzygyPath', ''),
        @('SyzygyProbeLimit', 0),
        @('Use NNUE', 'true')
    )
    Assert-JsonArray $Value.uci_options "$Label execution UCI options"
    $options = @($Value.uci_options)
    if ($options.Count -ne $expectedOptions.Count) {
        throw "$Label execution UCI option count differs"
    }
    for ($index = 0; $index -lt $options.Count; $index++) {
        Assert-ExactProperties $options[$index] @(
            'name', 'value'
        ) "$Label execution UCI option $index"
        Assert-JsonStringEquals (
            $options[$index].name
        ) $expectedOptions[$index][0] "$Label execution UCI option $index name"
        Assert-JsonScalarEquals (
            $options[$index].value
        ) $expectedOptions[$index][1] "$Label execution UCI option $index value"
    }
    Assert-ExactProperties $Value.network_options @(
        'current-v3', 'run3b'
    ) "$Label execution network options"
    foreach ($binding in @(
        @('current-v3', 'current_net'),
        @('run3b', 'teacher_net')
    )) {
        $role = $binding[0]
        $networkKey = $binding[1]
        Assert-JsonArray (
            $Value.network_options.$role
        ) "$Label execution $role network options"
        $networkOptions = @($Value.network_options.$role)
        if ($networkOptions.Count -ne 1) {
            throw "$Label execution $role network option count differs"
        }
        Assert-ExactProperties $networkOptions[0] @(
            'name', 'value'
        ) "$Label execution $role network option"
        Assert-JsonStringEquals (
            $networkOptions[0].name
        ) 'EvalFile' "$Label execution $role network option name"
        Assert-JsonStringEquals (
            $networkOptions[0].value
        ) $Receipt.input_snapshots.$networkKey.path (
            "$Label execution $role EvalFile"
        )
    }
}

function Assert-ExecutionReceipt(
    [string] $ReceiptPath,
    [string] $ExpectedReceiptSha256,
    [string] $ExpectedBatteryId,
    [string] $SchedulePath,
    [string] $ScheduleReceiptPath,
    [long] $ExpectedPairs,
    [long] $ExpectedGames,
    [string] $OutputRoot,
    [string] $Label,
    [switch] $RequireComplete
) {
    $document = Read-StrictJson $ReceiptPath "$Label execution receipt"
    if ($document.State.Sha256 -cne $ExpectedReceiptSha256) {
        throw "$Label execution receipt SHA-256 differs"
    }
    $receipt = $document.Value
    Assert-ExactProperties $receipt @(
        'battery_id',
        'executed_runtime_package',
        'execution',
        'experiment_id',
        'input_snapshots',
        'inputs',
        'namespace_guards',
        'native_build_artifacts',
        'outputs',
        'reconciliation',
        'runtime',
        'schedule',
        'schema',
        'status',
        'trust_boundary'
    ) "$Label execution receipt"
    Assert-JsonStringEquals (
        $receipt.schema
    ) $ExecutionReceiptSchema "$Label execution receipt schema"
    Assert-JsonStringEquals (
        $receipt.status
    ) 'committed' "$Label committed execution receipt status"
    Assert-JsonStringEquals (
        $receipt.experiment_id
    ) $ExperimentId "$Label execution receipt experiment_id"
    Assert-JsonStringEquals (
        $receipt.battery_id
    ) $ExpectedBatteryId "$Label execution receipt battery_id"
    Assert-ExactProperties $receipt.reconciliation @(
        'accepted_games',
        'accepted_pairs',
        'all_ids_recomputed',
        'all_pairs_atomic',
        'all_trajectories_legally_replayed',
        'draws',
        'losses_current',
        'time_losses',
        'wins_current',
        'zero_extra_duplicate_partial_rows'
    ) "$Label reconciliation"
    Assert-JsonIntegerEquals (
        $receipt.reconciliation.accepted_pairs
    ) $ExpectedPairs "$Label accepted pairs"
    Assert-JsonIntegerEquals (
        $receipt.reconciliation.accepted_games
    ) $ExpectedGames "$Label accepted games"
    foreach ($field in @(
        'all_ids_recomputed',
        'all_pairs_atomic',
        'all_trajectories_legally_replayed',
        'zero_extra_duplicate_partial_rows'
    )) {
        Assert-JsonBooleanEquals (
            $receipt.reconciliation.$field
        ) $true "$Label reconciliation $field"
    }
    foreach ($field in @(
        'draws', 'losses_current', 'time_losses', 'wins_current'
    )) {
        Assert-JsonIntegerMinimum (
            $receipt.reconciliation.$field
        ) 0 "$Label reconciliation $field" | Out-Null
    }
    $outcomeTotal = (
        (Get-JsonInt64 (
            $receipt.reconciliation.draws
        ) "$Label reconciliation draws") +
        (Get-JsonInt64 (
            $receipt.reconciliation.losses_current
        ) "$Label reconciliation losses_current") +
        (Get-JsonInt64 (
            $receipt.reconciliation.wins_current
        ) "$Label reconciliation wins_current")
    )
    if ($outcomeTotal -ne $ExpectedGames) {
        throw "$Label outcome reconciliation differs"
    }
    $scheduleState = Get-StableFileState $SchedulePath "$Label schedule"
    $scheduleReceiptState = Get-StableFileState (
        $ScheduleReceiptPath
    ) "$Label schedule receipt"
    Assert-ExactProperties $receipt.schedule @(
        'receipt_sha256', 'receipt_size_bytes', 'sha256', 'size_bytes'
    ) "$Label schedule binding"
    Assert-JsonStringEquals (
        $receipt.schedule.sha256
    ) $scheduleState.Sha256 "$Label schedule SHA-256"
    Assert-JsonIntegerEquals (
        $receipt.schedule.size_bytes
    ) $scheduleState.SizeBytes "$Label schedule size"
    Assert-JsonStringEquals (
        $receipt.schedule.receipt_sha256
    ) $scheduleReceiptState.Sha256 "$Label schedule receipt SHA-256"
    Assert-JsonIntegerEquals (
        $receipt.schedule.receipt_size_bytes
    ) $scheduleReceiptState.SizeBytes "$Label schedule receipt size"
    if ($RequireComplete) {
        $expectedInputKeys = @(
            Get-ExpectedExecutionInputKeys $receipt
        )
        Assert-ArtifactMap (
            $receipt.inputs
        ) "$Label input" $expectedInputKeys -RequireNonEmpty
        Assert-ArtifactMap (
            $receipt.input_snapshots
        ) "$Label snapshot" $expectedInputKeys -RequireNonEmpty
        foreach ($name in $expectedInputKeys) {
            Assert-JsonStringEquals (
                $receipt.input_snapshots.$name.sha256
            ) $receipt.inputs.$name.sha256 "$Label snapshot $name SHA-256"
            Assert-JsonIntegerEquals (
                $receipt.input_snapshots.$name.size_bytes
            ) (Get-JsonInt64 (
                $receipt.inputs.$name.size_bytes
            ) "$Label input $name size") "$Label snapshot $name size"
        }
        $runtimePackageKeys = @(
            'atomic_mining_init',
            'atomic_outcome_helper',
            'binding_builder',
            'common',
            'owned_process',
            'runner',
            'schedule_builder',
            'tools_init',
            'uci_session'
        )
        Assert-ArtifactMap (
            $receipt.executed_runtime_package
        ) "$Label runtime package" $runtimePackageKeys -RequireNonEmpty
        Assert-NamespaceGuards (
            $receipt.namespace_guards
        ) "$Label namespace guards"
        $nativeKeys = @(
            $receipt.namespace_guards.native_build.enumeration |
                Where-Object { $_.kind -ceq 'file' } |
                ForEach-Object { $_.path }
        )
        Assert-ArtifactMap (
            $receipt.native_build_artifacts
        ) "$Label native build artifact" $nativeKeys -RequireNonEmpty
        Assert-ExactProperties $receipt.trust_boundary @(
            'child_import_policy',
            'guarded_runtime',
            'hermetic',
            'namespace_policy',
            'residual_tcb'
        ) "$Label trust boundary"
        Assert-JsonBooleanEquals (
            $receipt.trust_boundary.hermetic
        ) $false "$Label trust boundary hermetic"
        foreach ($field in @(
            'child_import_policy', 'guarded_runtime', 'namespace_policy'
        )) {
            Assert-JsonString (
                $receipt.trust_boundary.$field
            ) "$Label trust boundary $field" -NonEmpty | Out-Null
        }
        Assert-JsonArray (
            $receipt.trust_boundary.residual_tcb
        ) "$Label trust boundary residual_tcb"
        Assert-ReceiptExecutionContract (
            $receipt.execution
        ) $receipt 14400.0 $Label
    }
    else {
        # The smoke evidence was deeply validated by the prepare launcher and
        # is authorized here by its exact receipt SHA-256.  Full rehydration
        # deliberately checks only this receipt's top-level durable bindings.
        Assert-ArtifactMap $receipt.inputs "$Label input"
        Assert-ArtifactMap $receipt.input_snapshots "$Label snapshot"
        Assert-ArtifactMap (
            $receipt.executed_runtime_package
        ) "$Label runtime package"
        Assert-ArtifactMap (
            $receipt.native_build_artifacts
        ) "$Label native build artifact"
    }

    Assert-ExactProperties $receipt.outputs @(
        'games', 'inventory', 'rejections'
    ) "$Label outputs"
    $expectedOutputNames = [ordered]@{
        games = 'games.jsonl'
        inventory = 'inventory.json'
        rejections = 'rejections.jsonl'
    }
    foreach ($name in $expectedOutputNames.Keys) {
        $binding = $receipt.outputs.$name
        Assert-ExactProperties $binding @(
            'path', 'sha256', 'size_bytes'
        ) "$Label output $name"
        $relative = Assert-JsonString (
            $binding.path
        ) "$Label output $name path" -NonEmpty
        if (
            $relative -cne $expectedOutputNames[$name] -or
            [System.IO.Path]::IsPathRooted($relative) -or
            $relative.Contains('..') -or
            $relative.Contains('/') -or
            $relative.Contains('\')
        ) {
            throw "$Label output $name path differs"
        }
        Assert-FileBinding (
            Join-Path $OutputRoot $relative
        ) $binding.sha256 $binding.size_bytes "$Label output $name" |
            Out-Null
    }
    try {
        Assert-JsonIntegerEquals (
            $receipt.outputs.rejections.size_bytes
        ) 0 "$Label rejection size"
        Assert-JsonStringEquals (
            $receipt.outputs.rejections.sha256
        ) (
            'e3b0c44298fc1c149afbf4c8996fb924' +
            '27ae41e4649b934ca495991b7852b855'
        ) "$Label rejection SHA-256"
    }
    catch {
        throw "$Label rejection ledger is not byte-empty: $($_.Exception.Message)"
    }
    return $document
}

function Assert-RuntimeBundle(
    [string] $ManifestPath,
    [string] $BuildReceiptPath,
    [string] $ExpectedBuildReceiptSha256,
    [object] $Source,
    [hashtable] $InputPaths
) {
    $buildDocument = Read-StrictJson (
        $BuildReceiptPath
    ) 'full runtime build receipt'
    if ($buildDocument.State.Sha256 -cne $ExpectedBuildReceiptSha256) {
        throw "full runtime build receipt SHA-256 differs from authorization"
    }
    $build = $buildDocument.Value
    Assert-ExactProperties $build @(
        'child_import_inventory',
        'execution',
        'inputs',
        'runtime_discovery',
        'runtime_manifest',
        'schema',
        'source'
    ) 'full runtime build receipt'
    Assert-JsonStringEquals (
        $build.schema
    ) $RuntimeBuildReceiptSchema 'full runtime build receipt schema'
    Assert-ExactProperties $build.source @(
        'commit', 'root', 'tree'
    ) 'full runtime source'
    $buildSourceRoot = Assert-JsonString (
        $build.source.root
    ) 'runtime source root' -NonEmpty
    Assert-PathEqual $buildSourceRoot $Source.Root 'runtime source root'
    Assert-JsonStringEquals (
        $build.source.commit
    ) $Source.Commit 'runtime source commit'
    Assert-JsonStringEquals (
        $build.source.tree
    ) $Source.Tree 'runtime source tree'
    Assert-JsonArray (
        $build.child_import_inventory
    ) 'full runtime child import inventory'
    Assert-ExactProperties $build.runtime_manifest @(
        'path', 'sha256', 'size_bytes'
    ) 'full runtime manifest binding'
    Assert-ExactProperties $build.runtime_discovery @(
        'path', 'sha256', 'size_bytes'
    ) 'full runtime discovery binding'
    $buildManifestPath = Assert-JsonString (
        $build.runtime_manifest.path
    ) 'runtime manifest path' -NonEmpty
    Assert-PathEqual (
        $buildManifestPath
    ) $ManifestPath 'runtime manifest'
    Assert-FileBinding (
        $ManifestPath
    ) $build.runtime_manifest.sha256 (
        $build.runtime_manifest.size_bytes
    ) 'full runtime manifest' | Out-Null
    $discoveryPath = Assert-JsonString (
        $build.runtime_discovery.path
    ) 'full runtime discovery path' -NonEmpty
    Assert-FileBinding (
        $discoveryPath
    ) $build.runtime_discovery.sha256 (
        $build.runtime_discovery.size_bytes
    ) 'full runtime discovery receipt' | Out-Null
    Assert-ExecutionContract $build.execution 14400 'full runtime'

    $actualInputNames = @($build.inputs.PSObject.Properties.Name | Sort-Object)
    $expectedInputNames = @($InputPaths.Keys | Sort-Object)
    if (
        $actualInputNames.Count -ne $expectedInputNames.Count -or
        (
            Compare-Object -ReferenceObject $expectedInputNames `
                -DifferenceObject $actualInputNames
        )
    ) {
        throw "full runtime input set differs"
    }
    foreach ($name in $expectedInputNames) {
        $expectedHash = Assert-LowerSha256 (
            $build.inputs.$name
        ) "full runtime input $name"
        $state = Get-StableFileState (
            [string] $InputPaths[$name]
        ) "full runtime input $name"
        if ($state.Sha256 -cne $expectedHash) {
            throw "full runtime input $name differs"
        }
    }

    $manifestDocument = Read-StrictJson $ManifestPath 'full runtime manifest'
    $manifest = $manifestDocument.Value
    Assert-ExactProperties $manifest @(
        'execution', 'inputs', 'runtime', 'schema'
    ) 'full runtime manifest'
    Assert-JsonStringEquals (
        $manifest.schema
    ) $RuntimeManifestSchema 'full runtime manifest schema'
    Assert-ExecutionContract $manifest.execution 14400 'full manifest'
    Assert-ExactProperties $manifest.runtime @(
        'modules', 'native_rules', 'python', 'source'
    ) 'full manifest runtime'
    Assert-ExactProperties $manifest.runtime.source @(
        'clean', 'commit', 'root', 'tree'
    ) 'manifest source'
    $manifestSourceRoot = Assert-JsonString (
        $manifest.runtime.source.root
    ) 'manifest source root' -NonEmpty
    Assert-PathEqual $manifestSourceRoot $Source.Root 'manifest source root'
    Assert-JsonBooleanEquals (
        $manifest.runtime.source.clean
    ) $true 'manifest source clean'
    Assert-JsonStringEquals (
        $manifest.runtime.source.commit
    ) $Source.Commit 'manifest source commit'
    Assert-JsonStringEquals (
        $manifest.runtime.source.tree
    ) $Source.Tree 'manifest source tree'
    $runtimePythonPath = Assert-JsonString (
        $manifest.runtime.python.executable
    ) 'runtime Python path' -NonEmpty
    $pythonState = Assert-FileBinding (
        $runtimePythonPath
    ) $manifest.runtime.python.executable_sha256 (
        $manifest.runtime.python.executable_size_bytes
    ) 'runtime Python'
    Assert-PathEqual $pythonState.Path $PythonPath 'runtime Python'

    $manifestInputs = @(
        $manifest.inputs.PSObject.Properties.Name | Sort-Object
    )
    if (
        $manifestInputs.Count -ne $actualInputNames.Count -or
        (
            Compare-Object -ReferenceObject $actualInputNames `
                -DifferenceObject $manifestInputs
        )
    ) {
        throw "runtime manifest input set differs"
    }
    foreach ($name in $actualInputNames) {
        Assert-JsonStringEquals (
            $manifest.inputs.$name
        ) $build.inputs.$name (
            "runtime manifest input $name differs from build receipt"
        )
    }
    return [pscustomobject]@{
        Build = $build
        BuildState = $buildDocument.State
        Manifest = $manifest
        ManifestState = $manifestDocument.State
        PythonState = $pythonState
    }
}

function Assert-AbsentLaunchTargets {
    if (Test-Path -LiteralPath $FullOutputRoot) {
        throw "full output root already exists"
    }
    foreach ($path in @($FullStdoutPath, $FullStderrPath)) {
        if (Test-Path -LiteralPath $path) {
            throw "full CLI capture already exists"
        }
    }
}

$RepositoryRoot = Resolve-RepositoryRoot (
    $RepositoryRoot
) $PSCommandPath 'invoke_e00_full.ps1'
$DesignRoot = Get-AbsolutePath $DesignRoot
$SmokeOutputRoot = Get-AbsolutePath $SmokeOutputRoot
$FullOutputRoot = Get-AbsolutePath $FullOutputRoot
$CaptureRoot = Get-AbsolutePath $CaptureRoot
$AuditReceiptPath = Get-AbsolutePath $AuditReceiptPath
$PythonPath = Get-AbsolutePath $PythonPath
$BookPath = Get-AbsolutePath $BookPath
$EnginePath = Get-AbsolutePath $EnginePath
$CurrentNetPath = Get-AbsolutePath $CurrentNetPath
$TeacherNetPath = Get-AbsolutePath $TeacherNetPath
$VariantConfigPath = Get-AbsolutePath $VariantConfigPath

$FullStdoutPath = Join-Path $CaptureRoot "$FullBatteryId.stdout.json"
$FullStderrPath = Join-Path $CaptureRoot "$FullBatteryId.stderr.bin"
$FullSchedulePath = Join-Path $DesignRoot 'full-schedule\schedule.jsonl'
$FullScheduleReceiptPath = Join-Path (
    $DesignRoot
) 'full-schedule\schedule.receipt.json'
$FullRuntimeManifestPath = Join-Path (
    $DesignRoot
) 'full-runtime\runtime-manifest.json'
$FullRuntimeBuildReceiptPath = Join-Path (
    $DesignRoot
) 'full-runtime\build.receipt.json'
$SmokeSchedulePath = Join-Path $DesignRoot 'smoke-schedule\schedule.jsonl'
$SmokeScheduleReceiptPath = Join-Path (
    $DesignRoot
) 'smoke-schedule\schedule.receipt.json'
$SmokeReceiptPath = Join-Path $SmokeOutputRoot 'receipt.json'
$NativeBindingRoot = Join-Path $DesignRoot 'native-binding\binding'
$PyffishBuildManifestPath = Join-Path (
    $DesignRoot
) 'native-binding\manifest.json'

if (-not (Test-Path -LiteralPath $DesignRoot -PathType Container)) {
    throw "Launch5 design root is absent"
}
if (-not (Test-Path -LiteralPath $SmokeOutputRoot -PathType Container)) {
    throw "Launch5 smoke root is absent"
}
if (-not (Test-Path -LiteralPath $CaptureRoot -PathType Container)) {
    throw "Launch5 capture root is absent"
}
Assert-PathOutside $CaptureRoot $DesignRoot 'capture root'
Assert-PathOutside $CaptureRoot $SmokeOutputRoot 'capture root'
Assert-PathOutside $FullOutputRoot $DesignRoot 'full output root'
Assert-AbsentLaunchTargets

$auditDocument = Read-CanonicalAuditReceipt $AuditReceiptPath
$audit = $auditDocument.Value
$source = Assert-RepositoryState (
    $RepositoryRoot
) $audit.source.commit $audit.source.tree
Assert-NoIgnoredPythonBytecode $source.Root

$bindings = @(
    Get-ChildItem -LiteralPath $NativeBindingRoot -File |
        Where-Object { $_.Name -like 'pyffish*.pyd' }
)
if ($bindings.Count -ne 1) {
    throw "Launch5 native bundle must contain exactly one pyffish binding"
}
$PyffishPath = $bindings[0].FullName

$modulePaths = @{
    tools_init = Join-Path $RepositoryRoot 'tools\__init__.py'
    atomic_mining_init = Join-Path (
        $RepositoryRoot
    ) 'tools\atomic_mining\__init__.py'
    common = Join-Path $RepositoryRoot 'tools\atomic_mining\common.py'
    schedule_builder = Join-Path (
        $RepositoryRoot
    ) 'tools\atomic_mining\build_e00_schedule.py'
    runner = Join-Path (
        $RepositoryRoot
    ) 'tools\atomic_mining\run_e00_source.py'
    uci_session = Join-Path (
        $RepositoryRoot
    ) 'tools\atomic_mining\uci_session.py'
    atomic_outcome_helper = Join-Path (
        $RepositoryRoot
    ) 'tools\atomic_mining\atomic_outcome_helper.py'
    binding_builder = Join-Path (
        $RepositoryRoot
    ) 'tools\atomic_mining\build_atomic_outcome_binding.py'
    owned_process = Join-Path (
        $RepositoryRoot
    ) 'tools\atomic_mining\owned_process.py'
}
$inputPaths = @{
    tools_init = $modulePaths.tools_init
    atomic_mining_init = $modulePaths.atomic_mining_init
    common = $modulePaths.common
    schedule_builder = $modulePaths.schedule_builder
    runner = $modulePaths.runner
    uci_session = $modulePaths.uci_session
    atomic_outcome_helper = $modulePaths.atomic_outcome_helper
    binding_builder = $modulePaths.binding_builder
    owned_process = $modulePaths.owned_process
    book = $BookPath
    engine = $EnginePath
    current_net = $CurrentNetPath
    teacher_net = $TeacherNetPath
    variant_config = $VariantConfigPath
    pyffish = $PyffishPath
    pyffish_build_manifest = $PyffishBuildManifestPath
    schedule = $FullSchedulePath
    schedule_receipt = $FullScheduleReceiptPath
}

$fullSchedule = Assert-ScheduleBundle (
    $FullSchedulePath
) $FullScheduleReceiptPath (
    $audit.full.schedule_receipt_sha256
) 84 168 'atomic-e00-src-full-v3-launch5-20260725' 'full schedule'
$runtime = Assert-RuntimeBundle (
    $FullRuntimeManifestPath
) $FullRuntimeBuildReceiptPath (
    $audit.full.runtime_build_receipt_sha256
) $source $inputPaths

$smokeScheduleReceiptState = Get-StableFileState (
    $SmokeScheduleReceiptPath
) 'smoke schedule receipt'
$smokeSchedule = Assert-ScheduleBundle (
    $SmokeSchedulePath
) $SmokeScheduleReceiptPath (
    $smokeScheduleReceiptState.Sha256
) 1 2 'atomic-e00-src-smoke-v3-launch5-20260725' 'smoke schedule'
$smoke = Assert-ExecutionReceipt (
    $SmokeReceiptPath
) $audit.smoke.execution_receipt_sha256 (
    $SmokeBatteryId
) $SmokeSchedulePath $SmokeScheduleReceiptPath 1 2 (
    $SmokeOutputRoot
) 'smoke'

Assert-AbsentLaunchTargets

$arguments = @(
    '-B', '-m', 'tools.atomic_mining.run_e00_source',
    '--schedule', $FullSchedulePath,
    '--schedule-receipt', $FullScheduleReceiptPath,
    '--output-dir', $FullOutputRoot,
    '--experiment-id', $ExperimentId,
    '--battery-id', $FullBatteryId,
    '--runtime-manifest', $FullRuntimeManifestPath,
    '--runtime-manifest-sha256', $runtime.ManifestState.Sha256,
    '--book', $BookPath,
    '--book-sha256', $runtime.Build.inputs.book,
    '--engine', $EnginePath,
    '--engine-sha256', $runtime.Build.inputs.engine,
    '--current-net', $CurrentNetPath,
    '--current-net-sha256', $runtime.Build.inputs.current_net,
    '--teacher-net', $TeacherNetPath,
    '--teacher-net-sha256', $runtime.Build.inputs.teacher_net,
    '--variant-config', $VariantConfigPath,
    '--variant-config-sha256', $runtime.Build.inputs.variant_config,
    '--pyffish', $PyffishPath,
    '--pyffish-sha256', $runtime.Build.inputs.pyffish,
    '--pyffish-build-manifest', $PyffishBuildManifestPath,
    '--pyffish-build-manifest-sha256',
        $runtime.Build.inputs.pyffish_build_manifest,
    '--runner', $modulePaths.runner,
    '--runner-sha256', $runtime.Build.inputs.runner,
    '--rules-source-root', $RepositoryRoot,
    '--rules-source-commit', $source.Commit,
    '--tools-init-sha256', $runtime.Build.inputs.tools_init,
    '--atomic-mining-init-sha256',
        $runtime.Build.inputs.atomic_mining_init,
    '--common-sha256', $runtime.Build.inputs.common,
    '--schedule-builder-sha256', $runtime.Build.inputs.schedule_builder,
    '--uci-session-sha256', $runtime.Build.inputs.uci_session,
    '--atomic-outcome-helper-sha256',
        $runtime.Build.inputs.atomic_outcome_helper,
    '--binding-builder-sha256', $runtime.Build.inputs.binding_builder,
    '--owned-process-sha256', $runtime.Build.inputs.owned_process,
    '--threads', '1',
    '--maximum-plies', '1024',
    '--command-timeout-seconds', '120',
    '--maximum-wall-seconds', '14400',
    '--maximum-game-wall-seconds', '1800'
)

$validation = [ordered]@{
    schema = $ValidationSchema
    status = 'validated-not-started'
    experiment_id = $ExperimentId
    battery_id = $FullBatteryId
    source = [ordered]@{
        commit = $source.Commit
        tree = $source.Tree
    }
    authorization = [ordered]@{
        path = $auditDocument.State.Path
        sha256 = $auditDocument.State.Sha256
    }
    smoke_receipt_sha256 = $smoke.State.Sha256
    full_schedule_sha256 = $fullSchedule.ScheduleState.Sha256
    full_schedule_receipt_sha256 = $fullSchedule.ReceiptState.Sha256
    full_runtime_manifest_sha256 = $runtime.ManifestState.Sha256
    full_runtime_build_receipt_sha256 = $runtime.BuildState.Sha256
    output_root = $FullOutputRoot
    stdout_path = $FullStdoutPath
    stderr_path = $FullStderrPath
    argv = @($arguments)
}

if ($ValidateOnly) {
    $validation | ConvertTo-Json -Depth 8 -Compress
    exit 0
}

# Claiming the two create-new capture files is deliberately the first
# mutating operation in the launcher.
Assert-AbsentLaunchTargets
$exitCode = Invoke-CapturedProcessCreateNew `
    -FilePath $PythonPath `
    -ArgumentList $arguments `
    -WorkingDirectory $RepositoryRoot `
    -StandardOutputPath $FullStdoutPath `
    -StandardErrorPath $FullStderrPath
if ($exitCode -ne 0) {
    throw "full runner exited $exitCode; Launch5 full is terminal"
}
if (
    -not (Test-Path -LiteralPath $FullStderrPath -PathType Leaf) -or
    (Get-Item -LiteralPath $FullStderrPath).Length -ne 0
) {
    throw "full runner stderr is not byte-empty; Launch5 full is terminal"
}
$summary = Read-StrictJson $FullStdoutPath 'full runner stdout'
$fullReceiptPath = Join-Path $FullOutputRoot 'receipt.json'
$fullReceiptState = Get-StableFileState $fullReceiptPath 'full execution receipt'
$full = Assert-ExecutionReceipt (
    $fullReceiptPath
) $fullReceiptState.Sha256 $FullBatteryId (
    $FullSchedulePath
) $FullScheduleReceiptPath 84 168 $FullOutputRoot 'full' -RequireComplete
Assert-ExactProperties $summary.Value @(
    'draws',
    'games',
    'games_sha256',
    'losses_current',
    'pairs',
    'receipt_sha256',
    'time_losses',
    'wins_current'
) 'full runner summary'
Assert-JsonIntegerEquals $summary.Value.pairs 84 'full runner summary pairs'
Assert-JsonIntegerEquals $summary.Value.games 168 'full runner summary games'
foreach ($field in @(
    'draws', 'losses_current', 'time_losses', 'wins_current'
)) {
    Assert-JsonIntegerMinimum (
        $summary.Value.$field
    ) 0 "full runner summary $field" | Out-Null
}
Assert-LowerSha256 (
    $summary.Value.games_sha256
) 'full runner summary games SHA-256' | Out-Null
Assert-JsonStringEquals (
    $summary.Value.receipt_sha256
) $full.State.Sha256 'full runner summary receipt SHA-256'

[ordered]@{
    schema = $ValidationSchema
    status = 'committed'
    experiment_id = $ExperimentId
    battery_id = $FullBatteryId
    receipt_sha256 = $full.State.Sha256
} | ConvertTo-Json -Depth 4 -Compress
