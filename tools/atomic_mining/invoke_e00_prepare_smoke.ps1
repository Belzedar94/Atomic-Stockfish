[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $ExpectedSourceCommit,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $ExpectedSourceTree,

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
$SmokeSeed = 'atomic-e00-src-smoke-v3-launch5-20260725'
$FullSeed = 'atomic-e00-src-full-v3-launch5-20260725'
$DesignInventorySchema = 'atomic-e00-launch5-design-inventory-v1'
$DesignReceiptSchema = 'atomic-e00-launch5-design-receipt-v1'
$ExecutionReceiptSchema = 'atomic-e00-execution-receipt-v3'
$GameSchema = 'atomic-e00-game-v3'
$EngineEvidenceSchema = 'atomic-e00-engine-evidence-v3'
$RefereeEvidenceSchema = 'atomic-e00-native-referee-evidence-v2'
$VerifierEvidenceSchema = 'atomic-e00-native-verifier-evidence-v2'
$OwnedProcessSchema = 'atomic-e00-owned-process-v1'
$RuntimeBuildSummarySchema =
    'atomic-e00-runtime-manifest-build-summary-v1'
$RuntimeBuildReceiptSchema =
    'atomic-e00-runtime-manifest-build-receipt-v1'
$RuntimeManifestSchema = 'atomic-e00-runtime-manifest-v2'
$ScheduleReceiptSchema = 'atomic-e00-schedule-receipt-v1'
$NativeBuildSummarySchema =
    'atomic-e00-native-outcome-build-cli-summary-v1'
$EmptySha256 = (
    'e3b0c44298fc1c149afbf4c8996fb924' +
    '27ae41e4649b934ca495991b7852b855'
)
$HandshakePreamble = @(
    'Atomic-Stockfish 1.0.3 by the Atomic-Stockfish developers ' +
        '(see AUTHORS file)'
)
$HandshakeIdentityOrder = @('name', 'author')

$PinnedInputs = [ordered]@{
    book = '28ed51c2f42e723d5e127d2d3f21c0bfa4a9b318615afdb299b93ea62dea2b1e'
    engine = '86d2bb669ff2a56123a78fd1892c2acf8b4294fb1464da049ddb30877ce5127f'
    current_net = '0797cdbaf857aa4552d4eab301227ce3ba7c23a731973c255bb1dfc763659e5b'
    teacher_net = '99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6'
    variant_config = '30a4779fde75b5259f732a148872aa81dca96da7c766238d0153a591d6624e37'
}

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

function Assert-LowerSha256([object] $Value, [string] $Label) {
    if (
        -not ($Value -is [string]) -or
        $Value -cnotmatch '^[0-9a-f]{64}$'
    ) {
        throw "$Label must be one lowercase SHA-256"
    }
    return [string] $Value
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

function Assert-JsonArray([object] $Value, [string] $Label) {
    if (-not ($Value -is [System.Array])) {
        throw "$Label must be a JSON array"
    }
}

function Assert-JsonStringArrayEquals(
    [object] $Value,
    [string[]] $Expected,
    [string] $Label
) {
    Assert-JsonArray $Value $Label
    $actual = @($Value)
    if ($actual.Count -ne $Expected.Count) {
        throw "$Label count differs"
    }
    for ($index = 0; $index -lt $actual.Count; $index++) {
        Assert-JsonStringEquals (
            $actual[$index]
        ) $Expected[$index] "$Label item $index"
    }
}

function ConvertTo-CanonicalJsonText([object] $Value) {
    if ($null -eq $Value) {
        return 'null'
    }
    if ($Value -is [string]) {
        return [string] (ConvertTo-Json -InputObject $Value -Compress)
    }
    if ($Value -is [bool]) {
        if ($Value) {
            return 'true'
        }
        return 'false'
    }
    if (Test-JsonIntegralType $Value) {
        return ([decimal] $Value).ToString(
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    }
    if (
        $Value -is [single] -or
        $Value -is [double] -or
        $Value -is [decimal]
    ) {
        $number = Get-JsonDouble $Value 'canonical JSON number'
        $text = $number.ToString(
            'R',
            [System.Globalization.CultureInfo]::InvariantCulture
        ).ToLowerInvariant()
        if ($text -cnotmatch '[.e]') {
            $text += '.0'
        }
        return $text
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $names = @($Value.Keys | ForEach-Object { [string] $_ })
        [Array]::Sort($names, [System.StringComparer]::Ordinal)
        $parts = @()
        foreach ($name in $names) {
            $parts += (
                (ConvertTo-CanonicalJsonText $name) + ':' +
                (ConvertTo-CanonicalJsonText $Value[$name])
            )
        }
        return '{' + ($parts -join ',') + '}'
    }
    if (
        $Value -is [System.Array] -or
        $Value -is [System.Collections.IList]
    ) {
        $parts = @(
            $Value | ForEach-Object {
                ConvertTo-CanonicalJsonText $_
            }
        )
        return '[' + ($parts -join ',') + ']'
    }
    $properties = @($Value.PSObject.Properties)
    if ($properties.Count -gt 0) {
        $names = @($properties.Name)
        [Array]::Sort($names, [System.StringComparer]::Ordinal)
        $parts = @()
        foreach ($name in $names) {
            $parts += (
                (ConvertTo-CanonicalJsonText $name) + ':' +
                (ConvertTo-CanonicalJsonText $Value.$name)
            )
        }
        return '{' + ($parts -join ',') + '}'
    }
    throw "unsupported canonical JSON value type $($Value.GetType().FullName)"
}

function Get-CanonicalJsonSha256([object] $Value) {
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $text = (ConvertTo-CanonicalJsonText $Value) + "`n"
    return Get-ByteArraySha256 ($utf8.GetBytes($text))
}

function Get-NamespacedJsonSha256(
    [string] $Namespace,
    [object] $Value
) {
    return Get-CanonicalJsonSha256 ([ordered]@{
        namespace = $Namespace
        value = $Value
    })
}

function Assert-ExactProperties(
    [object] $Value,
    [string[]] $Expected,
    [string] $Label
) {
    if ($null -eq $Value) {
        throw "$Label must be an object"
    }
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $wanted = @($Expected | Sort-Object)
    if (
        $actual.Count -ne $wanted.Count -or
        (Compare-Object -ReferenceObject $wanted -DifferenceObject $actual)
    ) {
        throw "$Label fields differ"
    }
}

function Assert-PathOutside(
    [string] $Path,
    [string] $ForbiddenRoot,
    [string] $Label
) {
    $candidate = (Get-AbsolutePath $Path).TrimEnd('\', '/')
    $root = (Get-AbsolutePath $ForbiddenRoot).TrimEnd('\', '/')
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
        throw "$Label must remain outside $ForbiddenRoot"
    }
}

function Assert-FreshDisjointRoots([hashtable] $Roots) {
    $names = @($Roots.Keys)
    for ($left = 0; $left -lt $names.Count; $left++) {
        $leftName = $names[$left]
        $leftPath = Get-AbsolutePath ([string] $Roots[$leftName])
        if (Test-Path -LiteralPath $leftPath) {
            throw "$leftName already exists; this Launch5 root is terminal"
        }
        for ($right = $left + 1; $right -lt $names.Count; $right++) {
            $rightName = $names[$right]
            $rightPath = Get-AbsolutePath ([string] $Roots[$rightName])
            Assert-PathOutside $leftPath $rightPath $leftName
            Assert-PathOutside $rightPath $leftPath $rightName
        }
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

function Get-StableFileState([string] $Path, [string] $Label) {
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
        throw "$Label changed while hashing"
    }
    return [pscustomobject]@{
        Path = $after.FullName
        Sha256 = Get-ByteArraySha256 $payload
        SizeBytes = [long] $payload.Length
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
        if ($text.Substring(0, $text.Length - 1).Contains("`n")) {
            throw "$Label must contain one compact JSON line"
        }
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

function Read-StrictJsonLines([string] $Path, [string] $Label) {
    $state = Get-StableFileState $Path $Label
    $payload = [System.IO.File]::ReadAllBytes($state.Path)
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
        throw "$Label is not strict newline-terminated UTF-8 JSONL"
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    try {
        $text = $utf8.GetString($payload)
        $lines = @($text.TrimEnd("`n").Split("`n"))
        $rows = @()
        foreach ($line in $lines) {
            if ($line.Length -eq 0) {
                throw "$Label contains an empty row"
            }
            $row = $line | ConvertFrom-Json -ErrorAction Stop
            if ($null -eq $row -or $row -is [System.Array]) {
                throw "$Label row must contain one JSON object"
            }
            $rows += $row
        }
    }
    catch {
        throw "$Label is not strict UTF-8 JSONL"
    }
    return [pscustomobject]@{
        Rows = $rows
        State = $state
    }
}

function Write-NewCompactJson(
    [string] $Path,
    [object] $Value,
    [string] $Label
) {
    $text = ($Value | ConvertTo-Json -Compress -Depth 64) + "`n"
    if ($text.Contains("`r")) {
        throw "$Label serialization contains CR"
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $payload = $utf8.GetBytes($text)
    $stream = $null
    try {
        $stream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $stream.Write($payload, 0, $payload.Length)
        $stream.Flush($true)
    }
    finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
    $document = Read-StrictJson $Path $Label
    if ($document.Text -cne $text) {
        throw "$Label differs after create-new publication"
    }
    return $document.State
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
                [void] $builder.Append([char] '\', (2 * $backslashes))
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
            throw "child process did not start"
        }
        $stdoutTask = $process.StandardOutput.BaseStream.CopyToAsync($stdout)
        $stderrTask = $process.StandardError.BaseStream.CopyToAsync($stderr)
        $process.WaitForExit()
        [void] $stdoutTask.GetAwaiter().GetResult()
        [void] $stderrTask.GetAwaiter().GetResult()
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

function Invoke-StrictPythonCli(
    [string] $Step,
    [string[]] $Arguments
) {
    $stdoutPath = Join-Path $CaptureRoot "$Step.stdout.json"
    $stderrPath = Join-Path $CaptureRoot "$Step.stderr.bin"
    $exitCode = Invoke-CapturedProcessCreateNew `
        -FilePath $PythonPath `
        -ArgumentList $Arguments `
        -WorkingDirectory $RepositoryRoot `
        -StandardOutputPath $stdoutPath `
        -StandardErrorPath $stderrPath
    if ($exitCode -ne 0) {
        throw "$Step exited $exitCode; Launch5 is terminal"
    }
    $stderrState = Get-StableFileState $stderrPath "$Step stderr"
    if (
        $stderrState.SizeBytes -ne 0 -or
        $stderrState.Sha256 -cne $EmptySha256
    ) {
        throw "$Step stderr is not byte-empty; Launch5 is terminal"
    }
    return Read-StrictJson $stdoutPath "$Step stdout"
}

function Assert-NoIgnoredPythonBytecode([string] $Root) {
    $toolsRoot = Join-Path $Root 'tools'
    if (-not (Test-Path -LiteralPath $toolsRoot -PathType Container)) {
        throw "tools source root is absent"
    }
    $forbidden = @(
        Get-ChildItem -LiteralPath $toolsRoot -Recurse -Force |
            Where-Object {
                $_.Name -ceq '__pycache__' -or
                $_.Extension -ceq '.pyc' -or
                $_.Extension -ceq '.pyo'
            }
    )
    if ($forbidden.Count -ne 0) {
        throw "ignored Python bytecode/cache is present"
    }
}

function Assert-RepositoryState() {
    if (-not (Test-Path -LiteralPath $RepositoryRoot -PathType Container)) {
        throw "repository root is absent"
    }
    $resolved = (Resolve-Path -LiteralPath $RepositoryRoot).Path
    $dirty = @(
        & git -C $resolved status --porcelain=v1 --untracked-files=all
    )
    if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) {
        throw "repository is not exactly clean"
    }
    $commit = (& git -C $resolved rev-parse HEAD).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) {
        throw "cannot inspect repository commit"
    }
    $tree = (
        & git -C $resolved rev-parse 'HEAD^{tree}'
    ).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) {
        throw "cannot inspect repository tree"
    }
    if (
        $commit -cne $ExpectedSourceCommit -or
        $tree -cne $ExpectedSourceTree
    ) {
        throw "repository commit/tree differs from the frozen request"
    }
    Assert-NoIgnoredPythonBytecode $resolved
    return [pscustomobject]@{
        Root = $resolved
        Commit = $commit
        Tree = $tree
    }
}

function Get-InputStates() {
    $paths = [ordered]@{
        book = $BookPath
        engine = $EnginePath
        current_net = $CurrentNetPath
        teacher_net = $TeacherNetPath
        variant_config = $VariantConfigPath
        python = $PythonPath
    }
    $states = [ordered]@{}
    foreach ($name in $paths.Keys) {
        $state = Get-StableFileState $paths[$name] "pinned $name"
        if (
            $name -ne 'python' -and
            $state.Sha256 -cne $PinnedInputs[$name]
        ) {
            throw "pinned $name SHA-256 differs"
        }
        $states[$name] = $state
    }
    return $states
}

function Assert-InputStatesEqual(
    [System.Collections.IDictionary] $Expected,
    [System.Collections.IDictionary] $Actual,
    [string] $Label
) {
    foreach ($name in $Expected.Keys) {
        if (
            $Expected[$name].Sha256 -cne $Actual[$name].Sha256 -or
            $Expected[$name].SizeBytes -ne $Actual[$name].SizeBytes -or
            -not [string]::Equals(
                $Expected[$name].Path,
                $Actual[$name].Path,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "$Label $name changed"
        }
    }
}

function Get-ModulePaths([string] $Root) {
    return [ordered]@{
        tools_init = Join-Path $Root 'tools\__init__.py'
        atomic_mining_init = Join-Path $Root 'tools\atomic_mining\__init__.py'
        common = Join-Path $Root 'tools\atomic_mining\common.py'
        schedule_builder =
            Join-Path $Root 'tools\atomic_mining\build_e00_schedule.py'
        runner = Join-Path $Root 'tools\atomic_mining\run_e00_source.py'
        uci_session = Join-Path $Root 'tools\atomic_mining\uci_session.py'
        atomic_outcome_helper =
            Join-Path $Root 'tools\atomic_mining\atomic_outcome_helper.py'
        binding_builder =
            Join-Path $Root 'tools\atomic_mining\build_atomic_outcome_binding.py'
        owned_process =
            Join-Path $Root 'tools\atomic_mining\owned_process.py'
    }
}

function Get-ModuleStates([System.Collections.IDictionary] $Paths) {
    $states = [ordered]@{}
    foreach ($name in $Paths.Keys) {
        $states[$name] = Get-StableFileState (
            $Paths[$name]
        ) "source module $name"
    }
    return $states
}

function Assert-ScheduleSummary(
    [object] $Summary,
    [long] $Pairs,
    [long] $Games,
    [string] $SchedulePath,
    [string] $ReceiptPath,
    [string] $Label
) {
    Assert-ExactProperties $Summary @(
        'book_root_count',
        'book_sha256',
        'game_count',
        'pair_count',
        'receipt_sha256',
        'receipt_size_bytes',
        'schedule_sha256',
        'schedule_size_bytes'
    ) "$Label summary"
    Assert-JsonIntegerEquals $Summary.pair_count $Pairs (
        "$Label summary pair_count"
    )
    Assert-JsonIntegerEquals $Summary.game_count $Games (
        "$Label summary game_count"
    )
    Assert-JsonIntegerMinimum $Summary.book_root_count 1 (
        "$Label summary book_root_count"
    ) | Out-Null
    Assert-JsonStringEquals $Summary.book_sha256 $PinnedInputs.book (
        "$Label summary book_sha256"
    )
    Assert-FileBinding (
        $SchedulePath
    ) $Summary.schedule_sha256 $Summary.schedule_size_bytes "$Label schedule" |
        Out-Null
    Assert-FileBinding (
        $ReceiptPath
    ) $Summary.receipt_sha256 $Summary.receipt_size_bytes "$Label receipt" |
        Out-Null
}

function Assert-ScheduleReceipt(
    [string] $Path,
    [string] $Seed,
    [long] $Pairs,
    [long] $Games,
    [object[]] $ExpectedTimeControls,
    [string] $Label
) {
    $document = Read-StrictJson $Path "$Label receipt"
    $receipt = $document.Value
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
    Assert-JsonStringEquals $receipt.schema $ScheduleReceiptSchema (
        "$Label receipt schema"
    )
    Assert-JsonStringEquals (
        $receipt.schedule_schema
    ) 'atomic-e00-schedule-v1' "$Label receipt schedule_schema"
    Assert-JsonStringEquals $receipt.seed $Seed "$Label receipt seed"
    Assert-JsonIntegerEquals $receipt.counts.pairs $Pairs (
        "$Label receipt counts.pairs"
    )
    Assert-JsonIntegerEquals $receipt.counts.games $Games (
        "$Label receipt counts.games"
    )
    Assert-JsonIntegerEquals (
        $receipt.counts.strata
    ) $ExpectedTimeControls.Count "$Label receipt counts.strata"
    Assert-JsonStringEquals (
        $receipt.book.sha256
    ) $PinnedInputs.book "$Label receipt book.sha256"
    Assert-JsonIntegerEquals (
        $receipt.book.root_count
    ) 6199 "$Label receipt book.root_count"
    $actual = @($receipt.time_controls)
    if ($actual.Count -ne $ExpectedTimeControls.Count) {
        throw "$Label time-control count differs"
    }
    for ($index = 0; $index -lt $actual.Count; $index++) {
        Assert-ExactProperties $actual[$index] @(
            'base_ms', 'increment_ms', 'name', 'pairs'
        ) "$Label time-control $index"
        Assert-JsonStringEquals (
            $actual[$index].name
        ) $ExpectedTimeControls[$index].name "$Label time-control $index name"
        foreach ($field in @('base_ms', 'increment_ms', 'pairs')) {
            Assert-JsonIntegerEquals (
                $actual[$index].$field
            ) $ExpectedTimeControls[$index].$field (
                "$Label time-control $index $field"
            )
        }
    }
    return $document.State
}

function Assert-RuntimeSummary(
    [object] $Summary,
    [string] $RuntimeRoot,
    [long] $MaximumWallSeconds,
    [object] $Source,
    [System.Collections.IDictionary] $ExpectedInputs,
    [string] $Label
) {
    Assert-ExactProperties $Summary @(
        'build_receipt',
        'child_import_inventory_rows',
        'output_dir',
        'runtime_discovery',
        'runtime_manifest',
        'schema'
    ) "$Label runtime summary"
    Assert-JsonStringEquals $Summary.schema $RuntimeBuildSummarySchema (
        "$Label runtime summary schema"
    )
    $childImportRows = Assert-JsonIntegerMinimum (
        $Summary.child_import_inventory_rows
    ) 1 "$Label runtime child import inventory rows"
    $expectedManifest = Join-Path $RuntimeRoot 'runtime-manifest.json'
    $expectedBuildReceipt = Join-Path $RuntimeRoot 'build.receipt.json'
    $expectedDiscovery = Join-Path (
        $RuntimeRoot
    ) 'runtime-discovery.receipt.json'
    $summaryOutputDir = Assert-JsonString (
        $Summary.output_dir
    ) "$Label runtime output_dir" -NonEmpty
    $summaryManifestPath = Assert-JsonString (
        $Summary.runtime_manifest.path
    ) "$Label runtime manifest path" -NonEmpty
    $summaryBuildReceiptPath = Assert-JsonString (
        $Summary.build_receipt.path
    ) "$Label runtime build receipt path" -NonEmpty
    $summaryDiscoveryPath = Assert-JsonString (
        $Summary.runtime_discovery.path
    ) "$Label runtime discovery path" -NonEmpty
    if (
        -not [string]::Equals(
            (Get-AbsolutePath $summaryOutputDir),
            (Get-AbsolutePath $RuntimeRoot),
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not [string]::Equals(
            (Get-AbsolutePath $summaryManifestPath),
            (Get-AbsolutePath $expectedManifest),
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not [string]::Equals(
            (Get-AbsolutePath $summaryBuildReceiptPath),
            (Get-AbsolutePath $expectedBuildReceipt),
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not [string]::Equals(
            (Get-AbsolutePath $summaryDiscoveryPath),
            (Get-AbsolutePath $expectedDiscovery),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "$Label runtime paths differ"
    }
    Assert-FileBinding (
        $expectedManifest
    ) $Summary.runtime_manifest.sha256 (
        $Summary.runtime_manifest.size_bytes
    ) "$Label runtime manifest" | Out-Null
    $buildState = Assert-FileBinding (
        $expectedBuildReceipt
    ) $Summary.build_receipt.sha256 (
        $Summary.build_receipt.size_bytes
    ) "$Label runtime build receipt"
    Assert-FileBinding (
        $expectedDiscovery
    ) $Summary.runtime_discovery.sha256 (
        $Summary.runtime_discovery.size_bytes
    ) "$Label runtime discovery" | Out-Null
    $build = (Read-StrictJson (
        $expectedBuildReceipt
    ) "$Label runtime build receipt").Value
    Assert-JsonStringEquals $build.schema $RuntimeBuildReceiptSchema (
        "$Label runtime build schema"
    )
    Assert-JsonStringEquals $build.source.commit $Source.Commit (
        "$Label runtime build source commit"
    )
    Assert-JsonStringEquals $build.source.tree $Source.Tree (
        "$Label runtime build source tree"
    )
    $buildSourceRoot = Assert-JsonString (
        $build.source.root
    ) "$Label runtime build source root" -NonEmpty
    if (
        -not [string]::Equals(
            (Get-AbsolutePath $buildSourceRoot),
            (Get-AbsolutePath ([string] $Source.Root)),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "$Label runtime build source root differs"
    }
    Assert-JsonArray $build.child_import_inventory (
        "$Label runtime child import inventory"
    )
    if (@($build.child_import_inventory).Count -ne $childImportRows) {
        throw "$Label runtime child import inventory count differs"
    }
    Assert-JsonIntegerEquals $build.execution.threads 1 (
        "$Label runtime execution threads"
    )
    Assert-JsonIntegerEquals $build.execution.maximum_plies 1024 (
        "$Label runtime maximum plies"
    )
    Assert-JsonNumberEquals (
        $build.execution.command_timeout_seconds
    ) 120.0 "$Label runtime command timeout"
    Assert-JsonNumberEquals (
        $build.execution.maximum_wall_seconds
    ) $MaximumWallSeconds "$Label runtime maximum wall seconds"
    Assert-JsonNumberEquals (
        $build.execution.maximum_game_wall_seconds
    ) 1800.0 "$Label runtime maximum game wall seconds"
    Assert-JsonStringEquals (
        $build.runtime_manifest.sha256
    ) $Summary.runtime_manifest.sha256 "$Label runtime manifest SHA binding"
    Assert-JsonIntegerEquals (
        $build.runtime_manifest.size_bytes
    ) (Get-JsonInt64 (
        $Summary.runtime_manifest.size_bytes
    ) "$Label runtime summary manifest size") (
        "$Label runtime manifest size binding"
    )
    Assert-JsonStringEquals (
        $build.runtime_discovery.sha256
    ) $Summary.runtime_discovery.sha256 "$Label runtime discovery SHA binding"
    Assert-JsonIntegerEquals (
        $build.runtime_discovery.size_bytes
    ) (Get-JsonInt64 (
        $Summary.runtime_discovery.size_bytes
    ) "$Label runtime summary discovery size") (
        "$Label runtime discovery size binding"
    )
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
    $actualOptions = @($build.execution.uci_options)
    if ($actualOptions.Count -ne $expectedOptions.Count) {
        throw "$Label runtime UCI option count differs"
    }
    for ($index = 0; $index -lt $expectedOptions.Count; $index++) {
        Assert-ExactProperties $actualOptions[$index] @(
            'name', 'value'
        ) "$Label runtime UCI option $index"
        Assert-JsonStringEquals (
            $actualOptions[$index].name
        ) $expectedOptions[$index][0] "$Label runtime UCI option $index name"
        $expectedValue = $expectedOptions[$index][1]
        if ($expectedValue -is [bool]) {
            Assert-JsonBooleanEquals (
                $actualOptions[$index].value
            ) $expectedValue "$Label runtime UCI option $index value"
        }
        elseif (Test-JsonIntegralType $expectedValue) {
            Assert-JsonIntegerEquals (
                $actualOptions[$index].value
            ) $expectedValue "$Label runtime UCI option $index value"
        }
        else {
            Assert-JsonStringEquals (
                $actualOptions[$index].value
            ) $expectedValue "$Label runtime UCI option $index value"
        }
    }
    $manifest = (Read-StrictJson (
        $expectedManifest
    ) "$Label runtime manifest").Value
    Assert-JsonStringEquals $manifest.schema $RuntimeManifestSchema (
        "$Label runtime manifest schema"
    )
    $actualBuildInputNames = @(
        $build.inputs.PSObject.Properties.Name | Sort-Object
    )
    $actualManifestInputNames = @(
        $manifest.inputs.PSObject.Properties.Name | Sort-Object
    )
    $expectedInputNames = @($ExpectedInputs.Keys | Sort-Object)
    if (
        $actualBuildInputNames.Count -ne $expectedInputNames.Count -or
        $actualManifestInputNames.Count -ne $expectedInputNames.Count -or
        (
            Compare-Object -ReferenceObject $expectedInputNames `
                -DifferenceObject $actualBuildInputNames
        ) -or
        (
            Compare-Object -ReferenceObject $expectedInputNames `
                -DifferenceObject $actualManifestInputNames
        )
    ) {
        throw "$Label runtime input set differs"
    }
    foreach ($name in $ExpectedInputs.Keys) {
        $inputSha256 = $build.inputs.$name
        Assert-JsonStringEquals (
            $inputSha256
        ) $ExpectedInputs[$name].Sha256 "$Label runtime build input $name"
        Assert-JsonStringEquals (
            $manifest.inputs.$name
        ) $ExpectedInputs[$name].Sha256 "$Label runtime manifest input $name"
        Get-StableFileState (
            $ExpectedInputs[$name].Path
        ) "$Label runtime input $name" | Out-Null
    }
    return [pscustomobject]@{
        ManifestPath = $expectedManifest
        ManifestState = Get-StableFileState (
            $expectedManifest
        ) "$Label runtime manifest"
        BuildReceiptPath = $expectedBuildReceipt
        BuildReceiptState = $buildState
    }
}

function Get-DesignInventoryEntries([string] $Root) {
    $rootPath = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\', '/')
    $items = @(
        Get-ChildItem -LiteralPath $rootPath -Recurse -Force
    )
    foreach ($item in $items) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw "design contains a reparse point"
        }
    }
    $relativePaths = @(
        $items |
            Where-Object {
                -not $_.PSIsContainer -and
                -not [string]::Equals(
                    $_.FullName,
                    (Join-Path $rootPath 'design.inventory.json'),
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -and
                -not [string]::Equals(
                    $_.FullName,
                    (Join-Path $rootPath 'design.receipt.json'),
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            } |
            ForEach-Object {
                $_.FullName.Substring($rootPath.Length + 1).Replace('\', '/')
            }
    )
    [Array]::Sort($relativePaths, [System.StringComparer]::Ordinal)
    $entries = @()
    foreach ($relative in $relativePaths) {
        $path = Join-Path $rootPath $relative.Replace('/', '\')
        $state = Get-StableFileState $path "design file $relative"
        $entries += [ordered]@{
            path = $relative
            sha256 = $state.Sha256
            size_bytes = $state.SizeBytes
        }
    }
    return $entries
}

function Get-DesignDirectoryPaths([string] $Root) {
    $rootPath = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\', '/')
    $directories = @(
        Get-ChildItem -LiteralPath $rootPath -Recurse -Directory -Force |
            ForEach-Object {
                if (
                    $_.Attributes -band
                        [System.IO.FileAttributes]::ReparsePoint
                ) {
                    throw "design contains a directory reparse point"
                }
                $_.FullName.Substring($rootPath.Length + 1).Replace('\', '/')
            }
    )
    [Array]::Sort($directories, [System.StringComparer]::Ordinal)
    return $directories
}

function Publish-DesignSeal(
    [object] $Source,
    [System.Collections.IDictionary] $Inputs,
    [object] $Native,
    [object] $SmokeSchedule,
    [object] $FullSchedule,
    [object] $SmokeRuntime,
    [object] $FullRuntime
) {
    $inventoryPath = Join-Path $DesignRoot 'design.inventory.json'
    $receiptPath = Join-Path $DesignRoot 'design.receipt.json'
    $entries = @(Get-DesignInventoryEntries $DesignRoot)
    $directories = @(Get-DesignDirectoryPaths $DesignRoot)
    $inventory = [ordered]@{
        directories = $directories
        directory_count = [long] $directories.Count
        entries = $entries
        file_count = [long] $entries.Count
        schema = $DesignInventorySchema
    }
    $inventoryState = Write-NewCompactJson (
        $inventoryPath
    ) $inventory 'Launch5 design inventory'
    $inputBindings = [ordered]@{}
    $inputNames = @($Inputs.Keys)
    [Array]::Sort($inputNames, [System.StringComparer]::Ordinal)
    foreach ($name in $inputNames) {
        $inputBindings[$name] = [ordered]@{
            path = $Inputs[$name].Path
            sha256 = $Inputs[$name].Sha256
            size_bytes = $Inputs[$name].SizeBytes
        }
    }
    $receipt = [ordered]@{
        experiment_id = $ExperimentId
        full = [ordered]@{
            battery_id = $FullBatteryId
            runtime_build_receipt_sha256 =
                $FullRuntime.BuildReceiptState.Sha256
            schedule_receipt_sha256 = $FullSchedule.ReceiptState.Sha256
            seed = $FullSeed
        }
        inputs = $inputBindings
        inventory = [ordered]@{
            path = 'design.inventory.json'
            sha256 = $inventoryState.Sha256
            size_bytes = $inventoryState.SizeBytes
        }
        native = [ordered]@{
            build_manifest_sha256 = $Native.ManifestState.Sha256
            pyffish_sha256 = $Native.BindingState.Sha256
        }
        schema = $DesignReceiptSchema
        smoke = [ordered]@{
            battery_id = $SmokeBatteryId
            runtime_build_receipt_sha256 =
                $SmokeRuntime.BuildReceiptState.Sha256
            schedule_receipt_sha256 = $SmokeSchedule.ReceiptState.Sha256
            seed = $SmokeSeed
        }
        source = [ordered]@{
            commit = $Source.Commit
            root = $Source.Root
            tree = $Source.Tree
        }
        status = 'sealed'
        trust_boundary = 'procedural-independent-hash-bound-v1'
    }
    $receiptState = Write-NewCompactJson (
        $receiptPath
    ) $receipt 'Launch5 design receipt'
    return [pscustomobject]@{
        InventoryPath = $inventoryPath
        InventoryState = $inventoryState
        ReceiptPath = $receiptPath
        ReceiptState = $receiptState
    }
}

function Assert-DesignSeal([object] $ExpectedSeal) {
    $inventoryDocument = Read-StrictJson (
        $ExpectedSeal.InventoryPath
    ) 'Launch5 design inventory'
    $receiptDocument = Read-StrictJson (
        $ExpectedSeal.ReceiptPath
    ) 'Launch5 design receipt'
    if (
        $inventoryDocument.State.Sha256 -cne
            $ExpectedSeal.InventoryState.Sha256 -or
        $receiptDocument.State.Sha256 -cne
            $ExpectedSeal.ReceiptState.Sha256
    ) {
        throw "Launch5 design seal changed"
    }
    $inventory = $inventoryDocument.Value
    Assert-JsonStringEquals (
        $inventory.schema
    ) $DesignInventorySchema 'Launch5 design inventory schema'
    Assert-JsonArray $inventory.entries 'Launch5 design inventory entries'
    Assert-JsonArray (
        $inventory.directories
    ) 'Launch5 design inventory directories'
    Assert-JsonIntegerEquals (
        $inventory.file_count
    ) @($inventory.entries).Count 'Launch5 design inventory file_count'
    Assert-JsonIntegerEquals (
        $inventory.directory_count
    ) @($inventory.directories).Count (
        'Launch5 design inventory directory_count'
    )
    $actualEntries = @(Get-DesignInventoryEntries $DesignRoot)
    $expectedEntries = @($inventory.entries)
    $actualDirectories = @(Get-DesignDirectoryPaths $DesignRoot)
    $expectedDirectories = @($inventory.directories)
    if ($actualDirectories.Count -ne $expectedDirectories.Count) {
        throw "Launch5 design directory count changed"
    }
    for ($index = 0; $index -lt $actualDirectories.Count; $index++) {
        $expectedDirectory = Assert-JsonString (
            $expectedDirectories[$index]
        ) "Launch5 design inventory directory $index" -NonEmpty
        if ($actualDirectories[$index] -cne $expectedDirectory) {
            throw "Launch5 design directory inventory changed"
        }
    }
    if ($actualEntries.Count -ne $expectedEntries.Count) {
        throw "Launch5 design file count changed"
    }
    for ($index = 0; $index -lt $actualEntries.Count; $index++) {
        if ($expectedEntries[$index] -is [System.Array]) {
            throw "Launch5 design inventory entry $index must be an object"
        }
        Assert-ExactProperties $expectedEntries[$index] @(
            'path', 'sha256', 'size_bytes'
        ) "Launch5 design inventory entry $index"
        Assert-JsonStringEquals (
            $expectedEntries[$index].path
        ) $actualEntries[$index].path (
            "Launch5 design inventory entry $index path"
        )
        Assert-JsonStringEquals (
            $expectedEntries[$index].sha256
        ) $actualEntries[$index].sha256 (
            "Launch5 design inventory entry $index SHA-256"
        )
        Assert-JsonIntegerEquals (
            $expectedEntries[$index].size_bytes
        ) $actualEntries[$index].size_bytes (
            "Launch5 design inventory entry $index size"
        )
    }
    Assert-JsonStringEquals (
        $receiptDocument.Value.schema
    ) $DesignReceiptSchema 'Launch5 design receipt schema'
    Assert-JsonStringEquals (
        $receiptDocument.Value.status
    ) 'sealed' 'Launch5 design receipt status'
    Assert-JsonStringEquals (
        $receiptDocument.Value.trust_boundary
    ) 'procedural-independent-hash-bound-v1' (
        'Launch5 design receipt trust boundary'
    )
}

function Assert-EngineHandshake([object] $Evidence, [string] $Label) {
    Assert-ExactProperties $Evidence @(
        'engines', 'schema', 'variant_path_policy'
    ) "$Label engine evidence"
    Assert-JsonStringEquals (
        $Evidence.schema
    ) $EngineEvidenceSchema "$Label engine evidence schema"
    Assert-JsonString $Evidence.variant_path_policy (
        "$Label VariantPath policy"
    ) -NonEmpty | Out-Null
    Assert-JsonArray $Evidence.engines "$Label engines"
    if (@($Evidence.engines).Count -ne 2) {
        throw "$Label engine evidence count differs"
    }
    foreach ($engine in @($Evidence.engines)) {
        Assert-ExactProperties $engine @(
            'advertised_options',
            'advertised_options_sha256',
            'configured_options',
            'handshake',
            'id',
            'network_proof',
            'role'
        ) "$Label engine entry"
        Assert-ExactProperties $engine.id @(
            'author', 'name'
        ) "$Label engine id"
        Assert-JsonStringEquals (
            $engine.id.name
        ) 'Atomic-Stockfish 1.0.3' "$Label engine id name"
        Assert-JsonStringEquals (
            $engine.id.author
        ) 'the Atomic-Stockfish developers (see AUTHORS file)' (
            "$Label engine id author"
        )
        $handshake = $engine.handshake
        Assert-ExactProperties $handshake @(
            'expect_single_blank_after_ids',
            'expected_identity_order',
            'expected_preamble',
            'observed_blank_after_ids',
            'observed_identity_order',
            'observed_preamble',
            'sha256'
        ) "$Label engine handshake"
        Assert-JsonStringArrayEquals (
            $handshake.expected_preamble
        ) $HandshakePreamble "$Label expected preamble"
        Assert-JsonStringArrayEquals (
            $handshake.observed_preamble
        ) $HandshakePreamble "$Label observed preamble"
        Assert-JsonStringArrayEquals (
            $handshake.expected_identity_order
        ) $HandshakeIdentityOrder "$Label expected identity order"
        Assert-JsonStringArrayEquals (
            $handshake.observed_identity_order
        ) $HandshakeIdentityOrder "$Label observed identity order"
        Assert-JsonBooleanEquals (
            $handshake.expect_single_blank_after_ids
        ) $true "$Label expected blank-after-ids"
        Assert-JsonBooleanEquals (
            $handshake.observed_blank_after_ids
        ) $true "$Label observed blank-after-ids"
        Assert-LowerSha256 (
            $handshake.sha256
        ) "$Label engine handshake SHA-256" | Out-Null
        $canonicalHandshake = (
            '{"namespace":"atomic-e00-uci-handshake-v1","value":' +
            '{"expect_single_blank_after_ids":true,' +
            '"expected_identity_order":["name","author"],' +
            '"expected_preamble":["' + $HandshakePreamble[0] + '"],' +
            '"observed_blank_after_ids":true,' +
            '"observed_identity_order":["name","author"],' +
            '"observed_preamble":["' + $HandshakePreamble[0] + '"]}}' +
            "`n"
        )
        $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
        if (
            $handshake.sha256 -cne (
                Get-ByteArraySha256 ($utf8.GetBytes($canonicalHandshake))
            )
        ) {
            throw "$Label engine handshake digest differs"
        }
    }
}

function Assert-CanonicalJsonEquals(
    [object] $Actual,
    [object] $Expected,
    [string] $Label
) {
    if (
        (ConvertTo-CanonicalJsonText $Actual) -cne
        (ConvertTo-CanonicalJsonText $Expected)
    ) {
        throw "$Label differs"
    }
}

function Assert-ArtifactMap(
    [object] $Value,
    [string] $Label,
    [string[]] $ExpectedKeys,
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
    foreach ($property in $Value.PSObject.Properties) {
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

function Assert-ArtifactEvidence(
    [object] $Value,
    [object] $Binding,
    [string] $Label
) {
    Assert-ExactProperties $Value @(
        'bytes', 'path', 'sha256'
    ) $Label
    Assert-JsonStringEquals $Value.path $Binding.path "$Label path"
    Assert-JsonStringEquals $Value.sha256 $Binding.sha256 "$Label SHA-256"
    Assert-JsonIntegerEquals $Value.bytes (
        Get-JsonInt64 $Binding.size_bytes "$Label binding size"
    ) "$Label bytes"
}

function Assert-OwnedProcessEvidence([object] $Value, [string] $Label) {
    Assert-ExactProperties $Value @(
        'active_processes_zero',
        'containment',
        'created_suspended_before_assignment',
        'kill_on_close',
        'resumed_primary_thread',
        'schema',
        'termination_requested'
    ) $Label
    Assert-JsonStringEquals $Value.schema $OwnedProcessSchema "$Label schema"
    Assert-JsonStringEquals (
        $Value.containment
    ) 'windows-job-object' "$Label containment"
    foreach ($field in @(
        'kill_on_close',
        'created_suspended_before_assignment',
        'resumed_primary_thread',
        'active_processes_zero'
    )) {
        Assert-JsonBooleanEquals $Value.$field $true "$Label $field"
    }
    Assert-JsonBooleanEquals (
        $Value.termination_requested
    ) $false "$Label termination_requested"
}

function Assert-RulesCalls([object] $Value, [string] $Label) {
    Assert-JsonArray $Value $Label
    $calls = @($Value)
    if ($calls.Count -eq 0) {
        throw "$Label must be non-empty"
    }
    $operations = @()
    for ($index = 0; $index -lt $calls.Count; $index++) {
        $call = $calls[$index]
        Assert-ExactProperties $call @(
            'operation',
            'request',
            'request_sha256',
            'response',
            'response_sha256'
        ) "$Label call $index"
        $operation = Assert-JsonString (
            $call.operation
        ) "$Label call $index operation" -NonEmpty
        if (
            $call.request_sha256 -cne
            (Get-CanonicalJsonSha256 $call.request)
        ) {
            throw "$Label call $index request digest differs"
        }
        if (
            $call.response_sha256 -cne
            (Get-CanonicalJsonSha256 $call.response)
        ) {
            throw "$Label call $index response digest differs"
        }
        $operations += $operation
    }
    return $operations
}

function Assert-ChildImportInventory(
    [object] $Value,
    [object] $Expected,
    [string] $Label
) {
    Assert-JsonArray $Value $Label
    $rows = @($Value)
    if ($rows.Count -eq 0) {
        throw "$Label must be non-empty"
    }
    for ($index = 0; $index -lt $rows.Count; $index++) {
        $row = $rows[$index]
        Assert-ExactProperties $row @(
            'module_names', 'path', 'sha256', 'size_bytes', 'source'
        ) "$Label row $index"
        Assert-JsonString (
            $row.source
        ) "$Label row $index source" -NonEmpty | Out-Null
        Assert-JsonString (
            $row.path
        ) "$Label row $index path" -NonEmpty | Out-Null
        Assert-LowerSha256 (
            $row.sha256
        ) "$Label row $index SHA-256" | Out-Null
        Assert-JsonIntegerMinimum (
            $row.size_bytes
        ) 1 "$Label row $index size" | Out-Null
        Assert-JsonArray $row.module_names "$Label row $index module_names"
        if (@($row.module_names).Count -eq 0) {
            throw "$Label row $index module_names must be non-empty"
        }
        foreach ($moduleName in @($row.module_names)) {
            Assert-JsonString (
                $moduleName
            ) "$Label row $index module name" -NonEmpty | Out-Null
        }
    }
    if ($null -ne $Expected) {
        Assert-CanonicalJsonEquals $Value $Expected $Label
    }
}

function Assert-NativeProvenance(
    [object] $Value,
    [object] $Receipt,
    [string] $Label
) {
    Assert-ExactProperties $Value @(
        'builder',
        'manifest_binding_path',
        'native',
        'owned_process',
        'uci_session'
    ) $Label
    Assert-ArtifactEvidence (
        $Value.builder
    ) $Receipt.executed_runtime_package.binding_builder "$Label builder"
    Assert-ArtifactEvidence (
        $Value.uci_session
    ) $Receipt.executed_runtime_package.uci_session "$Label uci_session"
    Assert-ArtifactEvidence (
        $Value.owned_process
    ) $Receipt.executed_runtime_package.owned_process "$Label owned_process"
    Assert-JsonStringEquals (
        $Value.manifest_binding_path
    ) $Receipt.inputs.pyffish.path "$Label manifest binding path"

    $native = $Value.native
    Assert-ExactProperties $native @(
        'build_manifest',
        'helper',
        'pyffish',
        'python',
        'rules_source',
        'schema'
    ) "$Label native"
    Assert-JsonStringEquals (
        $native.schema
    ) 'atomic-e00-native-outcome-provenance-v1' "$Label native schema"
    Assert-ArtifactEvidence (
        $native.pyffish
    ) $Receipt.input_snapshots.pyffish "$Label native pyffish"
    Assert-ArtifactEvidence (
        $native.build_manifest
    ) $Receipt.input_snapshots.pyffish_build_manifest (
        "$Label native build manifest"
    )
    Assert-ArtifactEvidence (
        $native.helper
    ) $Receipt.executed_runtime_package.atomic_outcome_helper (
        "$Label native helper"
    )
    Assert-ExactProperties $native.rules_source @(
        'commit', 'files', 'root'
    ) "$Label rules source"
    Assert-JsonStringEquals (
        $native.rules_source.commit
    ) $ExpectedSourceCommit "$Label rules source commit"
    $rulesRoot = Assert-JsonString (
        $native.rules_source.root
    ) "$Label rules source root" -NonEmpty
    if (
        -not [string]::Equals(
            (Get-AbsolutePath $rulesRoot),
            (Get-AbsolutePath $RepositoryRoot),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "$Label rules source root differs"
    }
    if (
        $null -eq $native.rules_source.files -or
        @($native.rules_source.files.PSObject.Properties).Count -eq 0
    ) {
        throw "$Label rules source files must be non-empty"
    }
    foreach ($property in $native.rules_source.files.PSObject.Properties) {
        Assert-ExactProperties $property.Value @(
            'bytes', 'sha256'
        ) "$Label rules source file $($property.Name)"
        Assert-JsonIntegerMinimum (
            $property.Value.bytes
        ) 1 "$Label rules source file $($property.Name) bytes" | Out-Null
        Assert-LowerSha256 (
            $property.Value.sha256
        ) "$Label rules source file $($property.Name) SHA-256" | Out-Null
    }

    Assert-ExactProperties $native.python @(
        'bytes',
        'cache_tag',
        'executable',
        'runtime_libraries',
        'sha256',
        'version'
    ) "$Label native Python"
    Assert-JsonIntegerMinimum (
        $native.python.bytes
    ) 1 "$Label native Python bytes" | Out-Null
    foreach ($field in @('cache_tag', 'executable', 'version')) {
        Assert-JsonString (
            $native.python.$field
        ) "$Label native Python $field" -NonEmpty | Out-Null
    }
    Assert-LowerSha256 (
        $native.python.sha256
    ) "$Label native Python SHA-256" | Out-Null
    if (
        $null -eq $native.python.runtime_libraries -or
        @($native.python.runtime_libraries.PSObject.Properties).Count -eq 0
    ) {
        throw "$Label native Python runtime libraries must be non-empty"
    }
}

function Assert-EngineEvidence(
    [object] $Evidence,
    [string[]] $ExpectedRoles,
    [object] $Receipt,
    [string] $Label
) {
    Assert-EngineHandshake $Evidence $Label
    $policy = Assert-JsonString (
        $Evidence.variant_path_policy
    ) "$Label VariantPath policy" -NonEmpty
    if ($policy -cnotin @('configured', 'unavailable-explicit')) {
        throw "$Label VariantPath policy differs"
    }
    $advertisedContract = $null
    for ($index = 0; $index -lt 2; $index++) {
        $engine = @($Evidence.engines)[$index]
        $role = Assert-JsonString (
            $engine.role
        ) "$Label engine $index role" -NonEmpty
        if ($role -cne $ExpectedRoles[$index]) {
            throw "$Label engine $index role differs"
        }
        Assert-JsonArray (
            $engine.advertised_options
        ) "$Label engine $index advertised options"
        $advertised = @($engine.advertised_options)
        if ($advertised.Count -eq 0) {
            throw "$Label engine $index advertised options must be non-empty"
        }
        $advertisedNames = @()
        for ($optionIndex = 0; $optionIndex -lt $advertised.Count; $optionIndex++) {
            $option = $advertised[$optionIndex]
            Assert-ExactProperties $option @(
                'choices',
                'default',
                'kind',
                'maximum',
                'minimum',
                'name',
                'raw'
            ) "$Label engine $index advertised option $optionIndex"
            $optionName = Assert-JsonString (
                $option.name
            ) "$Label engine $index advertised option $optionIndex name" -NonEmpty
            $kind = Assert-JsonString (
                $option.kind
            ) "$Label engine $index advertised option $optionIndex kind" -NonEmpty
            if ($kind -cnotin @('button', 'check', 'spin', 'combo', 'string')) {
                throw "$Label engine $index advertised option kind differs"
            }
            Assert-JsonArray (
                $option.choices
            ) "$Label engine $index advertised option $optionIndex choices"
            Assert-JsonString (
                $option.raw
            ) "$Label engine $index advertised option $optionIndex raw" -NonEmpty |
                Out-Null
            $advertisedNames += $optionName.ToLowerInvariant()
        }
        $sortedNames = @($advertisedNames)
        [Array]::Sort($sortedNames, [System.StringComparer]::Ordinal)
        if (
            (ConvertTo-CanonicalJsonText $advertisedNames) -cne
            (ConvertTo-CanonicalJsonText $sortedNames) -or
            @($advertisedNames | Select-Object -Unique).Count -ne
                $advertisedNames.Count
        ) {
            throw "$Label engine $index advertised options are not unique/sorted"
        }
        if (
            $engine.advertised_options_sha256 -cne
            (Get-NamespacedJsonSha256 (
                'atomic-e00-advertised-options-v2'
            ) $engine.advertised_options)
        ) {
            throw "$Label engine $index advertised option digest differs"
        }
        if ($null -eq $advertisedContract) {
            $advertisedContract = ConvertTo-CanonicalJsonText (
                $engine.advertised_options
            )
        }
        elseif (
            $advertisedContract -cne
            (ConvertTo-CanonicalJsonText $engine.advertised_options)
        ) {
            throw "$Label engines advertised different option contracts"
        }

        Assert-JsonArray (
            $engine.configured_options
        ) "$Label engine $index configured options"
        $configured = @($engine.configured_options)
        $expectedConfigured = @()
        if ($policy -ceq 'configured') {
            $expectedConfigured += ,@(
                'VariantPath',
                $Receipt.input_snapshots.variant_config.path
            )
        }
        foreach ($option in @($Receipt.execution.uci_options)) {
            $expectedConfigured += ,@($option.name, $option.value)
        }
        $networkKey = if ($role -ceq 'current-v3') {
            'current_net'
        }
        else {
            'teacher_net'
        }
        $expectedConfigured += ,@(
            'EvalFile',
            $Receipt.input_snapshots.$networkKey.path
        )
        if ($configured.Count -ne $expectedConfigured.Count) {
            throw "$Label engine $index configured option count differs"
        }
        for (
            $configuredIndex = 0;
            $configuredIndex -lt $configured.Count;
            $configuredIndex++
        ) {
            $option = $configured[$configuredIndex]
            Assert-ExactProperties $option @(
                'name', 'value'
            ) "$Label engine $index configured option $configuredIndex"
            Assert-JsonStringEquals (
                $option.name
            ) $expectedConfigured[$configuredIndex][0] (
                "$Label engine $index configured option $configuredIndex name"
            )
            Assert-JsonScalarEquals (
                $option.value
            ) $expectedConfigured[$configuredIndex][1] (
                "$Label engine $index configured option $configuredIndex value"
            )
        }
        $configuredNames = @(
            $configured | ForEach-Object {
                $_.name.ToLowerInvariant()
            }
        )
        foreach ($configuredName in $configuredNames) {
            if ($advertisedNames -cnotcontains $configuredName) {
                throw "$Label engine $index configured unadvertised option"
            }
        }

        $proof = $engine.network_proof
        Assert-ExactProperties $proof @(
            'backend',
            'evalfile_path',
            'evalfile_sha256',
            'first_go',
            'line',
            'line_sha256',
            'raw_lines',
            'raw_lines_sha256'
        ) "$Label engine $index network proof"
        $backend = if ($role -ceq 'current-v3') {
            'AtomicNNUEV3'
        }
        else {
            'Legacy Atomic V1'
        }
        Assert-JsonStringEquals (
            $proof.backend
        ) $backend "$Label engine $index network backend"
        Assert-JsonStringEquals (
            $proof.evalfile_path
        ) $Receipt.input_snapshots.$networkKey.path (
            "$Label engine $index network path"
        )
        Assert-JsonStringEquals (
            $proof.evalfile_sha256
        ) $Receipt.input_snapshots.$networkKey.sha256 (
            "$Label engine $index network SHA-256"
        )
        Assert-JsonStringEquals (
            $proof.first_go
        ) 'preflight-nodes-1' "$Label engine $index first go"
        $line = Assert-JsonString (
            $proof.line
        ) "$Label engine $index network line" -NonEmpty
        $expectedLinePrefix = (
            "info string NNUE evaluation using $backend " +
            "$($Receipt.input_snapshots.$networkKey.path) ("
        )
        if (
            -not $line.StartsWith($expectedLinePrefix) -or
            -not $line.EndsWith(')') -or
            $proof.line_sha256 -cne
            (Get-ByteArraySha256 (
                (New-Object System.Text.UTF8Encoding(
                    $false, $true
                )).GetBytes($line)
            ))
        ) {
            throw "$Label engine $index network line digest differs"
        }
        Assert-JsonArray (
            $proof.raw_lines
        ) "$Label engine $index network raw lines"
        if (@($proof.raw_lines).Count -eq 0) {
            throw "$Label engine $index network raw lines must be non-empty"
        }
        foreach ($rawLine in @($proof.raw_lines)) {
            Assert-JsonString (
                $rawLine
            ) "$Label engine $index network raw line" | Out-Null
        }
        if (
            $proof.raw_lines_sha256 -cne
            (Get-NamespacedJsonSha256 (
                'atomic-e00-uci-go-raw-lines-v2'
            ) $proof.raw_lines) -or
            @(
                $proof.raw_lines | Where-Object {
                    $_.StartsWith('info string NNUE evaluation using ')
                }
            ).Count -ne 1 -or
            -not (@($proof.raw_lines) -ccontains $line) -or
            @(
                $proof.raw_lines | Where-Object {
                    $_.Contains('ERROR:') -or
                    $_.Contains('Classical Atomic evaluation enabled')
                }
            ).Count -ne 0
        ) {
            throw "$Label engine $index network raw journal differs"
        }
    }
}

function Assert-TimingEvidence(
    [object] $Value,
    [object] $Game,
    [string[]] $ExpectedRoles,
    [object] $Receipt,
    [string] $Label
) {
    Assert-JsonArray $Value $Label
    $events = @($Value)
    if ($events.Count -eq 0) {
        throw "$Label must be non-empty"
    }
    $baseMs = Get-JsonInt64 $Game.time_control.base_ms (
        "$Label base_ms"
    )
    $incrementMs = Get-JsonInt64 $Game.time_control.increment_ms (
        "$Label increment_ms"
    )
    $clocks = @(
        [long] ($baseMs * 1000000),
        [long] ($baseMs * 1000000)
    )
    $incrementNs = [long] ($incrementMs * 1000000)
    $fenFields = @(
        (Assert-JsonString $Game.root_fen "$Label root FEN" -NonEmpty).Split(' ')
    )
    if ($fenFields.Count -ne 6 -or $fenFields[1] -cnotin @('w', 'b')) {
        throw "$Label root FEN is malformed"
    }
    $rootSide = if ($fenFields[1] -ceq 'w') { 0 } else { 1 }
    $appliedMoves = @()
    $expectedOperations = @('outcome')
    for ($index = 0; $index -lt $events.Count; $index++) {
        $event = $events[$index]
        Assert-ExactProperties $event @(
            'bestmove',
            'bestmove_completed_ns',
            'black_clock_ms',
            'elapsed_ns',
            'go_started_ns',
            'move_applied',
            'network_marker',
            'network_marker_sha256',
            'network_raw_lines',
            'network_raw_lines_sha256',
            'on_time',
            'ply',
            'remaining_after_ns',
            'remaining_before_ns',
            'role',
            'side_index',
            'white_clock_ms'
        ) "$Label event $index"
        Assert-JsonIntegerEquals $event.ply $index "$Label event $index ply"
        $side = if (($index % 2) -eq 0) {
            $rootSide
        }
        else {
            1 - $rootSide
        }
        Assert-JsonIntegerEquals (
            $event.side_index
        ) $side "$Label event $index side"
        Assert-JsonStringEquals (
            $event.role
        ) $ExpectedRoles[$side] "$Label event $index role"
        $remainingBefore = Get-JsonInt64 (
            $event.remaining_before_ns
        ) "$Label event $index remaining_before_ns"
        $whiteClockMs = Get-JsonInt64 (
            $event.white_clock_ms
        ) "$Label event $index white_clock_ms"
        $blackClockMs = Get-JsonInt64 (
            $event.black_clock_ms
        ) "$Label event $index black_clock_ms"
        $started = Get-JsonInt64 (
            $event.go_started_ns
        ) "$Label event $index go_started_ns"
        $completed = Get-JsonInt64 (
            $event.bestmove_completed_ns
        ) "$Label event $index bestmove_completed_ns"
        $elapsed = Get-JsonInt64 (
            $event.elapsed_ns
        ) "$Label event $index elapsed_ns"
        foreach ($number in @(
            $remainingBefore,
            $whiteClockMs,
            $blackClockMs,
            $started,
            $completed,
            $elapsed
        )) {
            if ($number -lt 0) {
                throw "$Label event $index contains a negative integer"
            }
        }
        if (
            $remainingBefore -ne $clocks[$side] -or
            $whiteClockMs -ne [math]::Floor($clocks[0] / 1000000) -or
            $blackClockMs -ne [math]::Floor($clocks[1] / 1000000) -or
            $completed - $started -ne $elapsed
        ) {
            throw "$Label event $index clock arithmetic differs"
        }
        $onTime = $elapsed -le $remainingBefore
        Assert-JsonBooleanEquals (
            $event.on_time
        ) $onTime "$Label event $index on_time"
        Assert-JsonBooleanEquals (
            $event.move_applied
        ) $onTime "$Label event $index move_applied"
        $marker = Assert-JsonString (
            $event.network_marker
        ) "$Label event $index network marker" -NonEmpty
        $networkKey = if ($event.role -ceq 'current-v3') {
            'current_net'
        }
        else {
            'teacher_net'
        }
        $backend = if ($event.role -ceq 'current-v3') {
            'AtomicNNUEV3'
        }
        else {
            'Legacy Atomic V1'
        }
        $expectedMarkerPrefix = (
            "info string NNUE evaluation using $backend " +
            "$($Receipt.input_snapshots.$networkKey.path) ("
        )
        if (
            -not $marker.StartsWith($expectedMarkerPrefix) -or
            -not $marker.EndsWith(')')
        ) {
            throw "$Label event $index network marker differs"
        }
        $markerBytes = (
            New-Object System.Text.UTF8Encoding($false, $true)
        ).GetBytes($marker)
        if (
            $event.network_marker_sha256 -cne
            (Get-ByteArraySha256 $markerBytes)
        ) {
            throw "$Label event $index network marker digest differs"
        }
        Assert-JsonArray (
            $event.network_raw_lines
        ) "$Label event $index network raw lines"
        if (
            @($event.network_raw_lines).Count -eq 0 -or
            $event.network_raw_lines_sha256 -cne
            (Get-NamespacedJsonSha256 (
                'atomic-e00-uci-go-raw-lines-v2'
            ) $event.network_raw_lines)
        ) {
            throw "$Label event $index network raw journal differs"
        }
        $markerLines = @(
            $event.network_raw_lines | Where-Object {
                -not ($_ -is [string])
            }
        )
        if ($markerLines.Count -ne 0) {
            throw "$Label event $index network raw line is not a string"
        }
        if (
            @(
                $event.network_raw_lines | Where-Object {
                    $_.StartsWith('info string NNUE evaluation using ')
                }
            ).Count -ne 1 -or
            -not (@($event.network_raw_lines) -ccontains $marker)
        ) {
            throw "$Label event $index network marker journal differs"
        }
        if ($onTime) {
            $move = Assert-JsonString (
                $event.bestmove
            ) "$Label event $index bestmove" -NonEmpty
            if ($move -cnotmatch '^[a-h][1-8][a-h][1-8][nbrq]?$') {
                throw "$Label event $index bestmove is malformed"
            }
            $remainingAfter = Get-JsonInt64 (
                $event.remaining_after_ns
            ) "$Label event $index remaining_after_ns"
            $expectedAfter = [long] (
                $remainingBefore - $elapsed + $incrementNs
            )
            if ($remainingAfter -ne $expectedAfter) {
                throw "$Label event $index resulting clock differs"
            }
            $clocks[$side] = $remainingAfter
            $appliedMoves += $move
            $expectedOperations += @('legal-moves', 'outcome')
        }
        else {
            if ($null -ne $event.remaining_after_ns) {
                throw "$Label event $index flag changed the clock"
            }
            if ($index + 1 -ne $events.Count) {
                throw "$Label continues after a flag"
            }
            $expectedOperations += @(
                'legal-moves', 'insufficient-material'
            )
        }
    }
    Assert-JsonStringArrayEquals (
        $Game.moves
    ) $appliedMoves "$Label applied moves"
    $lastOnTime = @($events)[-1].on_time
    Assert-JsonBooleanEquals (
        $Game.time_loss
    ) (-not $lastOnTime) "$Label time-loss result"
    return $expectedOperations
}

function Assert-RefereeEvidence(
    [object] $Value,
    [object] $Game,
    [string[]] $ExpectedRoles,
    [object] $Receipt,
    [object] $ExpectedInventory,
    [string] $Label
) {
    Assert-ExactProperties $Value @(
        'child_import_inventory',
        'provenance',
        'rules_calls',
        'schema',
        'timing_events'
    ) $Label
    Assert-JsonStringEquals (
        $Value.schema
    ) $RefereeEvidenceSchema "$Label schema"
    Assert-NativeProvenance $Value.provenance $Receipt "$Label provenance"
    Assert-ChildImportInventory (
        $Value.child_import_inventory
    ) $ExpectedInventory "$Label child import inventory"
    $operations = @(Assert-RulesCalls $Value.rules_calls "$Label rules calls")
    $expectedOperations = @(
        Assert-TimingEvidence (
            $Value.timing_events
        ) $Game $ExpectedRoles $Receipt "$Label timing events"
    )
    Assert-JsonStringArrayEquals (
        $operations
    ) $expectedOperations "$Label rule operations"
}

function Assert-VerifierEvidence(
    [object] $Value,
    [object] $Game,
    [object] $Receipt,
    [object] $ExpectedInventory,
    [string] $Label
) {
    Assert-ExactProperties $Value @(
        'child_import_inventory',
        'process',
        'provenance',
        'rules_calls',
        'schema',
        'verified'
    ) $Label
    Assert-JsonStringEquals (
        $Value.schema
    ) $VerifierEvidenceSchema "$Label schema"
    Assert-OwnedProcessEvidence $Value.process "$Label process"
    Assert-NativeProvenance $Value.provenance $Receipt "$Label provenance"
    Assert-ChildImportInventory (
        $Value.child_import_inventory
    ) $ExpectedInventory "$Label child import inventory"
    [void] (Assert-RulesCalls $Value.rules_calls "$Label rules calls")
    Assert-ExactProperties $Value.verified @(
        'moves',
        'result_white',
        'root_fen',
        'terminal_reason',
        'time_loss',
        'trajectory_sha256'
    ) "$Label verified"
    Assert-JsonStringArrayEquals (
        $Value.verified.moves
    ) @($Game.moves) "$Label verified moves"
    foreach ($field in @(
        'result_white',
        'root_fen',
        'terminal_reason',
        'trajectory_sha256'
    )) {
        Assert-JsonStringEquals (
            $Value.verified.$field
        ) $Game.$field "$Label verified $field"
    }
    Assert-JsonBooleanEquals (
        $Value.verified.time_loss
    ) $Game.time_loss "$Label verified time_loss"
}

function Get-ExpectedExecutionInputKeys([object] $Receipt) {
    Assert-ExactProperties $Receipt.runtime @(
        'manifest', 'observed_pre_and_post_equal'
    ) 'smoke runtime receipt'
    Assert-JsonBooleanEquals (
        $Receipt.runtime.observed_pre_and_post_equal
    ) $true 'smoke runtime pre/post equality'
    $manifest = $Receipt.runtime.manifest
    Assert-JsonStringEquals (
        $manifest.schema
    ) $RuntimeManifestSchema 'smoke runtime manifest schema'
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
        'smoke runtime manifest inputs'
    )
    $expected = @($fixed) + @('runtime_manifest', 'python_executable')
    $python = $manifest.runtime.python
    if ($null -eq $python) {
        throw "smoke runtime Python identity is absent"
    }
    $libraries = @($python.runtime_libraries.PSObject.Properties)
    if ($libraries.Count -eq 0) {
        throw "smoke runtime Python libraries must be non-empty"
    }
    for ($index = 1; $index -le $libraries.Count; $index++) {
        $expected += ('python_runtime_library_{0:D3}' -f $index)
    }
    Assert-JsonArray (
        $python.child_import_inventory
    ) 'smoke runtime child import inventory'
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

function Assert-ExecutionReceiptContract(
    [object] $Value,
    [object] $Receipt
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
    ) 'smoke execution policy'
    Assert-JsonIntegerEquals $Value.threads 1 'smoke execution threads'
    Assert-JsonIntegerEquals (
        $Value.maximum_plies
    ) 1024 'smoke execution maximum plies'
    Assert-JsonNumberEquals (
        $Value.command_timeout_seconds
    ) 120.0 'smoke execution command timeout'
    Assert-JsonNumberEquals (
        $Value.maximum_wall_seconds
    ) 3600.0 'smoke execution maximum wall seconds'
    Assert-JsonNumberEquals (
        $Value.maximum_game_wall_seconds
    ) 1800.0 'smoke execution maximum game wall seconds'
    Assert-JsonStringEquals (
        $Value.retry_policy
    ) 'none' 'smoke execution retry policy'
    Assert-JsonStringEquals (
        $Value.pair_order
    ) 'schedule-ordinal-then-leg' 'smoke execution pair order'
    Assert-JsonStringEquals (
        $Value.time_loss_rule
    ) 'draw-if-nonflagging-side-has-insufficient-atomic-mating-material-v1' (
        'smoke execution time-loss rule'
    )
    Assert-JsonStringEquals (
        $Value.owned_process_policy
    ) 'windows-job-create-suspended-assign-resume-active-zero-v1' (
        'smoke execution owned-process policy'
    )
    Assert-JsonStringEquals (
        $Value.referee_policy
    ) 'fresh-exact-pyffish-root-plus-history-v2' (
        'smoke execution referee policy'
    )
    Assert-JsonStringEquals (
        $Value.verifier_policy
    ) 'independent-fresh-pyffish-owned-process-replay-v2' (
        'smoke execution verifier policy'
    )
    Assert-ExactProperties $Value.clock_policy @(
        'charged_interval', 'equality', 'uci_millisecond_conversion'
    ) 'smoke execution clock policy'
    Assert-JsonStringEquals (
        $Value.clock_policy.charged_interval
    ) 'complete-go-write-flush-to-complete-bestmove-newline' (
        'smoke execution charged interval'
    )
    Assert-JsonStringEquals (
        $Value.clock_policy.equality
    ) 'elapsed-ns-equal-remaining-ns-is-on-time' (
        'smoke execution clock equality'
    )
    Assert-JsonStringEquals (
        $Value.clock_policy.uci_millisecond_conversion
    ) 'floor-nanoseconds' 'smoke execution clock conversion'

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
    Assert-JsonArray $Value.uci_options 'smoke execution UCI options'
    $options = @($Value.uci_options)
    if ($options.Count -ne $expectedOptions.Count) {
        throw "smoke execution UCI option count differs"
    }
    for ($index = 0; $index -lt $options.Count; $index++) {
        Assert-ExactProperties $options[$index] @(
            'name', 'value'
        ) "smoke execution UCI option $index"
        Assert-JsonStringEquals (
            $options[$index].name
        ) $expectedOptions[$index][0] "smoke execution UCI option $index name"
        Assert-JsonScalarEquals (
            $options[$index].value
        ) $expectedOptions[$index][1] "smoke execution UCI option $index value"
    }

    Assert-ExactProperties $Value.network_options @(
        'current-v3', 'run3b'
    ) 'smoke execution network options'
    foreach ($binding in @(
        @('current-v3', 'current_net'),
        @('run3b', 'teacher_net')
    )) {
        $role = $binding[0]
        $networkKey = $binding[1]
        Assert-JsonArray (
            $Value.network_options.$role
        ) "smoke execution $role network options"
        $networkOptions = @($Value.network_options.$role)
        if ($networkOptions.Count -ne 1) {
            throw "smoke execution $role network option count differs"
        }
        Assert-ExactProperties $networkOptions[0] @(
            'name', 'value'
        ) "smoke execution $role network option"
        Assert-JsonStringEquals (
            $networkOptions[0].name
        ) 'EvalFile' "smoke execution $role network option name"
        Assert-JsonStringEquals (
            $networkOptions[0].value
        ) $Receipt.input_snapshots.$networkKey.path (
            "smoke execution $role EvalFile"
        )
    }
}

function Assert-SmokeGame(
    [object] $Game,
    [object] $ScheduleRow,
    [object] $Receipt,
    [object] $ExpectedInventory,
    [string] $Label
) {
    Assert-ExactProperties $Game @(
        'battery_id',
        'black_network_role',
        'book_line',
        'book_sha256',
        'current_net_sha256',
        'engine_evidence',
        'engine_sha256',
        'experiment_id',
        'leg',
        'moves',
        'pair_id',
        'pair_ordinal',
        'ply_count',
        'process_evidence',
        'referee_evidence',
        'result_current',
        'result_white',
        'root_fen',
        'root_fen_sha256',
        'schedule_seed',
        'schedule_sha256',
        'schema',
        'source_game_id',
        'stderr_sha256',
        'stdout_sha256',
        'stratum',
        'teacher_net_sha256',
        'terminal_reason',
        'time_control',
        'time_loss',
        'trajectory_sha256',
        'verifier_evidence',
        'white_network_role'
    ) $Label
    Assert-JsonStringEquals $Game.schema $GameSchema "$Label schema"
    Assert-JsonStringEquals (
        $Game.experiment_id
    ) $ExperimentId "$Label experiment_id"
    Assert-JsonStringEquals (
        $Game.battery_id
    ) $SmokeBatteryId "$Label battery_id"
    foreach ($binding in @(
        @('schedule_seed', 'seed'),
        @('pair_id', 'pair_id'),
        @('book_sha256', 'book_sha256'),
        @('white_network_role', 'white_role'),
        @('black_network_role', 'black_role')
    )) {
        Assert-JsonStringEquals (
            $Game.($binding[0])
        ) $ScheduleRow.($binding[1]) "$Label $($binding[0])"
    }
    Assert-JsonStringEquals (
        $Game.schedule_sha256
    ) $Receipt.schedule.sha256 "$Label schedule_sha256"
    Assert-JsonIntegerEquals (
        $Game.pair_ordinal
    ) (Get-JsonInt64 $ScheduleRow.pair_ordinal "$Label scheduled ordinal") (
        "$Label pair_ordinal"
    )
    $leg = Get-JsonInt64 $Game.leg "$Label leg"
    Assert-JsonIntegerEquals (
        $ScheduleRow.leg
    ) $leg "$Label scheduled leg"
    if ($leg -notin @(0, 1)) {
        throw "$Label leg differs"
    }
    Assert-ExactProperties $Game.time_control @(
        'base_ms', 'increment_ms', 'name'
    ) "$Label time control"
    Assert-CanonicalJsonEquals (
        $Game.time_control
    ) $ScheduleRow.time_control "$Label time control"
    Assert-JsonStringEquals (
        $Game.stratum
    ) $Game.time_control.name "$Label stratum"
    Assert-JsonIntegerEquals (
        $Game.book_line
    ) (Get-JsonInt64 $ScheduleRow.root.book_line "$Label scheduled book line") (
        "$Label book_line"
    )
    Assert-JsonStringEquals (
        $Game.root_fen
    ) $ScheduleRow.root.fen "$Label root_fen"
    Assert-JsonStringEquals (
        $Game.root_fen_sha256
    ) $ScheduleRow.root.fen_sha256 "$Label root_fen_sha256"
    Assert-JsonStringEquals (
        $Game.engine_sha256
    ) $PinnedInputs.engine "$Label engine_sha256"
    Assert-JsonStringEquals (
        $Game.current_net_sha256
    ) $PinnedInputs.current_net "$Label current_net_sha256"
    Assert-JsonStringEquals (
        $Game.teacher_net_sha256
    ) $PinnedInputs.teacher_net "$Label teacher_net_sha256"

    $resultWhite = Assert-JsonString (
        $Game.result_white
    ) "$Label result_white" -NonEmpty
    if ($resultWhite -cnotin @('1-0', '0-1', '1/2-1/2')) {
        throw "$Label result_white differs"
    }
    $expectedCurrent = if ($resultWhite -ceq '1/2-1/2') {
        'draw'
    }
    elseif (
        ($resultWhite -ceq '1-0') -eq
        ($Game.white_network_role -ceq 'current-v3')
    ) {
        'win'
    }
    else {
        'loss'
    }
    Assert-JsonStringEquals (
        $Game.result_current
    ) $expectedCurrent "$Label result_current"
    if (-not ($Game.time_loss -is [bool])) {
        throw "$Label time_loss must be a JSON boolean"
    }
    $terminalReason = Assert-JsonString (
        $Game.terminal_reason
    ) "$Label terminal_reason" -NonEmpty
    if (
        $terminalReason -cmatch '[\x00-\x1f\x7f]' -or
        $Game.time_loss -ne (
            $terminalReason -cin @(
                'time-loss',
                'time-loss-insufficient-material-draw'
            )
        )
    ) {
        throw "$Label terminal result differs"
    }
    Assert-JsonArray $Game.moves "$Label moves"
    $moves = @($Game.moves)
    if ($moves.Count -gt 1024 -or ($moves.Count -eq 0 -and -not $Game.time_loss)) {
        throw "$Label move trajectory differs"
    }
    foreach ($move in $moves) {
        if (
            -not ($move -is [string]) -or
            $move -cnotmatch '^[a-h][1-8][a-h][1-8][nbrq]?$'
        ) {
            throw "$Label contains a malformed move"
        }
    }
    Assert-JsonIntegerEquals (
        $Game.ply_count
    ) $moves.Count "$Label ply_count"
    $trajectory = Get-NamespacedJsonSha256 (
        'atomic-e00-trajectory-v1'
    ) ([ordered]@{
        moves = $moves
        result_white = $resultWhite
        root_fen = $Game.root_fen
    })
    Assert-JsonStringEquals (
        $Game.trajectory_sha256
    ) $trajectory "$Label trajectory_sha256"
    $gameId = Get-NamespacedJsonSha256 (
        'atomic-e00-source-game-v1'
    ) ([ordered]@{
        battery_id = $Game.battery_id
        experiment_id = $Game.experiment_id
        leg = $leg
        pair_id = $Game.pair_id
        schedule_sha256 = $Game.schedule_sha256
        trajectory_sha256 = $trajectory
    })
    Assert-JsonStringEquals (
        $Game.source_game_id
    ) $gameId "$Label source_game_id"
    foreach ($field in @('stdout_sha256', 'stderr_sha256')) {
        Assert-LowerSha256 $Game.$field "$Label $field" | Out-Null
    }

    $roles = @($Game.white_network_role, $Game.black_network_role)
    Assert-EngineEvidence $Game.engine_evidence $roles $Receipt (
        "$Label engine evidence"
    )
    Assert-OwnedProcessEvidence (
        $Game.process_evidence
    ) "$Label process evidence"
    Assert-RefereeEvidence (
        $Game.referee_evidence
    ) $Game $roles $Receipt $ExpectedInventory "$Label referee evidence"
    Assert-VerifierEvidence (
        $Game.verifier_evidence
    ) $Game $Receipt $ExpectedInventory "$Label verifier evidence"
    return $leg
}

function Assert-SmokeExecution(
    [string] $SchedulePath,
    [string] $ScheduleReceiptPath
) {
    $receiptPath = Join-Path $SmokeOutputRoot 'receipt.json'
    $receiptDocument = Read-StrictJson $receiptPath 'smoke execution receipt'
    $receipt = $receiptDocument.Value
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
    ) 'smoke execution receipt'
    Assert-JsonStringEquals (
        $receipt.schema
    ) $ExecutionReceiptSchema 'smoke execution receipt schema'
    Assert-JsonStringEquals (
        $receipt.status
    ) 'committed' 'smoke execution receipt status'
    Assert-JsonStringEquals (
        $receipt.experiment_id
    ) $ExperimentId 'smoke execution receipt experiment_id'
    Assert-JsonStringEquals (
        $receipt.battery_id
    ) $SmokeBatteryId 'smoke execution receipt battery_id'
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
    ) 'smoke reconciliation'
    Assert-JsonIntegerEquals (
        $receipt.reconciliation.accepted_pairs
    ) 1 'smoke accepted pairs'
    Assert-JsonIntegerEquals (
        $receipt.reconciliation.accepted_games
    ) 2 'smoke accepted games'
    foreach ($field in @(
        'all_ids_recomputed',
        'all_pairs_atomic',
        'all_trajectories_legally_replayed',
        'zero_extra_duplicate_partial_rows'
    )) {
        Assert-JsonBooleanEquals (
            $receipt.reconciliation.$field
        ) $true "smoke reconciliation $field"
    }
    Assert-ExecutionReceiptContract $receipt.execution $receipt
    $scheduleState = Get-StableFileState $SchedulePath 'smoke schedule'
    $scheduleReceiptState = Get-StableFileState (
        $ScheduleReceiptPath
    ) 'smoke schedule receipt'
    Assert-ExactProperties $receipt.schedule @(
        'receipt_sha256', 'receipt_size_bytes', 'sha256', 'size_bytes'
    ) 'smoke schedule binding'
    Assert-JsonStringEquals (
        $receipt.schedule.sha256
    ) $scheduleState.Sha256 'smoke schedule SHA-256'
    Assert-JsonIntegerEquals (
        $receipt.schedule.size_bytes
    ) $scheduleState.SizeBytes 'smoke schedule size'
    Assert-JsonStringEquals (
        $receipt.schedule.receipt_sha256
    ) $scheduleReceiptState.Sha256 'smoke schedule receipt SHA-256'
    Assert-JsonIntegerEquals (
        $receipt.schedule.receipt_size_bytes
    ) $scheduleReceiptState.SizeBytes 'smoke schedule receipt size'

    $expectedInputKeys = @(
        Get-ExpectedExecutionInputKeys $receipt
    )
    Assert-ArtifactMap (
        $receipt.inputs
    ) 'smoke input' $expectedInputKeys -RequireNonEmpty
    Assert-ArtifactMap (
        $receipt.input_snapshots
    ) 'smoke snapshot' $expectedInputKeys -RequireNonEmpty
    foreach ($name in $expectedInputKeys) {
        Assert-JsonStringEquals (
            $receipt.input_snapshots.$name.sha256
        ) $receipt.inputs.$name.sha256 "smoke snapshot $name SHA-256"
        Assert-JsonIntegerEquals (
            $receipt.input_snapshots.$name.size_bytes
        ) (Get-JsonInt64 (
            $receipt.inputs.$name.size_bytes
        ) "smoke input $name size") "smoke snapshot $name size"
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
    ) 'smoke runtime package' $runtimePackageKeys -RequireNonEmpty
    Assert-NamespaceGuards $receipt.namespace_guards 'smoke namespace guards'
    $nativeKeys = @(
        $receipt.namespace_guards.native_build.enumeration |
            Where-Object { $_.kind -ceq 'file' } |
            ForEach-Object { $_.path }
    )
    Assert-ArtifactMap (
        $receipt.native_build_artifacts
    ) 'smoke native build artifact' $nativeKeys -RequireNonEmpty
    Assert-ExactProperties $receipt.trust_boundary @(
        'child_import_policy',
        'guarded_runtime',
        'hermetic',
        'namespace_policy',
        'residual_tcb'
    ) 'smoke trust boundary'
    Assert-JsonBooleanEquals (
        $receipt.trust_boundary.hermetic
    ) $false 'smoke trust boundary hermetic'
    foreach ($field in @(
        'child_import_policy', 'guarded_runtime', 'namespace_policy'
    )) {
        Assert-JsonString (
            $receipt.trust_boundary.$field
        ) "smoke trust boundary $field" -NonEmpty | Out-Null
    }
    Assert-JsonArray (
        $receipt.trust_boundary.residual_tcb
    ) 'smoke trust boundary residual_tcb'
    $expectedOutputs = [ordered]@{
        games = 'games.jsonl'
        inventory = 'inventory.json'
        rejections = 'rejections.jsonl'
    }
    Assert-ExactProperties $receipt.outputs @(
        'games', 'inventory', 'rejections'
    ) 'smoke outputs'
    foreach ($name in $expectedOutputs.Keys) {
        $binding = $receipt.outputs.$name
        Assert-ExactProperties $binding @(
            'path', 'sha256', 'size_bytes'
        ) "smoke output $name"
        $outputPath = Assert-JsonString (
            $binding.path
        ) "smoke output $name path" -NonEmpty
        if (
            $outputPath -cne $expectedOutputs[$name] -or
            [System.IO.Path]::IsPathRooted($outputPath)
        ) {
            throw "smoke output $name path differs"
        }
        Assert-FileBinding (
            Join-Path $SmokeOutputRoot $expectedOutputs[$name]
        ) $binding.sha256 $binding.size_bytes "smoke output $name" |
            Out-Null
    }
    Assert-JsonIntegerEquals (
        $receipt.outputs.rejections.size_bytes
    ) 0 'smoke rejection size'
    Assert-JsonStringEquals (
        $receipt.outputs.rejections.sha256
    ) $EmptySha256 'smoke rejection SHA-256'
    $gamesPath = Join-Path $SmokeOutputRoot 'games.jsonl'
    $gamesDocument = Read-StrictJsonLines $gamesPath 'smoke games'
    $gamesState = $gamesDocument.State
    $games = @($gamesDocument.Rows)
    $scheduleDocument = Read-StrictJsonLines (
        $SchedulePath
    ) 'smoke schedule'
    $scheduleRows = @($scheduleDocument.Rows)
    if ($scheduleRows.Count -ne 2) {
        throw "smoke schedule must contain exactly two rows"
    }
    for ($index = 0; $index -lt 2; $index++) {
        $scheduleRow = $scheduleRows[$index]
        Assert-ExactProperties $scheduleRow @(
            'black_role',
            'book_sha256',
            'leg',
            'pair_id',
            'pair_ordinal',
            'root',
            'schema',
            'seed',
            'time_control',
            'white_role'
        ) "smoke schedule row $index"
        Assert-JsonStringEquals (
            $scheduleRow.schema
        ) 'atomic-e00-schedule-v1' "smoke schedule row $index schema"
        Assert-JsonIntegerEquals (
            $scheduleRow.pair_ordinal
        ) 1 "smoke schedule row $index pair_ordinal"
        Assert-JsonIntegerEquals (
            $scheduleRow.leg
        ) $index "smoke schedule row $index leg"
        Assert-JsonStringEquals (
            $scheduleRow.seed
        ) $SmokeSeed "smoke schedule row $index seed"
        Assert-JsonStringEquals (
            $scheduleRow.book_sha256
        ) $PinnedInputs.book "smoke schedule row $index book SHA-256"
        Assert-ExactProperties $scheduleRow.root @(
            'book_line', 'fen', 'fen_sha256', 'order_sha256', 'root_identity'
        ) "smoke schedule row $index root"
        Assert-ExactProperties $scheduleRow.time_control @(
            'base_ms', 'increment_ms', 'name'
        ) "smoke schedule row $index time control"
    }
    Assert-CanonicalJsonEquals (
        $scheduleRows[0].root
    ) $scheduleRows[1].root 'smoke schedule paired root'
    Assert-CanonicalJsonEquals (
        $scheduleRows[0].time_control
    ) $scheduleRows[1].time_control 'smoke schedule paired time control'
    Assert-JsonStringEquals (
        $scheduleRows[0].pair_id
    ) $scheduleRows[1].pair_id 'smoke schedule paired pair_id'
    Assert-JsonStringArrayEquals @(
        $scheduleRows[0].white_role,
        $scheduleRows[0].black_role,
        $scheduleRows[1].white_role,
        $scheduleRows[1].black_role
    ) @('current-v3', 'run3b', 'run3b', 'current-v3') (
        'smoke schedule role inversion'
    )

    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $payload = [System.IO.File]::ReadAllBytes($gamesState.Path)
    $lines = @($utf8.GetString($payload).TrimEnd("`n").Split("`n"))
    if ($games.Count -ne 2 -or $lines.Count -ne 2) {
        throw "smoke games must contain exactly two rows"
    }
    $legs = @()
    $expectedInventory = $receipt.runtime.manifest.runtime.python.child_import_inventory
    for ($index = 0; $index -lt 2; $index++) {
        $leg = Assert-SmokeGame (
            $games[$index]
        ) $scheduleRows[$index] $receipt $expectedInventory (
            "smoke game $index"
        )
        $legs += $leg
    }
    [Array]::Sort($legs)
    if ($legs.Count -ne 2 -or $legs[0] -ne 0 -or $legs[1] -ne 1) {
        throw "smoke color-swapped legs differ"
    }

    $pairId = Assert-JsonString (
        $games[0].pair_id
    ) 'smoke pair_id' -NonEmpty
    Assert-JsonStringEquals (
        $games[1].pair_id
    ) $pairId 'smoke paired pair_id'
    $pairDirectory = Join-Path (
        Join-Path $SmokeOutputRoot '.pair-temporaries'
    ) ("000001-$pairId")
    if (-not (Test-Path -LiteralPath $pairDirectory -PathType Container)) {
        throw "smoke staged pair directory is absent"
    }
    $expectedPairFiles = @(
        'leg-0.json',
        'leg-0.stderr.bin',
        'leg-0.stdout.bin',
        'leg-1.json',
        'leg-1.stderr.bin',
        'leg-1.stdout.bin',
        'pair.json'
    )
    $actualPairFiles = @(
        Get-ChildItem -LiteralPath $pairDirectory -File -Force |
            ForEach-Object { $_.Name }
    )
    [Array]::Sort($actualPairFiles, [System.StringComparer]::Ordinal)
    if (
        $actualPairFiles.Count -ne $expectedPairFiles.Count -or
        (Compare-Object $expectedPairFiles $actualPairFiles)
    ) {
        throw "smoke staged pair files differ"
    }
    for ($leg = 0; $leg -lt 2; $leg++) {
        $stagedJson = Get-StableFileState (
            Join-Path $pairDirectory "leg-$leg.json"
        ) "smoke staged leg $leg JSON"
        $expectedJson = $utf8.GetBytes($lines[$leg] + "`n")
        if (
            $stagedJson.Sha256 -cne
            (Get-ByteArraySha256 $expectedJson) -or
            $stagedJson.SizeBytes -ne $expectedJson.Length
        ) {
            throw "smoke staged leg $leg JSON differs"
        }
        $stdout = Get-StableFileState (
            Join-Path $pairDirectory "leg-$leg.stdout.bin"
        ) "smoke staged leg $leg stdout"
        $stderr = Get-StableFileState (
            Join-Path $pairDirectory "leg-$leg.stderr.bin"
        ) "smoke staged leg $leg stderr"
        Assert-JsonStringEquals (
            $games[$leg].stdout_sha256
        ) $stdout.Sha256 "smoke leg $leg stdout SHA-256"
        Assert-JsonStringEquals (
            $games[$leg].stderr_sha256
        ) $stderr.Sha256 "smoke leg $leg stderr SHA-256"
        if (
            $stderr.SizeBytes -ne 0 -or
            $stderr.Sha256 -cne $EmptySha256
        ) {
            throw "smoke leg $leg stderr is not byte-empty"
        }
    }
    $pair = (Read-StrictJson (
        (Join-Path $pairDirectory 'pair.json')
    ) 'smoke staged pair receipt').Value
    Assert-ExactProperties $pair @(
        'legs', 'pair_id', 'rows_sha256', 'schema'
    ) 'smoke staged pair receipt'
    Assert-JsonStringEquals (
        $pair.schema
    ) 'atomic-e00-pair-v1' 'smoke staged pair schema'
    Assert-JsonStringEquals (
        $pair.pair_id
    ) $pairId 'smoke staged pair_id'
    Assert-JsonStringEquals (
        $pair.rows_sha256
    ) $gamesState.Sha256 'smoke staged rows SHA-256'
    Assert-JsonArray $pair.legs 'smoke staged legs'
    if (@($pair.legs).Count -ne 2) {
        throw "smoke staged legs count differs"
    }
    Assert-JsonIntegerEquals @($pair.legs)[0] 0 'smoke staged leg 0'
    Assert-JsonIntegerEquals @($pair.legs)[1] 1 'smoke staged leg 1'

    $expectedRootEntries = @(
        '.input-snapshots',
        '.pair-temporaries',
        '.runtime-package',
        'games.jsonl',
        'inventory.json',
        'receipt.json',
        'rejections.jsonl'
    )
    $actualRootEntries = @(
        Get-ChildItem -LiteralPath $SmokeOutputRoot -Force |
            ForEach-Object { $_.Name }
    )
    [Array]::Sort($actualRootEntries, [System.StringComparer]::Ordinal)
    if (
        $actualRootEntries.Count -ne $expectedRootEntries.Count -or
        (Compare-Object $expectedRootEntries $actualRootEntries)
    ) {
        throw "smoke output root contains missing or unexpected entries"
    }

    $inventory = (Read-StrictJson (
        (Join-Path $SmokeOutputRoot 'inventory.json')
    ) 'smoke output inventory').Value
    Assert-ExactProperties $inventory @(
        'entries', 'schema'
    ) 'smoke output inventory'
    Assert-JsonStringEquals (
        $inventory.schema
    ) 'atomic-e00-inventory-v1' 'smoke output inventory schema'
    Assert-JsonArray $inventory.entries 'smoke output inventory entries'
    $inventoryEntries = @($inventory.entries)
    if ($inventoryEntries.Count -ne 9) {
        throw "smoke output inventory must contain exactly nine entries"
    }
    $expectedInventoryPaths = @(
        'games.jsonl',
        'rejections.jsonl'
    ) + @(
        $expectedPairFiles | ForEach-Object {
            ".pair-temporaries/000001-$pairId/$_"
        }
    )
    for ($index = 0; $index -lt $inventoryEntries.Count; $index++) {
        $entry = $inventoryEntries[$index]
        Assert-ExactProperties $entry @(
            'kind', 'path', 'sha256', 'size_bytes'
        ) "smoke output inventory entry $index"
        Assert-JsonStringEquals (
            $entry.path
        ) $expectedInventoryPaths[$index] (
            "smoke output inventory entry $index path"
        )
        $expectedKind = if ($index -eq 0) {
            'accepted-games'
        }
        elseif ($index -eq 1) {
            'rejection-ledger'
        }
        else {
            'pair-staging'
        }
        Assert-JsonStringEquals (
            $entry.kind
        ) $expectedKind "smoke output inventory entry $index kind"
        $entryPath = Join-Path (
            $SmokeOutputRoot
        ) $entry.path.Replace('/', '\')
        Assert-FileBinding (
            $entryPath
        ) $entry.sha256 $entry.size_bytes (
            "smoke output inventory entry $index"
        ) | Out-Null
    }

    $wins = @($games | Where-Object {
        $_.result_current -ceq 'win'
    }).Count
    $losses = @($games | Where-Object {
        $_.result_current -ceq 'loss'
    }).Count
    $draws = @($games | Where-Object {
        $_.result_current -ceq 'draw'
    }).Count
    $timeLosses = @($games | Where-Object {
        $_.time_loss -is [bool] -and $_.time_loss
    }).Count
    Assert-JsonIntegerEquals (
        $receipt.reconciliation.wins_current
    ) $wins 'smoke reconciliation wins_current'
    Assert-JsonIntegerEquals (
        $receipt.reconciliation.losses_current
    ) $losses 'smoke reconciliation losses_current'
    Assert-JsonIntegerEquals (
        $receipt.reconciliation.draws
    ) $draws 'smoke reconciliation draws'
    Assert-JsonIntegerEquals (
        $receipt.reconciliation.time_losses
    ) $timeLosses 'smoke reconciliation time_losses'
    return [pscustomobject]@{
        ReceiptPath = $receiptPath
        ReceiptState = $receiptDocument.State
        GamesState = $gamesState
    }
}

function New-Schedule(
    [string] $Name,
    [string] $Seed,
    [string[]] $TimeControls,
    [long] $Pairs,
    [long] $Games,
    [object[]] $ExpectedTimeControls,
    [string] $Step
) {
    $root = Join-Path $DesignRoot $Name
    New-Item -ItemType Directory -Path $root | Out-Null
    $schedulePath = Join-Path $root 'schedule.jsonl'
    $receiptPath = Join-Path $root 'schedule.receipt.json'
    $arguments = @(
        '-B', '-m', 'tools.atomic_mining.build_e00_schedule',
        '--book', $BookPath,
        '--seed', $Seed
    )
    foreach ($timeControl in $TimeControls) {
        $arguments += @('--tc', $timeControl)
    }
    $arguments += @(
        '--output', $schedulePath,
        '--receipt', $receiptPath
    )
    $summaryDocument = Invoke-StrictPythonCli $Step $arguments
    Assert-ScheduleSummary (
        $summaryDocument.Value
    ) $Pairs $Games $schedulePath $receiptPath $Name
    $receiptState = Assert-ScheduleReceipt (
        $receiptPath
    ) $Seed $Pairs $Games $ExpectedTimeControls $Name
    return [pscustomobject]@{
        Root = $root
        SchedulePath = $schedulePath
        ScheduleState = Get-StableFileState $schedulePath "$Name schedule"
        ReceiptPath = $receiptPath
        ReceiptState = $receiptState
    }
}

function New-Runtime(
    [string] $Name,
    [object] $Schedule,
    [long] $MaximumWallSeconds,
    [object] $Source,
    [System.Collections.IDictionary] $Inputs,
    [System.Collections.IDictionary] $Modules,
    [object] $Native,
    [string] $Step
) {
    $root = Join-Path $DesignRoot $Name
    $arguments = @(
        '-B', '-m', 'tools.atomic_mining.build_e00_runtime_manifest',
        '--output-dir', $root,
        '--source-root', $Source.Root,
        '--expected-source-commit', $Source.Commit,
        '--expected-source-tree', $Source.Tree,
        '--book', $Inputs.book.Path,
        '--book-sha256', $Inputs.book.Sha256,
        '--engine', $Inputs.engine.Path,
        '--engine-sha256', $Inputs.engine.Sha256,
        '--current-net', $Inputs.current_net.Path,
        '--current-net-sha256', $Inputs.current_net.Sha256,
        '--teacher-net', $Inputs.teacher_net.Path,
        '--teacher-net-sha256', $Inputs.teacher_net.Sha256,
        '--variant-config', $Inputs.variant_config.Path,
        '--variant-config-sha256', $Inputs.variant_config.Sha256,
        '--pyffish', $Native.BindingState.Path,
        '--pyffish-sha256', $Native.BindingState.Sha256,
        '--pyffish-build-manifest', $Native.ManifestState.Path,
        '--pyffish-build-manifest-sha256', $Native.ManifestState.Sha256,
        '--runner', $Modules.runner.Path,
        '--runner-sha256', $Modules.runner.Sha256,
        '--schedule', $Schedule.SchedulePath,
        '--schedule-sha256', $Schedule.ScheduleState.Sha256,
        '--schedule-receipt', $Schedule.ReceiptPath,
        '--schedule-receipt-sha256', $Schedule.ReceiptState.Sha256,
        '--tools-init-sha256', $Modules.tools_init.Sha256,
        '--atomic-mining-init-sha256', $Modules.atomic_mining_init.Sha256,
        '--common-sha256', $Modules.common.Sha256,
        '--schedule-builder-sha256', $Modules.schedule_builder.Sha256,
        '--uci-session-sha256', $Modules.uci_session.Sha256,
        '--atomic-outcome-helper-sha256',
            $Modules.atomic_outcome_helper.Sha256,
        '--binding-builder-sha256', $Modules.binding_builder.Sha256,
        '--owned-process-sha256', $Modules.owned_process.Sha256,
        '--threads', '1',
        '--maximum-plies', '1024',
        '--command-timeout-seconds', '120',
        '--maximum-wall-seconds', ([string] $MaximumWallSeconds),
        '--maximum-game-wall-seconds', '1800',
        '--discovery-timeout-seconds', '120'
    )
    $summary = (Invoke-StrictPythonCli $Step $arguments).Value
    $expectedRuntimeInputs = [ordered]@{
        book = $Inputs.book
        engine = $Inputs.engine
        current_net = $Inputs.current_net
        teacher_net = $Inputs.teacher_net
        variant_config = $Inputs.variant_config
        pyffish = $Native.BindingState
        pyffish_build_manifest = $Native.ManifestState
        runner = $Modules.runner
        schedule = $Schedule.ScheduleState
        schedule_receipt = $Schedule.ReceiptState
    }
    foreach ($name in $Modules.Keys) {
        if ($name -ne 'runner') {
            $expectedRuntimeInputs[$name] = $Modules[$name]
        }
    }
    return Assert-RuntimeSummary (
        $summary
    ) $root $MaximumWallSeconds $Source $expectedRuntimeInputs $Name
}

function Get-RunnerArguments(
    [object] $Source,
    [System.Collections.IDictionary] $Inputs,
    [System.Collections.IDictionary] $Modules,
    [object] $Native,
    [object] $Schedule,
    [object] $Runtime
) {
    return @(
        '-B', '-m', 'tools.atomic_mining.run_e00_source',
        '--schedule', $Schedule.SchedulePath,
        '--schedule-receipt', $Schedule.ReceiptPath,
        '--output-dir', $SmokeOutputRoot,
        '--experiment-id', $ExperimentId,
        '--battery-id', $SmokeBatteryId,
        '--runtime-manifest', $Runtime.ManifestPath,
        '--runtime-manifest-sha256', $Runtime.ManifestState.Sha256,
        '--book', $Inputs.book.Path,
        '--book-sha256', $Inputs.book.Sha256,
        '--engine', $Inputs.engine.Path,
        '--engine-sha256', $Inputs.engine.Sha256,
        '--current-net', $Inputs.current_net.Path,
        '--current-net-sha256', $Inputs.current_net.Sha256,
        '--teacher-net', $Inputs.teacher_net.Path,
        '--teacher-net-sha256', $Inputs.teacher_net.Sha256,
        '--variant-config', $Inputs.variant_config.Path,
        '--variant-config-sha256', $Inputs.variant_config.Sha256,
        '--pyffish', $Native.BindingState.Path,
        '--pyffish-sha256', $Native.BindingState.Sha256,
        '--pyffish-build-manifest', $Native.ManifestState.Path,
        '--pyffish-build-manifest-sha256', $Native.ManifestState.Sha256,
        '--runner', $Modules.runner.Path,
        '--runner-sha256', $Modules.runner.Sha256,
        '--rules-source-root', $Source.Root,
        '--rules-source-commit', $Source.Commit,
        '--tools-init-sha256', $Modules.tools_init.Sha256,
        '--atomic-mining-init-sha256', $Modules.atomic_mining_init.Sha256,
        '--common-sha256', $Modules.common.Sha256,
        '--schedule-builder-sha256', $Modules.schedule_builder.Sha256,
        '--uci-session-sha256', $Modules.uci_session.Sha256,
        '--atomic-outcome-helper-sha256',
            $Modules.atomic_outcome_helper.Sha256,
        '--binding-builder-sha256', $Modules.binding_builder.Sha256,
        '--owned-process-sha256', $Modules.owned_process.Sha256,
        '--threads', '1',
        '--maximum-plies', '1024',
        '--command-timeout-seconds', '120',
        '--maximum-wall-seconds', '3600',
        '--maximum-game-wall-seconds', '1800'
    )
}

$RepositoryRoot = Resolve-RepositoryRoot (
    $RepositoryRoot
) $PSCommandPath 'invoke_e00_prepare_smoke.ps1'

$targetRoots = @{
    capture_root = $CaptureRoot
    design_root = $DesignRoot
    full_output_root = $FullOutputRoot
    smoke_output_root = $SmokeOutputRoot
}
Assert-FreshDisjointRoots $targetRoots
Assert-PathOutside $CaptureRoot $DesignRoot 'capture root'

$source = Assert-RepositoryState
foreach ($targetName in $targetRoots.Keys) {
    Assert-PathOutside (
        [string] $targetRoots[$targetName]
    ) $source.Root $targetName
}
$inputs = Get-InputStates
$modulePaths = Get-ModulePaths $source.Root
$modules = Get-ModuleStates $modulePaths

$planned = [ordered]@{
    capture_root = Get-AbsolutePath $CaptureRoot
    design_root = Get-AbsolutePath $DesignRoot
    experiment_id = $ExperimentId
    full = [ordered]@{
        battery_id = $FullBatteryId
        output_root = Get-AbsolutePath $FullOutputRoot
        pairs = 84
        seed = $FullSeed
    }
    mode = if ($ValidateOnly) { 'validate-only' } else { 'prepare-smoke' }
    schema = 'atomic-e00-launch5-prepare-smoke-validation-v1'
    smoke = [ordered]@{
        battery_id = $SmokeBatteryId
        output_root = Get-AbsolutePath $SmokeOutputRoot
        pairs = 1
        seed = $SmokeSeed
    }
    source = [ordered]@{
        commit = $source.Commit
        root = $source.Root
        tree = $source.Tree
    }
    status = 'validated-not-started'
}

if ($ValidateOnly) {
    $planned | ConvertTo-Json -Compress -Depth 16
    exit 0
}

# The first mutation occurs only after the complete read-only preflight.
New-Item -ItemType Directory -Path $CaptureRoot | Out-Null
New-Item -ItemType Directory -Path $DesignRoot | Out-Null

$nativeRoot = Join-Path $DesignRoot 'native-binding'
$nativeArguments = @(
    '-B', '-m', 'tools.atomic_mining.build_atomic_outcome_binding_cli',
    '--source-root', $source.Root,
    '--expected-source-commit', $source.Commit,
    '--expected-source-tree', $source.Tree,
    '--output-dir', $nativeRoot,
    '--timeout-seconds', '300'
)
$nativeSummary = (
    Invoke-StrictPythonCli '01-native-binding' $nativeArguments
).Value
Assert-ExactProperties $nativeSummary @(
    'binding', 'build_manifest', 'schema', 'source'
) 'native builder summary'
Assert-JsonStringEquals (
    $nativeSummary.schema
) $NativeBuildSummarySchema 'native builder summary schema'
Assert-JsonStringEquals (
    $nativeSummary.source.commit
) $source.Commit 'native builder source commit'
Assert-JsonStringEquals (
    $nativeSummary.source.tree
) $source.Tree 'native builder source tree'
$nativeSourceRoot = Assert-JsonString (
    $nativeSummary.source.root
) 'native builder source root' -NonEmpty
$nativeManifestPath = Assert-JsonString (
    $nativeSummary.build_manifest.path
) 'native builder manifest path' -NonEmpty
if (
    -not [string]::Equals(
        (Get-AbsolutePath $nativeSourceRoot),
        (Get-AbsolutePath ([string] $source.Root)),
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    -not [string]::Equals(
        (Get-AbsolutePath $nativeManifestPath),
        (Get-AbsolutePath (Join-Path $nativeRoot 'manifest.json')),
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "native builder summary differs"
}
$nativeBindingRoot = Join-Path $nativeRoot 'binding'
$nativeBindings = @(
    Get-ChildItem -LiteralPath $nativeBindingRoot -File -Force |
        Where-Object {
            $_.Name -clike 'pyffish*.pyd' -and
            -not (
                $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint
            )
        }
)
if (
    $nativeBindings.Count -ne 1 -or
    -not [string]::Equals(
        (Get-AbsolutePath ([string] $nativeSummary.binding.path)),
        (Get-AbsolutePath $nativeBindings[0].FullName),
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "native binding namespace differs"
}
$native = [pscustomobject]@{
    BindingState = Assert-FileBinding (
        [string] $nativeSummary.binding.path
    ) $nativeSummary.binding.sha256 (
        $nativeSummary.binding.size_bytes
    ) 'native pyffish binding'
    ManifestState = Assert-FileBinding (
        [string] $nativeSummary.build_manifest.path
    ) $nativeSummary.build_manifest.sha256 (
        $nativeSummary.build_manifest.size_bytes
    ) 'native build manifest'
}

$smokeTimeControls = @(
    [pscustomobject]@{
        name = 'VSTC'
        base_ms = 2000
        increment_ms = 20
        pairs = 1
    }
)
$fullTimeControls = @(
    [pscustomobject]@{
        name = 'VSTC'
        base_ms = 2000
        increment_ms = 20
        pairs = 48
    },
    [pscustomobject]@{
        name = 'STC'
        base_ms = 10000
        increment_ms = 100
        pairs = 24
    },
    [pscustomobject]@{
        name = 'LTC'
        base_ms = 30000
        increment_ms = 300
        pairs = 12
    }
)
$smokeSchedule = New-Schedule (
    'smoke-schedule'
) $SmokeSeed @('VSTC:2000:20:1') 1 2 (
    $smokeTimeControls
) '02-smoke-schedule'
$fullSchedule = New-Schedule (
    'full-schedule'
) $FullSeed @(
    'VSTC:2000:20:48',
    'STC:10000:100:24',
    'LTC:30000:300:12'
) 84 168 $fullTimeControls '03-full-schedule'

$smokeRuntime = New-Runtime (
    'smoke-runtime'
) $smokeSchedule 3600 $source $inputs $modules $native (
    '04-smoke-runtime'
)
$fullRuntime = New-Runtime (
    'full-runtime'
) $fullSchedule 14400 $source $inputs $modules $native (
    '05-full-runtime'
)

$seal = Publish-DesignSeal (
    $source
) $inputs $native $smokeSchedule $fullSchedule $smokeRuntime $fullRuntime
Assert-DesignSeal $seal
Assert-RepositoryState | Out-Null
$beforeSmokeInputs = Get-InputStates
Assert-InputStatesEqual $inputs $beforeSmokeInputs 'pre-smoke input'

$smokeArguments = Get-RunnerArguments (
    $source
) $inputs $modules $native $smokeSchedule $smokeRuntime
$smokeSummary = (
    Invoke-StrictPythonCli '06-smoke' $smokeArguments
).Value
Assert-ExactProperties $smokeSummary @(
    'draws',
    'games',
    'games_sha256',
    'losses_current',
    'pairs',
    'receipt_sha256',
    'time_losses',
    'wins_current'
) 'smoke runner summary'
Assert-JsonIntegerEquals (
    $smokeSummary.pairs
) 1 'smoke runner summary pairs'
Assert-JsonIntegerEquals (
    $smokeSummary.games
) 2 'smoke runner summary games'
foreach ($field in @(
    'wins_current', 'losses_current', 'draws', 'time_losses'
)) {
    Assert-JsonIntegerMinimum (
        $smokeSummary.$field
    ) 0 "smoke runner summary $field" | Out-Null
}
Assert-LowerSha256 (
    $smokeSummary.games_sha256
) 'smoke runner summary games SHA-256' | Out-Null
Assert-LowerSha256 (
    $smokeSummary.receipt_sha256
) 'smoke runner summary receipt SHA-256' | Out-Null
$smoke = Assert-SmokeExecution (
    $smokeSchedule.SchedulePath
) $smokeSchedule.ReceiptPath
Assert-JsonStringEquals (
    $smokeSummary.receipt_sha256
) $smoke.ReceiptState.Sha256 'smoke summary receipt SHA-256'
Assert-JsonStringEquals (
    $smokeSummary.games_sha256
) $smoke.GamesState.Sha256 'smoke summary games SHA-256'

Assert-DesignSeal $seal
$afterSmokeInputs = Get-InputStates
Assert-InputStatesEqual $inputs $afterSmokeInputs 'post-smoke input'
Assert-RepositoryState | Out-Null
if (Test-Path -LiteralPath $FullOutputRoot) {
    throw "full output root appeared; full execution is forbidden here"
}

[ordered]@{
    design = [ordered]@{
        inventory_sha256 = $seal.InventoryState.Sha256
        receipt_sha256 = $seal.ReceiptState.Sha256
        root = Get-AbsolutePath $DesignRoot
    }
    experiment_id = $ExperimentId
    full = [ordered]@{
        battery_id = $FullBatteryId
        output_absent = $true
        runtime_build_receipt_sha256 =
            $fullRuntime.BuildReceiptState.Sha256
        schedule_receipt_sha256 = $fullSchedule.ReceiptState.Sha256
    }
    schema = 'atomic-e00-launch5-prepare-smoke-summary-v1'
    smoke = [ordered]@{
        battery_id = $SmokeBatteryId
        games_sha256 = $smoke.GamesState.Sha256
        receipt_sha256 = $smoke.ReceiptState.Sha256
        status = 'committed'
    }
    source = [ordered]@{
        commit = $source.Commit
        tree = $source.Tree
    }
    status = 'sealed-smoke-committed'
} | ConvertTo-Json -Compress -Depth 16
