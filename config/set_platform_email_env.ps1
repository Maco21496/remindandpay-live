# FINAL VERSION OF set_platform_email_env.ps1
param(
  [Parameter(Mandatory = $true)]
  [string]$PostmarkServerToken
)

function Redact([string]$v, [int]$keep = 4) {
  if ([string]::IsNullOrWhiteSpace($v)) { return "(unset)" }
  $v = $v.Trim()
  if ($v.Length -le ($keep * 2)) { return ("*" * $v.Length) }
  return ($v.Substring(0, $keep) + "..." + $v.Substring($v.Length - $keep, $keep))
}

Write-Host "Setting machine-scope environment variables for Remind & Pay..." -ForegroundColor Cyan

# 1) POSTMARK_SERVER_TOKEN_DEFAULT - set/update to the provided value
[Environment]::SetEnvironmentVariable("POSTMARK_SERVER_TOKEN_DEFAULT", $PostmarkServerToken, "Machine")

# 2) APP_SECRETS_KEY - generate once if missing (32 bytes, Base64)
$existingKey = [Environment]::GetEnvironmentVariable("APP_SECRETS_KEY", "Machine")
if ([string]::IsNullOrWhiteSpace($existingKey)) {
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
  $newKey = [Convert]::ToBase64String($bytes)
  [Environment]::SetEnvironmentVariable("APP_SECRETS_KEY", $newKey, "Machine")
  Write-Host "Generated new APP_SECRETS_KEY (Base64, 32 bytes)." -ForegroundColor Green
} else {
  Write-Host "APP_SECRETS_KEY already set - leaving unchanged." -ForegroundColor Yellow
}

# Show results (redacted)
$pm = [Environment]::GetEnvironmentVariable("POSTMARK_SERVER_TOKEN_DEFAULT", "Machine")
$ak = [Environment]::GetEnvironmentVariable("APP_SECRETS_KEY", "Machine")

Write-Host "`n=== MACHINE scope now ===" -ForegroundColor Cyan
Write-Host ("POSTMARK_SERVER_TOKEN_DEFAULT: {0}" -f (Redact $pm 4))
Write-Host ("APP_SECRETS_KEY: {0}" -f (Redact $ak 4))

Write-Host "`nDone. Restart your app/IIS worker to pick up changes." -ForegroundColor Cyan
