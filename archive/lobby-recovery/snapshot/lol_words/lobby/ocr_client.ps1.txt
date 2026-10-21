# 仅对调用方创建的客户端图像进行本机 OCR，不读取其他文件或发送网络请求。
param([Parameter(Mandatory=$true)][string]$ImagePath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
$awaitMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Wait-WinRT($operation, $resultType) {
    $task = $awaitMethod.MakeGenericMethod($resultType).Invoke($null,@($operation))
    if (-not $task.Wait(2500)) { throw 'OCR timeout' }
    return $task.Result
}
$stream = $null
try {
    $file = Wait-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
    $stream = Wait-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Wait-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Wait-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) { throw 'OCR unavailable' }
    $result = Wait-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $ocrItems = @()
    foreach ($line in $result.Lines) {
        $text = $line.Text -replace '\s',''
        if ($text -notmatch '跳过.*(结算|等待|统计)|(?i)skip.*(waiting|stats)') { continue }
        $left = [double]::PositiveInfinity; $top = [double]::PositiveInfinity
        $right = 0; $bottom = 0
        foreach ($word in $line.Words) {
            $rect = $word.BoundingRect
            $left = [Math]::Min($left,$rect.X); $top = [Math]::Min($top,$rect.Y)
            $right = [Math]::Max($right,$rect.X+$rect.Width); $bottom = [Math]::Max($bottom,$rect.Y+$rect.Height)
        }
        $ocrItems += @{text=$text;rect=@($left,$top,$right,$bottom)}
    }
    ConvertTo-Json -InputObject @{items=@($ocrItems)} -Compress -Depth 5
} catch {
    '{"items":[],"reason":"本机文字识别不可用"}'
} finally {
    if ($null -ne $stream) { $stream.Dispose() }
}
