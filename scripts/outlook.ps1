# Scout's bridge to Outlook.com, through Microsoft's own Graph PowerShell module.
# It asks for Mail.ReadWrite and never Mail.Send, so it can save drafts but cannot send anything.
param([Parameter(Mandatory)][ValidateSet('login','whoami','draft','sent')][string]$Verb, [string]$InFile, [string]$Since)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
function Emit($o) { [Console]::Out.WriteLine(($o | ConvertTo-Json -Depth 6 -Compress)); [Console]::Out.Flush() }
try {
  $mod = Get-ChildItem "$env:LOCALAPPDATA\Scout\modules\Microsoft.Graph.Authentication\*\Microsoft.Graph.Authentication.psd1" | Sort-Object FullName | Select-Object -Last 1
  if (-not $mod) { throw 'The Microsoft Graph module is missing from %LOCALAPPDATA%\Scout\modules.' }
  Import-Module $mod.FullName
  $scopes = @('User.Read', 'Mail.ReadWrite')
  $record = "$env:USERPROFILE\.mg\mg.authrecord.json"
  if ($Verb -eq 'login') {
    # Connect-MgGraph signs in to one token cache and then reads another, so it asks for two codes, and drops
    # the lot if both aren't done within 120 seconds. Signing in to the cache it reads takes one code and no rush.
    $o = New-Object Azure.Identity.DeviceCodeCredentialOptions
    $o.ClientId = '14d82eec-204b-4c2f-b7e8-296a70dab67e'  # Microsoft Graph Command Line Tools
    $o.TenantId = 'consumers'
    $o.AuthorityHost = [Uri]'https://login.microsoftonline.com/consumers'
    $o.TokenCachePersistenceOptions = New-Object Azure.Identity.TokenCachePersistenceOptions -Property @{ Name = 'mg.msal.cache' }
    $ctx = New-Object Azure.Core.TokenRequestContext -ArgumentList ([string[]]$scopes), $null, $null, $null, $true
    $cred = New-Object Azure.Identity.DeviceCodeCredential -ArgumentList $o
    $signedIn = $cred.Authenticate($ctx, [Threading.CancellationToken]::None)
    New-Item -ItemType Directory -Force (Split-Path $record) | Out-Null
    $file = [IO.File]::Create($record)
    try { $signedIn.Serialize($file, [Threading.CancellationToken]::None) } finally { $file.Dispose() }
  }
  # Without this, Connect-MgGraph would start a sign-in nobody can see, and wipe the saved one when it gives up.
  if (-not (Test-Path $record)) { throw 'Not signed in to Outlook. Run `uv run scout outreach login`.' }
  Connect-MgGraph -TenantId consumers -Scopes $scopes -ContextScope CurrentUser -NoWelcome -UseDeviceCode | Out-Host
  $g = 'https://graph.microsoft.com/v1.0/me'
  $me = Invoke-MgGraphRequest GET "$g`?`$select=userPrincipalName,mail" -OutputType PSObject
  $account = if ($me.mail) { $me.mail } else { $me.userPrincipalName }
  Emit @{ type = 'account'; account = $account }
  if ($Verb -eq 'draft') {
    $job = Get-Content -Raw -Encoding UTF8 $InFile | ConvertFrom-Json
    if ($account -ne $job.account) { throw "Signed in as $account, but drafts belong in $($job.account)." }
    foreach ($m in $job.messages) {
      $body = [Text.Encoding]::UTF8.GetBytes(($m.message | ConvertTo-Json -Depth 8 -Compress))
      $r = Invoke-MgGraphRequest POST "$g/messages" -Body $body -ContentType 'application/json; charset=utf-8' -OutputType PSObject
      Emit @{ type = 'draft'; key = $m.key; id = $r.id }
    }
  }
  if ($Verb -eq 'sent') {
    $url = "$g/mailFolders/sentitems/messages?`$select=subject,toRecipients,ccRecipients,sentDateTime&`$filter=sentDateTime ge $Since&`$orderby=sentDateTime&`$top=100"
    while ($url) {
      $page = Invoke-MgGraphRequest GET $url -OutputType Json | ConvertFrom-Json
      foreach ($m in $page.value) {
        $to = @(@($m.toRecipients) + @($m.ccRecipients) | Where-Object { $_ } | ForEach-Object { $_.emailAddress.address })
        Emit @{ type = 'sent'; to = $to; subject = $m.subject; at = $m.sentDateTime }
      }
      $url = $page.'@odata.nextLink'
    }
  }
  Emit @{ type = 'done' }
} catch { Emit @{ type = 'error'; error = "$($_.Exception.Message)" }; exit 1 }
