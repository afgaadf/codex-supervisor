# Local test certificate only. Public distribution must use a CA code-signing certificate.
param(
  [string]$Subject = "CN=Codex Supervisor Dev",
  [string]$OutDir = (Join-Path $PSScriptRoot "local-cert")
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$cert = New-SelfSignedCertificate `
  -Type Custom `
  -Subject $Subject `
  -FriendlyName "Codex Supervisor Dev" `
  -KeyUsage DigitalSignature `
  -KeyExportPolicy Exportable `
  -NotAfter (Get-Date).AddYears(1) `
  -CertStoreLocation "Cert:\CurrentUser\My" `
  -TextExtension @(
    "2.5.29.37={text}1.3.6.1.5.5.7.3.3",
    "2.5.29.19={text}"
  )
$pwd = ConvertTo-SecureString -String "CodexSupervisorDev!" -AsPlainText -Force
$pfx = Join-Path $OutDir "CodexSupervisorDev.pfx"
$cer = Join-Path $OutDir "CodexSupervisorDev.cer"
Export-PfxCertificate -Cert $cert -FilePath $pfx -Password $pwd | Out-Null
Export-Certificate -Cert $cert -FilePath $cer -Type CERT | Out-Null
Import-Certificate -FilePath $cer -CertStoreLocation "Cert:\CurrentUser\TrustedPeople" | Out-Null
Write-Output "THUMBPRINT=$($cert.Thumbprint)"
Write-Output "PFX=$pfx"
Write-Output "CER=$cer"