# 컴프야V26 게임 창 좌표 확인 스크립트
# 게임 PC에서 컴프야V26을 실행한 상태로 이 스크립트 실행

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")]
    public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);
    public struct RECT {
        public int Left, Top, Right, Bottom;
    }
}
"@

$hwnd = [Win32]::FindWindow($null, "컴프야V26")

if ($hwnd -eq [IntPtr]::Zero) {
    Write-Host "[-] 컴프야V26 창을 찾을 수 없습니다. 게임을 먼저 실행해주세요." -ForegroundColor Red
    exit 1
}

$rect = New-Object Win32+RECT
[Win32]::GetWindowRect($hwnd, [ref]$rect) | Out-Null

$width  = $rect.Right  - $rect.Left
$height = $rect.Bottom - $rect.Top

Write-Host ""
Write-Host "=== 컴프야V26 창 좌표 ===" -ForegroundColor Cyan
Write-Host "Left   = $($rect.Left)"
Write-Host "Top    = $($rect.Top)"
Write-Host "Right  = $($rect.Right)"
Write-Host "Bottom = $($rect.Bottom)"
Write-Host "Width  = $width"
Write-Host "Height = $height"
Write-Host ""
Write-Host "config_cpbv.py 에 아래 값을 입력하세요:" -ForegroundColor Yellow
Write-Host "WINDOW = ($($rect.Left), $($rect.Top), $($rect.Right), $($rect.Bottom))"
