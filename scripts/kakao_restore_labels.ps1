param(
    [Parameter(Mandatory=$true)][int]$Left,
    [Parameter(Mandatory=$true)][int]$Top,
    [Parameter(Mandatory=$true)][ValidateRange(16, 900)][int]$Width,
    [Parameter(Mandatory=$true)][ValidateRange(16, 900)][int]$Height
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Media.Ocr, ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1

function Wait-LabelOperation($operation, [Type]$resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.GetAwaiter().GetResult()
}

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ko'))
if ($null -eq $engine) { exit 3 }

[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$scale = 3
$bitmap = [Drawing.Bitmap]::new($Width, $Height)
$image = [Drawing.Bitmap]::new($Width * $scale, $Height * $scale)
$memory = [IO.MemoryStream]::new()
try {
    $screen = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $screen.CopyFromScreen($Left, $Top, 0, 0, $bitmap.Size)
    } finally {
        $screen.Dispose()
    }
    $graphics = [Drawing.Graphics]::FromImage($image)
    try {
        $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.DrawImage($bitmap, 0, 0, $image.Width, $image.Height)
    } finally {
        $graphics.Dispose()
    }
    $image.Save($memory, [Drawing.Imaging.ImageFormat]::Png)
    $memory.Position = 0
    $stream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($memory)
    try {
        $decoder = Wait-LabelOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $softwareBitmap = Wait-LabelOperation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $result = Wait-LabelOperation ($engine.RecognizeAsync($softwareBitmap)) ([Windows.Media.Ocr.OcrResult])
            $lines = @(foreach ($line in $result.Lines) {
                $words = @($line.Words | ForEach-Object { $_ })
                if ($words.Length -eq 0) { continue }
                $first = $words[0].BoundingRect
                $last = $words[-1].BoundingRect
                [pscustomobject]@{
                    text = $line.Text
                    x = [int](($first.X + $last.Right) / (2 * $scale))
                    y = [int](($first.Y + $first.Height / 2) / $scale)
                }
            })
            ConvertTo-Json -InputObject $lines -Compress
        } finally {
            $softwareBitmap.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
} finally {
    $memory.Dispose()
    $image.Dispose()
    $bitmap.Dispose()
}
